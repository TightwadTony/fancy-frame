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
PHOTO_PLAY_DIR="/var/lib/photo-frame/playable-photos"

rebuild_playable_photos() {
  local source_dir="${PHOTO_SOURCE_DIR}"
  local play_dir="${PHOTO_PLAY_DIR}"
  local -i counter=0

  mkdir -p "${play_dir}"
  find "${play_dir}" -mindepth 1 -delete || true

  while IFS= read -r -d '' file; do
    local base ext link_name
    base="$(basename "${file}")"

    # Ignore macOS metadata files and hidden desktop artifacts.
    if [[ "${base}" == ._* || "${base}" == ".DS_Store" ]]; then
      continue
    fi

    ext="${base##*.}"
    ext="${ext,,}"
    case "${ext}" in
      jpg|jpeg|png|gif|bmp|webp|tif|tiff)
        ;;
      *)
        continue
        ;;
    esac

    # If ImageMagick is present, skip corrupt/unreadable images too.
    if command -v identify >/dev/null 2>&1; then
      if ! identify "${file}" >/dev/null 2>&1; then
        continue
      fi
    fi

    counter+=1
    printf -v link_name "%08d-%s" "${counter}" "${base}"
    ln -sf "${file}" "${play_dir}/${link_name}"
  done < <(find "${source_dir}" -type f -print0)
}

rebuild_playable_photos

export PHOTO_FRAME_SLIDE_SECONDS="${SLIDE_SECONDS}"
export PHOTO_FRAME_REFRESH_SECONDS="${REFRESH_SECONDS}"

exec python3 /opt/photo-frame/scripts/slideshow.py
