#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VOICE_GATEWAY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

SPEAKER_MAC="${VOICE_GATEWAY_SPEAKER_MAC:-50:88:11:6f:f2:a8}"
SPEAKER_HOST="${VOICE_GATEWAY_SPEAKER_HOST:-}"
SPEAKER_USER="${VOICE_GATEWAY_SPEAKER_USER:-root}"
SPEAKER_PASSWORD="${VOICE_GATEWAY_SPEAKER_PASSWORD:-}"
REMOTE_DIR="${VOICE_GATEWAY_REMOTE_DIR:-/data/voice-gateway}"
LEGACY_REMOTE_DIR="${VOICE_GATEWAY_LEGACY_REMOTE_DIR:-/data/open-xiaoai}"
MAC_IP="${VOICE_GATEWAY_MAC_IP:-}"
SERVER_PORT="${VOICE_GATEWAY_PORT:-4399}"
TTS_HTTP_PORT="${VOICE_GATEWAY_TTS_HTTP_PORT:-8765}"
SCAN_LAN="${VOICE_GATEWAY_SCAN_LAN:-1}"
SSH_TIMEOUT_SECONDS="${VOICE_GATEWAY_SSH_TIMEOUT_SECONDS:-15}"

usage() {
  cat <<'EOF'
Usage:
  scripts/sync-speaker-network.sh

Environment overrides:
  VOICE_GATEWAY_SPEAKER_HOST      Speaker IP. If empty, detect by ARP MAC.
  VOICE_GATEWAY_SPEAKER_MAC       Speaker MAC. Default: 50:88:11:6f:f2:a8.
  VOICE_GATEWAY_MAC_IP            Mac Mini LAN IP. If empty, auto-detect.
  VOICE_GATEWAY_SPEAKER_PASSWORD  SSH password. Uses sshpass or expect if present.
  VOICE_GATEWAY_PORT              WebSocket server port. Default: 4399.
  VOICE_GATEWAY_TTS_HTTP_PORT     TTS HTTP port. Default: 8765.
  VOICE_GATEWAY_SCAN_LAN          Ping-scan LAN before ARP lookup. Default: 1.

Example:
  VOICE_GATEWAY_SPEAKER_PASSWORD=open-xiaoai ./scripts/sync-speaker-network.sh
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

section() {
  echo
  echo "== $* =="
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

normalize_mac() {
  tr '[:upper:]' '[:lower:]' <<<"$1" | tr '-' ':'
}

is_ipv4() {
  [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]
}

all_lan_ips() {
  if command -v ifconfig >/dev/null 2>&1; then
    ifconfig | awk '/inet / && $2 !~ /^127\./ && $2 !~ /^169\.254\./ {print $2}'
  elif command -v ip >/dev/null 2>&1; then
    ip -4 addr show scope global | awk '/inet / {split($2, a, "/"); print a[1]}'
  fi
}

default_route_interface() {
  if command -v route >/dev/null 2>&1; then
    route -n get default 2>/dev/null | awk '/interface:/ {print $2; exit}'
  elif command -v ip >/dev/null 2>&1; then
    ip route get 1.1.1.1 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i=="dev") {print $(i+1); exit}}'
  fi
}

ip_for_interface() {
  local iface="$1"
  if [[ -z "$iface" ]]; then
    return 1
  fi
  if command -v ifconfig >/dev/null 2>&1; then
    ifconfig "$iface" 2>/dev/null | awk '/inet / && $2 !~ /^127\./ && $2 !~ /^169\.254\./ {print $2; exit}'
  elif command -v ip >/dev/null 2>&1; then
    ip -4 addr show dev "$iface" scope global 2>/dev/null | awk '/inet / {split($2, a, "/"); print a[1]; exit}'
  fi
}

detect_mac_ip() {
  local speaker_ip="${1:-}"
  if [[ -n "$MAC_IP" ]]; then
    echo "$MAC_IP"
    return 0
  fi

  if is_ipv4 "$speaker_ip"; then
    local prefix="${speaker_ip%.*}."
    local ip
    while IFS= read -r ip; do
      if [[ "$ip" == "$prefix"* ]]; then
        echo "$ip"
        return 0
      fi
    done < <(all_lan_ips)
  fi

  local iface
  iface="$(default_route_interface || true)"
  local route_ip
  route_ip="$(ip_for_interface "$iface" || true)"
  if is_ipv4 "$route_ip"; then
    echo "$route_ip"
    return 0
  fi

  all_lan_ips | head -n 1
}

arp_ip_for_mac() {
  local mac
  mac="$(normalize_mac "$1")"
  arp -an 2>/dev/null | awk -v mac="$mac" '
    tolower($0) ~ mac {
      gsub(/[()]/, "", $2)
      print $2
      exit
    }
  '
}

