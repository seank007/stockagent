"""AI 제공자 선택.

config.AI_PROVIDER 값에 따라 알맞은 Provider 인스턴스를 만들어 돌려준다.
각 provider는 필요한 SDK를 자기 안에서만 import 하므로,
실제로 쓰는 provider의 패키지만 설치돼 있어도 동작한다.
"""
from __future__ import annotations

import config
from .base import Provider


def get_provider(name: str) -> Provider:
    name = name.lower()
    if name == "mock":
        from .mock import MockProvider
        return MockProvider()
    if name == "claude":
        from .claude import ClaudeProvider
        return ClaudeProvider()
    if name == "openai":
        from .openai_provider import OpenAIProvider
        return OpenAIProvider()
    if name == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider()
    raise SystemExit(
        f"알 수 없는 AI_PROVIDER: {name!r} (mock / claude / openai / gemini 중 하나여야 함)"
    )
