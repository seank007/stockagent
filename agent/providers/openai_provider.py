"""OpenAI 호환 provider.

무거운 SDK import 비용을 피하려고 표준 라이브러리 REST 호출을 사용한다.
OPENAI_BASE_URL을 설정하면 OpenAI 호환 엔드포인트도 그대로 쓸 수 있다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import config
from .base import Provider


class OpenAIProvider(Provider):
    def __init__(self) -> None:
        self.model = config.MODELS["openai"]
        base_url = (config.OPENAI_BASE_URL or "https://api.openai.com/v1").rstrip("/")
        self.url = f"{base_url}/chat/completions"

    def decide(self, system_prompt: str, user_content: str, schema: dict) -> dict:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "decision", "strict": True, "schema": schema},
            },
        }
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {config.OPENAI_API_KEY}",
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
            raise RuntimeError(f"OpenAI HTTP {exc.code}: {detail[:500]}") from exc

        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
