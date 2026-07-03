"""빠른 AI provider 라우터.

선호 provider가 쿼터/인증/네트워크 오류를 내면 짧은 시간 동안 건너뛰고,
사용 가능한 다음 provider로 즉시 폴백한다.
"""
from __future__ import annotations

import time
import threading

import config
from . import get_provider
from .base import Provider


class FastFallbackProvider(Provider):
    _providers: dict[str, Provider] = {}
    _disabled_until: dict[str, float] = {}
    _last_error: dict[str, str] = {}
    _lock = threading.Lock()

    def __init__(self, preferred: str | None = None) -> None:
        self.preferred = (preferred or config.AI_PROVIDER).lower()

    def decide(self, system_prompt: str, user_content: str, schema: dict) -> dict:
        errors: list[str] = []
        now = time.monotonic()

        for name in self._provider_order():
            disabled_until = self._disabled_until.get(name, 0.0)
            if disabled_until > now:
                remain = int(disabled_until - now)
                last = self._last_error.get(name, "최근 오류")
                errors.append(f"{name}: cooldown {remain}s ({last})")
                continue

            try:
                data = self._provider(name).decide(system_prompt, user_content, schema)
            except Exception as exc:  # noqa: BLE001
                summary = _compact_error(exc)
                with self._lock:
                    self._last_error[name] = summary
                    self._disabled_until[name] = time.monotonic() + _cooldown_seconds(exc)
                errors.append(f"{name}: {summary}")
                continue

            if not isinstance(data, dict):
                raise RuntimeError(f"{name} provider 응답 형식 오류: {type(data).__name__}")
            out = dict(data)
            out["_provider"] = name
            out["_fallback"] = name != self.preferred
            return out

        raise RuntimeError("모든 AI provider 실패: " + " | ".join(errors))

    def _provider_order(self) -> list[str]:
        speed_first = [self.preferred, "openai", "gemini", "claude"]
        out: list[str] = []
        for name in speed_first:
            if name in out or not _provider_enabled(name):
                continue
            out.append(name)
        if not out and self.preferred == "mock":
            out.append("mock")
        return out

    @classmethod
    def _provider(cls, name: str) -> Provider:
        with cls._lock:
            if name not in cls._providers:
                cls._providers[name] = get_provider(name)
            return cls._providers[name]


def _provider_enabled(name: str) -> bool:
    if name == "mock":
        return config.AI_PROVIDER.lower() == "mock"
    if name == "openai":
        return _usable_key(config.OPENAI_API_KEY)
    if name == "gemini":
        return _usable_key(config.GEMINI_API_KEY)
    if name == "claude":
        return _usable_key(config.ANTHROPIC_API_KEY)
    return False


def _usable_key(value: str) -> bool:
    key = str(value or "").strip()
    lowered = key.lower()
    if not key:
        return False
    placeholders = ("your-", "your_", "placeholder", "example", "changeme", "api-key")
    return not any(marker in lowered for marker in placeholders)


def _cooldown_seconds(error: Exception) -> int:
    text = f"{type(error).__name__} {error}".lower()
    base = int(getattr(config, "AI_PROVIDER_COOLDOWN_SECONDS", 600))
    if any(key in text for key in ("401", "403", "api key", "authentication", "permission")):
        return max(base, 1800)
    if any(key in text for key in ("429", "quota", "rate limit", "rate-limit", "resource_exhausted")):
        return base
    if any(key in text for key in ("timeout", "temporarily", "503", "502", "500")):
        return min(base, 180)
    return min(base, 120)


def _compact_error(error: Exception, max_chars: int = 180) -> str:
    text = " ".join(str(error).split())
    if not text:
        text = type(error).__name__
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "..."
    return f"{type(error).__name__}: {text}"
