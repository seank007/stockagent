import json
import unittest
from unittest.mock import patch

import config
from agent.decision import DecisionAgent, FREE_DECISION_SCHEMA


class DecisionAgentRequestTests(unittest.TestCase):
    def test_free_trade_request_includes_hourly_profit_target_as_guidance_not_hard_limit(self):
        agent = DecisionAgent()
        with patch.object(config, "FREE_TRADE_MODE", True), patch.object(config, "TARGET_PROFIT_KRW", 15000):
            prompt, schema, user_content = agent._build_request(
                snapshot={"ticker": "KRW-TEST", "price": 100},
                position={"coin_balance": 0.0, "avg_buy_price": 0.0, "krw_balance": 50000.0},
            )

        self.assertIn("시간당 수익", prompt)
        self.assertIn("단일 포지션마다", prompt)
        self.assertEqual(schema, FREE_DECISION_SCHEMA)

        payload = json.loads(user_content.split("\n", 1)[1])
        self.assertEqual(payload["profit_objective"]["hourly_target_profit_krw"], 15000)
        self.assertEqual(payload["market"]["ticker"], "KRW-TEST")


if __name__ == "__main__":
    unittest.main()
