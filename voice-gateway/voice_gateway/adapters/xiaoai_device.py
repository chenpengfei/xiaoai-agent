from __future__ import annotations

import json
from dataclasses import dataclass

from voice_gateway.models import PlaybackResource


@dataclass(frozen=True)
class ShellResult:
    stdout: str
    stderr: str
    exit_code: int


class XiaoAIDeviceController:
    """Real XiaoAI playback controller backed by open_xiaoai_server.run_shell."""

    def __init__(self, open_xiaoai_server, *, timeout_ms: int = 10 * 60 * 1000) -> None:
        self.open_xiaoai_server = open_xiaoai_server
        self.timeout_ms = timeout_ms

    async def play_audio_resource(self, resource: PlaybackResource) -> bool:
        result = await self.run_shell(f"miplayer -f '{_shell_quote_single(resource.url)}'", timeout_ms=self.timeout_ms)
        return result.exit_code == 0

    async def play_text(self, text: str, *, timeout_ms: int = 60 * 1000) -> bool:
        safe_text = _shell_quote_single(text or "你好")
        result = await self.run_shell(f"/usr/sbin/tts_play.sh '{safe_text}'", timeout_ms=timeout_ms)
        return result.exit_code == 0

    async def run_shell(self, script: str, *, timeout_ms: int = 10 * 1000) -> ShellResult:
        raw = await self.open_xiaoai_server.run_shell(script, timeout_ms)
        try:
            data = json.loads(raw)
        except Exception:
            return ShellResult(stdout="", stderr=str(raw), exit_code=-1)
        return ShellResult(
            stdout=str(data.get("stdout", "")),
            stderr=str(data.get("stderr", "")),
            exit_code=int(data.get("exit_code", 0)),
        )


def _shell_quote_single(value: str) -> str:
    return value.replace("'", "'\\''")
