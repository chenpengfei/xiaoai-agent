from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Optional

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from server.observability import runtime_log


Callback = Callable[[Any], Any]


@dataclass
class _PendingRequest:
    future: asyncio.Future[dict[str, Any]]


class XiaoAIWebSocketServer:
    """Minimal XiaoAI websocket server owned by voice-gateway.

    The speaker patch uses a tiny RPC/stream protocol:
    text frames carry externally-tagged JSON messages such as
    {"Request": ...}, {"Response": ...}, {"Event": ...}; binary frames carry
    a JSON encoded Stream object whose `bytes` field is a list of byte values.
    """

    def __init__(self, *, host: str = "0.0.0.0", port: int = 4399) -> None:
        self.host = host
        self.port = port
        self._callbacks: dict[str, Callback] = {}
        self._active_ws: Optional[ServerConnection] = None
        self._active_task: Optional[asyncio.Task[None]] = None
        self._pending: dict[str, _PendingRequest] = {}
        self._stop_event: Optional[asyncio.Event] = None

    def register_fn(self, key: str, function: Callback) -> None:
        self._callbacks[key] = function

    def unregister_fn(self, key: str) -> None:
        self._callbacks.pop(key, None)

    async def start_server(self) -> None:
        self._stop_event = asyncio.Event()
        async with serve(self._handle_connection, self.host, self.port, ping_interval=15):
            runtime_log("gateway", "started", host=self.host, port=self.port, protocol="xiaoai_ws")
            await self._stop_event.wait()

    async def stop(self) -> None:
        if self._stop_event is not None:
            self._stop_event.set()
        if self._active_ws is not None:
            await self._active_ws.close()
        if self._active_task is not None:
            self._active_task.cancel()
            try:
                await self._active_task
            except asyncio.CancelledError:
                pass

    async def run_shell(self, script: str, timeout_ms: int | float = 10_000) -> str:
        response = await self._call_remote("run_shell", script, timeout_ms=timeout_ms)
        return json.dumps(response.get("data"), ensure_ascii=False)

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        if self._active_ws is not None:
            await self._active_ws.close()
        if self._active_task is not None and not self._active_task.done():
            self._active_task.cancel()
        self._active_ws = websocket
        self._active_task = asyncio.current_task()
        runtime_log("gateway", "connected", peer="xiaoai_speaker")
        bootstrap_task = asyncio.create_task(self._bootstrap_speaker())
        try:
            async for message in websocket:
                if isinstance(message, bytes):
                    await self._on_binary_message(message)
                else:
                    await self._on_text_message(message)
        except ConnectionClosed:
            pass
        finally:
            if self._active_ws is websocket:
                self._active_ws = None
            for pending in self._pending.values():
                if not pending.future.done():
                    pending.future.set_exception(ConnectionError("XiaoAI websocket disconnected"))
            self._pending.clear()
            if not bootstrap_task.done():
                bootstrap_task.cancel()
            runtime_log("gateway", "disconnected", peer="xiaoai_speaker")

    async def _bootstrap_speaker(self) -> None:
        # Start the speaker-side record/play audio services. Failures here are
        # logged but not fatal, because an already-running speaker may still
        # stream audio after reconnecting.
        try:
            await self._call_remote(
                "start_recording",
                _audio_config(sample_rate=16000),
                timeout_ms=5_000,
            )
        except Exception as exc:
            runtime_log("audio", "record_start_failed", level="warn", error=str(exc))
        try:
            await self._call_remote(
                "start_play",
                _audio_config(sample_rate=24000),
                timeout_ms=5_000,
            )
        except Exception as exc:
            runtime_log("playback", "start_failed", level="warn", error=str(exc))
        await self._invoke_callback("on_connected")

    async def _on_binary_message(self, message: bytes) -> None:
        try:
            stream = json.loads(message.decode("utf-8"))
        except Exception as exc:
            runtime_log("gateway", "invalid_stream", level="warn", error=str(exc))
            return
        if stream.get("tag") != "record":
            return
        raw_bytes = stream.get("bytes", [])
        try:
            data = bytes(raw_bytes)
        except Exception:
            runtime_log("audio", "invalid_record_bytes", level="warn")
            return
        callback = self._callbacks.get("on_input_data")
        if callback is not None:
            callback(data)

    async def _on_text_message(self, message: str) -> None:
        try:
            payload = json.loads(message)
        except Exception as exc:
            runtime_log("gateway", "invalid_text", level="warn", error=str(exc))
            return
        if "Response" in payload:
            self._on_response(payload["Response"])
        elif "Event" in payload:
            self._on_event(payload["Event"])
        elif "Request" in payload:
            await self._on_request(payload["Request"])

    def _on_response(self, response: dict[str, Any]) -> None:
        request_id = str(response.get("id", ""))
        pending = self._pending.pop(request_id, None)
        if pending is not None and not pending.future.done():
            pending.future.set_result(response)

    def _on_event(self, event: dict[str, Any]) -> None:
        callback = self._callbacks.get("on_event")
        if callback is not None:
            callback(json.dumps(event, ensure_ascii=False))

    async def _invoke_callback(self, key: str, *args: Any) -> None:
        callback = self._callbacks.get(key)
        if callback is None:
            return
        result = callback(*args)
        if inspect.isawaitable(result):
            await result

    async def _on_request(self, request: dict[str, Any]) -> None:
        response = {
            "id": request.get("id"),
            "code": -1,
            "msg": "command not found",
        }
        if request.get("command") == "get_version":
            response = {"id": request.get("id"), "data": "voice-gateway"}
        await self._send_text({"Response": response})

    async def _call_remote(self, command: str, payload: Any, *, timeout_ms: int | float) -> dict[str, Any]:
        if self._active_ws is None:
            raise ConnectionError("XiaoAI websocket is not connected")
        request_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = _PendingRequest(future)
        await self._send_text(
            {
                "Request": {
                    "id": request_id,
                    "command": command,
                    "payload": payload,
                }
            }
        )
        try:
            return await asyncio.wait_for(future, timeout=float(timeout_ms) / 1000)
        finally:
            self._pending.pop(request_id, None)

    async def _send_text(self, payload: dict[str, Any]) -> None:
        if self._active_ws is None:
            raise ConnectionError("XiaoAI websocket is not connected")
        await self._active_ws.send(json.dumps(payload, ensure_ascii=False))


def _audio_config(*, sample_rate: int) -> dict[str, Any]:
    return {
        "pcm": "noop",
        "channels": 1,
        "bits_per_sample": 16,
        "sample_rate": sample_rate,
        "period_size": 360,
        "buffer_size": 1440,
    }
