#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Detect the host LAN IPv4 automatically.
# macOS: prefer Wi-Fi (en0), then first active hardware port.
detect_ip_macos() {
  local ip=""
  ip="$(ipconfig getifaddr en0 2>/dev/null || true)"
  if [[ -n "$ip" ]]; then
    echo "$ip"
    return 0
  fi

  local dev
  while IFS= read -r dev; do
    [[ -z "$dev" ]] && continue
    ip="$(ipconfig getifaddr "$dev" 2>/dev/null || true)"
    if [[ -n "$ip" ]]; then
      echo "$ip"
      return 0
    fi
  done < <(networksetup -listallhardwareports 2>/dev/null | awk '/Device:/{print $2}')

  return 1
}

# Linux: ask kernel which source IP would be used for internet route.
detect_ip_linux() {
  ip route get 1.1.1.1 2>/dev/null | awk '/src/{for(i=1;i<=NF;i++) if ($i=="src") {print $(i+1); exit}}'
}

LAN_IP=""
MDNS_INSTANCE="${FANCY_FRAME_MDNS_INSTANCE:-Fancy Frame}"
MDNS_HOST="${FANCY_FRAME_MDNS_HOST:-fancy-frame-teststub}"
MDNS_PIDS=()

start_host_mdns_advertisement_macos() {
  # Register both service types the iOS app browses.
  dns-sd -R "$MDNS_INSTANCE" _fancyframe._tcp local 8080 >/tmp/fancy-frame-mdns-fancyframe.log 2>&1 &
  MDNS_PIDS+=("$!")
  dns-sd -R "$MDNS_INSTANCE" _photoframe._tcp local 8080 >/tmp/fancy-frame-mdns-photoframe.log 2>&1 &
  MDNS_PIDS+=("$!")

  echo "Registered host Bonjour services for $MDNS_INSTANCE on port 8080"
}

cleanup_mdns() {
  local pid
  for pid in "${MDNS_PIDS[@]:-}"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" >/dev/null 2>&1 || true
    fi
  done
}

case "$(uname -s)" in
  Darwin)
    LAN_IP="$(detect_ip_macos || true)"
    ;;
  Linux)
    LAN_IP="$(detect_ip_linux || true)"
    ;;
  *)
    ;;
esac

if [[ -z "$LAN_IP" ]]; then
  echo "Could not auto-detect LAN IPv4 address." >&2
  echo "Set FANCY_FRAME_ADVERTISED_IP manually and run docker compose up --build." >&2
  exit 1
fi

echo "Using LAN IP: $LAN_IP"
cd "$ROOT_DIR"

EXTRA_ENV=()
if [[ "$(uname -s)" == "Darwin" ]]; then
  start_host_mdns_advertisement_macos
  trap cleanup_mdns EXIT INT TERM
  EXTRA_ENV+=("FANCY_FRAME_DISABLE_MDNS=1")
fi

env \
  FANCY_FRAME_ADVERTISED_IP="$LAN_IP" \
  FANCY_FRAME_MDNS_INSTANCE="$MDNS_INSTANCE" \
  FANCY_FRAME_MDNS_HOST="$MDNS_HOST" \
  "${EXTRA_ENV[@]}" \
  docker compose up --build "$@"
