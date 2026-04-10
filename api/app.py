#!/usr/bin/env python3
"""
Photo Frame Management API
Exposes REST endpoints for reading/writing /srv/photos/photo-frame.conf
and triggering a system restart. Runs on port 8080.

Only started when the Pi is connected to Wi-Fi (not in AP/setup mode).
Advertised via Avahi mDNS as _photoframe._tcp so iOS clients can discover it.
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import time
import uuid
from pathlib import Path
from werkzeug.utils import secure_filename

from flask import Flask, jsonify, request

app = Flask(__name__)

CONFIG_FILE = Path('/srv/photos/photo-frame.conf')
PHOTOS_DIR  = Path('/srv/photos')

VALID_TRANSITIONS = {'crossfade', 'fade_to_black', 'wipe'}
VALID_IMAGE_EXTS  = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tif', '.tiff'}

CONFIG_DEFAULTS: dict[str, str] = {
    'slide_seconds':      '25',
    'fade_seconds':       '1.5',
    'transitions':        'crossfade, fade_to_black, wipe',
    'ken_burns':          'yes',
    'ken_burns_zoom_min': '1.02',
    'ken_burns_zoom_max': '1.20',
}

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _read_raw_config() -> dict[str, str]:
    """Parse key=value lines from config file, ignoring comments and blanks."""
    result: dict[str, str] = {}
    try:
        for line in CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, _, val = line.partition('=')
                result[key.strip()] = val.strip()
    except OSError:
        pass
    return result


def _write_config(values: dict[str, str]) -> None:
    """
    Write values back to the config file, preserving comments and ordering.
    Lines for keys in `values` are updated in-place; unknown keys are appended.
    """
    lines: list[str] = []
    written: set[str] = set()

    if CONFIG_FILE.exists():
        for line in CONFIG_FILE.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith('#') and '=' in stripped:
                key, _, _ = stripped.partition('=')
                key = key.strip()
                if key in values:
                    lines.append(f'{key} = {values[key]}')
                    written.add(key)
                    continue
            lines.append(line)

    # Append any keys that weren't already in the file
    for key, val in values.items():
        if key not in written:
            lines.append(f'{key} = {val}')

    CONFIG_FILE.write_text('\n'.join(lines) + '\n')
    os.chmod(CONFIG_FILE, 0o666)


def _config_to_dict(raw: dict[str, str]) -> dict:
    """Convert raw string config into typed API response dict."""
    merged = {**CONFIG_DEFAULTS, **raw}
    transitions = [t.strip() for t in merged['transitions'].split(',') if t.strip()]
    return {
        'slide_seconds':      float(merged['slide_seconds']),
        'fade_seconds':       float(merged['fade_seconds']),
        'transitions':        transitions,
        'ken_burns':          merged['ken_burns'].lower() in ('yes', '1', 'true'),
        'ken_burns_zoom_min': float(merged['ken_burns_zoom_min']),
        'ken_burns_zoom_max': float(merged['ken_burns_zoom_max']),
    }


def _validate_patch(data: dict) -> list[str]:
    """Return a list of validation error messages (empty = valid)."""
    errors: list[str] = []

    if 'slide_seconds' in data:
        try:
            v = float(data['slide_seconds'])
            if v < 1:
                errors.append('slide_seconds must be >= 1')
        except (TypeError, ValueError):
            errors.append('slide_seconds must be a number')

    if 'fade_seconds' in data:
        try:
            v = float(data['fade_seconds'])
            if v < 0:
                errors.append('fade_seconds must be >= 0')
        except (TypeError, ValueError):
            errors.append('fade_seconds must be a number')

    if 'transitions' in data:
        if not isinstance(data['transitions'], list):
            errors.append('transitions must be an array')
        else:
            unknown = set(data['transitions']) - VALID_TRANSITIONS
            if unknown:
                errors.append(f"unknown transitions: {sorted(unknown)}")
            if not data['transitions']:
                errors.append('transitions must contain at least one value')

    if 'ken_burns' in data:
        if not isinstance(data['ken_burns'], bool):
            errors.append('ken_burns must be a boolean')

    for key in ('ken_burns_zoom_min', 'ken_burns_zoom_max'):
        if key in data:
            try:
                v = float(data[key])
                if v < 1.0:
                    errors.append(f'{key} must be >= 1.0')
            except (TypeError, ValueError):
                errors.append(f'{key} must be a number')

    if 'ken_burns_zoom_min' in data and 'ken_burns_zoom_max' in data:
        try:
            if float(data['ken_burns_zoom_min']) > float(data['ken_burns_zoom_max']):
                errors.append('ken_burns_zoom_min must be <= ken_burns_zoom_max')
        except (TypeError, ValueError):
            pass

    return errors


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route('/api/info')
def get_info():
    """Return basic device information."""
    hostname = socket.gethostname()

    # Read uptime from /proc/uptime (seconds since boot)
    try:
        uptime_secs = float(Path('/proc/uptime').read_text().split()[0])
    except (OSError, ValueError):
        uptime_secs = 0.0

    # Best-effort local IP on wlan0
    ip_address = None
    try:
        result = subprocess.run(
            ['ip', '-4', 'addr', 'show', 'wlan0'],
            capture_output=True, text=True, timeout=3
        )
        match = re.search(r'inet (\d+\.\d+\.\d+\.\d+)', result.stdout)
        if match:
            ip_address = match.group(1)
    except Exception:
        pass

    return jsonify({
        'hostname':    hostname,
        'ip_address':  ip_address,
        'uptime_secs': uptime_secs,
    })


@app.route('/api/config')
def get_config():
    """Return current slideshow configuration."""
    raw = _read_raw_config()
    return jsonify(_config_to_dict(raw))


@app.route('/api/config', methods=['PATCH'])
def patch_config():
    """
    Update one or more configuration values.

    Accepts a JSON body with any subset of:
      slide_seconds (number), fade_seconds (number),
      transitions (array of strings), ken_burns (bool),
      ken_burns_zoom_min (number), ken_burns_zoom_max (number)
    """
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({'error': 'Request body must be a JSON object'}), 400

    errors = _validate_patch(data)
    if errors:
        return jsonify({'errors': errors}), 422

    raw = _read_raw_config()
    merged = {**CONFIG_DEFAULTS, **raw}

    if 'slide_seconds' in data:
        merged['slide_seconds'] = str(float(data['slide_seconds']))
    if 'fade_seconds' in data:
        merged['fade_seconds'] = str(float(data['fade_seconds']))
    if 'transitions' in data:
        merged['transitions'] = ', '.join(data['transitions'])
    if 'ken_burns' in data:
        merged['ken_burns'] = 'yes' if data['ken_burns'] else 'no'
    if 'ken_burns_zoom_min' in data:
        merged['ken_burns_zoom_min'] = str(float(data['ken_burns_zoom_min']))
    if 'ken_burns_zoom_max' in data:
        merged['ken_burns_zoom_max'] = str(float(data['ken_burns_zoom_max']))

    _write_config(merged)
    return jsonify(_config_to_dict(merged))


@app.route('/api/photos', methods=['GET'])
def get_photos():
    """Return the count of photos currently in the photos directory."""
    try:
        count = sum(
            1 for f in PHOTOS_DIR.iterdir()
            if f.suffix.lower() in VALID_IMAGE_EXTS
            and not f.name.startswith('.')
            and not f.name.startswith('._')
        )
    except OSError:
        count = 0
    return jsonify({'count': count})


@app.route('/api/photos', methods=['POST'])
def upload_photo():
    """
    Accept a multipart file upload (field name: 'photo') and save it to
    /srv/photos/. Only image extensions are accepted. Returns 201 with the
    saved filename on success.
    """
    if 'photo' not in request.files:
        return jsonify({'error': 'No photo field in request'}), 400

    file = request.files['photo']
    if not file.filename:
        return jsonify({'error': 'Empty filename'}), 400

    ext = Path(file.filename).suffix.lower()
    if ext not in VALID_IMAGE_EXTS:
        return jsonify({'error': f'Unsupported file type: {ext}'}), 422

    # Sanitize: strip any path components, replace spaces
    base = secure_filename(file.filename)
    if not base:
        base = f'photo_{uuid.uuid4().hex}{ext}'

    dest = PHOTOS_DIR / base
    # Avoid collisions by appending a short unique suffix if needed
    if dest.exists():
        stem = Path(base).stem
        dest = PHOTOS_DIR / f'{stem}_{uuid.uuid4().hex[:8]}{ext}'

    file.save(dest)
    os.chmod(dest, 0o666)

    return jsonify({'filename': dest.name}), 201


@app.route('/api/restart', methods=['POST'])
def restart():
    """Schedule a system reboot (runs after response is sent)."""
    # Fork off the reboot so the response can be delivered first
    subprocess.Popen(['bash', '-c', 'sleep 1 && systemctl reboot'])
    return jsonify({'status': 'rebooting'}), 202


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
