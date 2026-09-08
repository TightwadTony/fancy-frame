#!/usr/bin/env python3
"""
Fancy Frame slideshow with smooth transitions (crossfade, fade-to-black, wipe).

Reads photos from PHOTO_DIR, displays them fullscreen with a random transition
between each slide, and refreshes the photo list periodically to pick up
newly added images.

Settings are read from CONFIG_FILE at startup and re-checked every
CONFIG_CHECK_SECS seconds; the slideshow exits (systemd restarts it) if the
file changes so the new settings take effect cleanly.
"""

from __future__ import annotations

import io
import os
import sys
import random
import hashlib
import time
import threading
import traceback
import types
from pathlib import Path

import pygame
from PIL import Image, ImageOps

try:
    from OpenGL.GL import (
        GL_BLEND,
        GL_CLAMP_TO_EDGE,
        GL_COLOR_BUFFER_BIT,
        GL_LINEAR,
        GL_MODELVIEW,
        GL_ONE_MINUS_SRC_ALPHA,
        GL_PROJECTION,
        GL_RGB,
        GL_SRC_ALPHA,
        GL_TEXTURE_2D,
        GL_TEXTURE_MAG_FILTER,
        GL_TEXTURE_MIN_FILTER,
        GL_TEXTURE_WRAP_S,
        GL_TEXTURE_WRAP_T,
        GL_TRIANGLE_STRIP,
        GL_UNPACK_ALIGNMENT,
        GL_UNSIGNED_BYTE,
        GL_VENDOR,
        GL_RENDERER,
        GL_VERSION,
        glBegin,
        glBindTexture,
        glBlendFunc,
        glClear,
        glClearColor,
        glColor4f,
        glDeleteTextures,
        glEnable,
        glEnd,
        glGenTextures,
        glGetString,
        glLoadIdentity,
        glMatrixMode,
        glOrtho,
        glPixelStorei,
        glTexCoord2f,
        glTexImage2D,
        glTexParameteri,
        glVertex2f,
        glViewport,
    )
    OPENGL_IMPORT_ERROR: Exception | None = None
except Exception as exc:
    OPENGL_IMPORT_ERROR = exc

# ---------------------------------------------------------------------------
# Fixed constants
# ---------------------------------------------------------------------------

PHOTO_DIR = os.environ.get('FANCY_FRAME_PHOTO_DIR', '/srv/photos')
CONFIG_FILE = os.environ.get('FANCY_FRAME_CONFIG_FILE', '/srv/photos/fancy-frame.conf')
HW_PROFILE_FILE = Path(os.environ.get('FANCY_FRAME_HW_PROFILE_FILE', '/etc/fancy-frame-hw-profile'))
RENDER_CACHE_DIR = Path(os.environ.get('FANCY_FRAME_RENDER_CACHE_DIR', '/var/lib/fancy-frame/render-cache'))
CONFIG_CHECK_SECS = 300   # re-read config every 5 minutes
REFRESH_SECS = int(os.environ.get('FANCY_FRAME_REFRESH_SECONDS', '300'))
HW_PROFILE = ''
try:
    HW_PROFILE = HW_PROFILE_FILE.read_text().strip()
except OSError:
    HW_PROFILE = ''

DEFAULT_FPS = 30 if HW_PROFILE == 'pi45' else 20
FPS = int(os.environ.get('FANCY_FRAME_FPS', str(DEFAULT_FPS)))
DEFAULT_TRANSITION_FPS = 60 if HW_PROFILE == 'pi45' else FPS
TRANSITION_FPS = int(os.environ.get('FANCY_FRAME_TRANSITION_FPS', str(DEFAULT_TRANSITION_FPS)))
DEFAULT_KEN_BURNS_FPS = 60 if HW_PROFILE == 'pi45' else FPS
KEN_BURNS_FPS = int(os.environ.get('FANCY_FRAME_KEN_BURNS_FPS', str(DEFAULT_KEN_BURNS_FPS)))
DEFAULT_KEN_BURNS_MOTION_SECONDS = 18.0 if HW_PROFILE == 'pi45' else 0.0
KEN_BURNS_MOTION_SECONDS = float(
    os.environ.get('FANCY_FRAME_KEN_BURNS_MOTION_SECONDS', str(DEFAULT_KEN_BURNS_MOTION_SECONDS))
)
PI45_STATIC_ZOOM = os.environ.get('FANCY_FRAME_PI45_STATIC_ZOOM', '1').strip().lower() in ('1', 'true', 'yes')
DEFAULT_PI45_FIXED_ZOOM = 1.06
PI45_FIXED_ZOOM = float(os.environ.get('FANCY_FRAME_PI45_FIXED_ZOOM', str(DEFAULT_PI45_FIXED_ZOOM)))
RENDER_QUALITY = int(os.environ.get('FANCY_FRAME_RENDER_QUALITY', '88'))
FRAME_TIMING_ENABLED = os.environ.get('FANCY_FRAME_FRAME_TIMING', '').strip().lower() in ('1', 'true', 'yes')
RENDERER_NAME = os.environ.get('FANCY_FRAME_RENDERER', 'auto').strip().lower()
GPU_FPS = int(os.environ.get('FANCY_FRAME_GPU_FPS', '60'))

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tif', '.tiff'}
BLACK = (0, 0, 0)
DEFAULT_TRANSITION_NAMES = ('crossfade', 'fade_to_black', 'wipe')
PI45_TRANSITION_NAMES = ('slide', 'cover')

RENDER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_RENDER_LOCKS: dict[str, threading.Lock] = {}
_RENDER_LOCKS_GUARD = threading.Lock()


