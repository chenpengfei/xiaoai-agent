#!/bin/sh

cat << 'EOF'

▄▖      ▖▖▘    ▄▖▄▖
▌▌▛▌█▌▛▌▚▘▌▀▌▛▌▌▌▐ 
▙▌▙▌▙▖▌▌▌▌▌█▌▙▌▛▌▟▖
  ▌                 

v1.0.0  by: https://del.wang

EOF

set -e

MIN_SPACE_MB=32
DOWNLOAD_BASE_URL="${VOICE_GATEWAY_KWS_DOWNLOAD_BASE_URL:-}"


check_disk_space() {
    local space_kb=$(df -k "$1" | awk 'NR==2 {print $4}')
    if [ $((space_kb / 1024)) -lt "$MIN_SPACE_MB" ]; then
        echo 1
    else
        echo 0
    fi
}


BASE_DIR="/data"
if [ $(check_disk_space "$BASE_DIR") -eq 1 ]; then
    BASE_DIR="/tmp"
    if [ $(check_disk_space "$BASE_DIR") -eq 1 ]; then
        echo "❌ 磁盘空间不足，请先清理磁盘空间（至少需要 $MIN_SPACE_MB MB 空间）"
        exit 1
    fi
fi


WORK_DIR="$BASE_DIR/voice-gateway/kws"
KWS_DEBUG_BIN="$WORK_DIR/kws-debug"

if [ ! -d "$WORK_DIR" ]; then
    mkdir -p "$WORK_DIR"
fi

if [ ! -f "$WORK_DIR/models/encoder.onnx" ]; then
    if [ -z "$DOWNLOAD_BASE_URL" ]; then
        echo "❌ KWS 模型不存在：$WORK_DIR/models/encoder.onnx"
        echo "请先部署 KWS artifact，或设置 VOICE_GATEWAY_KWS_DOWNLOAD_BASE_URL"
        exit 1
    fi
    echo "🔥 正在下载模型文件..."
    curl -L -# -o "$WORK_DIR/kws.tar.gz" "$DOWNLOAD_BASE_URL/kws.tar.gz"
    tar -xzvf "$WORK_DIR/kws.tar.gz" -C "$WORK_DIR"
    rm "$WORK_DIR/kws.tar.gz"
    echo "✅ 模型文件下载完毕"
fi

if [ ! -f "$KWS_DEBUG_BIN" ]; then
    if [ -z "$DOWNLOAD_BASE_URL" ]; then
        echo "❌ kws-debug 不存在：$KWS_DEBUG_BIN"
        echo "请先部署 KWS artifact，或设置 VOICE_GATEWAY_KWS_DOWNLOAD_BASE_URL"
        exit 1
    fi
    echo "🔥 正在下载 kws-debug 文件..."
    curl -L -# -o "$KWS_DEBUG_BIN" "$DOWNLOAD_BASE_URL/kws-debug"
    chmod +x "$KWS_DEBUG_BIN"
    echo "✅ kws-debug 文件下载完毕"
fi

echo "🔥 正在启动唤醒词识别调试服务，请耐心等待..."
echo "🐢 模型加载较慢，请在提示 Started! Please speak 后，再使用自定义唤醒词"

kill -9 `ps|grep "voice-gateway/kws/kws-debug"|grep -v grep|awk '{print $1}'` > /dev/null 2>&1 || true
"$KWS_DEBUG_BIN" \
    --model-type=zipformer2 \
    --tokens="$WORK_DIR/models/tokens.txt" \
    --encoder="$WORK_DIR/models/encoder.onnx" \
    --decoder="$WORK_DIR/models/decoder.onnx" \
    --joiner="$WORK_DIR/models/joiner.onnx" \
    --provider=cpu \
    --num-threads=1 \
    --chunk-size=1024 \
    noop
