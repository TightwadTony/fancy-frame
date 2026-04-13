#!/usr/bin/env bash
# update.sh — update an existing photo-frame installation.
#
# Copies the latest app files to /opt/photo-frame/, refreshes systemd unit
# files, reloads the daemon, and restarts the running services.
#
# This script intentionally skips all initial-install-only steps such as
# package installation, Samba configuration, Xorg permissions, boot-target
# changes, and Wi-Fi AP setup.
#
# Usage:
#   sudo bash scripts/update.sh
#
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root: sudo bash scripts/update.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTALL_ROOT="/opt/photo-frame"

if [[ ! -d "${INSTALL_ROOT}" ]]; then
  echo "Error: ${INSTALL_ROOT} does not exist."
  echo "Run the full installer first: sudo bash scripts/install_initial_setup.sh"
  exit 1
fi

echo "Updating photo-frame installation from ${PROJECT_ROOT} ..."

# ------------------------------------------------------------------
# 1. Stop services gracefully before replacing files
# ------------------------------------------------------------------
echo "Stopping services..."
systemctl stop photo-frame.service         >/dev/null 2>&1 || true
systemctl stop photo-frame-api.service     >/dev/null 2>&1 || true

# ------------------------------------------------------------------
# 2. Copy updated app files
# ------------------------------------------------------------------
echo "Copying updated app files to ${INSTALL_ROOT} ..."
cp -a "${PROJECT_ROOT}/scripts" "${INSTALL_ROOT}/"
cp -a "${PROJECT_ROOT}/portal"  "${INSTALL_ROOT}/"
cp -a "${PROJECT_ROOT}/api"     "${INSTALL_ROOT}/"
cp -a "${PROJECT_ROOT}/config"  "${INSTALL_ROOT}/"
cp -a "${PROJECT_ROOT}/systemd" "${INSTALL_ROOT}/"
chmod +x "${INSTALL_ROOT}"/scripts/*.sh

# ------------------------------------------------------------------
# 3. Install updated systemd unit files
# ------------------------------------------------------------------
echo "Installing updated systemd unit files..."
install -m 0644 "${INSTALL_ROOT}/systemd/photo-frame.service"                /etc/systemd/system/photo-frame.service
install -m 0644 "${INSTALL_ROOT}/systemd/photo-frame-wifi-bootstrap.service" /etc/systemd/system/photo-frame-wifi-bootstrap.service
install -m 0644 "${INSTALL_ROOT}/systemd/photo-frame-setup-mode.service"     /etc/systemd/system/photo-frame-setup-mode.service
install -m 0644 "${INSTALL_ROOT}/systemd/photo-frame-setup-portal.service"   /etc/systemd/system/photo-frame-setup-portal.service
install -m 0644 "${INSTALL_ROOT}/systemd/photo-frame-api.service"            /etc/systemd/system/photo-frame-api.service

# ------------------------------------------------------------------
# 4. Install updated Avahi mDNS advertisement
# ------------------------------------------------------------------
echo "Installing updated Avahi mDNS advertisement..."
install -m 0644 "${INSTALL_ROOT}/config/avahi-photo-frame.service" /etc/avahi/services/photo-frame.service

# ------------------------------------------------------------------
# 5. Reload systemd and restart services
# ------------------------------------------------------------------
echo "Reloading systemd daemon..."
systemctl daemon-reload

echo "Restarting services..."
systemctl restart photo-frame-api.service  || true
systemctl restart photo-frame.service

echo
echo "Update complete."
echo "The slideshow and API have been restarted with the new version."
