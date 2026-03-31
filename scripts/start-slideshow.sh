#!/usr/bin/env bash
set -euo pipefail

xset s off
xset -dpms
xset s noblank

sleep 2

# Build a shuffled playlist of supported images, excluding macOS metadata files.
PLAYLIST=$(find /srv/photos -maxdepth 1 -type f \
  \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \
     -o -iname '*.gif' -o -iname '*.bmp' -o -iname '*.webp' \) \
  ! -name '._*' ! -name '.DS_Store' \
  | shuf)

if [[ -z "${PLAYLIST}" ]]; then
  echo "No images found in /srv/photos, waiting..."
  sleep 30
  exec "$0" "$@"
fi

exec mpv \
  --vo=gpu \
  --hwdec=auto \
  --image-display-duration=25 \
  --loop-playlist=inf \
  --shuffle \
  --no-osc \
  --no-input-default-bindings \
  --cursor-autohide=always \
  --really-quiet \
  --fs \
  --video-unscaled=downscale-big \
  ${PLAYLIST}