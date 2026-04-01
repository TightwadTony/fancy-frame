#!/usr/bin/env bash
set -euo pipefail

xset s off
xset -dpms
xset s noblank

sleep 2

OUTPUT_VIDEO="/var/lib/photo-frame/slideshow.mp4"

while true; do
  if [[ ! -f "${OUTPUT_VIDEO}" ]]; then
    echo "No slideshow video yet — waiting for render..."
    sleep 10
    continue
  fi

  VIDEO_MTIME=$(stat -c %Y "${OUTPUT_VIDEO}")

  mpv \
    --vo=gpu \
    --hwdec=auto \
    --loop-file=inf \
    --no-osc \
    --no-input-default-bindings \
    --cursor-autohide=always \
    --really-quiet \
    --fs \
    "${OUTPUT_VIDEO}" &
  MPV_PID=$!

  # Re-check every 10s; restart mpv when a new render replaces the video file.
  while kill -0 "${MPV_PID}" 2>/dev/null; do
    sleep 10
    CURRENT_MTIME=$(stat -c %Y "${OUTPUT_VIDEO}" 2>/dev/null || echo 0)
    if [[ "${CURRENT_MTIME}" != "${VIDEO_MTIME}" ]]; then
      echo "Slideshow video updated — restarting mpv."
      kill "${MPV_PID}" 2>/dev/null || true
      wait "${MPV_PID}" 2>/dev/null || true
      break
    fi
  done
done