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
  if ! find /srv/photos -maxdepth 1 -type f \
      \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \
         -o -iname '*.gif' -o -iname '*.bmp' -o -iname '*.webp' \) \
      ! -name '._*' ! -name '.DS_Store' | grep -q .; then
    echo "No images found in /srv/photos, waiting..."
    sleep 30
    continue
  fi

  # glslideshow reads imageDirectory from ~/.xscreensaver rather than CLI paths.
  if [[ -n "${HOME:-}" ]]; then
    mkdir -p "${HOME}"
    cat > "${HOME}/.xscreensaver" <<'EOF'
imageDirectory:    /srv/photos
EOF
  fi

  exec "${GLSLIDESHOW_BIN}" --root --duration 25 --fade 2 --zoom 100 --pan 20
done