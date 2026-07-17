from __future__ import annotations

import math
from statistics import pstdev

UPBIT_MIN_ORDER_KRW = 5_000
MAX_TARGET_WEIGHT_PCT = 22.0
MIN_TARGET_WEIGHT_PCT = 4.0
MAX_SINGLE_NAME_WEIGHT_PCT = 28.0


def _clip(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))



def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default



def _recent_close_series(snapshot: dict) -> list[float]:
    closes: list[float] = []
    for candle in snapshot.get("recent_candles") or []:
        close = _safe_float(candle.get("close"), 0.0)
        if close > 0:
            closes.append(close)
    return closes



def _volatility_pct(snapshot: dict) -> float:
    closes = _recent_close_series(snapshot)
    if len(closes) < 2:
        return abs(_safe_float(snapshot.get("period_change_pct"), 0.0))
    returns = []
    for prev, curr in zip(closes, closes[1:]):
        if prev > 0:
            returns.append((curr / prev - 1) * 100)
    if not returns:
        return abs(_safe_float(snapshot.get("period_change_pct"), 0.0))
    return round(pstdev(returns), 4)



def _target_weight_pct(long_score: float, volatility_pct: float, liquidity_score: float) -> float:
    raw = 4.0 + max(0.0, long_score - 60.0) * 0.35
    vol_penalty = min(8.0, volatility_pct * 1.7)
    liq_boost = max(0.0, (liquidity_score - 70.0) * 0.04)
    target = raw - vol_penalty + liq_boost
    return round(_clip(target, MIN_TARGET_WEIGHT_PCT, MAX_TARGET_WEIGHT_PCT), 2)



def _trim_pct(exit_score: float, long_score: float, pnl_pct: float, overweight: bool) -> float:
    edge = max(0.0, exit_score - long_score)
    base = 0.0
    if edge >= 25:
        base = 100.0
    elif edge >= 18:
        base = 75.0
    elif edge >= 10:
        base = 50.0
    elif edge >= 6:
        base = 25.0
    if pnl_pct >= 8:
        base = max(base, 50.0)
    if overweight:
        base = max(base, 35.0)
    return round(_clip(base, 0.0, 100.0), 2)



def _classify_regime(trend_up: bool, rsi: float, signed_change: float, volatility_pct: float) -> str:
    if signed_change >= 7.5 or rsi >= 74:
        return "breakout"
    if not trend_up and rsi <= 35:
        return "mean_reversion"
    if trend_up and volatility_pct <= 3.2:
        return "trend"
    return "balanced"



