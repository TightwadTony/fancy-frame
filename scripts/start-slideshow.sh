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

# Auto-start a recursive randomized slideshow on Kodi launch.
cat > /root/.kodi/userdata/autoexec.py <<'EOF'
import os
import time
import xbmc

PHOTO_DIR = '/srv/photos'

def start_slideshow(attempt):
  if not os.path.isdir(PHOTO_DIR):
    xbmc.log(f'photo-frame: {PHOTO_DIR} missing; slideshow not started', xbmc.LOGERROR)
    return
  xbmc.log(f'photo-frame: slideshow start attempt {attempt}', xbmc.LOGINFO)
  xbmc.executebuiltin(f'ActivateWindow(Pictures,{PHOTO_DIR},return)')
  xbmc.sleep(700)
  xbmc.executebuiltin(f'SlideShow({PHOTO_DIR},recursive,random)')

# Kodi can ignore slideshow commands very early in startup.
# Retry for a short window so we don't get stuck on the empty library screen.
for i in range(1, 31):
  start_slideshow(i)
  time.sleep(1)
EOF

export PHOTO_FRAME_SLIDE_SECONDS="${SLIDE_SECONDS}"

kodi --standalone --windowing=x11 &
KODI_PID=$!

# Additional safety net: push slideshow actions via kodi-send during startup,
# then refresh every N seconds so newly added photos are picked up.
if command -v kodi-send >/dev/null 2>&1; then
  (
    for _ in $(seq 1 20); do
      if ! kill -0 "${KODI_PID}" 2>/dev/null; then
        exit 0
      fi
      kodi-send --action="SetGUISetting(slideshow.staytime,${SLIDE_SECONDS})" >/dev/null 2>&1 || true
      kodi-send --action="ActivateWindow(Pictures,/srv/photos,return)" >/dev/null 2>&1 || true
      sleep 1
      kodi-send --action="SlideShow(/srv/photos,recursive,random)" >/dev/null 2>&1 || true
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