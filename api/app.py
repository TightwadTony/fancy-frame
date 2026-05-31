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
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shlex
import socket
import subprocess
import threading
import time
import textwrap
import uuid
from functools import wraps
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
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

APP_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = Path('/srv/photos/fancy-frame.conf')
PHOTOS_DIR = Path('/srv/photos')
HW_PROFILE_FILE = Path('/etc/fancy-frame-hw-profile')
THUMBNAIL_CACHE_DIR = Path('/var/lib/fancy-frame/thumb-cache')
VERSION_FILE = APP_ROOT / 'VERSION'
UPDATE_LOCK_FILE = Path('/var/lib/fancy-frame/update.lock')
UPDATE_LOG_FILE = Path('/var/log/fancy-frame-update.log')
SMB_CONF       = Path('/etc/samba/smb.conf')
SMB_SHARE_NAME = 'photos'
THUMBNAIL_LOCK = threading.Semaphore(1)
API_AUTH_USER = 'fancy-frame-api'
API_TOKEN_STORE = Path('/var/lib/fancy-frame/api-auth-tokens.json')
API_PASSWORD_HASH_FILE = Path('/etc/fancy-frame-api-password.hash')
TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60

GITHUB_REPO = 'TightwadTony/fancy-frame'
GITHUB_LATEST_RELEASE_URL = f'https://api.github.com/repos/{GITHUB_REPO}/releases/latest'
UPDATE_UNIT_PREFIX = 'fancy-frame-self-update'

DEFAULT_TRANSITION_NAMES = ('crossfade', 'fade_to_black', 'wipe')
PI45_TRANSITION_NAMES = ('slide', 'cover')
VALID_TRANSITIONS = set(DEFAULT_TRANSITION_NAMES) | set(PI45_TRANSITION_NAMES)
VALID_IMAGE_EXTS  = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tif', '.tiff'}

CONFIG_DEFAULTS: dict[str, str] = {
    'frame_name':         'Fancy Frame',
    'slide_seconds':      '25',
    'fade_seconds':       '1.5',
    'transitions':        ', '.join(DEFAULT_TRANSITION_NAMES),
    'ken_burns':          'yes',
    'ken_burns_zoom_min': '1.02',
    'ken_burns_zoom_max': '1.20',
}

THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
API_TOKEN_STORE.parent.mkdir(parents=True, exist_ok=True)


def _read_token_store() -> list[dict]:
    try:
        raw = json.loads(API_TOKEN_STORE.read_text())
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(raw, list):
        return []

    tokens: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        token_hash = item.get('token_hash')
        expires_at = item.get('expires_at')
        if not isinstance(token_hash, str) or not token_hash:
            continue
        if not isinstance(expires_at, int):
            continue
        tokens.append({'token_hash': token_hash, 'expires_at': expires_at})
    return tokens


def _write_token_store(tokens: list[dict]) -> None:
    tmp_path = API_TOKEN_STORE.with_name(f'.{API_TOKEN_STORE.name}.{os.getpid()}.tmp')
    try:
        tmp_path.write_text(json.dumps(tokens))
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, API_TOKEN_STORE)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def _prune_expired_tokens(tokens: list[dict], now: int | None = None) -> list[dict]:
    current = int(time.time()) if now is None else now
    return [entry for entry in tokens if int(entry.get('expires_at', 0)) > current]


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()


def _hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 260000)
    return salt.hex(), digest.hex()


