"""모든 AI provider가 따르는 공통 인터페이스."""
from __future__ import annotations

from abc import ABC, abstractmethod


class Provider(ABC):
    @abstractmethod
    def decide(self, system_prompt: str, user_content: str, schema: dict) -> dict:
        """시스템 프롬프트 + 사용자 내용 + JSON 스키마를 받아
        {"action","confidence","reasoning"} 형태의 dict를 반환한다."""
        raise NotImplementedError
