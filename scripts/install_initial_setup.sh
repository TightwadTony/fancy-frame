#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root: sudo bash scripts/install_initial_setup.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTALL_ROOT="/opt/photo-frame"

echo "Installing packages..."
apt update
apt install -y \
  xserver-xorg \
  xinit \
  feh \
  samba \
  avahi-daemon \
  hostapd \
  dnsmasq \
  python3-flask \
  iw \
  rfkill

echo "Preparing directories..."
mkdir -p /srv/photos
chown -R pi:pi /srv/photos
mkdir -p /var/lib/photo-frame
chown -R pi:pi /var/lib/photo-frame

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
if ! grep -q "BEGIN PHOTO-FRAME SHARE" /etc/samba/smb.conf; then
  cat "${INSTALL_ROOT}/config/smb-share.conf" >> /etc/samba/smb.conf
fi

systemctl enable smbd
systemctl restart smbd
systemctl enable avahi-daemon

cat <<'EOF'

Base install complete.

Next steps:
1. Set Samba password for user pi:
   sudo smbpasswd -a pi
2. Optionally customize onboarding AP credentials in /etc/hostapd/hostapd.conf
3. Reboot:
   sudo reboot

On next boot:
- If Wi-Fi connects: slideshow runs and SMB share is available.
- If Wi-Fi does not connect: onboarding AP + portal starts.
EOF
