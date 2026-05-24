#!/bin/sh

cat << 'EOF'

▄▖      ▖▖▘    ▄▖▄▖
▌▌▛▌█▌▛▌▚▘▌▀▌▛▌▌▌▐ 
▙▌▙▌▙▖▌▌▌▌▌█▌▙▌▛▌▟▖
  ▌                 

v1.0.0  by: https://del.wang

EOF

set -e


DOWNLOAD_BASE_URL="${VOICE_GATEWAY_CLIENT_DOWNLOAD_BASE_URL:-}"


WORK_DIR="/data/voice-gateway"
CLIENT_BIN="$WORK_DIR/client"
SERVER_ADDRESS="ws://127.0.0.1:4399" # 默认不会连接到任何 server

if [ ! -d "$WORK_DIR" ]; then
    mkdir -p "$WORK_DIR"
fi

if [ ! -f "$CLIENT_BIN" ]; then
    if [ -z "$DOWNLOAD_BASE_URL" ]; then
        echo "❌ Client 不存在：$CLIENT_BIN"
        echo "请先使用 voice-gateway/scripts/install-speaker-client.sh 部署，或设置 VOICE_GATEWAY_CLIENT_DOWNLOAD_BASE_URL"
        exit 1
    fi
    echo "🔥 正在下载 Client 端补丁程序..."
    curl -L -# -o "$CLIENT_BIN" "$DOWNLOAD_BASE_URL/client"
    chmod +x "$CLIENT_BIN"
    echo "✅ Client 端补丁程序下载完毕"
fi


if [ -f "$WORK_DIR/server.txt" ]; then
    SERVER_ADDRESS=$(cat "$WORK_DIR/server.txt")
fi

echo "🔥 正在启动 Client 端补丁程序..."

kill -9 `ps|grep "voice-gateway/client"|grep -v grep|awk '{print $1}'` > /dev/null 2>&1 || true

"$CLIENT_BIN" "$SERVER_ADDRESS"
