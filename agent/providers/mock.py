"""데모(mock) provider — API 키 없이 동작.

실제 AI를 부르지 않고, 시세 지표(RSI/추세)로 간단한 규칙 판단을 만들어낸다.
대시보드/전체 흐름을 키 없이 바로 눈으로 확인하는 용도. 실거래용 아님.
"""
from __future__ import annotations

from .base import Provider


class MockProvider(Provider):
    def decide(self, system_prompt: str, user_content: str, schema: dict) -> dict:
        import json
        import re

        # user_content 안의 JSON에서 지표만 대충 뽑아 규칙 판단
        rsi = _grab(user_content, "rsi14", 50.0)
        trend = "up" if '"trend": "up"' in user_content else "down"

        if rsi < 30:
            return {"action": "BUY", "confidence": 0.72,
                    "reasoning": f"[데모] RSI {rsi:.0f} 과매도 구간이라 반등 기대 매수."}
        if rsi > 70:
            return {"action": "SELL", "confidence": 0.70,
                    "reasoning": f"[데모] RSI {rsi:.0f} 과매수 구간이라 차익 실현 매도."}
        if trend == "up" and rsi < 60:
            return {"action": "BUY", "confidence": 0.63,
                    "reasoning": f"[데모] 상승추세 + RSI {rsi:.0f} 여유로 추세추종 매수."}
        return {"action": "HOLD", "confidence": 0.5,
                "reasoning": f"[데모] RSI {rsi:.0f}, 추세 {trend} — 신호 약해 관망."}


def _grab(text: str, key: str, default: float) -> float:
    import re
    m = re.search(rf'"{key}":\s*([0-9.]+)', text)
    return float(m.group(1)) if m else default
