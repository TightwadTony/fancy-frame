#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root: sudo bash scripts/install_initial_setup.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTALL_ROOT="/opt/photo-frame"

detect_target_user() {
  if [[ -n "${PHOTO_FRAME_USER:-}" ]] && id -u "${PHOTO_FRAME_USER}" >/dev/null 2>&1; then
    printf '%s' "${PHOTO_FRAME_USER}"
    return 0
  fi

  if [[ -n "${SUDO_USER:-}" ]] && [[ "${SUDO_USER}" != "root" ]] && id -u "${SUDO_USER}" >/dev/null 2>&1; then
    printf '%s' "${SUDO_USER}"
    return 0
  fi

  if id -u photo >/dev/null 2>&1; then
    printf '%s' "photo"
    return 0
  fi

  if id -u pi >/dev/null 2>&1; then
    printf '%s' "pi"
    return 0
  fi

  awk -F: '$3 >= 1000 && $1 != "nobody" {print $1; exit}' /etc/passwd
}

TARGET_USER="$(detect_target_user)"
if [[ -z "${TARGET_USER}" ]] || ! id -u "${TARGET_USER}" >/dev/null 2>&1; then
  echo "Could not determine a non-root user to own photo-frame files."
  echo "Set one explicitly, for example:"
  echo "  sudo PHOTO_FRAME_USER=photo bash scripts/install_initial_setup.sh"
  exit 1
fi

TARGET_HOME="$(getent passwd "${TARGET_USER}" | cut -d: -f6)"
if [[ -z "${TARGET_HOME}" ]]; then
  TARGET_HOME="/home/${TARGET_USER}"
fi

choose_smb_mode() {
  local selected="${SMB_ACCESS_MODE:-}"

  case "${selected}" in
    anonymous|credentials)
      printf '%s' "${selected}"
      return 0
      ;;
    "")
      ;;
    *)
      echo "Invalid SMB_ACCESS_MODE='${selected}'. Use 'anonymous' or 'credentials'."
      exit 1
      ;;
  esac

  if [[ -t 0 ]]; then
    echo
    echo "Choose SMB share access mode:"
    echo "  1) credentials (recommended)"
    echo "  2) anonymous"
    printf "Enter choice [1/2, default 1]: "
    read -r smb_choice
    case "${smb_choice}" in
      2)
        printf '%s' "anonymous"
        ;;
      *)
        printf '%s' "credentials"
        ;;
    esac
  else
    printf '%s' "credentials"
  fi
}

SMB_MODE="$(choose_smb_mode)"

echo "Using target user: ${TARGET_USER}"
echo "SMB mode: ${SMB_MODE}"

configure_no_splash_boot() {
  local cmdline_file=""

  # Bookworm moves boot partition to /boot/firmware; fall back to /boot
  if [[ -f "/boot/firmware/cmdline.txt" ]]; then
    cmdline_file="/boot/firmware/cmdline.txt"
  elif [[ -f "/boot/cmdline.txt" ]]; then
    cmdline_file="/boot/cmdline.txt"
  else
    echo "Skipping no-splash config: cmdline.txt not found in /boot/firmware or /boot."
    return 0
  fi

  local cmdline
  cmdline="$(tr -d '\n' < "${cmdline_file}")"

  # Remove splash token wherever it appears
  cmdline="${cmdline// splash / }"
  cmdline="${cmdline# splash}"
  cmdline="${cmdline% splash}"
  cmdline="${cmdline//splash /}"
  cmdline="${cmdline//splash/}"

  if [[ " ${cmdline} " != *" logo.nologo "* ]]; then
    cmdline+=" logo.nologo"
  fi

  if [[ " ${cmdline} " != *" vt.global_cursor_default=0 "* ]]; then
    cmdline+=" vt.global_cursor_default=0"
  fi

  # Collapse multiple spaces
  cmdline="$(echo "${cmdline}" | tr -s ' ' | sed 's/^ //;s/ $//')"

  echo "${cmdline}" > "${cmdline_file}"
  echo "Updated: ${cmdline_file}"

  # Disable plymouth splash service if present (source of Pi Desktop welcome screen)
  if systemctl list-units --all --no-pager 2>/dev/null | grep -q 'plymouth'; then
    systemctl disable plymouth.service >/dev/null 2>&1 || true
    systemctl mask plymouth.service >/dev/null 2>&1 || true
  fi
}

configure_xorg_permissions() {
  # Allow non-root users to start Xorg (needed on Bookworm for VT access)
  mkdir -p /etc/X11
  cat > /etc/X11/Xwrapper.config <<'EOF'
allowed_users=anybody
needs_root_rights=yes
EOF

  # Add user to groups required for display, input, and VT access
  usermod -aG video,input,tty "${TARGET_USER}" || true
}

