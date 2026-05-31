#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root: sudo bash scripts/install_initial_setup.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTALL_ROOT="/opt/fancy-frame"
LEGACY_INSTALL_ROOT="/opt/photo-frame"
API_AUTH_USER="fancy-frame-api"
API_AUTH_DEFAULT_PASSWORD="${FANCY_FRAME_API_DEFAULT_PASSWORD:-12345678}"
API_AUTH_HASH_FILE="/etc/fancy-frame-api-password.hash"

detect_target_user() {
  if [[ -n "${FANCY_FRAME_USER:-}" ]] && id -u "${FANCY_FRAME_USER}" >/dev/null 2>&1; then
    printf '%s' "${FANCY_FRAME_USER}"
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
  echo "Could not determine a non-root user to own fancy-frame files."
  echo "Set one explicitly, for example:"
  echo "  sudo FANCY_FRAME_USER=photo bash scripts/install_initial_setup.sh"
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
      echo "Invalid SMB_ACCESS_MODE='${selected}'. Use 'anonymous' or 'credentials'." >&2
      exit 1
      ;;
  esac

  if [[ -t 0 ]] && [[ -r /dev/tty ]] && [[ -w /dev/tty ]]; then
    echo >&2
    echo "Choose SMB share access mode:" >&2
    echo "  1) credentials (recommended)" >&2
    echo "  2) anonymous" >&2
    printf "Enter choice [1/2, default 1]: " > /dev/tty
    read -r smb_choice < /dev/tty
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

choose_frame_name() {
  local selected="${FANCY_FRAME_NAME:-}"

  if [[ -n "${selected}" ]]; then
    printf '%s' "${selected}"
    return 0
  fi

  if [[ -t 0 ]] && [[ -r /dev/tty ]] && [[ -w /dev/tty ]]; then
    local default_name="Fancy Frame"
    echo >&2
    printf "Enter Fancy Frame display name [default: %s]: " "${default_name}" > /dev/tty
    read -r entered_name < /dev/tty
    if [[ -n "${entered_name}" ]]; then
      printf '%s' "${entered_name}"
    else
      printf '%s' "${default_name}"
    fi
  else
    printf '%s' "Fancy Frame"
  fi
}

normalize_frame_name() {
  local name="$1"
  name="$(printf '%s' "${name}" | tr -d '\r\n' | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
  if [[ -z "${name}" ]]; then
    name="Fancy Frame"
  fi
  if [[ ${#name} -gt 64 ]]; then
    name="${name:0:64}"
    name="$(printf '%s' "${name}" | sed -E 's/[[:space:]]+$//')"
  fi
  printf '%s' "${name}"
}

ensure_api_auth_user() {
  local created="no"

  if ! id -u "${API_AUTH_USER}" >/dev/null 2>&1; then
    useradd --create-home --shell /usr/sbin/nologin "${API_AUTH_USER}"
    created="yes"
  fi

  if [[ "${created}" == "yes" ]]; then
    if [[ ${#API_AUTH_DEFAULT_PASSWORD} -lt 4 ]]; then
      echo "FANCY_FRAME_API_DEFAULT_PASSWORD must be at least 4 characters."
      exit 1
    fi
    printf '%s:%s\n' "${API_AUTH_USER}" "${API_AUTH_DEFAULT_PASSWORD}" | chpasswd
    echo "Created ${API_AUTH_USER} user with installer-provided default password."
  else
    echo "User ${API_AUTH_USER} already exists; keeping existing password."
  fi

  if [[ ! -f "${API_AUTH_HASH_FILE}" ]]; then
    python3 - "${API_AUTH_DEFAULT_PASSWORD}" "${API_AUTH_HASH_FILE}" <<'PY'
import hashlib
import json
import os
import secrets
import sys

password = sys.argv[1]
dest = sys.argv[2]
salt = secrets.token_bytes(16)
digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 260000)
payload = {
    'algo': 'pbkdf2_sha256',
    'iterations': 260000,
    'salt': salt.hex(),
    'hash': digest.hex(),
}
tmp = f"{dest}.{os.getpid()}.tmp"
with open(tmp, 'w', encoding='utf-8') as f:
    json.dump(payload, f)
os.chmod(tmp, 0o600)
os.replace(tmp, dest)
PY
    echo "Initialized ${API_AUTH_HASH_FILE}."
  fi
}

upsert_conf_key() {
  local file_path="$1"
  local key="$2"
  local value="$3"
  local tmp_file
  tmp_file="$(mktemp)"

  if [[ -f "${file_path}" ]]; then
    awk -v key="${key}" -v value="${value}" '
      BEGIN { found = 0 }
      {
        raw = $0
        trimmed = raw
        sub(/^[[:space:]]+/, "", trimmed)
        if (trimmed !~ /^#/ && trimmed ~ ("^" key "[[:space:]]*=")) {
          print key " = " value
          found = 1
        } else {
          print raw
        }
      }
      END {
        if (!found) {
          if (NR > 0) print ""
          print key " = " value
        }
      }
    ' "${file_path}" > "${tmp_file}"
  else
    printf '%s = %s\n' "${key}" "${value}" > "${tmp_file}"
  fi

  install -m 0666 "${tmp_file}" "${file_path}"
  rm -f "${tmp_file}"
}

FRAME_NAME="$(normalize_frame_name "$(choose_frame_name)")"

# Ask whether to run a full system upgrade before installing packages.
RUN_UPGRADE="no"
if [[ -n "${FANCY_FRAME_UPGRADE:-}" ]]; then
  case "${FANCY_FRAME_UPGRADE}" in
    [Yy][Ee][Ss]|1|[Tt][Rr][Uu][Ee]) RUN_UPGRADE="yes" ;;
  esac
elif [[ -t 0 ]]; then
  echo
  printf "Run full apt upgrade before installing? [y/N]: "
  read -r upgrade_choice
  if [[ "${upgrade_choice,,}" == "y" || "${upgrade_choice,,}" == "yes" ]]; then
    RUN_UPGRADE="yes"
  fi
fi

echo "Using target user: ${TARGET_USER}"
echo "SMB mode: ${SMB_MODE}"
echo "Fancy Frame display name: ${FRAME_NAME}"

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

  if [[ " ${cmdline} " != *" quiet "* ]]; then
    cmdline+=" quiet"
  fi

  if [[ " ${cmdline} " != *" loglevel=3 "* ]]; then
    cmdline+=" loglevel=3"
  fi

  if [[ " ${cmdline} " != *" systemd.show_status=auto "* ]]; then
    cmdline+=" systemd.show_status=auto"
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

configure_display_console() {
  # This device has a dedicated display, so hide the Linux login consoles on the
  # first two virtual terminals and let the slideshow own tty1.
  for unit in getty@tty1.service getty@tty2.service; do
    systemctl stop "${unit}" >/dev/null 2>&1 || true
    systemctl disable "${unit}" >/dev/null 2>&1 || true
    systemctl mask "${unit}" >/dev/null 2>&1 || true
  done
}

configure_samba_tuning() {
  if grep -Eq "BEGIN (FANCY|PHOTO)-FRAME SMB TUNING" /etc/samba/smb.conf; then
    awk '
      /# BEGIN (FANCY|PHOTO)-FRAME SMB TUNING/ {skip=1; next}
      /# END (FANCY|PHOTO)-FRAME SMB TUNING/ {skip=0; next}
      !skip {print}
    ' /etc/samba/smb.conf > /tmp/smb.conf.fancy-frame.tmp
    cp /tmp/smb.conf.fancy-frame.tmp /etc/samba/smb.conf
    rm -f /tmp/smb.conf.fancy-frame.tmp
  fi

  if grep -qi '^\s*\[global\]\s*$' /etc/samba/smb.conf; then
    awk '
      BEGIN {inserted=0}
      {
        print
        if (!inserted && $0 ~ /^[[:space:]]*\[global\][[:space:]]*$/) {
          print "   # BEGIN FANCY-FRAME SMB TUNING"
          print "   server min protocol = SMB2"
          print "   load printers = no"
          print "   printing = bsd"
          print "   printcap name = /dev/null"
          print "   disable spoolss = yes"
          print "   deadtime = 15"
          print "   # END FANCY-FRAME SMB TUNING"
          inserted=1
        }
      }
    ' /etc/samba/smb.conf > /tmp/smb.conf.fancy-frame.tmp
    cp /tmp/smb.conf.fancy-frame.tmp /etc/samba/smb.conf
    rm -f /tmp/smb.conf.fancy-frame.tmp
  else
    cat > /tmp/smb.conf.fancy-frame.tmp <<EOF
[global]
   # BEGIN FANCY-FRAME SMB TUNING
   server min protocol = SMB2
   load printers = no
   printing = bsd
   printcap name = /dev/null
   disable spoolss = yes
   deadtime = 15
   # END FANCY-FRAME SMB TUNING

EOF
    cat /etc/samba/smb.conf >> /tmp/smb.conf.fancy-frame.tmp
    cp /tmp/smb.conf.fancy-frame.tmp /etc/samba/smb.conf
    rm -f /tmp/smb.conf.fancy-frame.tmp
  fi
}

echo "Installing packages..."
apt update
if [[ "${RUN_UPGRADE}" == "yes" ]]; then
  echo "Running full apt upgrade..."
  apt upgrade -y
fi
apt install -y \
  xserver-xorg \
  xinit \
  x11-xserver-utils \
  python3-pygame \
  python3-pil \
  imagemagick \
  samba \
  avahi-daemon \
  hostapd \
  dnsmasq \
  dhcpcd \
  python3-flask \
  iw \
  rfkill

ensure_api_auth_user

echo "Preparing directories..."
mkdir -p /srv/photos
chown -R "${TARGET_USER}:${TARGET_USER}" /srv/photos
mkdir -p /var/lib/fancy-frame
if [[ -d /var/lib/photo-frame ]] && [[ ! -L /var/lib/photo-frame ]]; then
  cp -a /var/lib/photo-frame/. /var/lib/fancy-frame/ >/dev/null 2>&1 || true
fi
chown -R "${TARGET_USER}:${TARGET_USER}" /var/lib/fancy-frame

if [[ -f /srv/photos/photo-frame.conf ]] && [[ ! -e /srv/photos/fancy-frame.conf ]]; then
  mv /srv/photos/photo-frame.conf /srv/photos/fancy-frame.conf
fi
upsert_conf_key /srv/photos/fancy-frame.conf frame_name "${FRAME_NAME}"
ln -sfn /srv/photos/fancy-frame.conf /srv/photos/photo-frame.conf

mkdir -p "${INSTALL_ROOT}"
if [[ "$(realpath "${PROJECT_ROOT}")" != "$(realpath "${INSTALL_ROOT}")" ]]; then
  cp -a "${PROJECT_ROOT}/scripts" "${INSTALL_ROOT}/"
  cp -a "${PROJECT_ROOT}/portal" "${INSTALL_ROOT}/"
  cp -a "${PROJECT_ROOT}/api" "${INSTALL_ROOT}/"
  cp -a "${PROJECT_ROOT}/config" "${INSTALL_ROOT}/"
  cp -a "${PROJECT_ROOT}/systemd" "${INSTALL_ROOT}/"
  install -m 0644 "${PROJECT_ROOT}/VERSION" "${INSTALL_ROOT}/VERSION"
else
  echo "Installer running from ${INSTALL_ROOT}; skipping self-copy step."
fi
chmod +x "${INSTALL_ROOT}"/scripts/*.sh

if [[ -d "${LEGACY_INSTALL_ROOT}" ]] && [[ ! -L "${LEGACY_INSTALL_ROOT}" ]]; then
  rm -rf "${LEGACY_INSTALL_ROOT}"
fi
ln -sfn "${INSTALL_ROOT}" "${LEGACY_INSTALL_ROOT}"
ln -sfn /var/lib/fancy-frame /var/lib/photo-frame

# Dedicated-frame images must not drop into Raspberry Pi OS first-boot user setup.
systemctl disable userconfig.service >/dev/null 2>&1 || true
systemctl mask userconfig.service >/dev/null 2>&1 || true
rm -f /etc/xdg/autostart/piwiz.desktop


echo "Installing hostapd and dnsmasq configs..."
install -m 0644 "${INSTALL_ROOT}/config/hostapd.conf" /etc/hostapd/hostapd.conf
if grep -q '^#\?DAEMON_CONF=' /etc/default/hostapd; then
  sed -i 's|^#\?DAEMON_CONF=.*|DAEMON_CONF="/etc/hostapd/hostapd.conf"|' /etc/default/hostapd
else
  echo 'DAEMON_CONF="/etc/hostapd/hostapd.conf"' >> /etc/default/hostapd
fi
rm -f /etc/dnsmasq.d/photo-frame.conf
install -m 0644 "${INSTALL_ROOT}/config/dnsmasq-fancy-frame.conf" /etc/dnsmasq.d/fancy-frame.conf

# Ensure setup-mode scripts can start these services on demand.
systemctl unmask hostapd >/dev/null 2>&1 || true
systemctl unmask dnsmasq >/dev/null 2>&1 || true

systemctl disable hostapd >/dev/null 2>&1 || true
systemctl disable dnsmasq >/dev/null 2>&1 || true

# Use the wlan0-specific wpa_supplicant unit consistently.
systemctl disable wpa_supplicant.service >/dev/null 2>&1 || true
systemctl stop wpa_supplicant.service >/dev/null 2>&1 || true
systemctl unmask wpa_supplicant@wlan0.service >/dev/null 2>&1 || true
systemctl enable wpa_supplicant@wlan0.service >/dev/null 2>&1 || true


echo "Installing systemd services..."
for legacy_unit in \
  photo-frame.service \
  photo-frame-wifi-bootstrap.service \
  photo-frame-setup-mode.service \
  photo-frame-setup-portal.service \
  photo-frame-api.service; do
  systemctl disable "${legacy_unit}" >/dev/null 2>&1 || true
  rm -f "/etc/systemd/system/${legacy_unit}"
done

install -m 0644 "${INSTALL_ROOT}/systemd/fancy-frame.service" /etc/systemd/system/fancy-frame.service
install -m 0644 "${INSTALL_ROOT}/systemd/fancy-frame-wifi-bootstrap.service" /etc/systemd/system/fancy-frame-wifi-bootstrap.service
install -m 0644 "${INSTALL_ROOT}/systemd/fancy-frame-setup-mode.service" /etc/systemd/system/fancy-frame-setup-mode.service
install -m 0644 "${INSTALL_ROOT}/systemd/fancy-frame-setup-portal.service" /etc/systemd/system/fancy-frame-setup-portal.service
install -m 0644 "${INSTALL_ROOT}/systemd/fancy-frame-api.service" /etc/systemd/system/fancy-frame-api.service

if [[ ! -f /etc/fancy-frame-api.env ]]; then
  cat > /etc/fancy-frame-api.env <<'EOF'
# Optional GitHub fine-grained PAT used by /api/update-check.
# This value is not bundled into releases. Set it locally on the Pi.
# If you store the PAT in a GitHub repository secret, use the same value here.
RELEASESPAT=
EOF
  chmod 0600 /etc/fancy-frame-api.env
fi

systemctl daemon-reload
systemctl enable fancy-frame.service
systemctl enable fancy-frame-wifi-bootstrap.service
systemctl enable fancy-frame-api.service


echo "Installing Avahi mDNS advertisement..."
rm -f /etc/avahi/services/photo-frame.service
install -m 0644 "${INSTALL_ROOT}/config/avahi-fancy-frame.service" /etc/avahi/services/fancy-frame.service


echo "Configuring Samba share..."
configure_samba_tuning

if grep -Eq "BEGIN (FANCY|PHOTO)-FRAME SHARE" /etc/samba/smb.conf; then
  awk '
    /# BEGIN (FANCY|PHOTO)-FRAME SHARE/ {skip=1; next}
    /# END (FANCY|PHOTO)-FRAME SHARE/ {skip=0; next}
    !skip {print}
  ' /etc/samba/smb.conf > /tmp/smb.conf.fancy-frame.tmp
  cp /tmp/smb.conf.fancy-frame.tmp /etc/samba/smb.conf
  rm -f /tmp/smb.conf.fancy-frame.tmp
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
    ' /etc/samba/smb.conf > /tmp/smb.conf.fancy-frame.tmp
    cp /tmp/smb.conf.fancy-frame.tmp /etc/samba/smb.conf
    rm -f /tmp/smb.conf.fancy-frame.tmp
  else
    cat >> /etc/samba/smb.conf <<EOF

[global]
   map to guest = Bad User
EOF
  fi

  cat >> /etc/samba/smb.conf <<EOF

# BEGIN FANCY-FRAME SHARE
[photos]
   path = /srv/photos
   browseable = yes
   read only = no
   guest ok = yes
   guest only = yes
   force user = ${TARGET_USER}
   create mask = 0644
   directory mask = 0755
# END FANCY-FRAME SHARE
EOF
else
  cat >> /etc/samba/smb.conf <<EOF

# BEGIN FANCY-FRAME SHARE
[photos]
   path = /srv/photos
   browseable = yes
   read only = no
   create mask = 0644
   directory mask = 0755
   valid users = ${TARGET_USER}
# END FANCY-FRAME SHARE
EOF
fi

if grep -Eq "BEGIN (FANCY|PHOTO)-FRAME HOMES" /etc/samba/smb.conf; then
  awk '
    /# BEGIN (FANCY|PHOTO)-FRAME HOMES/ {skip=1; next}
    /# END (FANCY|PHOTO)-FRAME HOMES/ {skip=0; next}
    !skip {print}
  ' /etc/samba/smb.conf > /tmp/smb.conf.fancy-frame.tmp
  cp /tmp/smb.conf.fancy-frame.tmp /etc/samba/smb.conf
  rm -f /tmp/smb.conf.fancy-frame.tmp
fi

cat >> /etc/samba/smb.conf <<EOF

# BEGIN FANCY-FRAME HOMES
[homes]
   browseable = no
   available = no
# END FANCY-FRAME HOMES
EOF

systemctl enable smbd
systemctl restart smbd
systemctl enable avahi-daemon

echo "Configuring Xorg permissions..."
configure_xorg_permissions

echo "Configuring boot target (console-only)..."
configure_boot_target

echo "Configuring dedicated display console..."
configure_display_console

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
