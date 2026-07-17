"""Fail-closed regression tests for trading, input, and public-export boundaries."""
from __future__ import annotations

import math
import json
import re

import pytest

import config
import web
from brokers.upbit import UpbitBroker
from multiuser import exchange
from scripts import export_github_pages


def test_invalid_boolean_env_is_rejected(monkeypatch):
    monkeypatch.setenv("SAFETY_FLAG", "treu")
    with pytest.raises(ValueError):
        config._env_bool("SAFETY_FLAG", True)


def test_live_validation_requires_strong_dashboard_auth(monkeypatch):
    monkeypatch.setattr(config, "AI_PROVIDER", "mock")
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(config, "ALLOW_LIVE_TRADING", True)
    monkeypatch.setattr(config, "UPBIT_ACCESS_KEY", "real-access-value")
    monkeypatch.setattr(config, "UPBIT_SECRET_KEY", "real-secret-value")
    monkeypatch.setattr(config, "WEB_AUTH_TOKEN", "")
    monkeypatch.setattr(config, "WEB_HOST", "127.0.0.1")
    with pytest.raises(SystemExit, match="WEB_AUTH_TOKEN"):
        config.validate()


def test_live_order_requires_second_interlock(monkeypatch):
    calls = []

    class Client:
        def buy_market_order(self, ticker, amount):
            calls.append((ticker, amount))
            return {"uuid": "should-not-run"}

    broker = UpbitBroker.__new__(UpbitBroker)
    broker.client = Client()
    monkeypatch.setattr(config, "DRY_RUN", False)
    monkeypatch.setattr(config, "ALLOW_LIVE_TRADING", False)

    with pytest.raises(RuntimeError, match="ALLOW_LIVE_TRADING"):
        broker.buy("KRW-BTC", 10_000)
    assert calls == []


def test_available_balance_excludes_locked_amount():
    balances = [
        {"currency": "KRW", "balance": "10000", "locked": "90000"},
        {"currency": "BTC", "balance": "0.1", "locked": "0.9", "avg_buy_price": "100"},
    ]
    assert UpbitBroker.krw_from_balances(balances) == 10_000
    assert UpbitBroker.position_from_balances("KRW-BTC", balances) == (0.1, 100.0)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", math.nan, math.inf])
def test_manual_order_rejects_non_finite_numbers(value):
    with pytest.raises(ValueError):
        web._non_negative_float(value, "주문금액")


def test_withdraw_permission_check_rejects_rate_limit(monkeypatch):
    def fake_request(path, access, secret, params=None):
        if path == "/v1/accounts":
            return 200, [{"currency": "KRW"}]
        return 429, {"error": {"name": "too_many_requests", "message": "slow down"}}

    monkeypatch.setattr(exchange, "_request", fake_request)
    result = exchange.verify_upbit_key("access", "secret")
    assert result.valid is True
    assert result.can_withdraw is True
    assert result.acceptable is False


def test_public_export_is_demo_only_and_contains_no_order_material():
    portfolio = export_github_pages.portfolio_snapshot()
    ai = export_github_pages.ai_trade_snapshot()
    stocks = export_github_pages.stock_ai_snapshot()

    for name, payload in (("portfolio", portfolio), ("ai", ai), ("stocks", stocks)):
        export_github_pages._assert_public_snapshot_safe(payload, name=name)
        assert payload["data_mode"] == "demo"
    assert ai["config"]["dry_run"] is True
    assert ai["config"]["allow_live_trading"] is False
    assert ai["trades"] == []


def test_public_export_guard_rejects_raw_exchange_response():
    with pytest.raises(RuntimeError, match="공개 금지 필드"):
        export_github_pages._assert_public_snapshot_safe(
            {"data_mode": "demo", "trades": [{"raw_result": "secret"}]},
            name="bad",
        )


def test_committed_public_artifacts_are_sanitized():
    data_dir = export_github_pages.DOCS / "data"
    for filename in ("portfolio_snapshot.json", "ai_snapshot.json", "stock_snapshot.json"):
        payload = json.loads((data_dir / filename).read_text(encoding="utf-8"))
        export_github_pages._assert_public_snapshot_safe(payload, name=filename)

    uuid_pattern = re.compile(
        r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
        r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
    )
    for relative in ("index.html", "coin/index.html", "stocks/index.html"):
        text = (export_github_pages.DOCS / relative).read_text(encoding="utf-8")
        assert "raw_result" not in text
        assert not uuid_pattern.search(text)
        assert '"dry_run": false' not in text