class FrameTimingMonitor:
    """Collect presentation intervals for one animated phase at a time."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self._label = ''
        self._target_interval = 0.0
        self._started = 0.0
        self._previous_present: float | None = None
        self._intervals: list[float] = []

    def begin(self, label: str, target_fps: int) -> None:
        if not self.enabled:
            return
        self._label = label
        self._target_interval = 1.0 / max(1, target_fps)
        self._started = time.monotonic()
        self._previous_present = None
        self._intervals = []

    def presented(self) -> None:
        if not self.enabled or not self._label:
            return
        now = time.monotonic()
        if self._previous_present is not None:
            self._intervals.append(now - self._previous_present)
        self._previous_present = now

    def finish(self) -> None:
        if not self.enabled or not self._label:
            return

        intervals = sorted(self._intervals)
        elapsed = time.monotonic() - self._started
        if intervals:
            p95_index = int((len(intervals) - 1) * 0.95)
            late = sum(value > self._target_interval * 1.5 for value in intervals)
            late_percent = late / len(intervals) * 100.0
            print(
                'slideshow-timing: '
                f'phase={self._label} frames={len(intervals) + 1} elapsed={elapsed:.3f}s '
                f'p95={intervals[p95_index] * 1000:.2f}ms '
                f'max={intervals[-1] * 1000:.2f}ms late={late_percent:.2f}%',
                file=sys.stderr,
            )
        self._label = ''
        self._intervals = []
        self._previous_present = None


FRAME_TIMING = FrameTimingMonitor(FRAME_TIMING_ENABLED)


def _present() -> None:
    pygame.display.flip()
    FRAME_TIMING.presented()

# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def list_photos(directory: str) -> list[str]:
    """Return a sorted list of supported image paths in directory."""
    if not os.path.isdir(directory):
        return []
    return sorted(
        os.path.join(directory, f)
        for f in os.listdir(directory)
        if os.path.splitext(f.lower())[1] in IMAGE_EXTS
        and not f.startswith('.')
        and not f.startswith('._')
    )


def _get_render_lock(cache_key: str) -> threading.Lock:
    with _RENDER_LOCKS_GUARD:
        return _RENDER_LOCKS.setdefault(cache_key, threading.Lock())


def _prepare_rendered_image(path: str, size: tuple[int, int]) -> str:
    """Create a display-ready cached JPEG for the given photo and screen size."""
    sw, sh = size
    source = Path(path)
    stat = source.stat()
    key_src = f"{source.resolve()}|{stat.st_mtime_ns}|{stat.st_size}|{sw}x{sh}|q{RENDER_QUALITY}"
    key = hashlib.sha1(key_src.encode('utf-8')).hexdigest()
    cache_path = RENDER_CACHE_DIR / f"{key}.jpg"
    if cache_path.exists():
        return str(cache_path)

    render_lock = _get_render_lock(key)
    with render_lock:
        if cache_path.exists():
            return str(cache_path)

        with Image.open(source) as img:
            img = ImageOps.exif_transpose(img)
            if hasattr(img, 'draft'):
                img.draft('RGB', size)
            img = img.convert('RGB')
            img.thumbnail(size, Image.LANCZOS)

            canvas = Image.new('RGB', size, BLACK)
            x = (sw - img.size[0]) // 2
            y = (sh - img.size[1]) // 2
            canvas.paste(img, (x, y))

            tmp_path = cache_path.with_suffix('.tmp')
            canvas.save(tmp_path, format='JPEG', quality=RENDER_QUALITY, optimize=True)
            os.replace(tmp_path, cache_path)

    return str(cache_path)


def load_surface(path: str, size: tuple[int, int]) -> pygame.Surface | None:
    """
    Open an image file with Pillow, scale it to fit size while preserving
    aspect ratio (letter/pillarboxed on black), and return a pygame Surface.
    Returns None on any error so callers can skip bad files.
    """
    try:
        prepared_path = _prepare_rendered_image(path, size)
        prepared = pygame.image.load(prepared_path).convert()
        if prepared.get_size() == size:
            return prepared
    except Exception as exc:
        print(f'slideshow: cache prep fallback for {path}: {exc}', file=sys.stderr)

    try:
        img = Image.open(path)
        img = ImageOps.exif_transpose(img)
        img = img.convert('RGB')

        sw, sh = size
        iw, ih = img.size
        scale = min(sw / iw, sh / ih)
        nw, nh = int(iw * scale), int(ih * scale)
        img = img.resize((nw, nh), Image.LANCZOS)

        # Load into pygame via an in-memory BMP rather than frombuffer to
        # avoid stride/format mismatches on different Pi display configurations.
        buf = io.BytesIO()
        img.save(buf, format='BMP')
        buf.seek(0)
        pg_img = pygame.image.load(buf).convert()

        surface = pygame.Surface(size).convert()
        surface.fill(BLACK)
        surface.blit(pg_img, ((sw - nw) // 2, (sh - nh) // 2))
        return surface

    except Exception as exc:
        print(f'slideshow: skipping {path}: {exc}', file=sys.stderr)
        return None

# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def _tick(clock: pygame.time.Clock, fps: int = FPS) -> None:
    """Use busy-loop pacing when available for steadier frame timing."""
    if hasattr(clock, 'tick_busy_loop'):
        clock.tick_busy_loop(fps)
    else:
        clock.tick(fps)


def _pump(clock: pygame.time.Clock, fps: int = FPS) -> bool:
    """Tick clock and drain event queue. Returns False if quit requested."""
    _tick(clock, fps)
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            return False
        if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            return False
    return True


def crossfade(
    screen: pygame.Surface,
    old_surf: pygame.Surface,
    new_surf: pygame.Surface,
    duration: float,
    clock: pygame.time.Clock,
    _scratch_black: pygame.Surface | None = None,
) -> bool:
    """Alpha-blend old_surf → new_surf."""
    steps = max(1, int(duration * TRANSITION_FPS))
    for i in range(steps, 0, -1):
        alpha = int(255 * i / steps)
        screen.blit(new_surf, (0, 0))
        old_surf.set_alpha(alpha)
        screen.blit(old_surf, (0, 0))
        _present()
        if not _pump(clock, TRANSITION_FPS):
            old_surf.set_alpha(None)
            return False

    old_surf.set_alpha(None)
    screen.blit(new_surf, (0, 0))
    _present()
    return True


def fade_to_black(
    screen: pygame.Surface,
    old_surf: pygame.Surface,
    new_surf: pygame.Surface,
    duration: float,
    clock: pygame.time.Clock,
    scratch_black: pygame.Surface | None = None,
) -> bool:
    """Fade old image to black, then fade new image in."""
    half = duration / 2
    steps = max(1, int(half * TRANSITION_FPS))
    black = scratch_black if scratch_black is not None else pygame.Surface(screen.get_size()).convert()
    black.fill(BLACK)

    # Fade out
    for i in range(1, steps + 1):
        alpha = int(255 * i / steps)
        screen.blit(old_surf, (0, 0))
        black.set_alpha(alpha)
        screen.blit(black, (0, 0))
        _present()
        if not _pump(clock, TRANSITION_FPS):
            return False

    # Fade in: draw new image opaque, overlay black with decreasing alpha
    # (mirrors the fade-out pattern so SDL can use its solid-color fast path)
    for i in range(steps, 0, -1):
        alpha = int(255 * i / steps)
        screen.blit(new_surf, (0, 0))
        black.set_alpha(alpha)
        screen.blit(black, (0, 0))
        _present()
        if not _pump(clock, TRANSITION_FPS):
            return False

    screen.blit(new_surf, (0, 0))
    _present()
    return True


def wipe(
    screen: pygame.Surface,
    old_surf: pygame.Surface,
    new_surf: pygame.Surface,
    duration: float,
    clock: pygame.time.Clock,
    _scratch_black: pygame.Surface | None = None,
) -> bool:
    """Hard edge sweeps across revealing the new image."""
    sw, sh = screen.get_size()
    steps = max(1, int(duration * TRANSITION_FPS))
    direction = random.choice(('left', 'right', 'top', 'bottom'))

    for i in range(1, steps + 1):
        progress = i / steps
        screen.blit(old_surf, (0, 0))

        if direction == 'left':
            w = int(sw * progress)
            screen.blit(new_surf, (0, 0), (0, 0, w, sh))
        elif direction == 'right':
            w = int(sw * progress)
            x = sw - w
            screen.blit(new_surf, (x, 0), (x, 0, w, sh))
        elif direction == 'top':
            h = int(sh * progress)
            screen.blit(new_surf, (0, 0), (0, 0, sw, h))
        else:  # bottom
            h = int(sh * progress)
            y = sh - h
            screen.blit(new_surf, (0, y), (0, y, sw, h))

        _present()
        if not _pump(clock, TRANSITION_FPS):
            return False

    screen.blit(new_surf, (0, 0))
    _present()
    return True


def cover(
    screen: pygame.Surface,
    old_surf: pygame.Surface,
    new_surf: pygame.Surface,
    duration: float,
    clock: pygame.time.Clock,
    _scratch_black: pygame.Surface | None = None,
) -> bool:
    """Slide the new image over the old one using only offset blits."""
    sw, sh = screen.get_size()
    steps = max(1, int(duration * TRANSITION_FPS))
    direction = random.choice(('left', 'right', 'top', 'bottom'))

    for i in range(1, steps + 1):
        progress = i / steps
        screen.blit(old_surf, (0, 0))

        if direction == 'left':
            x = int((progress - 1.0) * sw)
            screen.blit(new_surf, (x, 0))
        elif direction == 'right':
            x = int((1.0 - progress) * sw)
            screen.blit(new_surf, (x, 0))
        elif direction == 'top':
            y = int((progress - 1.0) * sh)
            screen.blit(new_surf, (0, y))
        else:  # bottom
            y = int((1.0 - progress) * sh)
            screen.blit(new_surf, (0, y))

        _present()
        if not _pump(clock, TRANSITION_FPS):
            return False

    screen.blit(new_surf, (0, 0))
    _present()
    return True


def slide(
    screen: pygame.Surface,
    old_surf: pygame.Surface,
    new_surf: pygame.Surface,
    duration: float,
    clock: pygame.time.Clock,
    _scratch_black: pygame.Surface | None = None,
) -> bool:
    """Push the old image out while the new image enters from the opposite edge."""
    sw, sh = screen.get_size()
    steps = max(1, int(duration * TRANSITION_FPS))
    direction = random.choice(('left', 'right', 'top', 'bottom'))

    for i in range(1, steps + 1):
        progress = i / steps

        if direction == 'left':
            delta = int(progress * sw)
            screen.blit(old_surf, (-delta, 0))
            screen.blit(new_surf, (sw - delta, 0))
        elif direction == 'right':
            delta = int(progress * sw)
            screen.blit(old_surf, (delta, 0))
            screen.blit(new_surf, (delta - sw, 0))
        elif direction == 'top':
            delta = int(progress * sh)
            screen.blit(old_surf, (0, -delta))
            screen.blit(new_surf, (0, sh - delta))
        else:  # bottom
            delta = int(progress * sh)
            screen.blit(old_surf, (0, delta))
            screen.blit(new_surf, (0, delta - sh))

        _present()
        if not _pump(clock, TRANSITION_FPS):
            return False

    screen.blit(new_surf, (0, 0))
    _present()
    return True


# Maps config name → function, used by load_config().
TRANSITION_FNS: dict[str, object] = {
    'crossfade':     crossfade,
    'fade_to_black': fade_to_black,
    'cover':         cover,
    'slide':         slide,
    'wipe':          wipe,
}


def transition(
    screen: pygame.Surface,
    old_surf: pygame.Surface,
    new_kb: KenBurns,
    duration: float,
    clock: pygame.time.Clock,
    fns: list | None = None,
    scratch_surface: pygame.Surface | None = None,
    scratch_black: pygame.Surface | None = None,
) -> bool:
    """Capture the Ken Burns t=0 frame then run a random transition into it."""
    new_frame = scratch_surface if scratch_surface is not None else pygame.Surface(screen.get_size()).convert()
    new_kb.blit_at(new_frame, 0.0)
    pool = fns if fns else list(TRANSITION_FNS.values())
    fn = random.choice(pool)
    FRAME_TIMING.begin(getattr(fn, '__name__', 'transition'), TRANSITION_FPS)
    try:
        return fn(screen, old_surf, new_frame, duration, clock, scratch_black)
    finally:
        FRAME_TIMING.finish()

# ---------------------------------------------------------------------------
# Ken Burns effect
# ---------------------------------------------------------------------------

class KenBurns:
    """
    Animates a slow zoom (zoom_a → zoom_b) combined with a pan across the
    image. The image is pre-scaled to zoom_max once; each frame we crop
    and scale a screen-sized region from it — one transform per frame at
    ~18fps, which the Pi Zero 2W can sustain smoothly.
    """

    def __init__(
        self,
        surf: pygame.Surface,
        screen_size: tuple[int, int],
        zoom_min: float = 1.02,
        zoom_max: float = 1.20,
    ) -> None:
        sw, sh = screen_size
        self._sw, self._sh = sw, sh

        # Pre-scale to max zoom once so per-frame crops stay within bounds.
        self._big = pygame.transform.scale(surf, (int(sw * zoom_max),
                                                   int(sh * zoom_max)))
        bw, bh = self._big.get_size()

        # Random start/end zoom — occasionally reversed for zoom-out.
        za = random.uniform(zoom_min, zoom_max)
        zb = random.uniform(zoom_min, zoom_max)
        if random.random() < 0.5:
            za, zb = zb, za
        if HW_PROFILE == 'pi45' and PI45_STATIC_ZOOM:
            # Keep zoom fixed across and within slides to avoid edge shimmer.
            z = min(max(PI45_FIXED_ZOOM, zoom_min), zoom_max)
            za = z
            zb = z

        # Crop sizes at each end: smaller crop = more zoomed in.
        self._cwa = int(sw / za)
        self._cha = int(sh / za)
        self._cwb = int(sw / zb)
        self._chb = int(sh / zb)

        # Biased pan: ensure start and end come from opposite halves of the
        # available slack so every slide has a meaningful cross-frame pan.
        def _pan_pair(slack_a: int, slack_b: int) -> tuple[int, int]:
            mid_a, mid_b = slack_a // 2, slack_b // 2
            if random.random() < 0.5:  # start-side → end-side
                return random.randint(0, max(0, mid_a)), random.randint(mid_b, max(mid_b, slack_b))
            else:                       # end-side → start-side
                return random.randint(mid_a, max(mid_a, slack_a)), random.randint(0, max(0, mid_b))

        slack_xa = max(0, bw - self._cwa)
        slack_ya = max(0, bh - self._cha)
        slack_xb = max(0, bw - self._cwb)
        slack_yb = max(0, bh - self._chb)
        self._xa, self._xb = _pan_pair(slack_xa, slack_xb)
        self._ya, self._yb = _pan_pair(slack_ya, slack_yb)

    def blit_at(self, screen: pygame.Surface, t: float) -> None:
        """Render the zoom+pan at smoothstepped progress t ∈ [0, 1]."""
        # Pi 4/5 looks smoother with linear velocity; smoothstep lingers near ends.
        e = t if HW_PROFILE == 'pi45' else t * t * (3 - 2 * t)
        x  = round(self._xa  + (self._xb  - self._xa)  * e)
        y  = round(self._ya  + (self._yb  - self._ya)  * e)
        cw = round(self._cwa + (self._cwb - self._cwa) * e)
        ch = round(self._cha + (self._chb - self._cha) * e)
        bw, bh = self._big.get_size()
        cw = min(cw, bw - x)
        ch = min(ch, bh - y)
        crop = self._big.subsurface(pygame.Rect(x, y, cw, ch))
        if HW_PROFILE == 'pi45':
            pygame.transform.scale(crop, (self._sw, self._sh), screen)
        elif hasattr(pygame.transform, 'smoothscale'):
            pygame.transform.smoothscale(crop, (self._sw, self._sh), screen)
        else:
            pygame.transform.scale(crop, (self._sw, self._sh), screen)


def ken_burns_dwell(
    screen: pygame.Surface,
    kb: KenBurns,
    seconds: float,
    clock: pygame.time.Clock,
) -> bool:
    """Pan/zoom the KenBurns surface across the screen for `seconds`."""
    start = time.monotonic()
    end   = start + seconds
    FRAME_TIMING.begin('ken_burns', KEN_BURNS_FPS)
    try:
        while True:
            now = time.monotonic()
            if now >= end:
                break
            t = min(1.0, (now - start) / seconds)
            kb.blit_at(screen, t)
            _present()
            # On pi45 vsync (60 Hz) paces the loop; clock.tick would cause 3:2 pulldown judder.
            if HW_PROFILE != 'pi45':
                _tick(clock, KEN_BURNS_FPS)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return False
                if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                    return False
        return True
    finally:
        FRAME_TIMING.finish()


def wait(seconds: float, clock: pygame.time.Clock) -> bool:
    """Idle for `seconds`, processing events. Returns False if quit requested."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return False
        _tick(clock)
    return True

