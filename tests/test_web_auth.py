"""웹 대시보드 HTTP Basic 인증 및 성과 엔드포인트 검증."""
import base64

import config
import web


def _client():
    return web.app.test_client()


def _basic(pw, user="x"):
    raw = f"{user}:{pw}".encode()
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


def test_healthz_open_even_with_token():
    config.WEB_AUTH_TOKEN = "secret123"
    r = _client().get("/healthz")
    assert r.status_code != 401  # 헬스체크는 인증 면제


def test_protected_endpoint_requires_auth():
    config.WEB_AUTH_TOKEN = "secret123"
    r = _client().get("/api/performance")
    assert r.status_code == 401
    assert "Basic" in r.headers.get("WWW-Authenticate", "")


def test_protected_endpoint_rejects_wrong_token():
    config.WEB_AUTH_TOKEN = "secret123"
    r = _client().get("/api/performance", headers=_basic("wrong"))
    assert r.status_code == 401


def test_protected_endpoint_accepts_correct_token():
    config.WEB_AUTH_TOKEN = "secret123"
    r = _client().get("/api/performance", headers=_basic("secret123"))
    assert r.status_code == 200
    assert "win_rate" in r.get_json()


def test_no_token_configured_means_open():
    config.WEB_AUTH_TOKEN = ""
    r = _client().get("/api/performance")
    assert r.status_code == 200


def test_manual_order_blocked_without_auth():
    config.WEB_AUTH_TOKEN = "secret123"
    r = _client().post("/api/manual_order", json={"side": "buy", "ticker": "KRW-BTC", "krw_amount": 6000})
    assert r.status_code == 401


def test_stocks_portfolio_endpoint_shape():
    config.WEB_AUTH_TOKEN = ""
    r = _client().get("/api/stocks/portfolio")
    assert r.status_code in (200, 502)
    j = r.get_json()
    assert "holdings" in j and isinstance(j["holdings"], list)
