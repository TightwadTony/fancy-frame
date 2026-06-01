#!/usr/bin/env python3
"""Run the Fancy Frame API in a container and advertise it via mDNS."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from zeroconf import ServiceInfo, Zeroconf

LOG = logging.getLogger("fancy-frame-container")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

APP_ROOT = Path("/app")
API_SCRIPT = APP_ROOT / "api" / "app.py"
CONFIG_PATH = Path("/srv/photos/fancy-frame.conf")
PHOTOS_DIR = Path("/srv/photos")
THUMB_CACHE_DIR = Path("/var/lib/fancy-frame/thumb-cache")
TOKEN_STORE = Path("/var/lib/fancy-frame/api-auth-tokens.json")
API_PASSWORD_HASH = Path("/etc/fancy-frame-api-password.hash")

ITERATIONS = 260000


def _ensure_runtime_files() -> None:
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_STORE.parent.mkdir(parents=True, exist_ok=True)

    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(
            "\n".join(
                [
                    "frame_name = Fancy Frame",
                    "slide_seconds = 25",
                    "fade_seconds = 1.5",
                    "transitions = crossfade, fade_to_black, wipe",
                    "ken_burns = yes",
                    "ken_burns_zoom_min = 1.02",
                    "ken_burns_zoom_max = 1.20",
                    "",
                ]
            )
        )

    if not TOKEN_STORE.exists():
        TOKEN_STORE.write_text("[]\n")

    if not API_PASSWORD_HASH.exists():
        password = os.environ.get("FANCY_FRAME_API_PASSWORD", "1234")
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
        payload = {
            "algo": "pbkdf2_sha256",
            "iterations": ITERATIONS,
            "salt": salt.hex(),
            "hash": digest.hex(),
        }
        API_PASSWORD_HASH.write_text(json.dumps(payload))
        os.chmod(API_PASSWORD_HASH, 0o600)
        LOG.info("Created default API password hash file at %s", API_PASSWORD_HASH)


def _detect_ip() -> str:
    configured = os.environ.get("FANCY_FRAME_ADVERTISED_IP", "").strip()
    if configured:
        return configured

    # Standard UDP trick: no packets are sent, but socket gets the preferred local IP.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]


def _register_mdns(port: int, ip_address: str) -> tuple[Zeroconf, ServiceInfo]:
    service_instance = os.environ.get("FANCY_FRAME_MDNS_INSTANCE", "Fancy Frame").strip() or "Fancy Frame"
    host = os.environ.get("FANCY_FRAME_MDNS_HOST", socket.gethostname()).strip() or socket.gethostname()

    service_type = "_fancyframe._tcp.local."
    service_name = f"{service_instance}.{service_type}"
    server_name = f"{host}.local."

    info = ServiceInfo(
        type_=service_type,
        name=service_name,
        addresses=[socket.inet_aton(ip_address)],
        port=port,
        properties={b"path": b"/api/info"},
        server=server_name,
    )

    zeroconf = Zeroconf()
    zeroconf.register_service(info, allow_name_change=True)
    LOG.info("Advertised mDNS service %s at %s:%d", service_name, ip_address, port)
    return zeroconf, info


def main() -> int:
    _ensure_runtime_files()

    port = int(os.environ.get("FANCY_FRAME_API_PORT", "8080"))
    ip_address = _detect_ip()
    os.environ["FANCY_FRAME_ADVERTISED_IP"] = ip_address

    disable_mdns = os.environ.get("FANCY_FRAME_DISABLE_MDNS", "").strip().lower() in {"1", "true", "yes", "on"}

    zeroconf: Zeroconf | None = None
    service_info: ServiceInfo | None = None
    if disable_mdns:
        LOG.info("Container mDNS advertising disabled (FANCY_FRAME_DISABLE_MDNS is set)")
    else:
        zeroconf, service_info = _register_mdns(port=port, ip_address=ip_address)

    LOG.info("Public endpoint: http://%s:%d", ip_address, port)

    child_env = dict(os.environ)
    child_env.setdefault("PYTHONUNBUFFERED", "1")
    child_env.setdefault("FANCY_FRAME_STUB_MODE", "1")
    child_env.setdefault("FANCY_FRAME_SAMBA_USERNAME", "photo")
    child = subprocess.Popen([sys.executable, str(API_SCRIPT)], env=child_env)

    stop_requested = False

    def _shutdown(signum: int, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True
        LOG.info("Received signal %s, shutting down", signum)
        if child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        while child.poll() is None:
            time.sleep(0.25)
    finally:
        if zeroconf is not None and service_info is not None:
            try:
                zeroconf.unregister_service(service_info)
            except Exception:
                pass
            zeroconf.close()

    if stop_requested and child.returncode not in (0, None):
        return 0
    return child.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
