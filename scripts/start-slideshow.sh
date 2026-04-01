#!/usr/bin/env bash
set -euo pipefail

xset s off
xset -dpms
xset s noblank

sleep 2

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

def start_slideshow():
  if not os.path.isdir(PHOTO_DIR):
    xbmc.log(f'photo-frame: {PHOTO_DIR} missing; slideshow not started', xbmc.LOGERROR)
    return
  xbmc.executebuiltin(f'SlideShow({PHOTO_DIR},recursive,random)')

# Kodi can ignore slideshow commands very early in startup.
# Retry for a short window so we don't get stuck on the empty library screen.
for _ in range(12):
  start_slideshow()
  time.sleep(1)
EOF

exec kodi --standalone --windowing=x11