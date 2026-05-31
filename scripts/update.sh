#!/usr/bin/env bash
# update.sh — update an existing fancy-frame installation.
#
# Copies the latest app files to /opt/fancy-frame/, refreshes systemd unit
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

# Ensure RELEASESPAT is available for updater subprocesses
if [ -f /etc/fancy-frame-api.env ]; then
  set -a
  . /etc/fancy-frame-api.env
  set +a
fi

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root: sudo bash scripts/update.sh"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
INSTALL_ROOT="/opt/fancy-frame"
LEGACY_INSTALL_ROOT="/opt/photo-frame"
API_AUTH_USER="fancy-frame-api"
API_AUTH_DEFAULT_PASSWORD="${FANCY_FRAME_API_DEFAULT_PASSWORD:-12345678}"
API_AUTH_HASH_FILE="/etc/fancy-frame-api-password.hash"

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
    echo "Created ${API_AUTH_USER} user with updater-provided default password."
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

if [[ ! -d "${INSTALL_ROOT}" && ! -d "${LEGACY_INSTALL_ROOT}" ]]; then
  echo "Error: no existing fancy-frame installation was found."
  echo "Run the full installer first: sudo bash scripts/install_initial_setup.sh"
  exit 1
fi

mkdir -p "${INSTALL_ROOT}"

echo "Updating fancy-frame installation from ${PROJECT_ROOT} ..."

ensure_api_auth_user

# ------------------------------------------------------------------
# 1. Stop services gracefully before replacing files
# ------------------------------------------------------------------
echo "Stopping services..."
for unit in \
  fancy-frame.service \
  fancy-frame-api.service \
  fancy-frame-setup-portal.service \
  photo-frame.service \
  photo-frame-api.service \
  photo-frame-setup-portal.service; do
  systemctl stop "${unit}" >/dev/null 2>&1 || true
done

# ------------------------------------------------------------------
# 2. Copy updated app files
# ------------------------------------------------------------------
echo "Copying updated app files to ${INSTALL_ROOT} ..."
for dir in scripts portal api config systemd; do
  rm -rf "${INSTALL_ROOT:?}/${dir}"
  cp -a "${PROJECT_ROOT}/${dir}" "${INSTALL_ROOT}/"
done
install -m 0644 "${PROJECT_ROOT}/VERSION" "${INSTALL_ROOT}/VERSION"
chmod +x "${INSTALL_ROOT}"/scripts/*.sh

if [[ -d "${LEGACY_INSTALL_ROOT}" ]] && [[ ! -L "${LEGACY_INSTALL_ROOT}" ]]; then
  rm -rf "${LEGACY_INSTALL_ROOT}"
fi
ln -sfn "${INSTALL_ROOT}" "${LEGACY_INSTALL_ROOT}"

# ------------------------------------------------------------------
# 3. Install updated systemd unit files
# ------------------------------------------------------------------
echo "Installing updated systemd unit files..."
for legacy_unit in \
  photo-frame.service \
  photo-frame-wifi-bootstrap.service \
  photo-frame-setup-mode.service \
  photo-frame-setup-portal.service \
  photo-frame-api.service; do
  systemctl disable "${legacy_unit}" >/dev/null 2>&1 || true
  rm -f "/etc/systemd/system/${legacy_unit}"
done

install -m 0644 "${INSTALL_ROOT}/systemd/fancy-frame.service"                /etc/systemd/system/fancy-frame.service
install -m 0644 "${INSTALL_ROOT}/systemd/fancy-frame-wifi-bootstrap.service" /etc/systemd/system/fancy-frame-wifi-bootstrap.service
install -m 0644 "${INSTALL_ROOT}/systemd/fancy-frame-setup-mode.service"     /etc/systemd/system/fancy-frame-setup-mode.service
install -m 0644 "${INSTALL_ROOT}/systemd/fancy-frame-setup-portal.service"   /etc/systemd/system/fancy-frame-setup-portal.service
install -m 0644 "${INSTALL_ROOT}/systemd/fancy-frame-api.service"            /etc/systemd/system/fancy-frame-api.service

if [[ ! -f /etc/fancy-frame-api.env ]]; then
  cat > /etc/fancy-frame-api.env <<'EOF'
# Optional GitHub fine-grained PAT used by /api/update-check.
# This value is not bundled into releases. Set it locally on the Pi.
# If you store the PAT in a GitHub repository secret, use the same value here.
RELEASESPAT=
EOF
  chmod 0600 /etc/fancy-frame-api.env
fi

# ------------------------------------------------------------------
# 4. Install updated Avahi mDNS advertisement
# ------------------------------------------------------------------
echo "Installing updated Avahi mDNS advertisement..."
rm -f /etc/avahi/services/photo-frame.service
install -m 0644 "${INSTALL_ROOT}/config/avahi-fancy-frame.service" /etc/avahi/services/fancy-frame.service

# ------------------------------------------------------------------
# 5. Reload systemd and restart services
# ------------------------------------------------------------------
echo "Reloading systemd daemon..."
systemctl daemon-reload
systemctl enable fancy-frame.service fancy-frame-wifi-bootstrap.service fancy-frame-api.service >/dev/null 2>&1 || true

echo "Restarting services..."
systemctl restart fancy-frame-api.service || true
systemctl try-restart fancy-frame-setup-portal.service || true
systemctl restart fancy-frame.service

echo
echo "Update complete."
echo "The slideshow and API have been restarted with the new version."
