import unittest
from unittest.mock import patch

import config
from quant_engine import generate_trade_plan
from risk import RiskManager


class _Decision:
    def __init__(self, action: str, confidence: float = 0.9, size_pct: float = 100.0):
        self.action = action
        self.confidence = confidence
        self.size_pct = size_pct


class QuantEngineTests(unittest.TestCase):
    def test_strong_trending_liquid_market_becomes_buy_candidate(self):
        plan = generate_trade_plan(
            snapshot={
                "ticker": "KRW-AAA",
                "price": 102.0,
                "ma5": 101.0,
                "ma20": 96.0,
                "rsi14": 55.0,
                "period_change_pct": 2.4,
                "recent_candles": [
                    {"open": 97, "high": 99, "low": 96, "close": 98, "volume": 1000},
                    {"open": 98, "high": 100, "low": 97, "close": 99, "volume": 1200},
                    {"open": 99, "high": 101, "low": 98, "close": 100, "volume": 1500},
                    {"open": 100, "high": 102, "low": 99, "close": 101, "volume": 1700},
                    {"open": 101, "high": 103, "low": 100, "close": 102, "volume": 1900},
                ],
            },
            summary={
                "ticker": "KRW-AAA",
                "price": 102.0,
                "signed_change_pct": 3.8,
                "acc_trade_price_24h": 52000000000.0,
            },
            liquidity_score=96.0,
            held=False,
            coin_balance=0.0,
            avg_buy_price=0.0,
            krw_balance=300000.0,
            total_equity_krw=1000000.0,
            current_position_value=0.0,
        )
        self.assertEqual(plan["bias"], "BUY")
        self.assertTrue(plan["eligible_buy"])
        self.assertGreater(plan["max_buy_krw"], 0)
        self.assertGreater(plan["target_weight_pct"], 0)
        self.assertLessEqual(plan["target_weight_pct"], 22.0)
        self.assertIn(plan["regime"], {"trend", "balanced"})

    def test_overheated_market_is_blocked_from_fresh_buy(self):
        plan = generate_trade_plan(
            snapshot={
                "ticker": "KRW-HOT",
                "price": 130.0,
                "ma5": 129.0,
                "ma20": 118.0,
                "rsi14": 79.0,
                "period_change_pct": 18.0,
                "recent_candles": [
                    {"open": 100, "high": 110, "low": 99, "close": 108, "volume": 1000},
                    {"open": 108, "high": 116, "low": 106, "close": 114, "volume": 1200},
                    {"open": 114, "high": 121, "low": 113, "close": 120, "volume": 1500},
                    {"open": 120, "high": 127, "low": 119, "close": 126, "volume": 1700},
                    {"open": 126, "high": 132, "low": 124, "close": 130, "volume": 1900},
                ],
            },
            summary={
                "ticker": "KRW-HOT",
                "price": 130.0,
                "signed_change_pct": 9.2,
                "acc_trade_price_24h": 43000000000.0,
            },
            liquidity_score=94.0,
            held=False,
            coin_balance=0.0,
            avg_buy_price=0.0,
            krw_balance=300000.0,
            total_equity_krw=1000000.0,
            current_position_value=0.0,
        )
        self.assertFalse(plan["eligible_buy"])
        self.assertIn("overheated", plan["risk_flags"])
        self.assertEqual(plan["max_buy_krw"], 0)
        self.assertEqual(plan["regime"], "breakout")

    def test_held_downtrend_candidate_turns_into_sell_bias(self):
        plan = generate_trade_plan(
            snapshot={
                "ticker": "KRW-WEAK",
                "price": 82.0,
                "ma5": 83.0,
                "ma20": 91.0,
                "rsi14": 31.0,
                "period_change_pct": -7.5,
                "recent_candles": [
                    {"open": 95, "high": 96, "low": 92, "close": 94, "volume": 1000},
                    {"open": 94, "high": 95, "low": 90, "close": 91, "volume": 1300},
                    {"open": 91, "high": 92, "low": 88, "close": 89, "volume": 1600},
                    {"open": 89, "high": 90, "low": 85, "close": 86, "volume": 1800},
                    {"open": 86, "high": 87, "low": 81, "close": 82, "volume": 2100},
                ],
            },
            summary={
                "ticker": "KRW-WEAK",
                "price": 82.0,
                "signed_change_pct": -4.8,
                "acc_trade_price_24h": 18000000000.0,
            },
            liquidity_score=87.0,
            held=True,
            coin_balance=150.0,
            avg_buy_price=93.0,
            krw_balance=100000.0,
            total_equity_krw=1000000.0,
            current_position_value=12300.0,
        )
        self.assertEqual(plan["bias"], "SELL")
        self.assertGreater(plan["exit_score"], plan["long_score"])
        self.assertGreater(plan["trim_pct"], 0)
        self.assertEqual(plan["regime"], "mean_reversion")

    def test_risk_manager_blocks_buy_when_quant_plan_disallows_it(self):
        manager = RiskManager()
        order = manager.evaluate(
            _Decision("BUY", confidence=0.95),
            snapshot={
                "ticker": "KRW-HOT",
                "price": 130.0,
                "quant_plan": {
                    "eligible_buy": False,
                    "max_buy_krw": 0.0,
                    "trim_pct": 0.0,
                    "risk_flags": ["overheated"],
                },
            },
            krw_balance=200000.0,
            coin_balance=0.0,
            avg_buy_price=0.0,
        )
        self.assertEqual(order.side, "none")
        self.assertIn("quant", order.reason.lower())

    def test_risk_manager_scales_sell_to_quant_trim_pct(self):
        manager = RiskManager()
        order = manager.evaluate(
            _Decision("SELL", confidence=0.95),
            snapshot={
                "ticker": "KRW-BIRB",
                "price": 118.0,
                "quant_plan": {
                    "eligible_buy": False,
                    "max_buy_krw": 0.0,
                    "trim_pct": 35.0,
                    "risk_flags": ["overweight"],
                },
            },
            krw_balance=50.0,
            coin_balance=100.0,
            avg_buy_price=120.0,
        )
        self.assertEqual(order.side, "sell")
        self.assertAlmostEqual(order.volume, 35.0)
        self.assertIn("quant", order.reason.lower())

    def test_buy_candidate_has_positive_execution_priority_and_news_focus(self):
        plan = generate_trade_plan(
            snapshot={
                "ticker": "KRW-NEWS",
                "price": 104.0,
                "ma5": 103.0,
                "ma20": 96.0,
                "rsi14": 59.0,
                "period_change_pct": 3.0,
                "recent_candles": [
                    {"open": 98, "high": 100, "low": 97, "close": 99, "volume": 1200},
                    {"open": 99, "high": 101, "low": 98, "close": 100, "volume": 1400},
                    {"open": 100, "high": 102, "low": 99, "close": 101, "volume": 1600},
                    {"open": 101, "high": 103, "low": 100, "close": 102, "volume": 1800},
                    {"open": 102, "high": 105, "low": 101, "close": 104, "volume": 2200},
                ],
            },
            summary={
                "ticker": "KRW-NEWS",
                "price": 104.0,
                "signed_change_pct": 4.9,
                "acc_trade_price_24h": 88000000000.0,
            },
            liquidity_score=99.0,
            held=False,
            coin_balance=0.0,
            avg_buy_price=0.0,
            krw_balance=500000.0,
            total_equity_krw=1000000.0,
            current_position_value=0.0,
        )
        self.assertGreater(plan["execution_priority"], 0)
        self.assertTrue(plan["news_focus"])

    def test_risk_manager_does_not_force_sell_only_because_single_position_profit_exceeds_target_profit(self):
        manager = RiskManager()
        with patch.object(config, "FREE_TRADE_MODE", False), patch.object(config, "MIN_CONFIDENCE", 0.6):
            order = manager.evaluate(
                _Decision("HOLD", confidence=0.8),
                snapshot={
                    "ticker": "KRW-GAIN",
                    "price": 130.0,
                    "quant_plan": {
                        "eligible_buy": False,
                        "max_buy_krw": 0.0,
                        "trim_pct": 0.0,
                        "risk_flags": [],
                    },
                },
                krw_balance=100000.0,
                coin_balance=1000.0,
                avg_buy_price=100.0,
            )
        self.assertEqual(order.side, "none")
        self.assertEqual(order.reason, "HOLD")

    def test_hourly_profit_target_is_not_used_as_fixed_per_position_take_profit_threshold(self):
        self.assertEqual(config.TARGET_PROFIT_KRW, 15000)
        manager = RiskManager()
        with patch.object(config, "FREE_TRADE_MODE", False), patch.object(config, "MIN_CONFIDENCE", 0.6):
            order = manager.evaluate(
                _Decision("HOLD", confidence=0.8),
                snapshot={
                    "ticker": "KRW-HOUR",
                    "price": 120.0,
                    "quant_plan": {
                        "eligible_buy": False,
                        "max_buy_krw": 0.0,
                        "trim_pct": 0.0,
                        "risk_flags": [],
                    },
                },
                krw_balance=100000.0,
                coin_balance=1000.0,
                avg_buy_price=100.0,
            )
        self.assertNotEqual(order.side, "sell")


if __name__ == "__main__":
    unittest.main()
