#!/usr/bin/env bash
set -euo pipefail

WAIT_SECONDS=60

# Bookworm moved boot to /boot/firmware/; check both for compatibility
FORCE_FLAG="/boot/firmware/force-onboarding"
[[ -f "${FORCE_FLAG}" ]] || FORCE_FLAG="/boot/force-onboarding"

is_wifi_connected() {
  local ssid
  ssid="$(iwgetid -r 2>/dev/null || true)"
  [[ -n "${ssid}" ]] || return 1
  ip -4 addr show wlan0 | grep -q 'inet ' || return 1
  return 0
}

if [[ -f "${FORCE_FLAG}" ]]; then
  rm -f "${FORCE_FLAG}"
  systemctl start photo-frame-setup-mode.service
  systemctl start photo-frame-setup-portal.service
  exit 0
fi

for _ in $(seq 1 "${WAIT_SECONDS}"); do
  if is_wifi_connected; then
    systemctl stop photo-frame-setup-portal.service >/dev/null 2>&1 || true
    systemctl stop photo-frame-setup-mode.service >/dev/null 2>&1 || true
    exit 0
  fi
  sleep 1
done

systemctl start photo-frame-setup-mode.service
systemctl start photo-frame-setup-portal.service
