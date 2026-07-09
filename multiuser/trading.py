"""유저별 자동매매 엔진 (베타).

각 사용자의 등록된 키로, 그 사용자 계정에서만 1회 매매 사이클을 돈다.
전역 config/db(단일 봇)와 섞이지 않도록 설정·리스크·기록을 모두 user_id로 격리한다.

안전 기본값
- auto_enabled 기본 off, dry_run 기본 on(모의).
- 실주문은 dry_run=False + 명시적 실행에서만. 수동 "지금 실행"은 항상 모의로만 돈다.
- AI 판단은 서비스 운영자의 provider를 공용으로 쓴다(사용자는 거래소 키만 제공).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from . import db
from .broker_factory import broker_for_user

DEFAULT_TICKERS = ["KRW-BTC", "KRW-ETH"]
MIN_CONFIDENCE = 0.6
UPBIT_MIN_ORDER_KRW = 5_000


# --------------------------------------------------------------- 설정
def get_settings(user_id: int) -> dict:
    conn = db.connection()
    with db.lock():
        row = conn.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO user_settings (user_id, updated_at) VALUES (?, ?)", (user_id, now)
            )
            row = conn.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,)).fetchone()
    return _settings_dict(row)


def update_settings(user_id: int, **fields) -> dict:
    get_settings(user_id)  # 행 보장
    allowed = {"auto_enabled", "dry_run", "tickers", "max_order_krw"}
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "tickers":
            v = _normalize_tickers(v)
        if k in ("auto_enabled", "dry_run"):
            v = 1 if v else 0
        if k == "max_order_krw":
            v = max(UPBIT_MIN_ORDER_KRW, int(v))
        sets.append(f"{k} = ?")
        params.append(v)
    if sets:
        sets.append("updated_at = ?")
        params.append(datetime.now(timezone.utc).isoformat())
        params.append(user_id)
        conn = db.connection()
        with db.lock():
            conn.execute(f"UPDATE user_settings SET {', '.join(sets)} WHERE user_id = ?", params)
    return get_settings(user_id)


def _settings_dict(row) -> dict:
    return {
        "auto_enabled": bool(row["auto_enabled"]),
        "dry_run": bool(row["dry_run"]),
        "tickers": [t for t in (row["tickers"] or "").split(",") if t],
        "max_order_krw": int(row["max_order_krw"]),
        "updated_at": row["updated_at"],
    }


def _normalize_tickers(value) -> str:
    if isinstance(value, str):
        parts = value.split(",")
    else:
        parts = list(value or [])
    out = []
    for p in parts:
        t = str(p).strip().upper()
        if not t:
            continue
        if not t.startswith("KRW-"):
            t = "KRW-" + t
        if t not in out:
            out.append(t)
    return ",".join(out) or ",".join(DEFAULT_TICKERS)


# --------------------------------------------------------------- 리스크(경량·격리)
@dataclass
class Order:
    side: str  # buy | sell | none
    ticker: str
    krw_amount: float = 0.0
    volume: float = 0.0
    reason: str = ""


def evaluate(decision, snapshot: dict, krw_balance: float, coin_balance: float,
             max_order_krw: int, dry_run: bool) -> Order:
    ticker = snapshot["ticker"]
    conf = float(getattr(decision, "confidence", 0) or 0)
    action = str(getattr(decision, "action", "HOLD") or "HOLD").upper()

    if conf < MIN_CONFIDENCE:
        return Order("none", ticker, reason=f"신뢰도 {conf:.2f} < {MIN_CONFIDENCE} → 관망")

    if action == "BUY":
        amount = round(min(max_order_krw * conf, max_order_krw))
        if not dry_run and krw_balance < UPBIT_MIN_ORDER_KRW:
            return Order("none", ticker, reason="원화 잔고 부족")
        amount = amount if dry_run else round(min(amount, krw_balance))
        if amount < UPBIT_MIN_ORDER_KRW:
            return Order("none", ticker, reason="주문금액이 최소(5,000원) 미만")
        return Order("buy", ticker, krw_amount=amount, reason="매수 신호")

    if action == "SELL":
        if coin_balance <= 0 and not dry_run:
            return Order("none", ticker, reason="보유 수량 없음")
        return Order("sell", ticker, volume=coin_balance, reason="매도 신호")

    return Order("none", ticker, reason="HOLD")


# --------------------------------------------------------------- 실행
def run_once_for_user(user_id: int, agent=None, dry_run: bool | None = None) -> dict:
    """사용자 계정에서 1회 사이클. dry_run 미지정 시 사용자 설정을 따른다.

    반환: {"dry_run", "krw_balance", "results":[...]}
    """
    settings = get_settings(user_id)
    effective_dry = settings["dry_run"] if dry_run is None else bool(dry_run)
    tickers = settings["tickers"] or DEFAULT_TICKERS

    broker = broker_for_user(user_id)  # 키 없으면 LookupError
    try:
        balances = broker.get_balances()
        krw = broker.krw_from_balances(balances)
    except Exception:  # noqa: BLE001
        balances, krw = [], 0.0

    if agent is None:
        from agent.decision import DecisionAgent  # 지연 import(운영자 공용 AI)
        agent = DecisionAgent()

    available = krw
    results = []
    for ticker in tickers:
        try:
            snapshot = broker.market_snapshot(ticker)
            coin, avg = broker.position_from_balances(ticker, balances)
            decision = agent.decide(
                snapshot, {"coin_balance": coin, "avg_buy_price": avg, "krw_balance": available}
            )
            order = evaluate(decision, snapshot, available, coin, settings["max_order_krw"], effective_dry)

            executed = False
            price_now = float(snapshot.get("price") or 0)
            if not effective_dry and order.side == "buy":
                broker.buy(ticker, order.krw_amount)
                available = max(0.0, available - order.krw_amount)
                executed = True
                _record_trade(user_id, ticker, "buy", price_now,
                              (order.krw_amount / price_now if price_now else 0), order.krw_amount, effective_dry)
            elif not effective_dry and order.side == "sell":
                broker.sell(ticker, order.volume)
                executed = True
                _record_trade(user_id, ticker, "sell", price_now, order.volume,
                              order.volume * price_now, effective_dry)

            _save_decision(user_id, snapshot, decision, order, effective_dry)
            results.append({
                "ticker": ticker,
                "price": price_now,
                "rsi": snapshot.get("rsi14"),
                "trend": snapshot.get("trend"),
                "action": getattr(decision, "action", "HOLD"),
                "confidence": round(float(getattr(decision, "confidence", 0) or 0), 2),
                "reasoning": getattr(decision, "reasoning", ""),
                "order": order.side,
                "order_reason": order.reason,
                "executed": executed,
            })
        except Exception as e:  # noqa: BLE001
            results.append({"ticker": ticker, "error": str(e)})

    return {"dry_run": effective_dry, "krw_balance": krw, "results": results}


# --------------------------------------------------------------- 기록(격리)
def _save_decision(user_id: int, snapshot: dict, decision, order, dry_run: bool) -> None:
    conn = db.connection()
    with db.lock():
        conn.execute(
            """INSERT INTO user_decisions
               (user_id, ts, ticker, price, rsi, trend, change_pct, action,
                confidence, reasoning, order_side, order_reason, dry_run)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                user_id, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                snapshot.get("ticker"), snapshot.get("price"), snapshot.get("rsi14"),
                snapshot.get("trend"), snapshot.get("period_change_pct"),
                getattr(decision, "action", None), float(getattr(decision, "confidence", 0) or 0),
                getattr(decision, "reasoning", None), order.side, order.reason, 1 if dry_run else 0,
            ),
        )


def _record_trade(user_id: int, ticker: str, side: str, price: float, volume: float,
                  krw_amount: float, dry_run: bool, raw: dict | None = None) -> None:
    conn = db.connection()
    with db.lock():
        conn.execute(
            """INSERT INTO user_trades
               (user_id, ts, ticker, side, price, volume, krw_amount, dry_run, raw_result)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                user_id, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                ticker, side, price, volume, krw_amount, 1 if dry_run else 0,
                json.dumps(raw, ensure_ascii=False, default=str) if raw else None,
            ),
        )


def recent_decisions(user_id: int, limit: int = 30) -> list[dict]:
    conn = db.connection()
    with db.lock():
        rows = conn.execute(
            "SELECT * FROM user_decisions WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def recent_trades(user_id: int, limit: int = 30) -> list[dict]:
    conn = db.connection()
    with db.lock():
        rows = conn.execute(
            "SELECT * FROM user_trades WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]
