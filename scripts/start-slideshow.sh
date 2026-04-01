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
import xbmc

xbmc.executebuiltin('SlideShow(/srv/photos,recursive,random)')
EOF

exec kodi --standalone --windowing=x11