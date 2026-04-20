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

mkdir -p /var/lib/fancy-frame
cp /etc/wpa_supplicant/wpa_supplicant-wlan0.conf "/var/lib/fancy-frame/wpa_supplicant.conf.bak.$(date +%s)" >/dev/null 2>&1 || true

WPA_CONFIG_CONTENT="ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1
country=${COUNTRY_ESCAPED}

network={
    ssid=\"${SSID_ESCAPED}\"
    psk=\"${PSK_ESCAPED}\"
    key_mgmt=WPA-PSK
}
"

printf '%s\n' "${WPA_CONFIG_CONTENT}" > /etc/wpa_supplicant/wpa_supplicant-wlan0.conf
chmod 600 /etc/wpa_supplicant/wpa_supplicant-wlan0.conf

# Keep both config paths in sync. Some images bring up wlan0 via the generic
# wpa_supplicant.service, others via wpa_supplicant@wlan0.service.
printf '%s\n' "${WPA_CONFIG_CONTENT}" > /etc/wpa_supplicant/wpa_supplicant.conf
chmod 600 /etc/wpa_supplicant/wpa_supplicant.conf

/opt/fancy-frame/scripts/stop_setup_mode.sh

# Use the wlan0 instance explicitly so we apply the intended config file.
systemctl disable wpa_supplicant.service >/dev/null 2>&1 || true
systemctl stop wpa_supplicant.service >/dev/null 2>&1 || true
systemctl unmask wpa_supplicant@wlan0.service >/dev/null 2>&1 || true
systemctl enable wpa_supplicant@wlan0.service >/dev/null 2>&1 || true
systemctl restart wpa_supplicant@wlan0.service >/dev/null 2>&1 || true

wpa_cli -i wlan0 reconfigure >/dev/null 2>&1 || true
systemctl restart dhcpcd.service >/dev/null 2>&1 || true

ASSOCIATED_WITH_TARGET=0

for i in $(seq 1 90); do
  STATUS="$(wpa_cli -i wlan0 status 2>/dev/null || true)"
  CURRENT_SSID="$(printf '%s\n' "${STATUS}" | awk -F= '/^ssid=/{print $2; exit}')"
  WPA_STATE="$(printf '%s\n' "${STATUS}" | awk -F= '/^wpa_state=/{print $2; exit}')"

  if [[ "${CURRENT_SSID}" == "${SSID}" ]] && [[ "${WPA_STATE}" == "COMPLETED" ]]; then
    ASSOCIATED_WITH_TARGET=1
    if ip -4 addr show wlan0 | grep -q 'inet '; then
      touch /var/lib/fancy-frame/wifi-configured
      rm -f /boot/firmware/force-onboarding /boot/force-onboarding || true
      exit 0
    fi

    # If authentication completed but DHCP is slow, periodically nudge dhcpcd.
    if (( i % 10 == 0 )); then
      systemctl restart dhcpcd.service >/dev/null 2>&1 || true
    fi
  fi

  sleep 1
done

# If association succeeded but DHCP was delayed, treat onboarding as successful.
if [[ "${ASSOCIATED_WITH_TARGET}" -eq 1 ]]; then
  touch /var/lib/fancy-frame/wifi-configured
  rm -f /boot/firmware/force-onboarding /boot/force-onboarding || true
  exit 0
fi

# Do not force AP mode here. AP fallback is handled only by wifi_bootstrap.sh
# during boot if there is no Wi-Fi after the configured timeout.
exit 1
