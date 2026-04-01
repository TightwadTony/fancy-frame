#!/usr/bin/env bash
set -euo pipefail

xset s off
xset -dpms
xset s noblank

sleep 2

# Some distros place xscreensaver helper binaries outside the default PATH.
export PATH="/usr/libexec/xscreensaver:/usr/lib/xscreensaver:${PATH}"

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

  # glslideshow uses XScreenSaver resource/env for image directory.
  export XSCREENSAVER_IMAGE_DIRECTORY="/srv/photos"

  # Keep a local resource file as fallback for systems that ignore env var.
  if [[ -n "${HOME:-}" ]]; then
    mkdir -p "${HOME}"
    cat > "${HOME}/.xscreensaver" <<'EOF'
mode: one
selected: 0
imageDirectory: /srv/photos
*imageDirectory: /srv/photos
EOF

    # glslideshow reads settings from the X resource database.
    # Without this merge, it can load "(null)" images and show checkerboards.
    if command -v xrdb >/dev/null 2>&1; then
      xrdb -merge "${HOME}/.xscreensaver" || true
    fi
  fi

  # xscreensaver-getimage-file can return basenames from cache; run from photo dir.
  cd /srv/photos

  exec "${GLSLIDESHOW_BIN}" --root --duration 25 --fade 2 --zoom 100 --pan 20
done