configure_wifi_powersave_off() {
  cat > /etc/systemd/system/wifi-powersave-off.service <<'EOF'
[Unit]
Description=Disable WiFi power saving on wlan0
After=network-online.target wpa_supplicant.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'for i in $(seq 1 30); do if command -v iw >/dev/null 2>&1 && ip link show wlan0 >/dev/null 2>&1; then $(command -v iw) dev wlan0 set power_save off && exit 0; fi; sleep 1; done; exit 1'
RemainAfterExit=yes
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable wifi-powersave-off.service
  systemctl start wifi-powersave-off.service || true

  # Apply immediately in the current boot too, in case the service start race is missed.
  if command -v iw >/dev/null 2>&1 && ip link show wlan0 >/dev/null 2>&1; then
    "$(command -v iw)" dev wlan0 set power_save off || true
  fi
}

configure_boot_target() {
  # Disable lightdm display manager and boot to console (multi-user) target
  if systemctl list-units --all --no-pager 2>/dev/null | grep -q 'lightdm'; then
    echo "Disabling lightdm.service..."
    systemctl disable lightdm.service >/dev/null 2>&1 || true
    systemctl stop lightdm.service >/dev/null 2>&1 || true
  fi

  # Set default boot target to multi-user (console-only) instead of graphical
  echo "Setting default boot target to multi-user.target..."
  systemctl set-default multi-user.target >/dev/null 2>&1

  # Verify
  local default_target
  default_target="$(systemctl get-default)"
  echo "Current default boot target: ${default_target}"
}

configure_samba_tuning() {
  if grep -q "BEGIN PHOTO-FRAME SMB TUNING" /etc/samba/smb.conf; then
    awk '
      /# BEGIN PHOTO-FRAME SMB TUNING/ {skip=1; next}
      /# END PHOTO-FRAME SMB TUNING/ {skip=0; next}
      !skip {print}
    ' /etc/samba/smb.conf > /tmp/smb.conf.photo-frame.tmp
    cp /tmp/smb.conf.photo-frame.tmp /etc/samba/smb.conf
    rm -f /tmp/smb.conf.photo-frame.tmp
  fi

  if grep -qi '^\s*\[global\]\s*$' /etc/samba/smb.conf; then
    awk '
      BEGIN {inserted=0}
      {
        print
        if (!inserted && $0 ~ /^[[:space:]]*\[global\][[:space:]]*$/) {
          print "   # BEGIN PHOTO-FRAME SMB TUNING"
          print "   server min protocol = SMB2"
          print "   load printers = no"
          print "   printing = bsd"
          print "   printcap name = /dev/null"
          print "   disable spoolss = yes"
          print "   deadtime = 15"
          print "   # END PHOTO-FRAME SMB TUNING"
          inserted=1
        }
      }
    ' /etc/samba/smb.conf > /tmp/smb.conf.photo-frame.tmp
    cp /tmp/smb.conf.photo-frame.tmp /etc/samba/smb.conf
    rm -f /tmp/smb.conf.photo-frame.tmp
  else
    cat > /tmp/smb.conf.photo-frame.tmp <<EOF
[global]
   # BEGIN PHOTO-FRAME SMB TUNING
   server min protocol = SMB2
   load printers = no
   printing = bsd
   printcap name = /dev/null
   disable spoolss = yes
   deadtime = 15
   # END PHOTO-FRAME SMB TUNING

EOF
    cat /etc/samba/smb.conf >> /tmp/smb.conf.photo-frame.tmp
    cp /tmp/smb.conf.photo-frame.tmp /etc/samba/smb.conf
    rm -f /tmp/smb.conf.photo-frame.tmp
  fi
}

echo "Installing packages..."
apt update
apt install -y \
  xserver-xorg \
  xinit \
  kodi \
  kodi-eventclients-kodi-send \
  samba \
  avahi-daemon \
  hostapd \
  dnsmasq \
  python3-flask \
  iw \
  rfkill

echo "Preparing directories..."
mkdir -p /srv/photos
chown -R "${TARGET_USER}:${TARGET_USER}" /srv/photos
mkdir -p /var/lib/photo-frame
chown -R "${TARGET_USER}:${TARGET_USER}" /var/lib/photo-frame

