#!/usr/bin/env bash
set -euo pipefail

xset s off
xset -dpms
xset s noblank

sleep 2

SLIDE_SECONDS="${PHOTO_FRAME_SLIDE_SECONDS:-25}"
REFRESH_SECONDS="${PHOTO_FRAME_REFRESH_SECONDS:-300}"

if ! command -v kodi >/dev/null 2>&1; then
  echo "kodi binary not found"
  exit 1
fi

mkdir -p /root/.kodi/userdata

cat > /root/.kodi/userdata/advancedsettings.xml <<EOF
<advancedsettings>
  <slideshow>
    <staytime>${SLIDE_SECONDS}</staytime>
    <displayeffects>true</displayeffects>
  </slideshow>
</advancedsettings>
EOF

# Auto-start a recursive randomized slideshow on Kodi launch.
cat > /root/.kodi/userdata/autoexec.py <<'EOF'
import os
import xbmc

PHOTO_DIR = '/srv/photos'

if not os.path.isdir(PHOTO_DIR):
  xbmc.log(f'photo-frame: {PHOTO_DIR} missing; slideshow not started', xbmc.LOGERROR)
else:
  xbmc.log('photo-frame: slideshow start attempt 1', xbmc.LOGINFO)
  xbmc.executebuiltin(f'ActivateWindow(Pictures,{PHOTO_DIR},return)')
  xbmc.sleep(700)
  xbmc.executebuiltin(f'SlideShow({PHOTO_DIR},recursive,random)')
EOF

export PHOTO_FRAME_SLIDE_SECONDS="${SLIDE_SECONDS}"

kodi --standalone --windowing=x11 &
KODI_PID=$!

# On first boot, Kodi shows a "Spectrum" visualization addon prompt that blocks
# the slideshow. Wait for Kodi to create its addons DB, then disable the addon
# so it doesn't prompt on subsequent starts either.
(
  for _ in $(seq 1 60); do
    db="$(ls /root/.kodi/userdata/Database/Addons*.db 2>/dev/null | head -1)"
    if [[ -f "${db}" ]]; then
      sqlite3 "${db}" \
        "UPDATE installed SET enabled=0 WHERE addonID='visualization.spectrum'" \
        2>/dev/null || true
      exit 0
    fi
    sleep 1
  done
) &

# Additional safety net: push slideshow actions via kodi-send during startup,
# then refresh every N seconds so newly added photos are picked up.
if command -v kodi-send >/dev/null 2>&1; then
  (
    for i in $(seq 1 20); do
      if ! kill -0 "${KODI_PID}" 2>/dev/null; then
        exit 0
      fi
      kodi-send --action="SetGUISetting(slideshow.staytime,${SLIDE_SECONDS})" >/dev/null 2>&1 || true

      # Start slideshow once during startup to avoid visual resets.
      if [[ "${i}" -eq 1 ]]; then
        # Dismiss any first-run dialogs (e.g. addon prompts) before navigating.
        kodi-send --action="Back" >/dev/null 2>&1 || true
        sleep 1
        kodi-send --action="ActivateWindow(Pictures,/srv/photos,return)" >/dev/null 2>&1 || true
        sleep 1
        kodi-send --action="SlideShow(/srv/photos,recursive,random)" >/dev/null 2>&1 || true
      fi

      sleep 2
    done

    while kill -0 "${KODI_PID}" 2>/dev/null; do
      sleep "${REFRESH_SECONDS}"
      if ! kill -0 "${KODI_PID}" 2>/dev/null; then
        exit 0
      fi
      kodi-send --action="SetGUISetting(slideshow.staytime,${SLIDE_SECONDS})" >/dev/null 2>&1 || true
      kodi-send --action="ActivateWindow(Pictures,/srv/photos,return)" >/dev/null 2>&1 || true
      sleep 1
      kodi-send --action="SlideShow(/srv/photos,recursive,random)" >/dev/null 2>&1 || true
    done
  ) &
  REFRESH_PID=$!
fi

wait "${KODI_PID}"

if [[ -n "${REFRESH_PID:-}" ]]; then
  kill "${REFRESH_PID}" >/dev/null 2>&1 || true
fi