# ---------------------------------------------------------------------------
# Background preloader
# ---------------------------------------------------------------------------

class Preloader:
    """
    Loads the next image and pre-builds its KenBurns object on a background
    thread so the transition can start immediately without stalling on disk I/O,
    Pillow decode, or the pygame.transform.scale in KenBurns.__init__.
    """

    def __init__(self, size: tuple[int, int]) -> None:
        self._size     = size
        self._path:     str | None              = None
        self._surface:  pygame.Surface | None   = None
        self._kenburns: KenBurns | None         = None
        self._thread:   threading.Thread | None = None
        self._lock     = threading.Lock()

    def request(self, path: str, zoom_min: float = 1.02, zoom_max: float = 1.20) -> None:
        with self._lock:
            self._path     = path
            self._surface  = None
            self._kenburns = None
        t = threading.Thread(target=self._load, args=(path, zoom_min, zoom_max), daemon=True)
        t.start()
        self._thread = t

    def _load(self, path: str, zoom_min: float, zoom_max: float) -> None:
        surface = load_surface(path, self._size)
        kenburns = KenBurns(surface, self._size, zoom_min, zoom_max) if surface is not None else None
        with self._lock:
            if self._path == path:
                self._surface  = surface
                self._kenburns = kenburns

    def get(self, timeout: float = 10.0) -> tuple[pygame.Surface | None, KenBurns | None]:
        if self._thread:
            self._thread.join(timeout)
        with self._lock:
            return self._surface, self._kenburns

# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------

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


def _build_default_config(default_transition_value: str) -> str:
    return f"""\
# Fancy Frame Configuration
# Edit this file to change slideshow behaviour.
# Changes take effect within 5 minutes (the slideshow restarts automatically).

# Friendly frame name displayed in the iPhone app
frame_name = Fancy Frame

# Seconds each photo is displayed (including the transition)
slide_seconds = 15

# Transition duration in seconds
fade_seconds = 1.5

# Which transitions to use (comma-separated).
# Options: crossfade, fade_to_black, wipe, cover, slide
# Pi 4/5 fast set: slide, cover, wipe
transitions = {default_transition_value}

# Ken Burns zoom and pan effect (yes / no)
ken_burns = yes

# Zoom range for Ken Burns (1.0 = no zoom, 1.20 = 20% zoom out from centre)
ken_burns_zoom_min = 1.02
ken_burns_zoom_max = 1.20
"""

_CONFIG_DEFAULTS = {
    'frame_name':         'Fancy Frame',
    'slide_seconds':      '15',
    'fade_seconds':       '1.5',
    'transitions':        ', '.join(DEFAULT_TRANSITION_NAMES),
    'ken_burns':          'yes',
    'ken_burns_zoom_min': '1.02',
    'ken_burns_zoom_max': '1.20',
}


