#!/usr/bin/env bash
set -euo pipefail

xset s off
xset -dpms
xset s noblank

# Prefer 1920x1080 so the TV's scaler doesn't distort the image.
xrandr --output HDMI-1 --mode 1920x1080 2>/dev/null || true

sleep 3

SLIDE_SECONDS="${PHOTO_FRAME_SLIDE_SECONDS:-25}"
REFRESH_SECONDS="${PHOTO_FRAME_REFRESH_SECONDS:-300}"
PHOTO_SOURCE_DIR="/srv/photos"
PHOTO_RENDER_CACHE_DIR="/var/lib/photo-frame/render-cache"

mkdir -p "${PHOTO_SOURCE_DIR}"
mkdir -p "${PHOTO_RENDER_CACHE_DIR}"

export PHOTO_FRAME_SLIDE_SECONDS="${SLIDE_SECONDS}"
export PHOTO_FRAME_REFRESH_SECONDS="${REFRESH_SECONDS}"
export PHOTO_FRAME_PHOTO_DIR="${PHOTO_SOURCE_DIR}"
export PHOTO_FRAME_RENDER_CACHE_DIR="${PHOTO_RENDER_CACHE_DIR}"

exec python3 /opt/photo-frame/scripts/slideshow.py
