"""리스크 관리.

AI 판단을 그대로 실행하지 않고, 한도/잔고/신뢰도 검사를 거쳐 주문 가능 여부를 결정한다.
일일 손실 한도는 db.daily_pnl(오늘 행)에서 가져오므로 재시작에도 유지된다.
"""
from __future__ import annotations

from dataclasses import dataclass

import config
import db


@dataclass
class Order:
    side: str          # "buy" | "sell" | "none"
    ticker: str
    krw_amount: float = 0.0
    volume: float = 0.0
    reason: str = ""


def _as_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clip_pct(value) -> float:
    return max(0.0, min(_as_float(value), 100.0))


class RiskManager:
    def evaluate(
        self,
        decision,
        snapshot: dict,
        krw_balance: float,
        coin_balance: float,
        avg_buy_price: float = 0.0,
    ) -> Order:
        ticker = snapshot["ticker"]
        quant_plan = snapshot.get("quant_plan")
        has_quant_plan = isinstance(quant_plan, dict)

        # 자유 모드: AI가 주문 여부·크기를 전적으로 결정. 업비트 최소 주문만 검사.
        if config.FREE_TRADE_MODE:
            return self._evaluate_free(decision, snapshot, krw_balance, coin_balance)

        # 1) 하루 손실 한도 초과 시 전면 중단 (DB에서 조회 → 재시작 무관)
        today_pnl = db.get_today_realized_pnl()
        if today_pnl <= -config.MAX_DAILY_LOSS_KRW:
            return Order(
                "none",
                ticker,
                reason=f"하루 손실 {today_pnl:,.0f}원 ≥ 한도 {config.MAX_DAILY_LOSS_KRW:,}원 → 매매 중단",
            )

        # 2) 신뢰도 미달
        if decision.confidence < config.MIN_CONFIDENCE:
            return Order(
                "none",
                ticker,
                reason=f"신뢰도 {decision.confidence:.2f} < {config.MIN_CONFIDENCE} → 관망",
            )

        # 3) 매수
        if decision.action == "BUY":
            if has_quant_plan and not bool(quant_plan.get("eligible_buy")):
                flags = ", ".join(str(flag) for flag in quant_plan.get("risk_flags") or [])
                detail = f" ({flags})" if flags else ""
                return Order("none", ticker, reason=f"quant plan 매수 불가{detail}")

            amount = min(config.MAX_ORDER_KRW * decision.confidence, config.MAX_ORDER_KRW)
            if has_quant_plan:
                quant_cap = max(0.0, _as_float(quant_plan.get("max_buy_krw"), 0.0))
                amount = min(amount, quant_cap)
            if config.DRY_RUN:
                if amount < config.MIN_ORDER_KRW:
                    reason = "quant plan 주문한도가 최소금액 미만" if has_quant_plan else "주문금액이 최소금액 미만"
                    return Order("none", ticker, reason=reason)
                reason = "quant plan 한도 내 매수 신호" if has_quant_plan else "매수 신호"
                return Order("buy", ticker, krw_amount=round(amount), reason=reason)
            if krw_balance < config.MIN_ORDER_KRW:
                return Order("none", ticker, reason="원화 잔고 부족")
            amount = min(amount, krw_balance)
            if amount < config.MIN_ORDER_KRW:
                return Order("none", ticker, reason="주문 가능 금액이 최소금액 미만")
            reason = "quant plan 한도 내 매수 신호" if has_quant_plan else "매수 신호"
            return Order("buy", ticker, krw_amount=round(amount), reason=reason)

        # 4) 매도
        if decision.action == "SELL":
            if coin_balance <= 0 and not config.DRY_RUN:
                return Order("none", ticker, reason="보유 수량 없음")
            if has_quant_plan:
                trim_pct = _clip_pct(quant_plan.get("trim_pct"))
                if trim_pct <= 0:
                    return Order("none", ticker, reason="quant plan 매도 비중 0% → 관망")
                return Order(
                    "sell",
                    ticker,
                    volume=coin_balance * trim_pct / 100.0,
                    reason=f"quant plan 매도 신호 (보유분의 {trim_pct:g}%)",
                )
            return Order("sell", ticker, volume=coin_balance, reason="매도 신호")

        # 5) 관망
        return Order("none", ticker, reason="HOLD")

    def _evaluate_free(
        self,
        decision,
        snapshot: dict,
        krw_balance: float,
        coin_balance: float,
    ) -> Order:
        ticker = snapshot["ticker"]
        price = float(snapshot.get("price") or 0)
        pct = max(1.0, min(float(getattr(decision, "size_pct", 100.0) or 100.0), 100.0))

        if decision.action == "BUY":
            amount = krw_balance * pct / 100.0
            if config.DRY_RUN:
                amount = max(amount, config.UPBIT_MIN_ORDER_KRW)
                return Order("buy", ticker, krw_amount=round(amount),
                             reason=f"자유모드 매수 (가용 원화의 {pct:.0f}%)")
            if amount < config.UPBIT_MIN_ORDER_KRW:
                return Order(
                    "none", ticker,
                    reason=f"자유모드 매수 {amount:,.0f}원 < 업비트 최소 5,000원 (원화 잔고 부족)",
                )
            return Order("buy", ticker, krw_amount=round(min(amount, krw_balance)),
                         reason=f"자유모드 매수 (가용 원화의 {pct:.0f}%)")

        if decision.action == "SELL":
            if coin_balance <= 0 and not config.DRY_RUN:
                return Order("none", ticker, reason="보유 수량 없음")
            volume = coin_balance * pct / 100.0
            if not config.DRY_RUN and price > 0 and volume * price < config.UPBIT_MIN_ORDER_KRW:
                return Order(
                    "none", ticker,
                    reason=f"자유모드 매도 {volume * price:,.0f}원 < 업비트 최소 5,000원 "
                           f"(전량 매도(size_pct=100)로 재시도 가능)",
                )
            return Order("sell", ticker, volume=volume,
                         reason=f"자유모드 매도 (보유분의 {pct:.0f}%)")

        return Order("none", ticker, reason="HOLD")
