"""RiskManager.evaluate 안전장치 검증."""
from datetime import date
from types import SimpleNamespace

import config
import db
import risk


def _decision(action, confidence=1.0, size_pct=100.0):
    return SimpleNamespace(action=action, confidence=confidence, size_pct=size_pct)


def _snap(price=1000.0, ticker="KRW-BTC"):
    return {"ticker": ticker, "price": price, "rsi14": 50, "trend": "flat"}


def setup_function(_):
    # 각 테스트 전 결정적 상태로 초기화
    config.FREE_TRADE_MODE = False
    config.DRY_RUN = True
    config.MIN_CONFIDENCE = 0.6
    config.MAX_ORDER_KRW = 10_000
    config.MIN_ORDER_KRW = 5_000
    config.MAX_DAILY_LOSS_KRW = 30_000
    config.TARGET_PROFIT_KRW = 15_000
    conn = db._connect()
    conn.execute("DELETE FROM daily_pnl")


def test_low_confidence_holds():
    o = risk.RiskManager().evaluate(_decision("BUY", confidence=0.3), _snap(), 1_000_000, 0, 0)
    assert o.side == "none"


def test_buy_amount_capped_by_max_order():
    o = risk.RiskManager().evaluate(_decision("BUY", confidence=1.0), _snap(), 1_000_000, 0, 0)
    assert o.side == "buy"
    assert o.krw_amount <= config.MAX_ORDER_KRW


def test_take_profit_forces_sell():
    # 평가수익 (2000-1000)*100 = 100,000 ≥ 목표 15,000 → 강제 익절
    o = risk.RiskManager().evaluate(_decision("HOLD", confidence=0.0), _snap(price=2000), 0, 100, 1000)
    assert o.side == "sell"
    assert o.volume == 100


def test_daily_loss_halts_trading():
    conn = db._connect()
    conn.execute(
        "INSERT INTO daily_pnl (day, realized_pnl, trades_count) VALUES (?, ?, ?)",
        (date.today().isoformat(), -config.MAX_DAILY_LOSS_KRW, 1),
    )
    o = risk.RiskManager().evaluate(_decision("BUY", confidence=1.0), _snap(), 1_000_000, 0, 0)
    assert o.side == "none"
    assert "중단" in o.reason


def test_sell_signal_returns_full_volume():
    o = risk.RiskManager().evaluate(_decision("SELL", confidence=1.0), _snap(), 0, 5.0, 900)
    assert o.side == "sell"
    assert o.volume == 5.0
