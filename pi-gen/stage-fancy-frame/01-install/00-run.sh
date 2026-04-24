#!/bin/bash -e
# 00-run.sh — host-side stage script (runs on the build host, not inside the chroot).
#
# Responsible for:
#   1. Copying Fancy Frame project files into the image rootfs.
#   2. Tweaking /boot/firmware/cmdline.txt for a clean, splash-free boot.
#
# Variables provided by pi-gen's build.sh:
#   ROOTFS_DIR  — root of the image being assembled
#   STAGE_DIR   — this stage's directory
#   SCRIPT_DIR  — this substage's directory (01-install/)

SCRIPT_DIR_SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FANCY_FRAME_SRC="${SCRIPT_DIR_SELF}/files/fancy-frame"

if [[ -z "${ROOTFS_DIR:-}" ]] || [[ ! -d "${ROOTFS_DIR}" ]]; then
    echo "Error: ROOTFS_DIR is missing or invalid: '${ROOTFS_DIR:-}'" >&2
    exit 1
fi

# on_chroot mounts proc/sys/dev before executing 01-run-chroot.sh. Some builds
# can arrive here without those mount-point directories present.
install -d \
    "${ROOTFS_DIR}/proc" \
    "${ROOTFS_DIR}/sys" \
    "${ROOTFS_DIR}/dev" \
    "${ROOTFS_DIR}/dev/pts"

# ── 1. Install app files ───────────────────────────────────────────────────────
echo "==> Copying Fancy Frame project files into rootfs..."
install -d "${ROOTFS_DIR}/opt/fancy-frame"
cp -a "${FANCY_FRAME_SRC}/." "${ROOTFS_DIR}/opt/fancy-frame/"

target_model="${FANCY_FRAME_TARGET_MODEL:-zero2w}"
case "${target_model}" in
    zero2w|pi4|pi5)
        ;;
    *)
        echo "Warning: unknown FANCY_FRAME_TARGET_MODEL='${target_model}', using 'zero2w'" >&2
        target_model="zero2w"
        ;;
esac

target_profile="pi45"
if [[ "${target_model}" == "zero2w" ]]; then
    target_profile="zero2w"
fi

# Persist the model used during image build for on-device diagnostics.
install -d "${ROOTFS_DIR}/etc"
printf '%s\n' "${target_model}" > "${ROOTFS_DIR}/etc/fancy-frame-target-model"
printf '%s\n' "${target_profile}" > "${ROOTFS_DIR}/etc/fancy-frame-hw-profile"

apply_boot_profile() {
    local profile="$1"
    local config_file=""
    local managed_block=""
    local tmp_file

    for candidate in \
        "${ROOTFS_DIR}/boot/firmware/config.txt" \
        "${ROOTFS_DIR}/boot/config.txt"; do
        if [[ -f "${candidate}" ]]; then
            config_file="${candidate}"
            break
        fi
    done

    if [[ -z "${config_file}" ]]; then
        echo "Warning: boot config.txt not found; skipping hardware profile tuning." >&2
        return 0
    fi

    case "${profile}" in
        zero2w)
            managed_block=$(cat <<'EOF'
# BEGIN FANCY-FRAME HARDWARE PROFILE
# Profile: zero2w
[all]
dtoverlay=vc4-kms-v3d
disable_overscan=1
# Cap output to 1080p60 on all supported models.
hdmi_group=2
hdmi_mode=82
hdmi_enable_4kp60=0
gpu_mem=128
# END FANCY-FRAME HARDWARE PROFILE
EOF
)
            ;;
        pi45)
            managed_block=$(cat <<'EOF'
# BEGIN FANCY-FRAME HARDWARE PROFILE
# Profile: pi45
[all]
dtoverlay=vc4-kms-v3d
disable_overscan=1
# Cap output to 1080p60 on all supported models.
hdmi_group=2
hdmi_mode=82
hdmi_enable_4kp60=0
gpu_mem=192
# END FANCY-FRAME HARDWARE PROFILE
EOF
)
            ;;
        *)
            echo "Warning: unknown profile '${profile}', skipping boot profile tuning." >&2
            return 0
            ;;
    esac

    tmp_file="$(mktemp)"
    awk '
      /# BEGIN FANCY-FRAME HARDWARE PROFILE/ {skip=1; next}
      /# END FANCY-FRAME HARDWARE PROFILE/ {skip=0; next}
      !skip {print}
    ' "${config_file}" > "${tmp_file}"

    printf '\n%s\n' "${managed_block}" >> "${tmp_file}"
    cp "${tmp_file}" "${config_file}"
    rm -f "${tmp_file}"

    echo "==> Applied hardware profile '${profile}' to ${config_file}."
}

apply_boot_profile "${target_profile}"

# Make shell scripts executable (rsync may have lost the bit on some platforms)
find "${ROOTFS_DIR}/opt/fancy-frame/scripts" -name '*.sh' -exec chmod +x {} \;
find "${ROOTFS_DIR}/opt/fancy-frame/scripts" -name '*.py' -exec chmod +x {} \;

# ── 2. Patch cmdline.txt — disable boot splash, reduce console noise ───────────
for cmdline in \
    "${ROOTFS_DIR}/boot/firmware/cmdline.txt" \
    "${ROOTFS_DIR}/boot/cmdline.txt"; do

    [[ -f "${cmdline}" ]] || continue

    echo "==> Patching ${cmdline}..."
    line="$(tr -d '\r\n' < "${cmdline}")"

    # Strip splash wherever it appears
    line="${line// splash / }"
    line="${line# splash}"
    line="${line% splash}"
    line="${line//splash /}"
    line="${line//splash/}"

    # Add quiet / clean-console tokens if not already present
    for token in "logo.nologo" "vt.global_cursor_default=0" "quiet" "loglevel=3"; do
        [[ " ${line} " == *" ${token} "* ]] || line+=" ${token}"
    done

    # Normalise whitespace
    line="$(printf '%s' "${line}" | tr -s ' ' | sed 's/^ //;s/ $//')"
    printf '%s\n' "${line}" > "${cmdline}"
    echo "    → ${cmdline} updated."
    break
done