ping_subnet() {
  local prefix="$1"
  local jobs=0
  local i
  for i in $(seq 1 254); do
    (ping -c 1 -W 200 "${prefix}.${i}" >/dev/null 2>&1 || true) &
    jobs=$((jobs + 1))
    if (( jobs % 32 == 0 )); then
      wait
    fi
  done
  wait
}

detect_speaker_ip() {
  if [[ -n "$SPEAKER_HOST" ]]; then
    echo "$SPEAKER_HOST"
    return 0
  fi

  local ip
  ip="$(arp_ip_for_mac "$SPEAKER_MAC" || true)"
  if is_ipv4 "$ip"; then
    echo "$ip"
    return 0
  fi

  if [[ "$SCAN_LAN" != "0" ]]; then
    local lan_ip prefix
    while IFS= read -r lan_ip; do
      prefix="${lan_ip%.*}"
      log "scanning ${prefix}.0/24 for speaker MAC $SPEAKER_MAC..." >&2
      ping_subnet "$prefix"
      ip="$(arp_ip_for_mac "$SPEAKER_MAC" || true)"
      if is_ipv4 "$ip"; then
        echo "$ip"
        return 0
      fi
    done < <(all_lan_ips)
  fi

  return 1
}

ssh_opts() {
  printf '%s\n' \
    "-o" "HostKeyAlgorithms=+ssh-rsa" \
    "-o" "PubkeyAcceptedAlgorithms=+ssh-rsa" \
    "-o" "StrictHostKeyChecking=no" \
    "-o" "ConnectTimeout=$SSH_TIMEOUT_SECONDS"
}

run_ssh() {
  local remote="$1"
  local remote_cmd="$2"

  if [[ -n "$SPEAKER_PASSWORD" ]] && command -v sshpass >/dev/null 2>&1; then
    SSHPASS="$SPEAKER_PASSWORD" sshpass -e ssh $(ssh_opts) "$remote" "$remote_cmd"
    return
  fi

  if [[ -n "$SPEAKER_PASSWORD" ]] && command -v expect >/dev/null 2>&1; then
    REMOTE="$remote" REMOTE_CMD="$remote_cmd" SPEAKER_PASSWORD="$SPEAKER_PASSWORD" SSH_TIMEOUT_SECONDS="$SSH_TIMEOUT_SECONDS" expect <<'EXPECT'
set timeout $env(SSH_TIMEOUT_SECONDS)
set remote $env(REMOTE)
set remote_cmd $env(REMOTE_CMD)
set password $env(SPEAKER_PASSWORD)
set opts [list -o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa -o StrictHostKeyChecking=no -o ConnectTimeout=$env(SSH_TIMEOUT_SECONDS)]
log_user 0
spawn ssh {*}$opts $remote $remote_cmd
expect {
  "yes/no" {
    send "yes\r"
    exp_continue
  }
  "*assword:" {
    send "$password\r"
    log_user 1
    exp_continue
  }
  eof
}
set result [wait]
exit [lindex $result 3]
EXPECT
    return
  fi

  ssh $(ssh_opts) "$remote" "$remote_cmd"
}

section "Detecting Network"
speaker_ip="$(detect_speaker_ip || true)"
if ! is_ipv4 "$speaker_ip"; then
  fail "could not detect speaker IP. Set VOICE_GATEWAY_SPEAKER_HOST=192.168.1.2 and retry."
fi

mac_ip="$(detect_mac_ip "$speaker_ip" || true)"
if ! is_ipv4 "$mac_ip"; then
  fail "could not detect Mac Mini LAN IP. Set VOICE_GATEWAY_MAC_IP and retry."
fi

server_url="ws://${mac_ip}:${SERVER_PORT}"
tts_http_base_url="http://${mac_ip}:${TTS_HTTP_PORT}"
remote="${SPEAKER_USER}@${speaker_ip}"

echo "speaker_mac: $SPEAKER_MAC"
echo "speaker_ip:  $speaker_ip"
echo "mac_ip:      $mac_ip"
echo "server_url:  $server_url"
echo "tts_url:     $tts_http_base_url"

read -r -d '' remote_script <<EOF || true
set -eu
SERVER_URL='$server_url'
REMOTE_DIR='$REMOTE_DIR'
LEGACY_REMOTE_DIR='$LEGACY_REMOTE_DIR'

read_file() {
  if [ -f "\$1" ]; then
    cat "\$1"
  else
    printf '<missing>'
  fi
}

running_clients() {
  ps | grep -E 'voice-gateway/client|open-xiaoai/client' | grep -v grep || true
}

active_connections() {
  netstat -tn 2>/dev/null | grep ':4399' || true
}

print_clients() {
  clients="\$(running_clients)"
  if [ -n "\$clients" ]; then
    printf '%s\n' "\$clients" | sed 's/^/  /'
  else
    echo '  <none>'
  fi
}

