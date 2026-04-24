#!/usr/bin/env bash
# build-image.sh — Build a Fancy Frame Raspberry Pi OS image on macOS via Docker.
#
# Usage:
#   bash pi-gen/build-image.sh [--no-update]
#
# Prerequisites:
#   - Docker Desktop running
#   - Internet access (clones pi-gen; apt installs during image build)
#
# Output:
#   pi-gen/.build/pi-gen/deploy/YYYY-MM-DD-fancy-frame*.img.xz
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

WORK_DIR="${SCRIPT_DIR}/.build"
PIEGEN_CLONE="${WORK_DIR}/pi-gen"
STAGE_SRC="${SCRIPT_DIR}/stage-fancy-frame"
CONFIG_SRC="${SCRIPT_DIR}/config"

NO_UPDATE="${1:-}"

# Load user config so this wrapper can honor PI_GEN_BRANCH and model selection.
# shellcheck disable=SC1090
source "${CONFIG_SRC}"

PI_GEN_BRANCH="${PI_GEN_BRANCH:-arm64}"
TARGET_MODEL="${FANCY_FRAME_TARGET_MODEL:-zero2w}"

case "${TARGET_MODEL}" in
    zero2w|pi4|pi5)
        ;;
    *)
        echo "Error: FANCY_FRAME_TARGET_MODEL must be one of: zero2w, pi4, pi5" >&2
        echo "Current value: ${TARGET_MODEL}" >&2
        exit 1
        ;;
esac

BASE_IMG_NAME="${IMG_NAME:-fancy-frame}"
FINAL_IMG_NAME="${BASE_IMG_NAME}-${TARGET_MODEL}-arm64"
CONTAINER_NAME="pigen_work_${TARGET_MODEL}"

ensure_branch_checked_out() {
    local repo_dir="$1"
    local branch="$2"

    # FETCH_HEAD is guaranteed to exist after this fetch even in single-branch clones.
    git -C "${repo_dir}" fetch origin "${branch}" --depth 1
    git -C "${repo_dir}" checkout -B "${branch}" FETCH_HEAD

    # Best effort: wire upstream when origin/<branch> exists.
    git -C "${repo_dir}" branch --set-upstream-to="origin/${branch}" "${branch}" >/dev/null 2>&1 || true
}

# ── Prerequisites ──────────────────────────────────────────────────────────────
echo "==> Checking prerequisites..."
if ! command -v docker >/dev/null 2>&1; then
    echo "Error: Docker is not installed or not in PATH." >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "Error: Docker daemon is not running. Start Docker Desktop and try again." >&2
    exit 1
fi

# ── Clone / update pi-gen ─────────────────────────────────────────────────────
mkdir -p "${WORK_DIR}"

if [[ ! -d "${PIEGEN_CLONE}" ]]; then
    echo "==> Cloning pi-gen (${PI_GEN_BRANCH} branch)..."
    git clone --depth 1 --branch "${PI_GEN_BRANCH}" https://github.com/RPi-Distro/pi-gen.git "${PIEGEN_CLONE}"
elif [[ "${NO_UPDATE}" != "--no-update" ]]; then
    current_branch="$(git -C "${PIEGEN_CLONE}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
    if [[ "${current_branch}" != "${PI_GEN_BRANCH}" ]]; then
        echo "==> Switching pi-gen branch: ${current_branch} -> ${PI_GEN_BRANCH}"
        ensure_branch_checked_out "${PIEGEN_CLONE}" "${PI_GEN_BRANCH}"
    fi

    echo "==> Updating pi-gen (${PI_GEN_BRANCH})..."
    git -C "${PIEGEN_CLONE}" fetch origin "${PI_GEN_BRANCH}" --depth 1 || {
        echo "  (fetch failed — likely transient network issue; continuing with existing clone)"
    }
    git -C "${PIEGEN_CLONE}" merge --ff-only FETCH_HEAD || {
        echo "  (fast-forward merge failed — likely dirty work dir; continuing with existing clone)"
    }
else
    current_branch="$(git -C "${PIEGEN_CLONE}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
    if [[ "${current_branch}" != "${PI_GEN_BRANCH}" ]]; then
        echo "==> Switching pi-gen branch: ${current_branch} -> ${PI_GEN_BRANCH} (--no-update skips pull)"
        ensure_branch_checked_out "${PIEGEN_CLONE}" "${PI_GEN_BRANCH}"
    fi
    echo "==> Using existing pi-gen clone (--no-update)."
