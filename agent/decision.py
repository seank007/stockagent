"""매매 판단 오케스트레이션.

시세 요약 → 선택된 AI provider 호출 → {action, confidence, reasoning} 구조화 반환.
어떤 AI를 쓸지는 config.AI_PROVIDER 로 결정된다 (claude / openai / gemini).
"""
from __future__ import annotations

import json
from dataclasses import dataclass

import config
from agent.providers.fallback import FastFallbackProvider

# 제공자 공통 매매 규칙 (특정 AI에 종속되지 않게 작성)
SYSTEM_PROMPT = """당신은 'stockagent'라는 암호화폐 단기 매매 보조 에이전트다.
업비트 KRW 마켓에서 주어진 시세 요약과 보조지표를 보고 매수/매도/관망을 판단한다.

판단 원칙:
- 추세(ma5 vs ma20), 모멘텀(RSI), 최근 캔들 흐름을 종합한다.
- RSI 70 이상은 과매수, 30 이하는 과매도 신호로 본다.
- 확신이 없으면 HOLD를 택한다. 무리한 매매보다 자본 보존이 우선이다.
- 이미 보유 중인데 추세가 꺾이면 SELL을 고려한다.
- confidence는 0.0~1.0 사이로, 신호가 명확할수록 높게 매긴다.
- 절대 단정적 수익 보장을 하지 말고, 근거(reasoning)를 한국어로 간결히 적는다."""

# 자유 모드: 한도 없이 AI가 주문 여부와 크기를 전적으로 결정한다.
FREE_SYSTEM_PROMPT = """당신은 사용자의 업비트 계좌 운용을 전적으로 위임받은 자율 트레이딩 에이전트다.
주어진 시세 요약·보조지표·보유 포지션을 보고 매수/매도/관망과 주문 크기를 스스로 결정한다.
외부에서 강제하는 신뢰도 기준, 주문 한도, 손실 한도는 없다. 전략도 스스로 정한다.

규칙:
- size_pct: BUY면 사용 가능한 원화의 몇 %를 쓸지, SELL이면 보유 수량의 몇 %를 팔지 (1~100).
- 유일한 하드 제약은 업비트 규칙이다: 주문 금액 5,000원 미만은 거래소가 거절한다. 수수료는 0.05%.
- confidence는 0.0~1.0 사이 판단 신뢰도로 참고용으로만 기록된다. 주문을 막지 않는다.
- 근거(reasoning)는 한국어로 간결히 적는다. 단정적 수익 보장은 하지 않는다."""

# 모든 provider가 공유하는 출력 스키마
DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["BUY", "SELL", "HOLD"],
            "description": "매수/매도/관망 중 하나",
        },
        "confidence": {"type": "number", "description": "판단 신뢰도 0.0~1.0"},
        "reasoning": {"type": "string", "description": "판단 근거 (한국어, 2~3문장)"},
    },
    "required": ["action", "confidence", "reasoning"],
    "additionalProperties": False,
}

# 자유 모드 스키마: 주문 크기(size_pct)까지 AI가 정한다.
FREE_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        **DECISION_SCHEMA["properties"],
        "size_pct": {
            "type": "number",
            "description": "주문 크기: BUY는 가용 원화의 %, SELL은 보유 수량의 % (1~100)",
        },
    },
    "required": ["action", "confidence", "reasoning", "size_pct"],
    "additionalProperties": False,
}


@dataclass
class Decision:
    action: str
    confidence: float
    reasoning: str
    size_pct: float = 100.0


class DecisionAgent:
    def __init__(self) -> None:
        self.provider = FastFallbackProvider(config.AI_PROVIDER)

    def decide(self, snapshot: dict, position: dict) -> Decision:
        free = config.FREE_TRADE_MODE
        user_payload: dict = {
            "market": snapshot,
            "my_position": position,
        }
        if free:
            user_payload["exchange_rules"] = {
                "min_order_krw": config.UPBIT_MIN_ORDER_KRW,
                "fee_rate": 0.0005,
            }
            prompt = FREE_SYSTEM_PROMPT
            schema = FREE_DECISION_SCHEMA
        else:
            user_payload["limits"] = {
                "max_order_krw": config.MAX_ORDER_KRW,
                "min_confidence_to_trade": config.MIN_CONFIDENCE,
            }
            prompt = SYSTEM_PROMPT
            schema = DECISION_SCHEMA
        user_content = "다음 시장 상황을 분석해 매매를 판단하라:\n" + json.dumps(
            user_payload, ensure_ascii=False, indent=2
        )

        try:
            data = self.provider.decide(prompt, user_content, schema)
            provider = str(data.pop("_provider", config.AI_PROVIDER))
            fallback = bool(data.pop("_fallback", False))
            reasoning = str(data["reasoning"])
            if fallback:
                reasoning = f"[{provider} 폴백] {reasoning}"
            size_pct = 100.0
            if free:
                try:
                    size_pct = max(1.0, min(float(data.get("size_pct", 100.0)), 100.0))
                except (TypeError, ValueError):
                    size_pct = 100.0
            return Decision(
                action=str(data["action"]).upper(),
                confidence=float(data["confidence"]),
                reasoning=_compact_text(reasoning, 420),
                size_pct=size_pct,
            )
        except Exception as e:  # noqa: BLE001 - 판단 실패 시 안전하게 관망
            return Decision("HOLD", 0.0, _summarize_provider_error(e))


def _summarize_provider_error(error: Exception) -> str:
    msg = str(error)
    err_name = type(error).__name__
    if "모든 AI provider 실패" in msg:
        return _compact_text(f"AI provider 전체 실패: {msg}", 420)
    if "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower() or "rate-limit" in msg.lower():
        return f"AI 호출 한도 초과({err_name})로 판단 생략. 잠시 후 재시도하거나 AI_PROVIDER/키 설정을 확인하세요."
    if "401" in msg or "403" in msg or "API key" in msg:
        return f"AI 인증 오류({err_name})로 판단 생략. API 키와 권한을 확인하세요."
    if "timeout" in msg.lower():
        return f"AI 응답 지연({err_name})으로 판단 생략. 다음 주기에 재시도합니다."
    compact = " ".join(msg.split())
    if len(compact) > 160:
        compact = compact[:157] + "..."
    return f"AI 판단 실패({err_name}): {compact}"


def _compact_text(text: str, max_chars: int) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 1] + "..."
