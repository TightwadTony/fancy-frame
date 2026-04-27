#!/usr/bin/env bash
set -euo pipefail

if command -v xset >/dev/null 2>&1; then
	xset s off
	xset -dpms
	xset s noblank
fi

# Prefer 1920x1080 so the TV's scaler doesn't distort the image.
if command -v xrandr >/dev/null 2>&1; then
	xrandr --output HDMI-1 --mode 1920x1080 2>/dev/null || true
fi

sleep 3

SLIDE_SECONDS="${FANCY_FRAME_SLIDE_SECONDS:-25}"
REFRESH_SECONDS="${FANCY_FRAME_REFRESH_SECONDS:-300}"
PHOTO_SOURCE_DIR="${FANCY_FRAME_PHOTO_DIR:-/srv/photos}"
PHOTO_RENDER_CACHE_DIR="${FANCY_FRAME_RENDER_CACHE_DIR:-/var/lib/fancy-frame/render-cache}"

mkdir -p "${PHOTO_SOURCE_DIR}"
mkdir -p "${PHOTO_RENDER_CACHE_DIR}"

export FANCY_FRAME_SLIDE_SECONDS="${SLIDE_SECONDS}"
export FANCY_FRAME_REFRESH_SECONDS="${REFRESH_SECONDS}"
export FANCY_FRAME_PHOTO_DIR="${PHOTO_SOURCE_DIR}"
export FANCY_FRAME_RENDER_CACHE_DIR="${PHOTO_RENDER_CACHE_DIR}"

exec python3 /opt/fancy-frame/scripts/slideshow.py
