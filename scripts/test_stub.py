#!/usr/bin/env python3
"""
Photo Frame Test Stub

Advertises multiple fake Photo Frames on the local network via mDNS and serves
mock API responses for testing the iOS app with various frame counts.

Usage:
    python3 test_stub.py                    # Defaults to 3 frames
    python3 test_stub.py --frames 5         # Simulate 5 frames
    python3 test_stub.py --frames 1         # Single frame
    python3 test_stub.py --help             # Show options

Each frame advertises on the _photoframe._tcp service and runs a simple HTTP API
that responds to the iOS app's requests.

Press Ctrl+C to stop.
"""

import argparse
import json
import logging
import os
import re
import socket
import sys
import threading
import time
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import quote, unquote, urlparse

try:
    from zeroconf import ServiceInfo, Zeroconf
except ImportError:
    print(
        "zeroconf library not found. Install with:\n"
        "  pip install -r requirements-test-stub.txt\n"
    )
    sys.exit(1)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s"
)
logger = logging.getLogger("photo-frame-test")

VALID_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
SAMPLE_IMAGE_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02\x00\x00\x00\x0bIDATx\xdac\xfc\xff\x1f"
    b"\x00\x03\x03\x02\x00\xef\x93\xed\xdd\x00\x00\x00\x00IEND\xaeB`\x82"
)


