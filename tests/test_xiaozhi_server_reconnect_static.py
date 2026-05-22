import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SERVER_RS = ROOT / "open-xiaoai" / "examples" / "xiaozhi" / "src" / "server.rs"


def read_server() -> str:
    return SERVER_RS.read_text(encoding="utf-8")


def extract_run_body(src: str) -> str:
    marker = "pub async fn run()"
    start = src.index(marker)
    end = src.index("async fn handle_connection", start)
    return src[start:end]


class XiaoZhiServerReconnectStaticTest(unittest.TestCase):
    def test_accept_loop_does_not_block_on_current_connection(self):
        run_body = extract_run_body(read_server())

        self.assertIn("tokio::spawn", run_body)
        self.assertNotIn("// 同一时刻只处理一个连接", run_body)

    def test_new_connection_replaces_stale_active_connection(self):
        src = read_server()

        self.assertIn("active_connection", src)
        self.assertIn("abort()", src)
        self.assertIn("替换旧连接", src)

    def test_connection_lifecycle_logs_include_remote_addr_and_active_count(self):
        src = read_server()

        self.assertIn("active_client_count", src)
        self.assertIn("已连接", src)
        self.assertIn("已断开连接", src)
        self.assertIn("addr", src)

    def test_server_sends_websocket_heartbeat_and_disposes_task(self):
        src = read_server()

        self.assertIn("Message::Ping", src)
        self.assertIn('add("heartbeat"', src)
        self.assertIn('dispose("heartbeat"', src)


if __name__ == "__main__":
    unittest.main()
