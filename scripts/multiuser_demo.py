"""멀티유저 사용자 포털 데모 서버 (화면 확인용, 임시 DB + 가짜 자산).

실행: python scripts/multiuser_demo.py  → http://127.0.0.1:8010/app
- 실제 업비트 호출 없이 키검증/자산을 가짜로 대체하므로 안전하게 UI만 볼 수 있다.
- 운영 통합이 아니라 순수 미리보기용.
"""
import os
import sys

os.environ.setdefault("MULTIUSER_DB_PATH", "/tmp/mu_demo.db")
os.environ.setdefault("MULTIUSER_MASTER_KEY_FILE", "/tmp/mu_demo.key")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")
# 데모: 이 이메일로 가입하면 관리자(회원 관리 페이지 접근 가능)
os.environ.setdefault("ADMIN_EMAILS", "sean@example.com")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import types

from flask import Flask

import multiuser.web_auth as web_auth
from multiuser import accounts, broker_factory, exchange, trading

# --- 데모: 네트워크/AI 없이 동작하도록 검증/브로커/판단을 가짜로 대체 ---
accounts.verify_upbit_key = lambda a, s: exchange.VerifyResult(
    valid=True, can_withdraw=False, detail="데모: 유효(출금권한 없음)"
)


class _FakeBroker:
    def get_portfolio(self):
        return {
            "total_principal": 1_000_000,
            "total_value": 1_123_400,
            "total_return_pct": 12.34,
            "items": [
                {"ticker": "KRW-BTC", "currency": "BTC", "current_value": 720_000, "weight": 64.1, "return_pct": 18.7},
                {"ticker": "KRW-ETH", "currency": "ETH", "current_value": 280_400, "weight": 25.0, "return_pct": -4.2},
                {"ticker": "KRW", "currency": "KRW", "current_value": 123_000, "weight": 10.9, "return_pct": 0},
            ],
        }

    # run_once 데모용
    def get_balances(self):
        return [{"currency": "KRW", "balance": "123000", "locked": "0"}]

    @staticmethod
    def krw_from_balances(balances):
        return 123_000.0

    def market_snapshot(self, ticker):
        px = {"KRW-BTC": 92_000_000, "KRW-ETH": 4_800_000}.get(ticker, 1000)
        return {"ticker": ticker, "price": px, "rsi14": 58, "trend": "up", "period_change_pct": 2.1}

    @staticmethod
    def position_from_balances(ticker, balances):
        return 0.0, 0.0


class _FakeAgent:
    _seq = {"KRW-BTC": ("BUY", 0.82), "KRW-ETH": ("HOLD", 0.4)}

    def decide(self, snapshot, position):
        action, conf = self._seq.get(snapshot["ticker"], ("HOLD", 0.5))
        rsn = "상승추세 + RSI 중립 → 분할 매수 적정" if action == "BUY" else "신호 약함 → 관망"
        return types.SimpleNamespace(action=action, confidence=conf, reasoning=rsn)


broker_factory.broker_for_user = lambda uid, **k: _FakeBroker()
trading.broker_for_user = lambda uid, **k: _FakeBroker()
_orig_run = trading.run_once_for_user
trading.run_once_for_user = lambda uid, agent=None, dry_run=None: _orig_run(
    uid, agent=_FakeAgent(), dry_run=dry_run
)

app = Flask(__name__)
web_auth.register(app)


@app.get("/")
def _root():
    from flask import redirect
    return redirect("/app")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8010, debug=False)
