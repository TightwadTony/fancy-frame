#!/usr/bin/env bash
set -euo pipefail

xset s off
xset -dpms
xset s noblank

sleep 2
sleep 5

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

if ! command -v kodi >/dev/null 2>&1; then
  echo "kodi binary not found"
  exit 1
fi

rebuild_playable_photos

mkdir -p /root/.kodi/userdata

cat > /root/.kodi/userdata/advancedsettings.xml <<EOF
<advancedsettings>
  <slideshow>
    <staytime>${SLIDE_SECONDS}</staytime>
    <displayeffects>true</displayeffects>
  </slideshow>
</advancedsettings>
EOF

# Auto-start a recursive randomized slideshow on Kodi launch.
cat > /root/.kodi/userdata/autoexec.py <<'EOF'
import os
import xbmc

PHOTO_DIR = '/var/lib/photo-frame/playable-photos'

if not os.path.isdir(PHOTO_DIR):
  xbmc.log(f'photo-frame: {PHOTO_DIR} missing; slideshow not started', xbmc.LOGERROR)
else:
  xbmc.sleep(2000)
  xbmc.log('photo-frame: slideshow start attempt 1', xbmc.LOGINFO)
  xbmc.executebuiltin(f'ActivateWindow(Pictures,{PHOTO_DIR},return)')
  xbmc.sleep(700)
  xbmc.executebuiltin(f'SlideShow({PHOTO_DIR},recursive,random)')
EOF

export PHOTO_FRAME_SLIDE_SECONDS="${SLIDE_SECONDS}"

kodi --standalone --windowing=x11 &
KODI_PID=$!

# On first boot, Kodi shows a "Spectrum" visualization addon prompt that blocks
# the slideshow. Wait for Kodi to create its addons DB, then disable the addon
# so it doesn't prompt on subsequent starts either.
(
  for _ in $(seq 1 60); do
    db="$(ls /root/.kodi/userdata/Database/Addons*.db 2>/dev/null | head -1)"
    if [[ -f "${db}" ]]; then
      sqlite3 "${db}" \
        "UPDATE installed SET enabled=0 WHERE addonID='visualization.spectrum'" \
        2>/dev/null || true
      exit 0
    fi
    sleep 1
  done
) &

# Additional safety net: push slideshow actions via kodi-send during startup,
# then refresh every N seconds so newly added photos are picked up.
if command -v kodi-send >/dev/null 2>&1; then
  (
    for i in $(seq 1 20); do
      if ! kill -0 "${KODI_PID}" 2>/dev/null; then
        exit 0
      fi
      kodi-send --action="SetGUISetting(slideshow.staytime,${SLIDE_SECONDS})" >/dev/null 2>&1 || true

      # Start slideshow at launch and one delayed retry to avoid startup misses.
      if [[ "${i}" -eq 1 || "${i}" -eq 6 ]]; then
        # Dismiss any first-run dialogs (e.g. addon prompts) before navigating.
        kodi-send --action="Back" >/dev/null 2>&1 || true
        sleep 1
        kodi-send --action="ActivateWindow(Pictures,${PHOTO_PLAY_DIR},return)" >/dev/null 2>&1 || true
        sleep 1
        kodi-send --action="SlideShow(${PHOTO_PLAY_DIR},recursive,random)" >/dev/null 2>&1 || true
      fi

      sleep 2
    done

    while kill -0 "${KODI_PID}" 2>/dev/null; do
      sleep "${REFRESH_SECONDS}"
      if ! kill -0 "${KODI_PID}" 2>/dev/null; then
        exit 0
      fi

      rebuild_playable_photos

      kodi-send --action="SetGUISetting(slideshow.staytime,${SLIDE_SECONDS})" >/dev/null 2>&1 || true
      kodi-send --action="ActivateWindow(Pictures,${PHOTO_PLAY_DIR},return)" >/dev/null 2>&1 || true
      sleep 1
      kodi-send --action="SlideShow(${PHOTO_PLAY_DIR},recursive,random)" >/dev/null 2>&1 || true
    done
  ) &
  REFRESH_PID=$!
fi

wait "${KODI_PID}"

if [[ -n "${REFRESH_PID:-}" ]]; then
  kill "${REFRESH_PID}" >/dev/null 2>&1 || true
fi