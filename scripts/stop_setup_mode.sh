#!/usr/bin/env bash
set -euo pipefail

rm -f /run/fancy-frame-setup-active

systemctl stop hostapd >/dev/null 2>&1 || true
systemctl stop dnsmasq >/dev/null 2>&1 || true

ip addr flush dev wlan0 || true

systemctl unmask wpa_supplicant@wlan0.service >/dev/null 2>&1 || true
systemctl stop wpa_supplicant.service >/dev/null 2>&1 || true
systemctl restart wpa_supplicant@wlan0.service >/dev/null 2>&1 || true
systemctl restart dhcpcd.service >/dev/null 2>&1 || true
