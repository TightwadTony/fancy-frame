#!/usr/bin/env bash
set -euo pipefail

section() {
  echo
  echo "============================================================"
  echo "$1"
  echo "============================================================"
}

section "Recent boots / shutdowns"
last -x | head -n 30 || true

section "Current uptime"
uptime || true
cat /proc/uptime || true

section "Fancy Frame slideshow logs (current boot)"
journalctl -u fancy-frame.service -b --no-pager -n 200 || true

section "Fancy Frame API logs (current boot)"
journalctl -u fancy-frame-api.service -b --no-pager -n 120 || true

section "Wi-Fi bootstrap logs (current boot)"
journalctl -u fancy-frame-wifi-bootstrap.service -b --no-pager -n 120 || true

section "Previous boot errors"
journalctl -b -1 -p warning..alert --no-pager -n 200 || true

section "Kernel warnings that often explain random restarts"
dmesg -T 2>/dev/null | egrep -i 'under.?voltage|voltage|oom|out of memory|killed process|panic|watchdog|thermal|reset' || true

section "GPU / display related errors"
journalctl -b --no-pager | egrep -i 'xorg|pygame|sdl|drm|hdmi|vc4' | tail -n 200 || true

section "Hint"
echo "If you see undervoltage, the likely cause is power supply or cabling."
echo "If you see only fancy-frame.service restarts with no new boot in 'last -x', the Pi did not reboot; the slideshow process crashed and systemd restarted it."
