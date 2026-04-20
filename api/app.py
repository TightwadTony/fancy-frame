#!/usr/bin/env python3
"""
Fancy Frame Management API
Exposes REST endpoints for reading and writing the slideshow config
and triggering a system restart. Runs on port 8080.

Only started when the Pi is connected to Wi-Fi, not in AP/setup mode.
Advertised via Avahi mDNS as _fancyframe._tcp so clients can discover it.
"""

from __future__ import annotations

import io
import logging
import os
import re
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote
from werkzeug.utils import secure_filename

from flask import Flask, jsonify, request, send_file, send_from_directory

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover
    Image = None  # type: ignore[misc]
    ImageOps = None  # type: ignore[misc]

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger('fancy-frame-api')

CONFIG_FILE = Path('/srv/photos/fancy-frame.conf')
PHOTOS_DIR = Path('/srv/photos')
THUMBNAIL_CACHE_DIR = Path('/var/lib/fancy-frame/thumb-cache')
SMB_CONF       = Path('/etc/samba/smb.conf')
SMB_SHARE_NAME = 'photos'
THUMBNAIL_LOCK = threading.Semaphore(1)

VALID_TRANSITIONS = {'crossfade', 'fade_to_black', 'wipe'}
VALID_IMAGE_EXTS  = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tif', '.tiff'}

CONFIG_DEFAULTS: dict[str, str] = {
    'frame_name':         'Fancy Frame',
    'slide_seconds':      '25',
    'fade_seconds':       '1.5',
    'transitions':        'crossfade, fade_to_black, wipe',
    'ken_burns':          'yes',
    'ken_burns_zoom_min': '1.02',
    'ken_burns_zoom_max': '1.20',
}

THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

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


