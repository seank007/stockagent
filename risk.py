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


class RiskManager:
    def evaluate(
        self,
        decision,
        snapshot: dict,
        krw_balance: float,
        coin_balance: float,
    ) -> Order:
        ticker = snapshot["ticker"]

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
            amount = min(config.MAX_ORDER_KRW * decision.confidence, config.MAX_ORDER_KRW)
            if config.DRY_RUN:
                if amount < config.MIN_ORDER_KRW:
                    return Order("none", ticker, reason="주문금액이 최소금액 미만")
                return Order("buy", ticker, krw_amount=round(amount), reason="매수 신호")
            if krw_balance < config.MIN_ORDER_KRW:
                return Order("none", ticker, reason="원화 잔고 부족")
            amount = min(amount, krw_balance)
            if amount < config.MIN_ORDER_KRW:
                return Order("none", ticker, reason="주문 가능 금액이 최소금액 미만")
            return Order("buy", ticker, krw_amount=round(amount), reason="매수 신호")

        # 4) 매도
        if decision.action == "SELL":
            if coin_balance <= 0 and not config.DRY_RUN:
                return Order("none", ticker, reason="보유 수량 없음")
            return Order("sell", ticker, volume=coin_balance, reason="매도 신호")

        # 5) 관망
        return Order("none", ticker, reason="HOLD")
