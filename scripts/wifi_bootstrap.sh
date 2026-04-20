#!/usr/bin/env bash
set -euo pipefail

WAIT_SECONDS=60

# Bookworm moved boot to /boot/firmware/; check both for compatibility
FORCE_FLAG="/boot/firmware/force-onboarding"
[[ -f "${FORCE_FLAG}" ]] || FORCE_FLAG="/boot/force-onboarding"

is_wifi_connected() {
  local ssid status wpa_state
  ssid="$(iwgetid -r 2>/dev/null || true)"

  # Consider Wi-Fi connected once association/auth is complete, even if DHCP is
  # still negotiating an IPv4 address.
  if [[ -n "${ssid}" ]]; then
    return 0
  fi

  status="$(wpa_cli -i wlan0 status 2>/dev/null || true)"
  wpa_state="$(printf '%s\n' "${status}" | awk -F= '/^wpa_state=/{print $2; exit}')"
  [[ "${wpa_state}" == "COMPLETED" ]]
}

# Manual override: touch /boot/firmware/force-onboarding then reboot.
if [[ -f "${FORCE_FLAG}" ]]; then
  rm -f "${FORCE_FLAG}"
  systemctl start fancy-frame-setup-mode.service
  systemctl start fancy-frame-setup-portal.service
  exit 0
fi

# Only fall back to AP mode if WiFi is not connected within 60 seconds of boot.
# Never enter AP mode after that — a temporary outage should not trigger onboarding.
for _ in $(seq 1 "${WAIT_SECONDS}"); do
  if is_wifi_connected; then
    exit 0
  fi
  sleep 1
done

systemctl start fancy-frame-setup-mode.service
systemctl start fancy-frame-setup-portal.service