def generate_trade_plan(
    *,
    snapshot: dict,
    summary: dict,
    liquidity_score: float,
    held: bool,
    coin_balance: float,
    avg_buy_price: float,
    krw_balance: float,
    total_equity_krw: float,
    current_position_value: float,
) -> dict:
    ticker = str(snapshot.get("ticker") or summary.get("ticker") or "").upper()
    price = _safe_float(snapshot.get("price") or summary.get("price"), 0.0)
    ma5 = _safe_float(snapshot.get("ma5"), 0.0)
    ma20 = _safe_float(snapshot.get("ma20"), 0.0)
    rsi = _safe_float(snapshot.get("rsi14"), 50.0)
    period_change = _safe_float(snapshot.get("period_change_pct"), 0.0)
    signed_change = _safe_float(summary.get("signed_change_pct"), 0.0)
    liquidity_score = _clip(liquidity_score)
    ma_gap_pct = ((ma5 / ma20 - 1) * 100) if ma20 else 0.0
    trend_up = ma5 >= ma20 if ma20 else False
    pnl_pct = ((price / avg_buy_price - 1) * 100) if held and avg_buy_price > 0 and price > 0 else 0.0
    volatility_pct = _volatility_pct(snapshot)

    trend_score = _clip((74 if trend_up else 22) + ma_gap_pct * 8)
    momentum_score = _clip(50 + signed_change * 7)
    reversal_score = 82 if 34 <= rsi <= 48 else 68 if 48 < rsi <= 60 else 52 if rsi < 34 else 34 if rsi >= 72 else 46
    stability_score = _clip(100 - abs(period_change) * 3.2 - volatility_pct * 8.0)

    long_score = round(
        0.28 * liquidity_score
        + 0.24 * trend_score
        + 0.18 * momentum_score
        + 0.12 * reversal_score
        + 0.10 * stability_score
        + 0.08 * _clip(100 - volatility_pct * 10),
        2,
    )

    exit_trend = _clip(100 - trend_score)
    exit_momentum = _clip(50 - signed_change * 7)
    exit_rsi = 90 if rsi >= 74 else 74 if rsi >= 66 else 64 if rsi <= 30 else 46
    profit_lock_score = _clip(50 + pnl_pct * 4.5)
    exit_score = round(
        0.20 * liquidity_score
        + 0.24 * exit_trend
        + 0.22 * exit_momentum
        + 0.18 * exit_rsi
        + 0.16 * profit_lock_score,
        2,
    )

    total_equity_krw = max(_safe_float(total_equity_krw, 0.0), _safe_float(krw_balance, 0.0), 1.0)
    current_weight_pct = current_position_value / total_equity_krw * 100 if total_equity_krw > 0 else 0.0
    target_weight_pct = _target_weight_pct(long_score, volatility_pct, liquidity_score)
    overweight = held and current_weight_pct > min(MAX_SINGLE_NAME_WEIGHT_PCT, target_weight_pct + 6.0)

    regime = _classify_regime(trend_up, rsi, signed_change, volatility_pct)

    risk_flags: list[str] = []
    if liquidity_score < 35:
        risk_flags.append("weak_liquidity")
    if rsi >= 74 or signed_change >= 8.5 or period_change >= 15:
        risk_flags.append("overheated")
    if not trend_up and rsi <= 33:
        risk_flags.append("falling_knife")
    if volatility_pct >= 5.0:
        risk_flags.append("high_volatility")
    if overweight:
        risk_flags.append("overweight")

    max_position_krw = total_equity_krw * min(MAX_SINGLE_NAME_WEIGHT_PCT, target_weight_pct) / 100.0
    target_position_krw = total_equity_krw * target_weight_pct / 100.0
    position_room_krw = max(0.0, target_position_krw - current_position_value)
    max_buy_krw = min(_safe_float(krw_balance, 0.0), max_position_krw - current_position_value, position_room_krw)
    if max_buy_krw < UPBIT_MIN_ORDER_KRW:
        max_buy_krw = 0.0

    eligible_buy = (
        long_score >= 72
        and (long_score - exit_score) >= 10
        and "weak_liquidity" not in risk_flags
        and "overheated" not in risk_flags
        and "falling_knife" not in risk_flags
        and max_buy_krw >= UPBIT_MIN_ORDER_KRW
    )
    if not eligible_buy:
        max_buy_krw = 0.0

    trim_pct = _trim_pct(exit_score, long_score, pnl_pct, overweight)
    news_focus = liquidity_score >= 92 and abs(signed_change) >= 4.0 and volatility_pct <= 3.5
    execution_priority = round(
        max(0.0, long_score - exit_score)
        + max(0.0, liquidity_score - 70.0) * 0.35
        - len(risk_flags) * 8.0,
        2,
    )

    if held and ((exit_score - long_score) >= 8 or trim_pct >= 25):
        bias = "SELL"
    elif eligible_buy:
        bias = "BUY"
    else:
        bias = "HOLD"

    quant_reason_parts = [
        "유동성 상위" if liquidity_score >= 70 else "유동성 보통" if liquidity_score >= 40 else "유동성 낮음",
        "상승 추세" if trend_up else "하락 추세",
        "과열권" if rsi >= 70 else "과매도권" if rsi <= 33 else "RSI 중립",
    ]
    if volatility_pct >= 4.0:
        quant_reason_parts.append("변동성 높음")
    if overweight:
        quant_reason_parts.append("포지션 비중 과다")

    return {
        "ticker": ticker,
        "held": held,
        "price": round(price, 4),
        "my_balance": round(_safe_float(coin_balance), 8),
        "my_avg_buy_price": round(_safe_float(avg_buy_price), 4),
        "my_unrealized_return_pct": round(pnl_pct, 2),
        "current_position_value": round(_safe_float(current_position_value), 2),
        "current_weight_pct": round(current_weight_pct, 2),
        "target_weight_pct": round(target_weight_pct, 2),
        "signed_change_pct": round(signed_change, 2),
        "period_change_pct": round(period_change, 2),
        "acc_trade_price_24h": round(_safe_float(summary.get("acc_trade_price_24h"), 0.0), 2),
        "rsi14": round(rsi, 2),
        "ma5": round(ma5, 4),
        "ma20": round(ma20, 4),
        "ma_gap_pct": round(ma_gap_pct, 2),
        "volatility_pct": round(volatility_pct, 4),
        "trend": "up" if trend_up else "down",
        "regime": regime,
        "liquidity_score": round(liquidity_score, 2),
        "trend_score": round(trend_score, 2),
        "momentum_score": round(momentum_score, 2),
        "reversal_score": round(reversal_score, 2),
        "stability_score": round(stability_score, 2),
        "long_score": long_score,
        "exit_score": exit_score,
        "bias": bias,
        "eligible_buy": eligible_buy,
        "news_focus": news_focus,
        "execution_priority": execution_priority,
        "max_buy_krw": round(max(0.0, max_buy_krw), 2),
        "trim_pct": round(trim_pct, 2),
        "risk_flags": risk_flags,
        "quant_reason": ", ".join(quant_reason_parts),
    }
