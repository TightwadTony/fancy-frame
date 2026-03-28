#!/usr/bin/env bash
set -euo pipefail

rfkill unblock wifi || true

systemctl stop wpa_supplicant.service >/dev/null 2>&1 || true
systemctl stop wpa_supplicant@wlan0.service >/dev/null 2>&1 || true

# Some Raspberry Pi images ship hostapd masked by default.
systemctl unmask hostapd.service >/dev/null 2>&1 || true
systemctl unmask dnsmasq.service >/dev/null 2>&1 || true

ip link set wlan0 down || true
ip addr flush dev wlan0 || true
ip link set wlan0 up
ip addr add 192.168.4.1/24 dev wlan0

systemctl restart dnsmasq
systemctl restart hostapd
