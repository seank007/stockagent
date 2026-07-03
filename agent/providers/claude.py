"""Anthropic Claude provider.

무거운 SDK import 비용을 피하려고 표준 라이브러리 REST 호출을 사용한다.
tool use로 구조화된 판단 결과를 받는다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import config
from .base import Provider


class ClaudeProvider(Provider):
    def __init__(self) -> None:
        self.model = config.MODELS["claude"]
        self.url = "https://api.anthropic.com/v1/messages"

    def decide(self, system_prompt: str, user_content: str, schema: dict) -> dict:
        tool = {
            "name": "submit_decision",
            "description": "매매 판단 결과를 제출한다.",
            "input_schema": schema,
        }
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "system": system_prompt,
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": "submit_decision"},
            "messages": [{"role": "user", "content": user_content}],
        }
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                req,
                timeout=float(getattr(config, "AI_HTTP_TIMEOUT_SECONDS", 12)),
            ) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Claude HTTP {exc.code}: {detail[:500]}") from exc

        for block in data.get("content") or []:
            if block.get("type") == "tool_use" and block.get("name") == "submit_decision":
                return dict(block.get("input") or {})
        raise RuntimeError("Claude가 도구를 호출하지 않음")