def _read_password_hash_record() -> dict | None:
    try:
        raw = json.loads(API_PASSWORD_HASH_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(raw, dict):
        return None

    algo = raw.get('algo')
    iterations = raw.get('iterations')
    salt = raw.get('salt')
    password_hash = raw.get('hash')
    if algo != 'pbkdf2_sha256':
        return None
    if not isinstance(iterations, int) or iterations <= 0:
        return None
    if not isinstance(salt, str) or not salt:
        return None
    if not isinstance(password_hash, str) or not password_hash:
        return None
    return raw


def _write_password_hash(password: str) -> None:
    salt_hex, digest_hex = _hash_password(password)
    payload = {
        'algo': 'pbkdf2_sha256',
        'iterations': 260000,
        'salt': salt_hex,
        'hash': digest_hex,
    }

    tmp_path = API_PASSWORD_HASH_FILE.with_name(f'.{API_PASSWORD_HASH_FILE.name}.{os.getpid()}.tmp')
    try:
        tmp_path.write_text(json.dumps(payload))
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, API_PASSWORD_HASH_FILE)
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def _extract_bearer_token() -> str | None:
    header = request.headers.get('Authorization', '').strip()
    if not header:
        return None
    if not header.lower().startswith('bearer '):
        return None
    token = header[7:].strip()
    return token or None


def _is_valid_auth_token(raw_token: str) -> bool:
    now = int(time.time())
    token_hash = _hash_token(raw_token)
    tokens = _read_token_store()
    active = _prune_expired_tokens(tokens, now=now)
    changed = len(active) != len(tokens)

    valid = False
    for entry in active:
        if hmac.compare_digest(entry.get('token_hash', ''), token_hash):
            valid = True
            break

    if changed:
        _write_token_store(active)

    return valid


def _issue_auth_token() -> tuple[str, int]:
    now = int(time.time())
    expires_at = now + TOKEN_TTL_SECONDS
    raw_token = secrets.token_urlsafe(32)

    tokens = _prune_expired_tokens(_read_token_store(), now=now)
    tokens.append({'token_hash': _hash_token(raw_token), 'expires_at': expires_at})
    _write_token_store(tokens)
    return raw_token, expires_at


def _revoke_all_tokens() -> None:
    _write_token_store([])


def _verify_api_user_password(password: str) -> bool:
    record = _read_password_hash_record()
    if record is None:
        logger.error('API password hash file is missing or invalid: %s', API_PASSWORD_HASH_FILE)
        return False

    salt = str(record['salt'])
    expected = str(record['hash'])
    _, calculated = _hash_password(password, salt)
    return hmac.compare_digest(calculated, expected)


def require_auth(fn):
    @wraps(fn)
    def _wrapped(*args, **kwargs):
        token = _extract_bearer_token()
        if not token or not _is_valid_auth_token(token):
            return jsonify({'error': 'Unauthorized'}), 401
        return fn(*args, **kwargs)

    return _wrapped

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


def _read_hw_profile() -> str:
    try:
        return HW_PROFILE_FILE.read_text().strip()
    except OSError:
        return ''


def _default_transition_names(hw_profile: str | None = None) -> tuple[str, ...]:
    profile = hw_profile if hw_profile is not None else _read_hw_profile()
    if profile == 'pi45':
        return PI45_TRANSITION_NAMES
    return DEFAULT_TRANSITION_NAMES


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
    default_transition_names = _default_transition_names()
    default_transition_value = ', '.join(default_transition_names)
    transition_value = raw.get('transitions', default_transition_value)
    transitions = [t.strip() for t in transition_value.split(',') if t.strip() in VALID_TRANSITIONS]
    if not transitions:
        logger.warning('No valid transitions in config; falling back to hardware defaults')
        transitions = list(default_transition_names)

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


def _read_local_version_file() -> str | None:
    try:
        version = VERSION_FILE.read_text().strip()
    except OSError:
        return None
    return version or None


