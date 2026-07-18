import json
import os
import unittest
from unittest.mock import patch

from brokers.kis import KISBroker, PAPER_WEBSOCKET_URL, REAL_WEBSOCKET_URL
from realtime import (
    coin_sse_stream,
    normalize_upbit_message,
    parse_kis_trade_message,
    stock_sse_stream,
)


def _sse_data(event: str) -> dict:
    line = next(line for line in event.splitlines() if line.startswith("data: "))
    return json.loads(line.removeprefix("data: "))


class _FakeWebSocket:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def recv(self, timeout=None):
        return next(self.messages)


class _FakeKISBroker:
    paper = False
    websocket_url = "ws://ops.koreainvestment.com:21000/tryitout"

    def is_realtime_configured(self):
        return True

    def websocket_approval_key(self):
        return "approval-key"


class RealtimeTests(unittest.TestCase):
    def test_kis_realtime_configuration_does_not_require_account_number(self):
        with patch.dict(os.environ, {
            "KIS_APP_KEY": "app-key",
            "KIS_APP_SECRET": "app-secret",
            "KIS_ACCOUNT_NO": "",
            "KIS_PAPER": "true",
        }):
            broker = KISBroker()

        self.assertTrue(broker.is_realtime_configured())
        self.assertFalse(broker.is_configured())
        self.assertEqual(broker.websocket_url, PAPER_WEBSOCKET_URL)

    def test_kis_websocket_approval_key_uses_official_contract_and_is_cached(self):
        with (
            patch.dict(os.environ, {
                "KIS_APP_KEY": "app-key",
                "KIS_APP_SECRET": "app-secret",
                "KIS_ACCOUNT_NO": "12345678-01",
                "KIS_PAPER": "false",
            }),
            patch("brokers.kis._http_json", return_value={"approval_key": "approval-key"}) as request,
        ):
            broker = KISBroker()
            first = broker.websocket_approval_key()
            second = broker.websocket_approval_key()

        self.assertEqual(first, "approval-key")
        self.assertEqual(second, "approval-key")
        self.assertEqual(broker.websocket_url, REAL_WEBSOCKET_URL)
        request.assert_called_once_with(
            "https://openapi.koreainvestment.com:9443/oauth2/Approval",
            data={
                "grant_type": "client_credentials",
                "appkey": "app-key",
                "secretkey": "app-secret",
            },
        )

    def test_upbit_ticker_payload_is_normalized(self):
        event, payload = normalize_upbit_message({
            "type": "ticker",
            "code": "KRW-BTC",
            "trade_price": 101_250_000,
            "signed_change_price": -250_000,
            "signed_change_rate": -0.002463,
            "trade_volume": 0.001,
            "timestamp": 1_750_000_000_000,
            "trade_timestamp": 1_750_000_000_000,
        })

        self.assertEqual(event, "ticker")
        self.assertEqual(payload["ticker"], "KRW-BTC")
        self.assertEqual(payload["price"], 101_250_000)
        self.assertEqual(payload["change_price"], -250_000)
        self.assertEqual(payload["source"], "UPBIT_WS")

    def test_upbit_orderbook_payload_keeps_levels(self):
        event, payload = normalize_upbit_message({
            "type": "orderbook",
            "code": "KRW-BTC",
            "timestamp": 1_750_000_000_000,
            "total_ask_size": 1.25,
            "total_bid_size": 2.5,
            "orderbook_units": [{
                "ask_price": 101,
                "bid_price": 100,
                "ask_size": 0.3,
                "bid_size": 0.4,
            }],
        })

        self.assertEqual(event, "orderbook")
        self.assertEqual(payload["units"][0]["ask_price"], 101)
        self.assertEqual(payload["units"][0]["bid_size"], 0.4)

    def test_coin_stream_subscribes_to_ticker_and_orderbook(self):
        websocket = _FakeWebSocket([json.dumps({
            "type": "ticker",
            "code": "KRW-BTC",
            "trade_price": 100,
            "timestamp": 1_750_000_000_000,
        }).encode()])
        with patch("realtime.connect", return_value=websocket):
            stream = coin_sse_stream("KRW-BTC")
            next(stream)
            self.assertEqual(_sse_data(next(stream))["state"], "connecting")
            self.assertEqual(_sse_data(next(stream))["state"], "connected")
            tick = _sse_data(next(stream))
            stream.close()

        requests = websocket.sent[0]
        self.assertEqual(requests[1], {"type": "ticker", "codes": ["KRW-BTC"]})
        self.assertEqual(requests[2], {"type": "orderbook", "codes": ["KRW-BTC"]})
        self.assertEqual(tick["price"], 100)

    def test_kis_batched_trade_frame_is_parsed(self):
        row = [""] * 46
        row[0] = "005930"
        row[1] = "101530"
        row[2] = "72000"
        row[3] = "5"
        row[4] = "1200"
        row[5] = "1.64"
        row[7] = "73100"
        row[8] = "73500"
        row[9] = "71800"
        row[10] = "72100"
        row[11] = "72000"
        row[12] = "17"
        row[13] = "1234567"
        row[33] = "20260710"
        row[43] = "0"

        trades = parse_kis_trade_message("0|H0UNCNT0|001|" + "^".join(row))

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["code"], "005930")
        self.assertEqual(trades[0]["price"], 72000)
        self.assertEqual(trades[0]["change"], -1200)
        self.assertEqual(trades[0]["change_pct"], -1.64)
        self.assertEqual(trades[0]["traded_at"], "2026-07-10T10:15:30+09:00")

    def test_stock_stream_reports_missing_kis_keys(self):
        with patch.dict(os.environ, {"KIS_APP_KEY": "", "KIS_APP_SECRET": ""}):
            stream = stock_sse_stream(["005930"])
            next(stream)
            status = _sse_data(next(stream))
            stream.close()

        self.assertEqual(status["state"], "unavailable")
        self.assertEqual(status["reason"], "missing_kis_credentials")
        self.assertEqual(status["required"], ["KIS_APP_KEY", "KIS_APP_SECRET"])

    def test_stock_stream_uses_unified_market_subscription(self):
        websocket = _FakeWebSocket([])
        with (
            patch("realtime.KISBroker", return_value=_FakeKISBroker()),
            patch("realtime.connect", return_value=websocket),
            patch("realtime.time.sleep"),
        ):
            stream = stock_sse_stream(["005930", "000660"])
            next(stream)
            self.assertEqual(_sse_data(next(stream))["state"], "connecting")
            self.assertEqual(_sse_data(next(stream))["state"], "connected")
            stream.close()

        self.assertEqual(len(websocket.sent), 2)
        for request in websocket.sent:
            self.assertEqual(request["body"]["input"]["tr_id"], "H0UNCNT0")
            self.assertEqual(request["header"]["tr_type"], "1")


if __name__ == "__main__":
    unittest.main()
