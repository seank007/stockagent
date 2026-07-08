"""유저별 자동매매 엔진 테스트 (네트워크/AI 없이 가짜 broker·agent 사용)."""
import importlib
import types

import pytest


@pytest.fixture()
def mu(tmp_path, monkeypatch):
    monkeypatch.setenv("MULTIUSER_DB_PATH", str(tmp_path / "mu.db"))
    monkeypatch.setenv("MULTIUSER_MASTER_KEY_FILE", str(tmp_path / "master.key"))
    monkeypatch.delenv("MULTIUSER_MASTER_KEY", raising=False)
    from multiuser import db, vault, exchange, accounts, trading
    for m in (db, vault, exchange, accounts, trading):
        importlib.reload(m)
    db.reset_for_tests()
    vault.reset_for_tests()
    return accounts, trading


class FakeAgent:
    def __init__(self, action="BUY", confidence=0.9):
        self._a, self._c = action, confidence

    def decide(self, snapshot, position):
        return types.SimpleNamespace(action=self._a, confidence=self._c, reasoning="테스트 판단")


class FakeBroker:
    def get_balances(self):
        return [{"currency": "KRW", "balance": "100000", "locked": "0"}]

    @staticmethod
    def krw_from_balances(balances):
        return 100000.0

    def market_snapshot(self, ticker):
        return {"ticker": ticker, "price": 50_000_000, "rsi14": 55, "trend": "up", "period_change_pct": 1.2}

    @staticmethod
    def position_from_balances(ticker, balances):
        return 0.0, 0.0

    def buy(self, *a, **k):
        raise AssertionError("dry_run에서는 buy가 호출되면 안 됨")

    def sell(self, *a, **k):
        raise AssertionError("dry_run에서는 sell이 호출되면 안 됨")


def test_settings_defaults_and_update(mu):
    accounts, trading = mu
    u = accounts.register("t@x.com", "password123")
    s = trading.get_settings(u["id"])
    assert s["auto_enabled"] is False and s["dry_run"] is True  # 안전 기본값
    s2 = trading.update_settings(u["id"], tickers="btc, eth", max_order_krw=20000)
    assert s2["tickers"] == ["KRW-BTC", "KRW-ETH"]
    assert s2["max_order_krw"] == 20000


def test_max_order_floor(mu):
    accounts, trading = mu
    u = accounts.register("t2@x.com", "password123")
    s = trading.update_settings(u["id"], max_order_krw=100)  # 업비트 최소 미만
    assert s["max_order_krw"] == 5000


def test_evaluate_confidence_gate(mu):
    _, trading = mu
    snap = {"ticker": "KRW-BTC", "price": 1000}
    low = types.SimpleNamespace(action="BUY", confidence=0.3)
    assert trading.evaluate(low, snap, 100000, 0, 10000, True).side == "none"


def test_evaluate_buy_and_sell(mu):
    _, trading = mu
    snap = {"ticker": "KRW-BTC", "price": 1000}
    buy = trading.evaluate(types.SimpleNamespace(action="BUY", confidence=0.9), snap, 100000, 0, 10000, True)
    assert buy.side == "buy" and buy.krw_amount >= 5000
    sell = trading.evaluate(types.SimpleNamespace(action="SELL", confidence=0.9), snap, 0, 0.5, 10000, True)
    assert sell.side == "sell" and sell.volume == 0.5


def test_run_once_dry_run_records_and_isolates(mu, monkeypatch):
    accounts, trading = mu
    monkeypatch.setattr(trading, "broker_for_user", lambda uid, **k: FakeBroker())
    u1 = accounts.register("r1@x.com", "password123")
    u2 = accounts.register("r2@x.com", "password123")
    trading.update_settings(u1["id"], tickers="BTC")

    out = trading.run_once_for_user(u1["id"], agent=FakeAgent("BUY", 0.9), dry_run=True)
    assert out["dry_run"] is True
    assert out["results"][0]["order"] == "buy" and out["results"][0]["executed"] is False

    # u1엔 판단 기록이 있고, u2는 비어 있어야(격리)
    assert len(trading.recent_decisions(u1["id"])) == 1
    assert trading.recent_decisions(u2["id"]) == []


def test_run_once_without_key_raises(mu):
    accounts, trading = mu
    u = accounts.register("nokey@x.com", "password123")
    # broker_for_user 실제 구현: 키 없으면 LookupError
    with pytest.raises(LookupError):
        trading.run_once_for_user(u["id"], agent=FakeAgent(), dry_run=True)
