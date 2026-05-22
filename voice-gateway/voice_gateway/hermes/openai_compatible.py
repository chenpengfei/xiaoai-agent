from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request

from voice_gateway.config import HermesConfig
from voice_gateway.models import HermesResponse, HermesTurn


class OpenAICompatibleHermesConnector:
    def __init__(self, config: HermesConfig = HermesConfig()) -> None:
        self.config = config

    async def ask(self, turn: HermesTurn) -> HermesResponse:
        started = time.perf_counter()
        text = await asyncio.to_thread(self._request_completion, turn)
        return HermesResponse(
            text=text[:400] or "Hermes 没有返回内容。",
            should_speak=True,
            model=self.config.model,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )

    def _request_completion(self, turn: HermesTurn) -> str:
        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是一个接在小爱音箱后面的家庭智能助手。回答要简短、自然、适合直接朗读。",
                },
                {
                    "role": "user",
                    "content": (
                        "请用适合语音播报的中文回答，控制在 200 字以内，"
                        "口语化，不要使用 Markdown，不要输出代码块，不要输出很长列表。用户问题："
                        + turn.user_text
                    ),
                },
            ],
            "temperature": 0.7,
            "max_tokens": self.config.max_tokens,
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        request = urllib.request.Request(
            f"{self.config.base_url}/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Hermes API HTTP {exc.code}: {details}") from exc

        choices = data.get("choices") or []
        if not choices:
            return ""
        first = choices[0]
        message = first.get("message") or {}
        return (message.get("content") or first.get("text") or "").strip()
