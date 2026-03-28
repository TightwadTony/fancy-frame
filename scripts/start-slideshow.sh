#!/usr/bin/env bash
set -euo pipefail

xset s off
xset -dpms
xset s noblank

sleep 2

exec feh \
  --fullscreen \
  --auto-zoom \
  --randomize \
  --slideshow-delay 10 \
  --reload 15 \
  /srv/photos
