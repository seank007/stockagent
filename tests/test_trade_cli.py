import io
import json
import unittest
from contextlib import redirect_stdout
from typing import Any, cast
from unittest.mock import patch

from scripts import trade_cli


class _Broker:
    def __init__(self, *, krw=0.0, coin=0.0, price=100.0):
        self._krw = krw
        self._coin = coin
        self._price = price
        self.buy_calls = []
        self.sell_calls = []

    def get_krw_balance(self):
        return self._krw

    def get_coin_balance(self, _ticker):
        return self._coin

    def market_snapshot(self, _ticker):
        return {"price": self._price}

    def buy(self, ticker, amount):
        self.buy_calls.append((ticker, amount))
        return {"ok": True, "ticker": ticker, "amount": amount}

    def sell(self, ticker, volume):
        self.sell_calls.append((ticker, volume))
        return {"ok": True, "ticker": ticker, "volume": volume}


class TradeCliTests(unittest.TestCase):
    def test_cmd_buy_blocks_small_fragmented_entry(self):
        broker = _Broker(krw=20_000, price=100.0)
        buf = io.StringIO()
        with patch.object(trade_cli.db, "record_trade", lambda **_: {"realized_pnl": 0.0}):
            with redirect_stdout(buf):
                with self.assertRaises(SystemExit):
                    trade_cli.cmd_buy(cast(Any, broker), "KRW-AAA", "9000")
        out = json.loads(buf.getvalue())
        self.assertFalse(out["ok"])
        self.assertIn("신규진입 최소", out["error"])
        self.assertEqual(broker.buy_calls, [])

    def test_cmd_sell_promotes_too_small_trim_to_full_exit(self):
        broker = _Broker(coin=100.0, price=100.0)
        buf = io.StringIO()
        with patch.object(trade_cli.db, "record_trade", lambda **_: {"realized_pnl": 123.0}):
            with redirect_stdout(buf):
                trade_cli.cmd_sell(cast(Any, broker), "KRW-AAA", "40")
        out = json.loads(buf.getvalue())
        self.assertEqual(broker.sell_calls, [("KRW-AAA", 100.0)])
        self.assertEqual(out["executed_pct"], 100.0)
        self.assertEqual(out["requested_pct"], 40.0)

    def test_cmd_sell_promotes_partial_exit_when_leftover_would_be_dust(self):
        broker = _Broker(coin=90.0, price=100.0)
        buf = io.StringIO()
        with patch.object(trade_cli.db, "record_trade", lambda **_: {"realized_pnl": 55.0}):
            with redirect_stdout(buf):
                trade_cli.cmd_sell(cast(Any, broker), "KRW-AAA", "60")
        out = json.loads(buf.getvalue())
        self.assertEqual(broker.sell_calls, [("KRW-AAA", 90.0)])
        self.assertEqual(out["executed_pct"], 100.0)
        self.assertEqual(out["requested_pct"], 60.0)
        self.assertEqual(out["approx_krw"], 9000)


if __name__ == "__main__":
    unittest.main()