fi

# ── Inject our stage and config ───────────────────────────────────────────────
echo "==> Copying stage-fancy-frame into pi-gen work dir..."
rm -rf "${PIEGEN_CLONE}/stage-fancy-frame"
cp -r "${STAGE_SRC}" "${PIEGEN_CLONE}/stage-fancy-frame"

echo "==> Copying config..."
cp "${CONFIG_SRC}" "${PIEGEN_CLONE}/config"

# Ensure target model and final image name are explicit in the copied config.
{
    echo ""
    echo "# Appended by build-image.sh"
    echo "export FANCY_FRAME_TARGET_MODEL=${TARGET_MODEL}"
    echo "IMG_NAME=\"${FINAL_IMG_NAME}\""
    echo "CONTAINER_NAME=${CONTAINER_NAME}"
} >> "${PIEGEN_CLONE}/config"

# ── Sync project files into the stage's files/ directory ─────────────────────
echo "==> Syncing project files into stage..."
STAGE_FILES_DIR="${PIEGEN_CLONE}/stage-fancy-frame/01-install/files"
mkdir -p "${STAGE_FILES_DIR}/fancy-frame"

rsync -a \
    --delete \
    --exclude='.git/' \
    --exclude='.venv/' \
    --exclude='pi-gen/' \
    --exclude='ios/' \
    --exclude='*.pyc' \
    --exclude='__pycache__/' \
    --exclude='*.img' \
    --exclude='*.img.xz' \
    --exclude='.DS_Store' \
    "${REPO_ROOT}/" \
    "${STAGE_FILES_DIR}/fancy-frame/"

# Remove the placeholder so the real files aren't confused with it
rm -f "${STAGE_FILES_DIR}/fancy-frame/pi-gen/stage-fancy-frame/01-install/files/.gitkeep"

# ── Skip intermediate image exports (only export the final stage) ─────────────
echo "==> Suppressing intermediate image exports..."
touch "${PIEGEN_CLONE}/stage0/SKIP_IMAGES"
touch "${PIEGEN_CLONE}/stage1/SKIP_IMAGES"
touch "${PIEGEN_CLONE}/stage2/SKIP_IMAGES"

# ── Build ─────────────────────────────────────────────────────────────────────
echo ""
echo "==> Starting pi-gen Docker build."
echo "    Branch: ${PI_GEN_BRANCH}"
echo "    Target model: ${TARGET_MODEL}"
echo "    Image name: ${FINAL_IMG_NAME}"
echo "    Build container: ${CONTAINER_NAME}"
echo "    This typically takes 20–40 minutes on first run."
echo "    Subsequent builds are faster thanks to Docker layer caching."
echo ""

existing_container_id="$(docker ps -aq --filter "name=^/${CONTAINER_NAME}$")"
running_container_id="$(docker ps -q --filter "name=^/${CONTAINER_NAME}$")"
if [[ -n "${running_container_id}" ]]; then
    echo "Error: build container '${CONTAINER_NAME}' is already running." >&2
    echo "Stop it first: docker stop ${CONTAINER_NAME}" >&2
    exit 1
fi
if [[ -n "${existing_container_id}" ]]; then
    echo "==> Removing stale stopped container: ${CONTAINER_NAME}"
    docker rm -v "${CONTAINER_NAME}" >/dev/null
fi

cd "${PIEGEN_CLONE}"
bash build-docker.sh

# ── Report output ─────────────────────────────────────────────────────────────
DEPLOY_DIR="${PIEGEN_CLONE}/deploy"
echo ""
echo "==> Build complete!"
if [[ -d "${DEPLOY_DIR}" ]]; then
    echo "    Output image(s):"
    ls -lh "${DEPLOY_DIR}"/*.xz 2>/dev/null || ls -lh "${DEPLOY_DIR}"
    echo ""
    echo "    To flash with Raspberry Pi Imager: open the .img.xz directly."
    echo "    To flash with dd:"
    echo "      xz -d <image.img.xz>"
    echo "      sudo dd if=<image.img> of=/dev/diskN bs=4m status=progress"
else
    echo "    Deploy directory not found. Check ${PIEGEN_CLONE} for output."
fi
