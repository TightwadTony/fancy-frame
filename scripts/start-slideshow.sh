#!/usr/bin/env bash
set -euo pipefail

xset s off
xset -dpms
xset s noblank

sleep 2

exec feh \
  --fullscreen \
  --auto-rotate \
  --auto-zoom \
  --randomize \
  --slideshow-delay 25 \
  --quiet \
  --hide-pointer \
  --reload 15 \
  --exclude "^\._|^\.DS_Store" \
  /srv/photos