mkdir -p "${INSTALL_ROOT}"
cp -a "${PROJECT_ROOT}/scripts" "${INSTALL_ROOT}/"
cp -a "${PROJECT_ROOT}/portal" "${INSTALL_ROOT}/"
cp -a "${PROJECT_ROOT}/config" "${INSTALL_ROOT}/"
cp -a "${PROJECT_ROOT}/systemd" "${INSTALL_ROOT}/"
chmod +x "${INSTALL_ROOT}"/scripts/*.sh


echo "Installing hostapd and dnsmasq configs..."
install -m 0644 "${INSTALL_ROOT}/config/hostapd.conf" /etc/hostapd/hostapd.conf
if grep -q '^#\?DAEMON_CONF=' /etc/default/hostapd; then
  sed -i 's|^#\?DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
else
  echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' >> /etc/default/hostapd
fi
install -m 0644 "${INSTALL_ROOT}/config/dnsmasq-photo-frame.conf" /etc/dnsmasq.d/photo-frame.conf

# Ensure setup-mode scripts can start these services on demand.
systemctl unmask hostapd >/dev/null 2>&1 || true
systemctl unmask dnsmasq >/dev/null 2>&1 || true

systemctl disable hostapd >/dev/null 2>&1 || true
systemctl disable dnsmasq >/dev/null 2>&1 || true


echo "Installing systemd services..."
install -m 0644 "${INSTALL_ROOT}/systemd/photo-frame.service" /etc/systemd/system/photo-frame.service
install -m 0644 "${INSTALL_ROOT}/systemd/photo-frame-wifi-bootstrap.service" /etc/systemd/system/photo-frame-wifi-bootstrap.service
install -m 0644 "${INSTALL_ROOT}/systemd/photo-frame-setup-mode.service" /etc/systemd/system/photo-frame-setup-mode.service
install -m 0644 "${INSTALL_ROOT}/systemd/photo-frame-setup-portal.service" /etc/systemd/system/photo-frame-setup-portal.service

systemctl daemon-reload
systemctl enable photo-frame.service
systemctl enable photo-frame-wifi-bootstrap.service


echo "Configuring Samba share..."
configure_samba_tuning

if grep -q "BEGIN PHOTO-FRAME SHARE" /etc/samba/smb.conf; then
  awk '
    /# BEGIN PHOTO-FRAME SHARE/ {skip=1; next}
    /# END PHOTO-FRAME SHARE/ {skip=0; next}
    !skip {print}
  ' /etc/samba/smb.conf > /tmp/smb.conf.photo-frame.tmp
  cp /tmp/smb.conf.photo-frame.tmp /etc/samba/smb.conf
  rm -f /tmp/smb.conf.photo-frame.tmp
fi

if [[ "${SMB_MODE}" == "anonymous" ]]; then
  if grep -qi '^\s*map to guest\s*=' /etc/samba/smb.conf; then
    sed -i 's|^\s*map to guest\s*=.*|   map to guest = Bad User|I' /etc/samba/smb.conf
  elif grep -qi '^\s*\[global\]\s*$' /etc/samba/smb.conf; then
    awk '
      BEGIN {inserted=0}
      {
        print
        if (!inserted && $0 ~ /^\s*\[global\]\s*$/) {
          print "   map to guest = Bad User"
          inserted=1
        }
      }
    ' /etc/samba/smb.conf > /tmp/smb.conf.photo-frame.tmp
    cp /tmp/smb.conf.photo-frame.tmp /etc/samba/smb.conf
    rm -f /tmp/smb.conf.photo-frame.tmp
  else
    cat >> /etc/samba/smb.conf <<EOF

[global]
   map to guest = Bad User
EOF
  fi

  cat >> /etc/samba/smb.conf <<EOF

# BEGIN PHOTO-FRAME SHARE
[photos]
   path = /srv/photos
   browseable = yes
   read only = no
   guest ok = yes
   guest only = yes
   force user = ${TARGET_USER}
   create mask = 0644
   directory mask = 0755
# END PHOTO-FRAME SHARE
EOF
else
  cat >> /etc/samba/smb.conf <<EOF

# BEGIN PHOTO-FRAME SHARE
[photos]
   path = /srv/photos
   browseable = yes
   read only = no
   create mask = 0644
   directory mask = 0755
   valid users = ${TARGET_USER}
# END PHOTO-FRAME SHARE
EOF
fi

if grep -q "BEGIN PHOTO-FRAME HOMES" /etc/samba/smb.conf; then
  awk '
    /# BEGIN PHOTO-FRAME HOMES/ {skip=1; next}
    /# END PHOTO-FRAME HOMES/ {skip=0; next}
    !skip {print}
  ' /etc/samba/smb.conf > /tmp/smb.conf.photo-frame.tmp
  cp /tmp/smb.conf.photo-frame.tmp /etc/samba/smb.conf
  rm -f /tmp/smb.conf.photo-frame.tmp
fi

cat >> /etc/samba/smb.conf <<EOF

# BEGIN PHOTO-FRAME HOMES
[homes]
   browseable = no
   available = no
# END PHOTO-FRAME HOMES
EOF

systemctl enable smbd
systemctl restart smbd
systemctl enable avahi-daemon

echo "Configuring Xorg permissions..."
configure_xorg_permissions

echo "Configuring boot target (console-only)..."
configure_boot_target

echo "Configuring no-splash boot..."
configure_no_splash_boot

echo "Configuring WiFi power-save off..."
configure_wifi_powersave_off

cat <<'EOF'

Base install complete.

Next steps:
1. If SMB mode is 'credentials', set Samba password for the target user shown above:
  sudo smbpasswd -a <target-user>
2. Optionally customize onboarding AP credentials in /etc/hostapd/hostapd.conf
3. Reboot:
   sudo reboot

On next boot:
- If Wi-Fi connects: slideshow runs and SMB share is available.
- If Wi-Fi does not connect: onboarding AP + portal starts.
EOF