def _parse_config_file(path: str) -> dict[str, str]:
    result: dict[str, str] = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, _, val = line.partition('=')
                    result[key.strip()] = val.strip()
    except OSError:
        pass
    return result


def _safe_float(raw: dict[str, str], key: str, *, minimum: float | None = None, maximum: float | None = None) -> float:
    default = float(_CONFIG_DEFAULTS[key])
    value = raw.get(key, _CONFIG_DEFAULTS[key])
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        print(f'slideshow: invalid {key}={value!r}; using default {default}', file=sys.stderr)
        parsed = default

    if minimum is not None and parsed < minimum:
        print(f'slideshow: clamping {key}={parsed} to minimum {minimum}', file=sys.stderr)
        parsed = minimum
    if maximum is not None and parsed > maximum:
        print(f'slideshow: clamping {key}={parsed} to maximum {maximum}', file=sys.stderr)
        parsed = maximum
    return parsed


def load_config(path: str = CONFIG_FILE) -> types.SimpleNamespace:
    """
    Read the config file and return a SimpleNamespace of typed settings.
    Creates a default config file if none exists.
    """
    hw_profile = _read_hw_profile()
    default_transition_names = _default_transition_names(hw_profile)
    default_transition_value = ', '.join(default_transition_names)

    if not os.path.exists(path):
        try:
            with open(path, 'w') as f:
                f.write(_build_default_config(default_transition_value))
            os.chmod(path, 0o666)
            print(f'slideshow: created default config at {path}', file=sys.stderr)
        except OSError as exc:
            print(f'slideshow: could not write default config: {exc}', file=sys.stderr)

    file_raw = _parse_config_file(path)
    raw = {**_CONFIG_DEFAULTS, **file_raw}

    # Parse transition function list
    transition_value = file_raw.get('transitions', default_transition_value)
    names = [n.strip() for n in transition_value.split(',')]
    fns = [TRANSITION_FNS[n] for n in names if n in TRANSITION_FNS]
    if not fns:
        print('slideshow: no valid transitions configured; falling back to hardware defaults', file=sys.stderr)
        fns = [TRANSITION_FNS[name] for name in default_transition_names]

    slide_secs = _safe_float(raw, 'slide_seconds', minimum=1.0)
    fade_secs = _safe_float(raw, 'fade_seconds', minimum=0.0)
    zoom_min = _safe_float(raw, 'ken_burns_zoom_min', minimum=1.0, maximum=3.0)
    zoom_max = _safe_float(raw, 'ken_burns_zoom_max', minimum=zoom_min, maximum=3.0)
    if fade_secs > slide_secs:
        print(
            f'slideshow: fade_seconds={fade_secs} exceeds slide_seconds={slide_secs}; clamping fade',
            file=sys.stderr,
        )
        fade_secs = slide_secs

    return types.SimpleNamespace(
        slide_secs = slide_secs,
        fade_secs  = fade_secs,
        fns        = fns,
        ken_burns  = raw.get('ken_burns', _CONFIG_DEFAULTS['ken_burns']).strip().lower() in ('yes', '1', 'true'),
        zoom_min   = zoom_min,
        zoom_max   = zoom_max,
    )


