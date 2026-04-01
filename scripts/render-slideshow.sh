#!/usr/bin/env bash
set -euo pipefail

PHOTO_DIR="/srv/photos"
OUTPUT_VIDEO="/var/lib/photo-frame/slideshow.mp4"
OUTPUT_TMP="/var/lib/photo-frame/slideshow.tmp.mp4"
SNAPSHOT_FILE="/var/lib/photo-frame/photo-snapshot"
FFMPEG_LOG="/var/lib/photo-frame/ffmpeg-last.log"

SLIDE_DURATION=25         # seconds each image is displayed (including transition)
TRANSITION_DURATION=2     # seconds for crossfade between images
REFRESH_INTERVAL=300      # seconds between change-detection checks
NEW_FILE_AGE_THRESHOLD=30 # skip render if newest photo is younger than this (upload in progress)
MAX_PHOTOS=100            # maximum images included per render cycle
RESOLUTION="1280:720"     # output resolution (width:height)

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] render-slideshow: $*"
}

cleanup() {
  rm -f "${OUTPUT_TMP}"
}
trap cleanup EXIT

get_photo_snapshot() {
  find "${PHOTO_DIR}" -maxdepth 1 -type f \
    \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \
       -o -iname '*.gif' -o -iname '*.bmp' -o -iname '*.webp' \) \
    ! -name '._*' ! -name '.DS_Store' \
    -printf '%f %s\n' 2>/dev/null | sort | md5sum | cut -d' ' -f1
}

get_newest_photo_age_sec() {
  local newest_ts
  newest_ts=$(find "${PHOTO_DIR}" -maxdepth 1 -type f \
    \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \
       -o -iname '*.gif' -o -iname '*.bmp' -o -iname '*.webp' \) \
    ! -name '._*' ! -name '.DS_Store' \
    -printf '%T@\n' 2>/dev/null | sort -n | tail -1)

  if [[ -z "${newest_ts:-}" ]]; then
    echo 99999
    return
  fi

  local now
  now=$(date +%s)
  echo $(( now - ${newest_ts%.*} ))
}

render_video() {
  local photos=()
  mapfile -t photos < <(
    find "${PHOTO_DIR}" -maxdepth 1 -type f \
      \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' \
         -o -iname '*.gif' -o -iname '*.bmp' -o -iname '*.webp' \) \
      ! -name '._*' ! -name '.DS_Store' \
      | shuf | head -n "${MAX_PHOTOS}"
  )

  local count="${#photos[@]}"
  if [[ $count -eq 0 ]]; then
    log "No photos found, skipping render."
    return 1
  fi

  local start_ts
  start_ts=$(date +%s)
  log "Render started: ${count} images, SLIDE=${SLIDE_DURATION}s, TRANS=${TRANSITION_DURATION}s, MAX=${MAX_PHOTOS}, RES=${RESOLUTION}"

  local inputs=()
  for photo in "${photos[@]}"; do
    inputs+=(-loop 1 -t "${SLIDE_DURATION}" -i "${photo}")
  done

  local effective=$(( SLIDE_DURATION - TRANSITION_DURATION ))
  local filter=""

  if [[ $count -eq 1 ]]; then
    filter="[0:v]scale=${RESOLUTION}:force_original_aspect_ratio=decrease,pad=${RESOLUTION}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,format=yuv420p[outv]"
  else
    for (( i=0; i<count; i++ )); do
      filter+="[${i}:v]scale=${RESOLUTION}:force_original_aspect_ratio=decrease,pad=${RESOLUTION}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,format=yuv420p[v${i}];"
    done

    local prev="v0"
    local next
    for (( i=1; i<count; i++ )); do
      local offset=$(( i * effective ))
      if [[ $i -eq $(( count - 1 )) ]]; then
        next="outv"
      else
        next="x${i}"
      fi
      filter+="[${prev}][v${i}]xfade=transition=fade:duration=${TRANSITION_DURATION}:offset=${offset}[${next}];"
      prev="${next}"
    done
  fi

  filter="${filter%;}"

  log "Running ffmpeg (log: ${FFMPEG_LOG})..."
  if ffmpeg -y \
      "${inputs[@]}" \
      -filter_complex "${filter}" \
      -map "[outv]" \
      -c:v libx264 \
      -preset ultrafast \
      -crf 28 \
      -r 24 \
      -pix_fmt yuv420p \
      "${OUTPUT_TMP}" > "${FFMPEG_LOG}" 2>&1; then
    mv "${OUTPUT_TMP}" "${OUTPUT_VIDEO}"
    local elapsed
    elapsed=$(( $(date +%s) - start_ts ))
    log "Render complete: ${count} images in ${elapsed}s ($(( elapsed / 60 ))m$(( elapsed % 60 ))s)"
  else
    local elapsed
    elapsed=$(( $(date +%s) - start_ts ))
    log "Render FAILED after ${elapsed}s. Last ffmpeg output:"
    tail -20 "${FFMPEG_LOG}" | while IFS= read -r line; do log "  ffmpeg: ${line}"; done
    return 1
  fi
}

check_and_render() {
  local current_snapshot
  current_snapshot=$(get_photo_snapshot)

  local saved_snapshot
  saved_snapshot=$(cat "${SNAPSHOT_FILE}" 2>/dev/null || echo "")

  if [[ "${current_snapshot}" == "${saved_snapshot}" ]]; then
    log "No photo changes detected."
    return
  fi

  local age
  age=$(get_newest_photo_age_sec)
  if [[ "${age}" -lt "${NEW_FILE_AGE_THRESHOLD}" ]]; then
    log "Newest photo is ${age}s old (< ${NEW_FILE_AGE_THRESHOLD}s threshold). Assuming upload in progress — will retry next cycle."
    return
  fi

  if render_video; then
    echo "${current_snapshot}" > "${SNAPSHOT_FILE}"
    log "Snapshot updated."
  fi
}

log "Starting up. Performing initial check..."
check_and_render

log "Entering ${REFRESH_INTERVAL}s refresh loop..."
while true; do
  sleep "${REFRESH_INTERVAL}"
  check_and_render
done