def _safe_config_float(raw: dict[str, str], key: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    default = float(CONFIG_DEFAULTS[key])
    value = raw.get(key, CONFIG_DEFAULTS[key])
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        logger.warning('Invalid config value for %s=%r; using default %s', key, value, default)
        parsed = default

    if minimum is not None and parsed < minimum:
        logger.warning('Clamping %s=%s to minimum %s', key, parsed, minimum)
        parsed = minimum
    if maximum is not None and parsed > maximum:
        logger.warning('Clamping %s=%s to maximum %s', key, parsed, maximum)
        parsed = maximum
    return parsed


def _write_config(values: dict[str, str]) -> None:
    """
    Write values back to the config file atomically, preserving comments and ordering.
    Lines for keys in values are updated in-place; unknown keys are appended.
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

    for key, val in values.items():
        if key not in written:
            lines.append(f'{key} = {val}')

    tmp_path = CONFIG_FILE.with_name(f'.{CONFIG_FILE.name}.{os.getpid()}.tmp')
    try:
        tmp_path.write_text('\n'.join(lines) + '\n')
        os.chmod(tmp_path, 0o666)
        os.replace(tmp_path, CONFIG_FILE)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def _config_to_dict(raw: dict[str, str]) -> dict:
    """Convert raw string config into typed API response dict."""
    merged = {**CONFIG_DEFAULTS, **raw}
    transitions = [t.strip() for t in merged['transitions'].split(',') if t.strip() in VALID_TRANSITIONS]
    if not transitions:
        logger.warning('No valid transitions in config; falling back to defaults')
        transitions = [t.strip() for t in CONFIG_DEFAULTS['transitions'].split(',') if t.strip()]

    slide_seconds = _safe_config_float(merged, 'slide_seconds', minimum=1.0)
    fade_seconds = _safe_config_float(merged, 'fade_seconds', minimum=0.0, maximum=slide_seconds)
    zoom_min = _safe_config_float(merged, 'ken_burns_zoom_min', minimum=1.0, maximum=3.0)
    zoom_max = _safe_config_float(merged, 'ken_burns_zoom_max', minimum=zoom_min, maximum=3.0)
    frame_name = merged['frame_name'].strip() or CONFIG_DEFAULTS['frame_name']
    return {
        'frame_name':         frame_name,
        'slide_seconds':      slide_seconds,
        'fade_seconds':       fade_seconds,
        'transitions':        transitions,
        'ken_burns':          merged['ken_burns'].lower() in ('yes', '1', 'true'),
        'ken_burns_zoom_min': zoom_min,
        'ken_burns_zoom_max': zoom_max,
    }


def _validate_patch(data: dict) -> list[str]:
    """Return a list of validation error messages (empty = valid)."""
    errors: list[str] = []

    if 'frame_name' in data:
        if not isinstance(data['frame_name'], str):
            errors.append('frame_name must be a string')
        else:
            value = data['frame_name'].strip()
            if not value:
                errors.append('frame_name must not be empty')
            if len(value) > 64:
                errors.append('frame_name must be <= 64 characters')

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

    try:
        slide_value = float(data.get('slide_seconds', CONFIG_DEFAULTS['slide_seconds']))
        fade_value = float(data.get('fade_seconds', CONFIG_DEFAULTS['fade_seconds']))
        if fade_value > slide_value:
            errors.append('fade_seconds must be <= slide_seconds')
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
            frame_name (string),
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

    if 'frame_name' in data:
        merged['frame_name'] = data['frame_name'].strip()

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


def _sanitize_photo_filename(filename: str) -> str | None:
    """Return a validated basename for a photo filename or None if invalid."""
    if not filename or filename != Path(filename).name:
        return None
    if filename.startswith('.') or filename.startswith('._'):
        return None
    if Path(filename).suffix.lower() not in VALID_IMAGE_EXTS:
        return None
    return filename


def _thumbnail_cache_path(photo_path: Path, max_size: tuple[int, int]) -> Path:
    stat = photo_path.stat()
    key = f"{photo_path.stem}_{stat.st_mtime_ns}_{stat.st_size}_{max_size[0]}x{max_size[1]}.jpg"
    return THUMBNAIL_CACHE_DIR / secure_filename(key)


def _create_thumbnail(photo_path: Path, max_size: tuple[int, int] = (120, 120)) -> bytes | None:
    if Image is None:
        logger.warning('Pillow not available; cannot generate thumbnail for %s', photo_path.name)
        return None

    cache_path = _thumbnail_cache_path(photo_path, max_size)
    try:
        if cache_path.exists():
            return cache_path.read_bytes()
    except OSError:
        logger.warning('Could not read cached thumbnail for %s', photo_path.name)

    try:
        with THUMBNAIL_LOCK:
            if cache_path.exists():
                return cache_path.read_bytes()

            start = time.monotonic()
            with Image.open(photo_path) as image:
                image_format = getattr(image, 'format', 'unknown')
                if image_format == 'JPEG':
                    image.draft('RGB', max_size)
                logger.info(
                    'Generating thumbnail for %s format=%s mode=%s size=%sx%s',
                    photo_path.name,
                    image_format,
                    getattr(image, 'mode', 'unknown'),
                    image.size[0],
                    image.size[1],
                )
                if ImageOps is not None:
                    image = ImageOps.exif_transpose(image)
                image.thumbnail(max_size, Image.LANCZOS)
                if image.mode != 'RGB':
                    image = image.convert('RGB')
                output = io.BytesIO()
                image.save(output, format='JPEG', quality=50, optimize=True)
                data = output.getvalue()

            try:
                cache_path.write_bytes(data)
            except OSError:
                logger.warning('Could not write cached thumbnail for %s', photo_path.name)

            elapsed_ms = int((time.monotonic() - start) * 1000)
            logger.info(
                'Thumbnail ready for %s -> %sx%s in %dms (%d bytes)',
                photo_path.name,
                image.size[0],
                image.size[1],
                elapsed_ms,
                len(data),
            )
            return data
    except Exception:
        logger.exception('Thumbnail generation failed for %s', photo_path.name)
        return None


def _serve_photo(photo_path: Path, thumbnail: bool = False):
    if thumbnail:
        data = _create_thumbnail(photo_path)
        if data is not None:
            return send_file(io.BytesIO(data), mimetype='image/jpeg', as_attachment=False)
        logger.warning('Falling back to original image for %s after thumbnail failure', photo_path.name)
    return send_from_directory(PHOTOS_DIR, photo_path.name)


def _make_photo_url(filename: str, thumbnail: bool = False, version: str | None = None) -> str:
    root = request.url_root.rstrip('/')
    encoded = quote(filename, safe='')
    url = f"{root}/api/photos/{encoded}"
    query: list[str] = []
    if thumbnail:
        query.append('thumbnail=1')
    if version is not None:
        query.append(f"version={quote(version, safe='')}")
    if query:
        url += '?' + '&'.join(query)
    return url


@app.route('/api/photos/list')
def get_photo_list():
    """Return a list of photo filenames currently stored in the photos directory."""
    try:
        photos = []
        for f in sorted(PHOTOS_DIR.iterdir(), key=lambda path: path.name):
            if not f.is_file() or f.suffix.lower() not in VALID_IMAGE_EXTS:
                continue
            if f.name.startswith('.') or f.name.startswith('._'):
                continue
            stat = f.stat()
            version = f"{stat.st_mtime_ns}-{stat.st_size}"
            photos.append({
                'filename': f.name,
                'version': version,
                'thumbnail_url': _make_photo_url(f.name, thumbnail=True, version=version),
            })
    except OSError:
        photos = []
    return jsonify({'photos': photos})


@app.route('/api/photos/<path:filename>')
def get_photo(filename: str):
    safe_name = _sanitize_photo_filename(filename)
    if not safe_name:
        logger.warning('Rejected invalid photo request filename=%r from %s', filename, request.remote_addr)
        return jsonify({'error': 'Invalid filename'}), 400

    photo_path = PHOTOS_DIR / safe_name
    if not photo_path.exists() or not photo_path.is_file():
        logger.warning('Photo not found filename=%s from %s', safe_name, request.remote_addr)
        return jsonify({'error': 'Not found'}), 404

    thumbnail = request.args.get('thumbnail', '').lower() in ('1', 'true', 'yes')
    logger.info(
        'Serving photo filename=%s thumbnail=%s version=%s from=%s',
        safe_name,
        thumbnail,
        request.args.get('version', ''),
        request.remote_addr,
    )
    return _serve_photo(photo_path, thumbnail=thumbnail)


@app.route('/api/photos/<path:filename>', methods=['DELETE'])
def delete_photo(filename: str):
    safe_name = _sanitize_photo_filename(filename)
    if not safe_name:
        return jsonify({'error': 'Invalid filename'}), 400

    photo_path = PHOTOS_DIR / safe_name
    if not photo_path.exists() or not photo_path.is_file():
        return jsonify({'error': 'Not found'}), 404

    try:
        photo_path.unlink()
    except OSError:
        return jsonify({'error': 'Could not delete file'}), 500

    return jsonify({'status': 'deleted'}), 200


@app.route('/api/restart', methods=['POST'])
def restart():
    """Schedule a system reboot (runs after response is sent)."""
    # Fork off the reboot so the response can be delivered first
    subprocess.Popen(['bash', '-c', 'sleep 1 && systemctl reboot'])
    return jsonify({'status': 'rebooting'}), 202


# ---------------------------------------------------------------------------
# Samba helpers
# ---------------------------------------------------------------------------

def _read_samba_settings() -> dict:
    """Parse /etc/samba/smb.conf to get Fancy Frame share settings."""
    result: dict = {'guest_access': False, 'username': None}
    try:
        in_share = False
        valid_users: str | None = None
        force_user:  str | None = None
        for line in SMB_CONF.read_text().splitlines():
            stripped = line.strip()
            if re.match(rf'^\[{re.escape(SMB_SHARE_NAME)}\]\s*$', stripped, re.IGNORECASE):
                in_share = True
                continue
            if in_share and stripped.startswith('['):
                in_share = False
                continue
            if in_share and '=' in stripped and not stripped.startswith('#'):
                key, _, val = stripped.partition('=')
                key = key.strip().lower()
                val = val.strip()
                if key == 'guest ok' and val.lower() in ('yes', 'true', '1'):
                    result['guest_access'] = True
                elif key == 'valid users':
                    valid_users = val
                elif key == 'force user':
                    force_user = val
        # In credentials mode the login account is 'valid users';
        # in guest mode there is no 'valid users' but 'force user' names the OS user.
        result['username'] = valid_users or force_user
    except OSError:
        pass
    return result


def _write_samba_share(guest_access: bool, username: str) -> None:
    """Rewrite the Fancy Frame share block and update map-to-guest in smb.conf."""
    try:
        text = SMB_CONF.read_text()
    except OSError as exc:
        raise RuntimeError(f'Could not read {SMB_CONF}: {exc}') from exc

    if guest_access:
        new_block = (
            '# BEGIN FANCY-FRAME SHARE\n'
            f'[{SMB_SHARE_NAME}]\n'
            '   path = /srv/photos\n'
            '   browseable = yes\n'
            '   read only = no\n'
            '   guest ok = yes\n'
            '   guest only = yes\n'
            f'   force user = {username}\n'
            '   create mask = 0644\n'
            '   directory mask = 0755\n'
            '# END FANCY-FRAME SHARE'
        )
    else:
        new_block = (
            '# BEGIN FANCY-FRAME SHARE\n'
            f'[{SMB_SHARE_NAME}]\n'
            '   path = /srv/photos\n'
            '   browseable = yes\n'
            '   read only = no\n'
            '   create mask = 0644\n'
            '   directory mask = 0755\n'
            f'   valid users = {username}\n'
            '# END FANCY-FRAME SHARE'
        )

    if re.search(r'# BEGIN (?:FANCY|PHOTO)-FRAME SHARE.*?# END (?:FANCY|PHOTO)-FRAME SHARE', text, flags=re.DOTALL):
        text = re.sub(
            r'# BEGIN (?:FANCY|PHOTO)-FRAME SHARE.*?# END (?:FANCY|PHOTO)-FRAME SHARE',
            new_block,
            text,
            flags=re.DOTALL,
        )
    else:
        text += f'\n{new_block}\n'

    # Keep map-to-guest in [global] in sync with guest_access
    if guest_access:
        if re.search(r'^\s*map to guest\s*=', text, re.MULTILINE | re.IGNORECASE):
            # Normalize any existing value to 'Bad User'
            text = re.sub(
                r'^[ \t]*map to guest[ \t]*=[^\n]*',
                '   map to guest = Bad User',
                text,
                flags=re.MULTILINE | re.IGNORECASE,
            )
        else:
            text = re.sub(
                r'(^\s*\[global\]\s*$)',
                r'\1\n   map to guest = Bad User',
                text,
                count=1,
                flags=re.MULTILINE | re.IGNORECASE,
            )
    else:
        # Remove the whole line (including its trailing newline) to preserve formatting
        text = re.sub(r'^[ \t]*map to guest[ \t]*=[^\n]*\n?', '', text,
                      flags=re.MULTILINE | re.IGNORECASE)

    SMB_CONF.write_text(text)


class SambaVerificationError(Exception):
    """Raised when the Samba password cannot be verified due to an operational problem."""


def _verify_samba_password(username: str, password: str) -> bool:
    """Return True if credentials are correct, False if incorrect.

    Raises SambaVerificationError if verification could not be performed
    (smbclient missing, smbd not running, timeout, etc.).
    """
    try:
        env = os.environ.copy()
        env['PASSWD'] = password
        result = subprocess.run(
            ['smbclient', f'//127.0.0.1/{SMB_SHARE_NAME}', '-U', username, '-c', 'quit'],
            capture_output=True,
            timeout=10,
            env=env,
        )
        if result.returncode == 0:
            return True
        stderr = result.stderr.decode(errors='replace').lower()
        if 'nt_status_logon_failure' in stderr or 'nt_status_access_denied' in stderr:
            return False
        raise SambaVerificationError(f'smbclient exited with code {result.returncode}')
    except subprocess.TimeoutExpired as exc:
        raise SambaVerificationError('smbclient timed out') from exc
    except FileNotFoundError as exc:
        raise SambaVerificationError('smbclient is not installed') from exc


def _change_samba_password(username: str, new_password: str) -> None:
    """Change the Samba password for username (must be run as root)."""
    proc = subprocess.run(
        ['smbpasswd', '-s', username],
        input=f'{new_password}\n{new_password}\n',
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        raise RuntimeError(f'smbpasswd failed: {proc.stderr.strip()}')


# ---------------------------------------------------------------------------
# Samba routes
# ---------------------------------------------------------------------------

@app.route('/api/samba')
def get_samba():
    """Return current Samba share settings."""
    return jsonify(_read_samba_settings())


@app.route('/api/samba/password', methods=['POST'])
def change_samba_password():
    """
    Change the Samba share password.

    Requires a JSON body with:
      current_password (string) — the existing password to verify
      new_password     (string) — the desired new password
    """
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({'error': 'Request body must be a JSON object'}), 400

    current_password = data.get('current_password', '')
    new_password     = data.get('new_password', '')

    if not isinstance(current_password, str) or not current_password:
        return jsonify({'error': 'current_password is required'}), 422
    if not isinstance(new_password, str) or not new_password:
        return jsonify({'error': 'new_password is required'}), 422
    if len(new_password) < 6:
        return jsonify({'error': 'new_password must be at least 6 characters'}), 422

    settings = _read_samba_settings()
    if settings.get('guest_access'):
        return jsonify({'error': 'Cannot change password while guest access is enabled'}), 422

    username = settings.get('username')
    if not username:
        return jsonify({'error': 'Could not determine Samba username from configuration'}), 500

    try:
        verified = _verify_samba_password(username, current_password)
    except SambaVerificationError:
        logger.exception('Could not verify Samba password for user %s', username)
        return jsonify({'error': 'Could not verify the current password. Please try again later.'}), 503
    if not verified:
        return jsonify({'error': 'Current password is incorrect'}), 401

    try:
        _change_samba_password(username, new_password)
    except RuntimeError:
        logger.exception('smbpasswd failed for user %s', username)
        return jsonify({'error': 'Failed to change password. Please try again.'}), 500

    return jsonify({'status': 'ok'})


@app.route('/api/samba', methods=['PATCH'])
def patch_samba():
    """
    Update Samba share guest-access setting.

    Accepts a JSON body with:
      guest_access (bool) — true to allow anonymous access, false to require credentials
    """
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({'error': 'Request body must be a JSON object'}), 400

    if 'guest_access' not in data:
        return jsonify({'error': 'guest_access field is required'}), 422
    if not isinstance(data['guest_access'], bool):
        return jsonify({'error': 'guest_access must be a boolean'}), 422

    settings = _read_samba_settings()
    username = settings.get('username')
    if not username:
        return jsonify({'error': 'Could not determine Samba username from configuration'}), 500

    try:
        _write_samba_share(data['guest_access'], username)
        subprocess.run(['systemctl', 'reload', 'smbd'], check=True, timeout=10)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        logger.exception('Failed to update Samba share configuration')
        return jsonify({'error': 'Failed to update share configuration. Please try again.'}), 500

    return jsonify(_read_samba_settings())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
