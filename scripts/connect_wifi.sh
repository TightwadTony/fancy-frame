#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "connect_wifi.sh must run as root"
  exit 1
fi

SSID="${1:-}"
PSK="${2:-}"
COUNTRY="${3:-US}"

if [[ -z "${SSID}" || -z "${PSK}" ]]; then
  echo "Usage: connect_wifi.sh <ssid> <password> [country]"
  exit 2
fi

escape_wpa() {
  local value="${1}"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '%s' "${value}"
}

SSID_ESCAPED="$(escape_wpa "${SSID}")"
PSK_ESCAPED="$(escape_wpa "${PSK}")"
COUNTRY_ESCAPED="$(printf '%s' "${COUNTRY}" | tr -cd '[:alpha:]' | tr '[:lower:]' '[:upper:]')"
if [[ -z "${COUNTRY_ESCAPED}" ]]; then
  COUNTRY_ESCAPED="US"
fi

mkdir -p /var/lib/photo-frame
cp /etc/wpa_supplicant/wpa_supplicant.conf "/var/lib/photo-frame/wpa_supplicant.conf.bak.$(date +%s)" >/dev/null 2>&1 || true

cat > /etc/wpa_supplicant/wpa_supplicant.conf <<EOF
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=${COUNTRY_ESCAPED}

network={
    ssid="${SSID_ESCAPED}"
    psk="${PSK_ESCAPED}"
    key_mgmt=WPA-PSK
}
EOF
chmod 600 /etc/wpa_supplicant/wpa_supplicant.conf

/opt/photo-frame/scripts/stop_setup_mode.sh

wpa_cli -i wlan0 reconfigure >/dev/null 2>&1 || true
systemctl restart dhcpcd.service >/dev/null 2>&1 || true

for _ in $(seq 1 40); do
  CURRENT_SSID="$(iwgetid -r 2>/dev/null || true)"
  if [[ "${CURRENT_SSID}" == "${SSID}" ]] && ip -4 addr show wlan0 | grep -q 'inet '; then
    touch /var/lib/photo-frame/wifi-configured
    rm -f /var/lib/photo-frame/force-onboarding-active || true
    rm -f /boot/firmware/force-onboarding /boot/force-onboarding || true
    exit 0
  fi
  sleep 1
done

exit 1