def _read_local_git_version() -> str | None:
    try:
        result = subprocess.run(
            ['git', '-C', str(APP_ROOT), 'describe', '--tags', '--always', '--dirty'],
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None

    version = result.stdout.strip()
    return version or None


def _get_local_release_version() -> tuple[str | None, str | None]:
    version = _read_local_version_file()
    if version:
        return version, 'version_file'

    version = _read_local_git_version()
    if version:
        return version, 'git'

    return None, None


def _parse_release_version(value: str | None) -> tuple[int, int, int] | None:
    if not value:
        return None

    match = re.match(r'^v?(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$', value.strip())
    if not match:
        return None

    return tuple(int(group) for group in match.groups())


def _compare_release_versions(current_version: str | None, latest_version: str | None) -> str:
    if not current_version or not latest_version:
        return 'unknown'

    if current_version == latest_version:
        return 'current'

    current_semver = _parse_release_version(current_version)
    latest_semver = _parse_release_version(latest_version)
    if current_semver is None or latest_semver is None:
        return 'unknown'

    if current_semver < latest_semver:
        return 'update_available'
    if current_semver > latest_semver:
        return 'ahead'
    return 'current'


def _github_api_headers() -> dict[str, str]:
    headers = {
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'fancy-frame-api',
        'X-GitHub-Api-Version': '2022-11-28',
    }

    token = os.environ.get('RELEASESPAT', '').strip()
    if token:
        headers['Authorization'] = f'Bearer {token}'

    return headers


def _extract_release_info(payload: dict) -> dict:
    tag_name = payload.get('tag_name')
    if not isinstance(tag_name, str) or not tag_name.strip():
        raise RuntimeError('GitHub API response did not include a valid tag_name')

    release_api_url = payload.get('url')
    if not isinstance(release_api_url, str) or not release_api_url:
        raise RuntimeError('GitHub API response did not include a valid release url')

    assets = []
    for asset in payload.get('assets', []):
        if not isinstance(asset, dict):
            continue
        name = asset.get('name')
        api_url = asset.get('url')
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(api_url, str) or not api_url:
            continue
        assets.append({
            'name': name,
            'api_url': api_url,
            'size': asset.get('size'),
        })

    return {
        'tag_name': tag_name.strip(),
        'name': payload.get('name'),
        'api_url': release_api_url,
        'published_at': payload.get('published_at'),
        'prerelease': bool(payload.get('prerelease', False)),
        'draft': bool(payload.get('draft', False)),
        'assets': assets,
    }


def _fetch_latest_release() -> dict:
    request = Request(
        GITHUB_LATEST_RELEASE_URL,
        headers=_github_api_headers(),
    )

    try:
        with urlopen(request, timeout=5) as response:
            payload = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace').strip()
        raise RuntimeError(f'GitHub API returned HTTP {exc.code}: {detail or exc.reason}') from exc
    except URLError as exc:
        raise RuntimeError(f'Could not reach GitHub API: {exc.reason}') from exc
    except TimeoutError as exc:
        raise RuntimeError('Timed out while contacting GitHub API') from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError('GitHub API returned invalid JSON') from exc

    return _extract_release_info(payload)


def _find_release_archive_asset(release: dict) -> dict | None:
    tag_name = release['tag_name']
    expected_name = f'fancy-frame-{tag_name}.tar.gz'
    for asset in release.get('assets', []):
        if asset.get('name') == expected_name:
            return asset

    for asset in release.get('assets', []):
        name = asset.get('name', '')
        if isinstance(name, str) and name.startswith('fancy-frame-') and name.endswith('.tar.gz'):
            return asset

    return None


def _read_update_lock() -> dict | None:
    try:
        return json.loads(UPDATE_LOCK_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _write_update_lock(lock_data: dict) -> None:
    with UPDATE_LOCK_FILE.open('x') as lock_file:
        json.dump(lock_data, lock_file)


def _update_unit_is_active(unit_name: str) -> bool:
    if not unit_name:
        return False

    try:
        result = subprocess.run(
            ['systemctl', 'is-active', unit_name],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False

    return result.stdout.strip() in {'active', 'activating', 'reloading'}


def _acquire_update_lock(lock_data: dict) -> tuple[bool, dict | None]:
    try:
        _write_update_lock(lock_data)
        return True, lock_data
    except FileExistsError:
        pass
    except OSError:
        existing = _read_update_lock()
        return False, existing

    existing = _read_update_lock()
    if existing and _update_unit_is_active(str(existing.get('unit_name', ''))):
        return False, existing

    try:
        UPDATE_LOCK_FILE.unlink(missing_ok=True)
        _write_update_lock(lock_data)
    except OSError:
        existing = _read_update_lock()
        return False, existing

    return True, lock_data


def _release_update_lock() -> None:
    try:
        UPDATE_LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        logger.warning('Could not remove update lock file %s', UPDATE_LOCK_FILE)


def _read_update_log_tail(max_lines: int = 20) -> list[str]:
    try:
        lines = UPDATE_LOG_FILE.read_text().splitlines()
    except OSError:
        return []
    return lines[-max_lines:]


def _current_update_status() -> dict:
    lock_data = _read_update_lock()
    unit_name = str(lock_data.get('unit_name', '')) if lock_data else ''
    is_running = _update_unit_is_active(unit_name)

    if lock_data and not is_running:
        _release_update_lock()
        lock_data = None
        unit_name = ''

    return {
        'status': 'installing' if is_running else 'idle',
        'in_progress': is_running,
        'active_update': lock_data,
        'unit_name': unit_name or None,
        'log_path': str(UPDATE_LOG_FILE),
        'log_exists': UPDATE_LOG_FILE.exists(),
        'log_tail': _read_update_log_tail(),
        'checked_at': int(time.time()),
    }


def _build_install_latest_script(release: dict, asset: dict) -> str:
    tag_name = release['tag_name']
    asset_name = asset['name']
    asset_api_url = asset['api_url']
    extracted_dir = f'fancy-frame-{tag_name}'

    return textwrap.dedent(
        f"""\
        set -euo pipefail
        exec >> {shlex.quote(str(UPDATE_LOG_FILE))} 2>&1

        echo "=== $(date -Is) starting Fancy Frame self-update to {tag_name} ==="
        source /etc/fancy-frame-api.env || true

        work_dir=$(mktemp -d /tmp/fancy-frame-update.XXXXXX)
        trap 'rm -f {shlex.quote(str(UPDATE_LOCK_FILE))}; rm -rf "$work_dir"' EXIT

        archive="$work_dir"/{shlex.quote(asset_name)}
        extract_dir="$work_dir"/{shlex.quote(extracted_dir)}

        python3 - {shlex.quote(asset_api_url)} "$archive" "${{RELEASESPAT:-}}" <<'PY'
import shutil
import sys
from urllib.request import Request, urlopen

asset_url, archive_path, token = sys.argv[1], sys.argv[2], sys.argv[3].strip()
headers = {{
    'Accept': 'application/octet-stream',
    'User-Agent': 'fancy-frame-updater',
    'X-GitHub-Api-Version': '2022-11-28',
}}
if token:
    headers['Authorization'] = 'token ' + token

request = Request(asset_url, headers=headers)
with urlopen(request, timeout=120) as response, open(archive_path, 'wb') as archive_file:
    shutil.copyfileobj(response, archive_file)
PY

        tar xzf "$archive" -C "$work_dir"
        cd "$extract_dir"
        bash scripts/update.sh
        echo "=== $(date -Is) finished Fancy Frame self-update to {tag_name} ==="
        """
    )


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
@require_auth
def get_config():
    """Return current slideshow configuration."""
    raw = _read_raw_config()
    return jsonify(_config_to_dict(raw))


@app.route('/api/update-check')
def get_update_check():
    """Return GitHub latest release info and whether this frame is behind it."""
    current_version, current_version_source = _get_local_release_version()

    try:
        latest_release = _fetch_latest_release()
    except RuntimeError as exc:
        logger.warning('Release check failed: %s', exc)
        return jsonify({'error': str(exc), 'repository': GITHUB_REPO}), 503

    comparison = _compare_release_versions(current_version, latest_release['tag_name'])
    update_available = comparison == 'update_available'
    if comparison == 'unknown':
        update_available = None

    return jsonify({
        'repository': GITHUB_REPO,
        'current_version': current_version,
        'current_version_source': current_version_source,
        'latest_release': latest_release,
        'comparison': comparison,
        'update_available': update_available,
        'checked_at': int(time.time()),
    })


@app.route('/api/update', methods=['POST'])
@require_auth
def install_latest_update():
    """Download and install the latest released version in a detached updater unit."""
    current_version, current_version_source = _get_local_release_version()

    try:
        latest_release = _fetch_latest_release()
    except RuntimeError as exc:
        logger.warning('Update install check failed: %s', exc)
        return jsonify({'error': str(exc), 'repository': GITHUB_REPO}), 503

    comparison = _compare_release_versions(current_version, latest_release['tag_name'])
    if comparison == 'current':
        return jsonify({
            'status': 'already_current',
            'repository': GITHUB_REPO,
            'current_version': current_version,
            'current_version_source': current_version_source,
            'latest_version': latest_release['tag_name'],
        }), 200

    if comparison == 'ahead':
        return jsonify({
            'error': 'Current version is newer than the latest release; refusing automatic downgrade',
            'repository': GITHUB_REPO,
            'current_version': current_version,
            'latest_version': latest_release['tag_name'],
        }), 409

    asset = _find_release_archive_asset(latest_release)
    if asset is None:
        return jsonify({
            'error': 'Latest release does not include a fancy-frame tar.gz asset',
            'repository': GITHUB_REPO,
            'latest_version': latest_release['tag_name'],
        }), 503

    unit_name = f'{UPDATE_UNIT_PREFIX}-{int(time.time())}'
    lock_data = {
        'unit_name': unit_name,
        'target_version': latest_release['tag_name'],
        'created_at': int(time.time()),
    }
    lock_acquired, existing_lock = _acquire_update_lock(lock_data)
    if not lock_acquired:
        return jsonify({
            'status': 'install_in_progress',
            'repository': GITHUB_REPO,
            'current_version': current_version,
            'latest_version': latest_release['tag_name'],
            'existing_update': existing_lock,
        }), 409

    script = _build_install_latest_script(latest_release, asset)
    try:
        result = subprocess.run(
            [
                'systemd-run',
                '--unit', unit_name,
                '--collect',
                '--no-block',
                '--property=Type=oneshot',
                '/bin/bash',
                '-lc',
                script,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        _release_update_lock()
        logger.exception('Could not launch updater unit %s', unit_name)
        return jsonify({'error': f'Could not launch updater: {exc}'}), 500

    if result.returncode != 0:
        _release_update_lock()
        stderr = result.stderr.strip() or result.stdout.strip() or 'unknown error'
        logger.error('Updater unit launch failed for %s: %s', unit_name, stderr)
        return jsonify({'error': f'Failed to launch updater: {stderr}'}), 500

    return jsonify({
        'status': 'installing',
        'repository': GITHUB_REPO,
        'current_version': current_version,
        'current_version_source': current_version_source,
        'target_version': latest_release['tag_name'],
        'unit_name': unit_name,
        'log_path': str(UPDATE_LOG_FILE),
    }), 202


@app.route('/api/update/status')
def get_update_status():
    """Return whether a self-update is currently running and where to read logs."""
    current_version, current_version_source = _get_local_release_version()
    status = _current_update_status()
    status.update({
        'repository': GITHUB_REPO,
        'current_version': current_version,
        'current_version_source': current_version_source,
    })
    return jsonify(status)


@app.route('/api/config', methods=['PATCH'])
@require_auth
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
@require_auth
def upload_photo():
    """
    Accept multipart photo uploads and save them to /srv/photos/.
    Supported field names: 'photo', 'photos', and 'photos[]'.
    Returns 201 with saved filenames on success, or a detailed error payload
    when no files can be saved.
    """
    uploaded: list = []
    for field in ('photo', 'photos', 'photos[]'):
        uploaded.extend(request.files.getlist(field))

    if not uploaded:
        uploaded = list(request.files.values())

    if not uploaded:
        return jsonify({'error': 'No photo field in request'}), 400

    saved: list[str] = []
    errors: list[dict[str, str]] = []

    for file in uploaded:
        if not file.filename:
            errors.append({'filename': '', 'error': 'Empty filename'})
            continue

        ext = Path(file.filename).suffix.lower()
        if ext not in VALID_IMAGE_EXTS:
            errors.append({'filename': file.filename, 'error': f'Unsupported file type: {ext}'})
            continue

        # Sanitize: strip any path components, replace spaces
        base = secure_filename(file.filename)
        if not base:
            base = f'photo_{uuid.uuid4().hex}{ext}'

        dest = PHOTOS_DIR / base
        # Avoid collisions by appending a short unique suffix if needed
        if dest.exists():
            stem = Path(base).stem
            dest = PHOTOS_DIR / f'{stem}_{uuid.uuid4().hex[:8]}{ext}'

        try:
            file.save(dest)
            os.chmod(dest, 0o666)
            saved.append(dest.name)
        except OSError as exc:
            logger.exception('Failed saving uploaded photo %s', file.filename)
            errors.append({'filename': file.filename, 'error': f'Failed to save file: {exc}'})

    if not saved:
        return jsonify({'error': 'No photos were uploaded', 'details': errors}), 422

    if len(saved) == 1 and not errors:
        return jsonify({'filename': saved[0]}), 201

    payload = {'filenames': saved, 'uploaded_count': len(saved)}
    if errors:
        payload['errors'] = errors
    return jsonify(payload), 201


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
@require_auth
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
@require_auth
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
@require_auth
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
@require_auth
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
@require_auth
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
@require_auth
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


@app.route('/api/auth/token', methods=['POST'])
def create_auth_token():
    """
    Exchange the fancy-frame-api account password for an API auth token.
    Token lifetime is 30 days.
    """
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({'error': 'Request body must be a JSON object'}), 400

    password = data.get('password', '')
    if not isinstance(password, str) or not password:
        return jsonify({'error': 'password is required'}), 422

    if not _verify_api_user_password(password):
        return jsonify({'error': 'Invalid credentials'}), 401

    token, expires_at = _issue_auth_token()
    return jsonify({
        'token': token,
        'token_type': 'Bearer',
        'expires_in': TOKEN_TTL_SECONDS,
        'expires_at': expires_at,
    })


@app.route('/api/auth/password', methods=['POST'])
@require_auth
def change_api_auth_password():
    """
    Change the fancy-frame-api account password and revoke all existing tokens.

    JSON body:
      current_password (string)
      new_password     (string)
    """
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({'error': 'Request body must be a JSON object'}), 400

    current_password = data.get('current_password', '')
    new_password = data.get('new_password', '')

    if not isinstance(current_password, str) or not current_password:
        return jsonify({'error': 'current_password is required'}), 422
    if not isinstance(new_password, str) or not new_password:
        return jsonify({'error': 'new_password is required'}), 422
    if len(new_password) < 4:
        return jsonify({'error': 'new_password must be at least 4 characters'}), 422

    if not _verify_api_user_password(current_password):
        return jsonify({'error': 'Current password is incorrect'}), 401

    system_password_synced = True
    try:
        result = subprocess.run(
            ['chpasswd'],
            input=f'{API_AUTH_USER}:{new_password}\n',
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if result.returncode != 0:
            system_password_synced = False
            logger.warning('chpasswd failed for %s: %s', API_AUTH_USER, result.stderr.strip())
    except (FileNotFoundError, subprocess.SubprocessError):
        system_password_synced = False
        logger.exception('Failed invoking chpasswd for API auth user')

    try:
        _write_password_hash(new_password)
    except OSError:
        logger.exception('Failed writing API password hash file')
        return jsonify({'error': 'Failed to persist password change'}), 500

    _revoke_all_tokens()
    return jsonify({
        'status': 'ok',
        'tokens_revoked': True,
        'system_password_synced': system_password_synced,
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=False)
