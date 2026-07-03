"""Google Gemini provider.

무거운 SDK import 비용을 피하려고 표준 라이브러리 REST 호출을 사용한다.
JSON 모드 + 스키마 힌트로 형식을 받는다.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import config
from .base import Provider

class GeminiProvider(Provider):
    def __init__(self) -> None:
        self.model = config.MODELS["gemini"]
        query = urllib.parse.urlencode({"key": config.GEMINI_API_KEY})
        self.url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{urllib.parse.quote(self.model, safe='')}:generateContent?{query}"
        )

    def decide(self, system_prompt: str, user_content: str, schema: dict) -> dict:
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_content + _json_hint(schema)}]}],
            "generationConfig": {"responseMimeType": "application/json"},
        }
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
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
            raise RuntimeError(f"Gemini HTTP {exc.code}: {detail[:500]}") from exc

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(_strip_fences(text))


def _json_hint(schema: dict) -> str:
    return (
        "\n\n반드시 아래 JSON Schema를 만족하는 JSON 객체만 출력하라. "
        "코드블록(```)이나 설명 문장은 절대 넣지 않는다:\n"
        + json.dumps(schema, ensure_ascii=False)
    )


def _strip_fences(text: str) -> str:
    """혹시 ```json ... ``` 으로 감싸 오면 벗겨낸다."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]          # 첫 줄(```json) 제거
        if t.endswith("```"):
            t = t[: -3]
    return t.strip()