def _config_mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _render_placeholder(size: tuple[int, int]) -> pygame.Surface:
    """
    Build a static 'no photos' screen shown when the photo directory is empty.
    Uses only pygame's built-in font so there are no extra dependencies.
    No Ken Burns or transition effects are applied to this surface.
    """
    surf = pygame.Surface(size)
    try:
        surf = surf.convert()
    except pygame.error:
        # OpenGL display surfaces cannot always be used as a conversion target.
        pass
    surf.fill(BLACK)
    w, h = size

    try:
        pygame.font.init()

        title_size = max(64, h // 7)
        sub_size   = max(28, h // 20)

        title_font = pygame.font.Font(None, title_size)
        sub_font   = pygame.font.Font(None, sub_size)

        title_surf = title_font.render('FANCY FRAME', True, (230, 230, 230))
        sub_surf   = sub_font.render('Add photos to get started', True, (110, 110, 110))

        gap     = max(16, h // 30)
        block_h = title_surf.get_height() + gap + sub_surf.get_height()
        ty      = (h - block_h) // 2
        sy      = ty + title_surf.get_height() + gap

        tx = (w - title_surf.get_width()) // 2
        sx = (w - sub_surf.get_width()) // 2

        surf.blit(title_surf, (tx, ty))
        surf.blit(sub_surf,   (sx, sy))

        # Subtle decorative border around the title text.
        pad_x = max(24, w // 25)
        pad_y = max(14, h // 40)
        border_rect = pygame.Rect(
            tx - pad_x,
            ty - pad_y,
            title_surf.get_width() + pad_x * 2,
            title_surf.get_height() + pad_y * 2,
        )
        pygame.draw.rect(surf, (70, 70, 70), border_rect, 2, border_radius=6)

    except Exception as exc:
        print(f'slideshow: placeholder render failed: {exc}', file=sys.stderr)

    return surf


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


class SoftwareFrame:
    """A decoded slide and its optional CPU-rendered Ken Burns state."""

    def __init__(self, surface: pygame.Surface, ken_burns: KenBurns | None = None) -> None:
        self.surface = surface
        self.ken_burns = ken_burns


class SoftwareRenderer:
    """Current Pygame renderer behind the interface the GPU backend will use."""

    def __init__(self) -> None:
        pygame.init()
        pygame.mouse.set_visible(False)

        display_flags = pygame.FULLSCREEN | pygame.DOUBLEBUF | getattr(pygame, 'HWSURFACE', 0)
        try:
            self.screen = pygame.display.set_mode((0, 0), display_flags, vsync=1)
        except TypeError:
            self.screen = pygame.display.set_mode((0, 0), display_flags)

        pygame.display.set_caption('Fancy Frame')
        self.size = self.screen.get_size()
        self.clock = pygame.time.Clock()
        self.scratch_surface = pygame.Surface(self.size).convert()
        self.scratch_black = pygame.Surface(self.size).convert()
        self.scratch_black.fill(BLACK)
        self.preloader = Preloader(self.size)

    def make_black_frame(self) -> SoftwareFrame:
        surface = pygame.Surface(self.size).convert()
        surface.fill(BLACK)
        return SoftwareFrame(surface)

    def make_placeholder_frame(self) -> SoftwareFrame:
        return SoftwareFrame(_render_placeholder(self.size))

    def present(self, frame: SoftwareFrame) -> None:
        self.screen.blit(frame.surface, (0, 0))
        _present()

    def load_frame(self, path: str, cfg: types.SimpleNamespace, use_preloaded: bool) -> SoftwareFrame | None:
        if use_preloaded:
            surface, ken_burns = self.preloader.get()
            if not cfg.ken_burns:
                ken_burns = None
        else:
            surface = load_surface(path, self.size)
            ken_burns = (
                KenBurns(surface, self.size, cfg.zoom_min, cfg.zoom_max)
                if surface is not None and cfg.ken_burns
                else None
            )
        if surface is None:
            return None
        return SoftwareFrame(surface, ken_burns)

    def request_preload(self, path: str, cfg: types.SimpleNamespace) -> None:
        if cfg.ken_burns:
            self.preloader.request(path, cfg.zoom_min, cfg.zoom_max)
        else:
            self.preloader.request(path, 1.0, 1.0)

    def transition_to(
        self,
        current: SoftwareFrame,
        next_frame: SoftwareFrame,
        cfg: types.SimpleNamespace,
    ) -> bool:
        incoming = next_frame.ken_burns or _StaticFrame(next_frame.surface)
        return transition(
            self.screen,
            current.surface,
            incoming,
            cfg.fade_secs,
            self.clock,
            cfg.fns,
            self.scratch_surface,
            self.scratch_black,
        )

    def dwell(self, frame: SoftwareFrame, seconds: float) -> bool:
        if frame.ken_burns is not None:
            motion_secs = seconds
            if HW_PROFILE == 'pi45' and KEN_BURNS_MOTION_SECONDS > 0.0:
                motion_secs = min(seconds, KEN_BURNS_MOTION_SECONDS)
            if not ken_burns_dwell(self.screen, frame.ken_burns, motion_secs, self.clock):
                return False
            still_secs = max(0.0, seconds - motion_secs)
            return still_secs <= 0.0 or wait(still_secs, self.clock)
        return wait(seconds, self.clock)

    def finish_frame(self, frame: SoftwareFrame) -> SoftwareFrame:
        if frame.ken_burns is None:
            return frame
        surface = pygame.Surface(self.size).convert()
        frame.ken_burns.blit_at(surface, 1.0)
        return SoftwareFrame(surface)

    def wait(self, seconds: float) -> bool:
        return wait(seconds, self.clock)

    @staticmethod
    def release_frame(_frame: SoftwareFrame) -> None:
        pass

    def close(self) -> None:
        pygame.quit()


class GPUKenBurns:
    """Normalized texture-coordinate motion evaluated without image rescaling."""

    def __init__(self, zoom_min: float, zoom_max: float, enabled: bool) -> None:
        self.animated = enabled and zoom_max > 1.0
        if not enabled:
            self._start = (0.0, 0.0, 1.0, 1.0)
            self._end = self._start
            return

        zoom_a = random.uniform(zoom_min, zoom_max)
        zoom_b = random.uniform(zoom_min, zoom_max)
        if random.random() < 0.5:
            zoom_a, zoom_b = zoom_b, zoom_a

        width_a, height_a = 1.0 / zoom_a, 1.0 / zoom_a
        width_b, height_b = 1.0 / zoom_b, 1.0 / zoom_b

        def pan_pair(slack_a: float, slack_b: float) -> tuple[float, float]:
            if random.random() < 0.5:
                return random.uniform(0.0, slack_a / 2.0), random.uniform(slack_b / 2.0, slack_b)
            return random.uniform(slack_a / 2.0, slack_a), random.uniform(0.0, slack_b / 2.0)

        x_a, x_b = pan_pair(1.0 - width_a, 1.0 - width_b)
        y_a, y_b = pan_pair(1.0 - height_a, 1.0 - height_b)
        self._start = (x_a, y_a, width_a, height_a)
        self._end = (x_b, y_b, width_b, height_b)

    def at(self, progress: float) -> tuple[float, float, float, float]:
        eased = progress * progress * (3.0 - 2.0 * progress)
        return tuple(
            start + (end - start) * eased
            for start, end in zip(self._start, self._end)
        )


class GPUFrame:
    def __init__(self, texture: int, motion: GPUKenBurns) -> None:
        self.texture = texture
        self.motion = motion


class GPUPreloader:
    """Prepare display-sized RGB pixels off-thread; texture upload stays on the GL thread."""

    def __init__(self, size: tuple[int, int]) -> None:
        self._size = size
        self._path: str | None = None
        self._pixels: bytes | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def request(self, path: str) -> None:
        with self._lock:
            self._path = path
            self._pixels = None
        thread = threading.Thread(target=self._load, args=(path,), daemon=True)
        thread.start()
        self._thread = thread

    def _load(self, path: str) -> None:
        pixels = _load_gpu_pixels(path, self._size)
        with self._lock:
            if self._path == path:
                self._pixels = pixels

    def get(self, timeout: float = 10.0) -> bytes | None:
        if self._thread:
            self._thread.join(timeout)
        with self._lock:
            return self._pixels


def _load_gpu_pixels(path: str, size: tuple[int, int]) -> bytes | None:
    try:
        prepared_path = _prepare_rendered_image(path, size)
        with Image.open(prepared_path) as image:
            image = image.convert('RGB')
            if image.size != size:
                image = image.resize(size, Image.LANCZOS)
            return image.tobytes()
    except Exception as exc:
        print(f'slideshow: GPU image preparation failed for {path}: {exc}', file=sys.stderr)
        return None


class GPURenderer:
    """OpenGL texture renderer for transitions and Ken Burns motion."""

    def __init__(self) -> None:
        if OPENGL_IMPORT_ERROR is not None:
            raise RuntimeError(f'PyOpenGL unavailable: {OPENGL_IMPORT_ERROR}')

        pygame.init()
        pygame.mouse.set_visible(False)
        flags = pygame.FULLSCREEN | pygame.DOUBLEBUF | pygame.OPENGL
        try:
            self.screen = pygame.display.set_mode((0, 0), flags, vsync=1)
        except TypeError:
            self.screen = pygame.display.set_mode((0, 0), flags)
        pygame.display.set_caption('Fancy Frame GPU')
        self.size = self.screen.get_size()
        self.clock = pygame.time.Clock()
        self.preloader = GPUPreloader(self.size)
        self._configure_gl()

        vendor = self._gl_string(GL_VENDOR)
        gl_renderer = self._gl_string(GL_RENDERER)
        version = self._gl_string(GL_VERSION)
        print(
            f'slideshow: renderer=gpu vendor={vendor} device={gl_renderer} version={version}',
            file=sys.stderr,
        )
        if 'llvmpipe' in gl_renderer.lower() or 'softpipe' in gl_renderer.lower():
            raise RuntimeError(f'OpenGL is using software rendering ({gl_renderer})')

    @staticmethod
    def _gl_string(name: int) -> str:
        value = glGetString(name) or b'unknown'
        return value.decode('utf-8', errors='replace')

    def _configure_gl(self) -> None:
        glViewport(0, 0, self.size[0], self.size[1])
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        glOrtho(-1.0, 1.0, -1.0, 1.0, -1.0, 1.0)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()
        glClearColor(0.0, 0.0, 0.0, 1.0)
        glEnable(GL_TEXTURE_2D)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

    def _upload_texture(self, pixels: bytes, size: tuple[int, int] | None = None) -> int:
        width, height = size or self.size
        texture = int(glGenTextures(1))
        glBindTexture(GL_TEXTURE_2D, texture)
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        glTexImage2D(
            GL_TEXTURE_2D,
            0,
            GL_RGB,
            width,
            height,
            0,
            GL_RGB,
            GL_UNSIGNED_BYTE,
            pixels,
        )
        return texture

    @staticmethod
    def _draw(
        frame: GPUFrame,
        progress: float,
        alpha: float = 1.0,
        geometry: tuple[float, float, float, float] = (-1.0, -1.0, 1.0, 1.0),
        uv_override: tuple[float, float, float, float] | None = None,
    ) -> None:
        x, y, width, height = uv_override or frame.motion.at(progress)
        u0, u1 = x, x + width
        # Pixel row zero comes from the top of the Pillow image. Map it to the
        # top vertices directly; flipping V here would display photos upside down.
        v_top, v_bottom = y, y + height
        left, bottom, right, top = geometry

        glBindTexture(GL_TEXTURE_2D, frame.texture)
        glColor4f(1.0, 1.0, 1.0, alpha)
        glBegin(GL_TRIANGLE_STRIP)
        glTexCoord2f(u0, v_top)
        glVertex2f(left, top)
        glTexCoord2f(u1, v_top)
        glVertex2f(right, top)
        glTexCoord2f(u0, v_bottom)
        glVertex2f(left, bottom)
        glTexCoord2f(u1, v_bottom)
        glVertex2f(right, bottom)
        glEnd()

    @staticmethod
    def _events_ok() -> bool:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return False
        return True

    def make_black_frame(self) -> GPUFrame:
        texture = self._upload_texture(bytes(BLACK), (1, 1))
        return GPUFrame(texture, GPUKenBurns(1.0, 1.0, False))

    def make_placeholder_frame(self) -> GPUFrame:
        surface = _render_placeholder(self.size)
        pixels = pygame.image.tostring(surface, 'RGB')
        return GPUFrame(self._upload_texture(pixels), GPUKenBurns(1.0, 1.0, False))

    def present(self, frame: GPUFrame) -> None:
        glClear(GL_COLOR_BUFFER_BIT)
        self._draw(frame, 1.0)
        _present()

    def load_frame(self, path: str, cfg: types.SimpleNamespace, use_preloaded: bool) -> GPUFrame | None:
        pixels = self.preloader.get() if use_preloaded else _load_gpu_pixels(path, self.size)
        if pixels is None:
            return None
        texture = self._upload_texture(pixels)
        motion = GPUKenBurns(cfg.zoom_min, cfg.zoom_max, cfg.ken_burns)
        return GPUFrame(texture, motion)

    def request_preload(self, path: str, _cfg: types.SimpleNamespace) -> None:
        self.preloader.request(path)

    def _draw_transition(
        self,
        name: str,
        current: GPUFrame,
        incoming: GPUFrame,
        progress: float,
        direction: str,
    ) -> None:
        glClear(GL_COLOR_BUFFER_BIT)
        if name == 'crossfade':
            self._draw(current, 1.0)
            self._draw(incoming, 0.0, progress)
            return
        if name == 'fade_to_black':
            if progress < 0.5:
                self._draw(current, 1.0, 1.0 - progress * 2.0)
            else:
                self._draw(incoming, 0.0, progress * 2.0 - 1.0)
            return
        if name == 'wipe':
            self._draw(current, 1.0)
            base_x, base_y, base_width, base_height = incoming.motion.at(0.0)
            if direction == 'left':
                geometry = (-1.0, -1.0, -1.0 + 2.0 * progress, 1.0)
                uv = (base_x, base_y, base_width * progress, base_height)
            elif direction == 'right':
                geometry = (1.0 - 2.0 * progress, -1.0, 1.0, 1.0)
                uv = (
                    base_x + base_width * (1.0 - progress),
                    base_y,
                    base_width * progress,
                    base_height,
                )
            elif direction == 'top':
                geometry = (-1.0, 1.0 - 2.0 * progress, 1.0, 1.0)
                uv = (base_x, base_y, base_width, base_height * progress)
            else:
                geometry = (-1.0, -1.0, 1.0, -1.0 + 2.0 * progress)
                uv = (
                    base_x,
                    base_y + base_height * (1.0 - progress),
                    base_width,
                    base_height * progress,
                )
            self._draw(incoming, 0.0, geometry=geometry, uv_override=uv)
            return

        if direction == 'left':
            delta_x, delta_y = -2.0 * progress, 0.0
            incoming_geometry = (1.0 + delta_x, -1.0, 3.0 + delta_x, 1.0)
        elif direction == 'right':
            delta_x, delta_y = 2.0 * progress, 0.0
            incoming_geometry = (-3.0 + delta_x, -1.0, -1.0 + delta_x, 1.0)
        elif direction == 'top':
            delta_x, delta_y = 0.0, 2.0 * progress
            incoming_geometry = (-1.0, -3.0 + delta_y, 1.0, -1.0 + delta_y)
        else:
            delta_x, delta_y = 0.0, -2.0 * progress
            incoming_geometry = (-1.0, 1.0 + delta_y, 1.0, 3.0 + delta_y)

        if name == 'cover':
            self._draw(current, 1.0)
        else:  # slide
            current_geometry = (-1.0 + delta_x, -1.0 + delta_y, 1.0 + delta_x, 1.0 + delta_y)
            self._draw(current, 1.0, geometry=current_geometry)
        self._draw(incoming, 0.0, geometry=incoming_geometry)

    def transition_to(self, current: GPUFrame, next_frame: GPUFrame, cfg: types.SimpleNamespace) -> bool:
        transition_fn = random.choice(cfg.fns)
        name = getattr(transition_fn, '__name__', 'crossfade')
        if name not in TRANSITION_FNS:
            name = 'crossfade'
        direction = random.choice(('left', 'right', 'top', 'bottom'))
        duration = cfg.fade_secs
        started = time.monotonic()
        FRAME_TIMING.begin(f'gpu_{name}', GPU_FPS)
        try:
            while True:
                now = time.monotonic()
                progress = 1.0 if duration <= 0.0 else min(1.0, (now - started) / duration)
                eased = progress * progress * (3.0 - 2.0 * progress)
                self._draw_transition(name, current, next_frame, eased, direction)
                _present()
                if not self._events_ok():
                    return False
                if progress >= 1.0:
                    return True
                self.clock.tick_busy_loop(GPU_FPS)
        finally:
            FRAME_TIMING.finish()

    def dwell(self, frame: GPUFrame, seconds: float) -> bool:
        if not frame.motion.animated:
            self.present(frame)
            return self.wait(seconds)
        started = time.monotonic()
        end = started + seconds
        FRAME_TIMING.begin('gpu_ken_burns', GPU_FPS)
        try:
            while True:
                now = time.monotonic()
                if now >= end:
                    return True
                progress = 1.0 if seconds <= 0.0 else min(1.0, (now - started) / seconds)
                glClear(GL_COLOR_BUFFER_BIT)
                self._draw(frame, progress)
                _present()
                if not self._events_ok():
                    return False
                self.clock.tick_busy_loop(GPU_FPS)
        finally:
            FRAME_TIMING.finish()

    @staticmethod
    def finish_frame(frame: GPUFrame) -> GPUFrame:
        return frame

    def wait(self, seconds: float) -> bool:
        return wait(seconds, self.clock)

    @staticmethod
    def release_frame(frame: GPUFrame) -> None:
        glDeleteTextures([frame.texture])

    def close(self) -> None:
        pygame.quit()


def create_renderer() -> SoftwareRenderer | GPURenderer:
    if RENDERER_NAME not in ('software', 'gpu', 'auto'):
        raise ValueError(f'Unknown FANCY_FRAME_RENDERER value: {RENDERER_NAME!r}')
    if RENDERER_NAME in ('gpu', 'auto'):
        try:
            return GPURenderer()
        except Exception as exc:
            pygame.quit()
            if RENDERER_NAME == 'gpu':
                raise
            print(f'slideshow: GPU renderer unavailable, using software: {exc}', file=sys.stderr)
    print('slideshow: renderer=software', file=sys.stderr)
    return SoftwareRenderer()


def main() -> None:
    cfg = load_config()
    config_mtime      = _config_mtime(CONFIG_FILE)
    last_config_check = time.monotonic()

    renderer = create_renderer()
    current = renderer.make_black_frame()
    renderer.present(current)

    photos:              list[str]            = []
    idx:                 int                  = 0
    last_refresh:        float                = 0.0
    preloaded_path:      str | None           = None
    placeholder_frame = None

    while True:
        now = time.monotonic()

        # Check for config file changes every CONFIG_CHECK_SECS seconds.
        if now - last_config_check >= CONFIG_CHECK_SECS:
            last_config_check = now
            mtime = _config_mtime(CONFIG_FILE)
            if mtime != config_mtime:
                print('slideshow: config changed, restarting…', file=sys.stderr)
                renderer.close()
                sys.exit(0)

        # Refresh photo list periodically.
        if now - last_refresh >= REFRESH_SECS or not photos:
            fresh = list_photos(PHOTO_DIR)
            if fresh and fresh != photos:
                photos = fresh
                random.shuffle(photos)
                idx = 0
            last_refresh = now

        if not photos:
            # Show the logo placeholder statically — no effects, no Ken Burns.
            if placeholder_frame is None:
                placeholder_frame = renderer.make_placeholder_frame()
            renderer.present(placeholder_frame)
            # Use as the base for the first real photo's transition.
            current = placeholder_frame
            if not renderer.wait(5):
                break
            continue

        placeholder_frame = None  # release once photos are available

        # Pick the next photo; reshuffle when the list is exhausted.
        path = photos[idx % len(photos)]
        idx += 1
        if idx >= len(photos):
            random.shuffle(photos)
            idx = 0

        # Use the preloaded surface+KenBurns when available, otherwise load inline.
        next_frame = renderer.load_frame(path, cfg, path == preloaded_path)
        if next_frame is None:
            continue

        # Kick off preload of the image after this one (includes KenBurns pre-scale).
        next_path = photos[idx % len(photos)]
        if next_path != preloaded_path:
            renderer.request_preload(next_path, cfg)
            preloaded_path = next_path

        # Transition and dwell.
        if not renderer.transition_to(current, next_frame, cfg):
            break
        dwell = max(0.0, cfg.slide_secs - cfg.fade_secs)
        if not renderer.dwell(next_frame, dwell):
            break
        previous = current
        current = renderer.finish_frame(next_frame)
        renderer.release_frame(previous)

    renderer.close()


class _StaticFrame:
    """Minimal stand-in for KenBurns when ken_burns is disabled."""
    def __init__(self, surf: pygame.Surface) -> None:
        self._surf = surf

    def blit_at(self, screen: pygame.Surface, _t: float) -> None:
        screen.blit(self._surf, (0, 0))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('slideshow: fatal unhandled exception', file=sys.stderr)
        traceback.print_exc()
        sys.stderr.flush()
        try:
            pygame.quit()
        except Exception:
            pass
        time.sleep(2)
        sys.exit(1)
