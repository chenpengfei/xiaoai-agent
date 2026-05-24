import asyncio
import json
import unittest

from voice_gateway.adapters.xiaoai_ws_server import XiaoAIWebSocketServer


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(message)

    async def close(self):
        pass


class XiaoAIWebSocketServerTest(unittest.IsolatedAsyncioTestCase):
    async def test_record_stream_invokes_callback(self):
        server = XiaoAIWebSocketServer(host="127.0.0.1", port=4399)
        received = []
        server.register_fn("on_input_data", received.append)

        await server._on_binary_message(json.dumps({"id": "s1", "tag": "record", "bytes": [1, 2, 3]}).encode())

        assert received == [b"\x01\x02\x03"]

    async def test_run_shell_uses_rpc_request_response(self):
        server = XiaoAIWebSocketServer(host="127.0.0.1", port=4399)
        websocket = FakeWebSocket()
        server._active_ws = websocket

        call_task = asyncio.create_task(server.run_shell("echo hi", timeout_ms=1000))
        await asyncio.sleep(0)
        request = json.loads(websocket.sent[0])["Request"]

        assert request["command"] == "run_shell"
        assert request["payload"] == "echo hi"

        server._on_response(
            {
                "id": request["id"],
                "data": {"stdout": "hi\n", "stderr": "", "exit_code": 0},
            }
        )

        assert json.loads(await call_task) == {"stdout": "hi\n", "stderr": "", "exit_code": 0}

    async def test_bootstrap_invokes_connected_callback_after_audio_services(self):
        server = XiaoAIWebSocketServer(host="127.0.0.1", port=4399)
        commands = []
        callbacks = []

        async def fake_call_remote(command, payload, *, timeout_ms):
            commands.append((command, payload, timeout_ms))
            return {"data": {"stdout": "", "stderr": "", "exit_code": 0}}

        async def on_connected():
            callbacks.append("connected")

        server._call_remote = fake_call_remote
        server.register_fn("on_connected", on_connected)

        await server._bootstrap_speaker()

        assert [command for command, _payload, _timeout_ms in commands] == ["start_recording", "start_play"]
        assert callbacks == ["connected"]


if __name__ == "__main__":
    unittest.main()
