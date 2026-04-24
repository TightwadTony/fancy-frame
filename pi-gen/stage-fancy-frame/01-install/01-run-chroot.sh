#!/bin/bash -e
# 01-run-chroot.sh — runs *inside* the image chroot (the Pi's future filesystem).
#
# Delegates entirely to the existing install_initial_setup.sh so there is no
# logic duplication.  A thin systemctl wrapper is exported so that service
# start/stop/restart/daemon-reload calls (which require a live systemd D-Bus)
# are silently skipped; enable/disable/mask/unmask/set-default still reach the
# real systemctl binary and work correctly via symlink manipulation.

# ── systemctl shim ─────────────────────────────────────────────────────────────
# Operations that communicate with a running systemd daemon will always fail
# inside a pi-gen chroot because no systemd process is present.  We intercept
# them here so set -e in the installer doesn't abort the build.
#
# Operations that only manipulate files (enable, disable, mask, unmask,
# set-default, list-unit-files, is-enabled) are passed through unchanged.
systemctl() {
    case "${1:-}" in
        start|stop|restart|reload|try-restart|condrestart|force-reload|\
        daemon-reload|status|is-active|is-failed|list-units)
            echo "[pi-gen chroot] Skipping: systemctl $*" >&2
            return 0
            ;;
        *)
            /bin/systemctl "$@"
            ;;
    esac
}
export -f systemctl

# ── Run the installer non-interactively ───────────────────────────────────────
# Environment variables consumed by install_initial_setup.sh:
#
#   FANCY_FRAME_USER   — non-root user to own files (must already exist)
#   SMB_ACCESS_MODE    — 'anonymous' or 'credentials'
#   FANCY_FRAME_NAME   — display name written into fancy-frame.conf
#   FANCY_FRAME_UPGRADE — 'yes' to run apt upgrade before installing packages
#
# The 'photo' user is created by pi-gen via FIRST_USER_NAME=photo in config.
# SMB anonymous mode is used so no Samba password needs to be set in the image;
# a password can be added with `sudo smbpasswd -a photo` after first boot.

FANCY_FRAME_USER=photo \
SMB_ACCESS_MODE=anonymous \
FANCY_FRAME_NAME="Fancy Frame" \
FANCY_FRAME_UPGRADE=no \
bash /opt/fancy-frame/scripts/install_initial_setup.sh

target_profile="pi45"
if [[ -f /etc/fancy-frame-hw-profile ]]; then
    target_profile="$(tr -d '\r\n[:space:]' < /etc/fancy-frame-hw-profile)"
fi

case "${target_profile}" in
    zero2w)
        echo "==> Applying strict service pruning profile: zero2w"

        # Dedicated-frame devices on Zero 2 W benefit from minimizing background
        # activity and boot-time noise to preserve responsiveness.
        for unit in \
            bluetooth.service \
            hciuart.service \
            triggerhappy.service \
            dphys-swapfile.service; do
            systemctl disable "${unit}" >/dev/null 2>&1 || true
            systemctl mask "${unit}" >/dev/null 2>&1 || true
        done

        for unit in \
            apt-daily.timer \
            apt-daily-upgrade.timer \
            man-db.timer; do
            systemctl disable "${unit}" >/dev/null 2>&1 || true
            systemctl mask "${unit}" >/dev/null 2>&1 || true
        done
        ;;
    pi45)
        echo "==> Applying balanced performance profile: pi45"
        # Keep default background services for Pi 4/5 while using the boot-time
        # display/GPU tuning written by 00-run.sh.
        ;;
    *)
        echo "==> Unknown hardware profile '${target_profile}', leaving service set unchanged."
        ;;
esac

echo "==> install_initial_setup.sh completed successfully inside chroot."