class MockPhotoFrame:
    """Simulated photo frame with mutable config."""

    def __init__(self, frame_id: int, hostname: str, ip_address: str, port: int):
        self.frame_id = frame_id
        self.hostname = hostname
        self.ip_address = ip_address
        self.port = port
        self.uptime_secs = 86400.0 + (frame_id * 3600)  # Stagger uptimes

        self.config = {
            "frame_name": f"Test Frame {frame_id}",
            "slide_seconds": 25.0,
            "fade_seconds": 1.5,
            "transitions": ["crossfade", "fade_to_black", "wipe"],
            "ken_burns": True,
            "ken_burns_zoom_min": 1.02,
            "ken_burns_zoom_max": 1.20,
        }

        self.photos = [
            self._make_photo_record(f"photo_{i:03d}.jpg", version=f"v{i}")
            for i in range(1, 6)
        ]

    def _make_photo_record(self, filename: str, version: str | None = None) -> dict[str, Any]:
        safe_name = Path(filename).name.strip() or f"photo_{self.frame_id}_{int(time.time())}.jpg"
        photo_version = version or f"v{time.time_ns()}"
        return {
            "filename": safe_name,
            "version": photo_version,
            "thumbnail_url": (
                f"http://{self.ip_address}:{self.port}/api/photos/"
                f"{quote(safe_name, safe='')}?thumbnail=1&version={quote(photo_version, safe='')}"
            ),
        }

    def add_photo(self, filename: str | None = None) -> dict[str, Any]:
        suggested = Path(filename or f"upload_{self.frame_id}_{int(time.time())}.jpg").name.strip()
        if not suggested:
            raise ValueError("Invalid filename")

        stem = Path(suggested).stem or f"upload_{self.frame_id}_{int(time.time())}"
        suffix = Path(suggested).suffix.lower() or ".jpg"
        if suffix not in VALID_IMAGE_EXTS:
            raise ValueError(f"Unsupported file type: {suffix}")

        safe_name = f"{stem}{suffix}"
        if any(photo["filename"] == safe_name for photo in self.photos):
            safe_name = f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"

        photo = self._make_photo_record(safe_name)
        self.photos.append(photo)
        return photo

    def has_photo(self, filename: str) -> bool:
        return any(photo["filename"] == filename for photo in self.photos)

    def delete_photo(self, filename: str) -> bool:
        for index, photo in enumerate(self.photos):
            if photo["filename"] == filename:
                del self.photos[index]
                return True
        return False

    def get_info(self) -> dict[str, Any]:
        """Return /api/info response."""
        return {
            "hostname": self.hostname,
            "ip_address": self.ip_address,
            "uptime_secs": self.uptime_secs,
        }

    def get_config(self) -> dict[str, Any]:
        """Return /api/config response."""
        return self.config

    def patch_config(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Update config and return the new config."""
        # Simple validation: frame_name must be string, non-empty, <=64 chars
        if "frame_name" in updates:
            name = updates["frame_name"]
            if not isinstance(name, str) or not name.strip() or len(name) > 64:
                raise ValueError("Invalid frame_name")
            self.config["frame_name"] = name.strip()

        # Update other fields (simple merge)
        for key in ("slide_seconds", "fade_seconds", "transitions", "ken_burns", "ken_burns_zoom_min", "ken_burns_zoom_max"):
            if key in updates:
                self.config[key] = updates[key]

        return self.config

    def get_photos(self) -> dict[str, Any]:
        """Return /api/photos response."""
        return {"count": len(self.photos)}

    def get_photo_list(self) -> dict[str, Any]:
        """Return /api/photos/list response."""
        return {"photos": self.photos}


class MockAPIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for mock Photo Frame API."""

    # Class attribute to map endpoint to frame instance
    frames: dict[int, MockPhotoFrame] = {}
    frame_for_port: dict[int, int] = {}  # port -> frame_id

    def log_message(self, format_str: str, *args: Any) -> None:
        """Log HTTP requests to our logger."""
        logger.info(f"[Frame {self.frame_for_port.get(self.server.server_port, '?')}] {format_str % args}")

    def do_GET(self) -> None:
        """Handle GET requests."""
        frame_id = self.frame_for_port.get(self.server.server_port)
        frame = self.frames.get(frame_id) if frame_id else None

        if not frame:
            self.error_404()
            return

        path = urlparse(self.path).path

        if path == "/api/info":
            self.respond_json(frame.get_info())
        elif path == "/api/config":
            self.respond_json(frame.get_config())
        elif path == "/api/photos":
            self.respond_json(frame.get_photos())
        elif path == "/api/photos/list":
            self.respond_json(frame.get_photo_list())
        elif path.startswith("/api/photos/"):
            filename = unquote(path.removeprefix("/api/photos/"))
            if frame.has_photo(filename):
                self.respond_bytes(SAMPLE_IMAGE_BYTES, content_type="image/png")
            else:
                self.error_404()
        else:
            self.error_404()

    def do_PATCH(self) -> None:
        """Handle PATCH requests."""
        frame_id = self.frame_for_port.get(self.server.server_port)
        frame = self.frames.get(frame_id) if frame_id else None

        if not frame:
            self.error_404()
            return

        if self.path == "/api/config":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length).decode("utf-8")
                updates = json.loads(body)
                new_config = frame.patch_config(updates)
                self.respond_json(new_config)
            except (ValueError, json.JSONDecodeError) as e:
                self.respond_json({"error": str(e)}, status=422)
        else:
            self.error_404()

    def do_POST(self) -> None:
        """Handle POST requests."""
        frame_id = self.frame_for_port.get(self.server.server_port)
        frame = self.frames.get(frame_id) if frame_id else None
        if not frame:
            self.error_404()
            return

        path = urlparse(self.path).path

        if path == "/api/restart":
            # Simulate restart: just return 202 Accepted
            self.send_response(202)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status": "rebooting"}')
        elif path == "/api/photos":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length) if content_length > 0 else b""
                photo = frame.add_photo(self._extract_filename_from_upload(body))
                self.respond_json(photo, status=201)
            except ValueError as error:
                self.respond_json({"error": str(error)}, status=422)
        else:
            self.error_404()

    def do_DELETE(self) -> None:
        """Handle DELETE requests."""
        frame_id = self.frame_for_port.get(self.server.server_port)
        frame = self.frames.get(frame_id) if frame_id else None
        if not frame:
            self.error_404()
            return

        path = urlparse(self.path).path
        if path.startswith("/api/photos/"):
            filename = unquote(path.removeprefix("/api/photos/"))
            if frame.delete_photo(filename):
                self.respond_json({"status": "deleted"}, status=200)
            else:
                self.error_404()
        else:
            self.error_404()

    @staticmethod
    def _extract_filename_from_upload(body: bytes) -> str | None:
        match = re.search(br'filename="([^"]+)"', body)
        if not match:
            return None
        return match.group(1).decode("utf-8", errors="ignore")

    def respond_json(self, data: dict[str, Any], status: int = 200) -> None:
        """Send JSON response."""
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def respond_bytes(self, data: bytes, content_type: str = "application/octet-stream", status: int = 200) -> None:
        """Send a binary response body."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def error_404(self) -> None:
        """Send 404 error."""
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error": "not found"}')


def detect_advertised_ip() -> str:
    """Best-effort detection of the LAN IP to advertise for API reachability."""
    configured = os.getenv("TEST_STUB_ADVERTISED_IP", "").strip()
    if configured:
        return configured

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            if ip:
                return ip
    except OSError:
        pass

    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass

    return "127.0.0.1"


def run_test_frames(num_frames: int, bind_host: str) -> None:
    """
    Advertise num_frames Photo Frames via mDNS and run HTTP servers for each.
    """
    logger.info(f"Starting test stub with {num_frames} frame(s)...")

    frames = {}
    services = []
    servers: list[ThreadingHTTPServer] = []
    threads = []
    zeroconf_instance = Zeroconf()

    advertised_ip = detect_advertised_ip()
    logger.info("Advertising test frames on IP %s", advertised_ip)

    # Start with base port 9000; each frame gets its own port
    base_port = 9000

    try:
        for i in range(1, num_frames + 1):
            frame_id = i
            port = base_port + i - 1
            hostname = f"test-frame-{i}.local"

            # Create mock frame
            frame = MockPhotoFrame(frame_id, hostname, advertised_ip, port)
            frames[frame_id] = frame

            logger.info(f"Frame {frame_id}: '{frame.config['frame_name']}' on port {port}")

            # Set up HTTP server for this frame
            MockAPIHandler.frames = frames
            MockAPIHandler.frame_for_port[port] = frame_id

            server = ThreadingHTTPServer((bind_host, port), MockAPIHandler)
            servers.append(server)

            # Run server in a background thread
            def run_server(srv: ThreadingHTTPServer, port_num: int) -> None:
                logger.info(f"HTTP server listening on port {port_num}")
                srv.serve_forever()

            t = threading.Thread(target=run_server, args=(server, port), daemon=True)
            t.start()
            threads.append(t)

            # Advertise via mDNS
            service_name = f"Photo Frame ({frame.config['frame_name']})"
            service_type = "_photoframe._tcp.local."

            service_info = ServiceInfo(
                service_type,
                f"{service_name}.{service_type}",
                port=port,
                server=f"{hostname}",
                addresses=[socket.inet_aton(advertised_ip)],
                properties={},
            )

            zeroconf_instance.register_service(service_info)
            services.append(service_info)

            logger.info(f"Advertised: {service_name}")

        # Keep running until interrupted
        logger.info("All frames running. Press Ctrl+C to stop.")
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        # Clean up
        for srv in servers:
            srv.shutdown()
        for svc in services:
            zeroconf_instance.unregister_service(svc)
        zeroconf_instance.close()
        logger.info("Stopped.")


def main() -> None:
    default_frames = int(os.getenv("TEST_STUB_FRAMES", "3"))
    default_bind = os.getenv("TEST_STUB_BIND_HOST", "0.0.0.0")

    parser = argparse.ArgumentParser(
        description="Photo Frame Test Stub - simulate multiple frames on local network",
        epilog="Example: python3 test_stub.py --frames 3",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=default_frames,
        help="Number of frames to simulate (default: 3)",
    )
    parser.add_argument(
        "--bind-host",
        default=default_bind,
        help="Host/interface to bind API servers to (default: 0.0.0.0)",
    )
    args = parser.parse_args()

    if args.frames < 1:
        print("Error: --frames must be >= 1", file=sys.stderr)
        sys.exit(1)

    run_test_frames(args.frames, args.bind_host)


if __name__ == "__main__":
    main()