print_connections() {
  connections="\$(active_connections)"
  if [ -n "\$connections" ]; then
    printf '%s\n' "\$connections" | sed 's/^/  /'
  else
    echo '  <none>'
  fi
}

start_client() {
  client_path="\$1"
  log_file="\$2"
  if command -v nohup >/dev/null 2>&1; then
    nohup "\$client_path" "\$SERVER_URL" >"\$log_file" 2>&1 &
  else
    ("\$client_path" "\$SERVER_URL" >"\$log_file" 2>&1 </dev/null &)
  fi
}

before_voice_gateway="\$(read_file "\$REMOTE_DIR/server.txt")"
before_legacy="\$(read_file "\$LEGACY_REMOTE_DIR/server.txt")"

echo "Remote before:"
echo "  \$REMOTE_DIR/server.txt: \$before_voice_gateway"
echo "  \$LEGACY_REMOTE_DIR/server.txt: \$before_legacy"
echo "  running client processes:"
print_clients
echo "  active 4399 connections:"
print_connections

mkdir -p "\$REMOTE_DIR"
printf '%s\n' "\$SERVER_URL" > "\$REMOTE_DIR/server.txt"

if [ -d "\$LEGACY_REMOTE_DIR" ]; then
  printf '%s\n' "\$SERVER_URL" > "\$LEGACY_REMOTE_DIR/server.txt"
fi

if [ -f "\$REMOTE_DIR/boot.sh" ]; then
  cp "\$REMOTE_DIR/boot.sh" /data/init.sh
  chmod +x /data/init.sh
elif [ -f "\$LEGACY_REMOTE_DIR/boot.sh" ]; then
  cp "\$LEGACY_REMOTE_DIR/boot.sh" /data/init.sh
  chmod +x /data/init.sh
fi

kill -9 \$(ps | grep 'voice-gateway/client' | grep -v grep | awk '{print \$1}') >/dev/null 2>&1 || true
kill -9 \$(ps | grep 'open-xiaoai/client' | grep -v grep | awk '{print \$1}') >/dev/null 2>&1 || true

STARTED='none'
if [ -x "\$REMOTE_DIR/client" ]; then
  start_client "\$REMOTE_DIR/client" /tmp/voice-gateway-client.log
  STARTED="\$REMOTE_DIR/client"
elif [ -x "\$LEGACY_REMOTE_DIR/client" ]; then
  start_client "\$LEGACY_REMOTE_DIR/client" "\$LEGACY_REMOTE_DIR/client.log"
  STARTED="\$LEGACY_REMOTE_DIR/client"
fi

sleep 1

after_voice_gateway="\$(read_file "\$REMOTE_DIR/server.txt")"
after_legacy="\$(read_file "\$LEGACY_REMOTE_DIR/server.txt")"

echo
echo "Remote changes:"
if [ "\$before_voice_gateway" = "\$after_voice_gateway" ]; then
  echo "  unchanged: \$REMOTE_DIR/server.txt remains \$after_voice_gateway"
else
  echo "  updated:   \$REMOTE_DIR/server.txt"
  echo "             \$before_voice_gateway -> \$after_voice_gateway"
fi

if [ "\$before_legacy" = "\$after_legacy" ]; then
  echo "  unchanged: \$LEGACY_REMOTE_DIR/server.txt remains \$after_legacy"
else
  echo "  updated:   \$LEGACY_REMOTE_DIR/server.txt"
  echo "             \$before_legacy -> \$after_legacy"
fi

after_clients="\$(running_clients)"
after_connections="\$(active_connections)"

if [ "\$STARTED" = 'none' ]; then
  echo "  warning:   no executable client found under \$REMOTE_DIR or \$LEGACY_REMOTE_DIR"
else
  echo "  restart:   attempted \$STARTED \$SERVER_URL"
  if [ -z "\$after_clients" ] && [ -z "\$after_connections" ]; then
    echo "  warning:   no client process or 4399 connection detected after restart"
  fi
fi

echo
echo "Remote after:"
echo "  \$REMOTE_DIR/server.txt: \$after_voice_gateway"
echo "  \$LEGACY_REMOTE_DIR/server.txt: \$after_legacy"
echo "  running client processes:"
print_clients
echo "  active 4399 connections:"
print_connections
EOF

section "Syncing Speaker"
log "connecting to $remote over SSH..."
run_ssh "$remote" "$remote_script"

section "Mac-Side Values"
cat <<EOF
  export VOICE_GATEWAY_TTS_HTTP_BASE_URL=$tts_http_base_url
  export VOICE_GATEWAY_SERVER_URL=$server_url
  export VOICE_GATEWAY_SPEAKER_HOST=$speaker_ip

Restart services after syncing:
  ./scripts/run-tts-http-server.sh
  ./scripts/run-voice-gateway-minimal.sh
EOF
