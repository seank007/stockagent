"""종목 분석(정보 제공) 에이전트.

매매 판단(decision.py)과 별개로, '이 종목 지금 어떤 상태인가?'에 대한
정보형 리포트를 생성한다. 매수/매도 추천이 아닌 시황·지표·리스크 요약.
"""
from __future__ import annotations

import json

import config
from agent.providers.fallback import FastFallbackProvider

ANALYSIS_SYSTEM = """당신은 'stockagent'의 시장 분석가다.
사용자가 특정 종목의 시세·지표를 보고 의사결정을 할 수 있도록, 객관적 정보 위주로 정리한다.
매수/매도 권유나 가격 예측은 하지 말고, "현재 상태"를 한국어로 명료히 설명한다."""

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary":   {"type": "string", "description": "한 문장 요약"},
        "trend":     {"type": "string", "description": "추세 (상승/하락/횡보) 및 근거"},
        "momentum":  {"type": "string", "description": "모멘텀(RSI 등) 해석"},
        "support_resistance": {
            "type": "string",
            "description": "최근 캔들 기준 추정 지지선·저항선 (대략 값)",
        },
        "risks":     {"type": "string", "description": "투자자가 유의할 리스크 요인"},
        "watch":     {"type": "string", "description": "관찰할 만한 신호(트리거)"},
    },
    "required": ["summary", "trend", "momentum", "support_resistance", "risks", "watch"],
    "additionalProperties": False,
}


def analyze(snapshot: dict) -> dict:
    """snapshot은 UpbitBroker.market_snapshot 결과와 동일 포맷."""
    provider = FastFallbackProvider(config.AI_PROVIDER)
    user = "다음 종목의 현재 상태를 분석하라:\n" + json.dumps(
        snapshot, ensure_ascii=False, indent=2
    )
    try:
        report = provider.decide(ANALYSIS_SYSTEM, user, ANALYSIS_SCHEMA)
        used_provider = report.pop("_provider", config.AI_PROVIDER)
        if report.pop("_fallback", False):
            report["summary"] = f"[{used_provider} 폴백] {report.get('summary', '')}".strip()
        return report
    except Exception as e:  # noqa: BLE001
        return {
            "summary": f"분석 실패: {type(e).__name__}",
            "trend": "-",
            "momentum": "-",
            "support_resistance": "-",
            "risks": str(e),
            "watch": "-",
        }
