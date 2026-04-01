#!/usr/bin/env bash
set -euo pipefail

xset s off
xset -dpms
xset s noblank

sleep 2

if command -v glslideshow >/dev/null 2>&1; then
  GLSLIDESHOW_BIN="$(command -v glslideshow)"
elif [[ -x "/usr/lib/xscreensaver/glslideshow" ]]; then
  GLSLIDESHOW_BIN="/usr/lib/xscreensaver/glslideshow"
elif [[ -x "/usr/libexec/xscreensaver/glslideshow" ]]; then
  GLSLIDESHOW_BIN="/usr/libexec/xscreensaver/glslideshow"
else
  echo "glslideshow binary not found"
  exit 1
fi

while true; do
  mapfile -t PLAYLIST < <(
    find /srv/photos -maxdepth 1 -type f \
      \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \
         -o -iname '*.gif' -o -iname '*.bmp' -o -iname '*.webp' \) \
      ! -name '._*' ! -name '.DS_Store' \
      | shuf
  )

  if [[ "${#PLAYLIST[@]}" -eq 0 ]]; then
    echo "No images found in /srv/photos, waiting..."
    sleep 30
    continue
  fi

  exec "${GLSLIDESHOW_BIN}" -root "${PLAYLIST[@]}"
done