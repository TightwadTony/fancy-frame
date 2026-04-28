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

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tif', '.tiff'}
BLACK = (0, 0, 0)
DEFAULT_TRANSITION_NAMES = ('crossfade', 'fade_to_black', 'wipe')
PI45_TRANSITION_NAMES = ('slide', 'cover')

RENDER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_RENDER_LOCKS: dict[str, threading.Lock] = {}
_RENDER_LOCKS_GUARD = threading.Lock()

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
        pygame.display.flip()
        if not _pump(clock, TRANSITION_FPS):
            old_surf.set_alpha(None)
            return False

    old_surf.set_alpha(None)
    screen.blit(new_surf, (0, 0))
    pygame.display.flip()
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
        pygame.display.flip()
        if not _pump(clock, TRANSITION_FPS):
            return False

    # Fade in: draw new image opaque, overlay black with decreasing alpha
    # (mirrors the fade-out pattern so SDL can use its solid-color fast path)
    for i in range(steps, 0, -1):
        alpha = int(255 * i / steps)
        screen.blit(new_surf, (0, 0))
        black.set_alpha(alpha)
        screen.blit(black, (0, 0))
        pygame.display.flip()
        if not _pump(clock, TRANSITION_FPS):
            return False

    screen.blit(new_surf, (0, 0))
    pygame.display.flip()
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

        pygame.display.flip()
        if not _pump(clock, TRANSITION_FPS):
            return False

    screen.blit(new_surf, (0, 0))
    pygame.display.flip()
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

        pygame.display.flip()
        if not _pump(clock, TRANSITION_FPS):
            return False

    screen.blit(new_surf, (0, 0))
    pygame.display.flip()
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

        pygame.display.flip()
        if not _pump(clock, TRANSITION_FPS):
            return False

    screen.blit(new_surf, (0, 0))
    pygame.display.flip()
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
    return fn(screen, old_surf, new_frame, duration, clock, scratch_black)

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
    while True:
        now = time.monotonic()
        if now >= end:
            break
        t = min(1.0, (now - start) / seconds)
        kb.blit_at(screen, t)
        pygame.display.flip()
        # On pi45 vsync (60 Hz) paces the loop; clock.tick would cause 3:2 pulldown judder.
        if HW_PROFILE != 'pi45':
            _tick(clock, KEN_BURNS_FPS)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return False
    return True


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
slide_seconds = 25

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
    'slide_seconds':      '25',
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
    surf = pygame.Surface(size).convert()
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

def main() -> None:
    cfg = load_config()
    config_mtime      = _config_mtime(CONFIG_FILE)
    last_config_check = time.monotonic()

    pygame.init()
    pygame.mouse.set_visible(False)

    display_flags = pygame.FULLSCREEN | pygame.DOUBLEBUF | getattr(pygame, 'HWSURFACE', 0)
    try:
        screen = pygame.display.set_mode((0, 0), display_flags, vsync=1)
    except TypeError:
        screen = pygame.display.set_mode((0, 0), display_flags)

    pygame.display.set_caption('Fancy Frame')
    size  = screen.get_size()
    clock = pygame.time.Clock()
    scratch_surface = pygame.Surface(size).convert()
    scratch_black = pygame.Surface(size).convert()
    scratch_black.fill(BLACK)

    screen.fill(BLACK)
    pygame.display.flip()

    current: pygame.Surface = pygame.Surface(size).convert()
    current.fill(BLACK)

    photos:              list[str]            = []
    idx:                 int                  = 0
    last_refresh:        float                = 0.0
    preloader           = Preloader(size)
    preloaded_path:      str | None           = None
    placeholder_surf:    pygame.Surface | None = None

    while True:
        now = time.monotonic()

        # Check for config file changes every CONFIG_CHECK_SECS seconds.
        if now - last_config_check >= CONFIG_CHECK_SECS:
            last_config_check = now
            mtime = _config_mtime(CONFIG_FILE)
            if mtime != config_mtime:
                print('slideshow: config changed, restarting…', file=sys.stderr)
                pygame.quit()
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
            if placeholder_surf is None:
                placeholder_surf = _render_placeholder(size)
            screen.blit(placeholder_surf, (0, 0))
            pygame.display.flip()
            # Use as the base for the first real photo's transition.
            current = placeholder_surf
            if not wait(5, clock):
                break
            continue

        placeholder_surf = None  # release once photos are available

        # Pick the next photo; reshuffle when the list is exhausted.
        path = photos[idx % len(photos)]
        idx += 1
        if idx >= len(photos):
            random.shuffle(photos)
            idx = 0

        # Use the preloaded surface+KenBurns when available, otherwise load inline.
        if path == preloaded_path:
            next_surf, next_kb = preloader.get()
        else:
            next_surf = load_surface(path, size)
            if next_surf is not None and cfg.ken_burns:
                next_kb = KenBurns(next_surf, size, cfg.zoom_min, cfg.zoom_max)
            else:
                next_kb = None

        if next_surf is None:
            continue

        # Kick off preload of the image after this one (includes KenBurns pre-scale).
        next_path = photos[idx % len(photos)]
        if next_path != preloaded_path:
            if cfg.ken_burns:
                preloader.request(next_path, cfg.zoom_min, cfg.zoom_max)
            else:
                preloader.request(next_path, 1.0, 1.0)
            preloaded_path = next_path

        # Transition and dwell.
        if next_kb is not None:
            # Ken Burns: transition into t=0 frame, then animate.
            if not transition(
                screen,
                current,
                next_kb,
                cfg.fade_secs,
                clock,
                cfg.fns,
                scratch_surface,
                scratch_black,
            ):
                break
            dwell = max(0.0, cfg.slide_secs - cfg.fade_secs)
            motion_secs = dwell
            if HW_PROFILE == 'pi45' and KEN_BURNS_MOTION_SECONDS > 0.0:
                motion_secs = min(dwell, KEN_BURNS_MOTION_SECONDS)
            if not ken_burns_dwell(screen, next_kb, motion_secs, clock):
                break
            still_secs = max(0.0, dwell - motion_secs)
            if still_secs > 0.0 and not wait(still_secs, clock):
                break
            # Capture the final pan position as the base for the next transition.
            current = pygame.Surface(size).convert()
            next_kb.blit_at(current, 1.0)
        else:
            # Ken Burns disabled: simple transition into static image.
            static_kb = _StaticFrame(next_surf)
            if not transition(
                screen,
                current,
                static_kb,
                cfg.fade_secs,
                clock,
                cfg.fns,
                scratch_surface,
                scratch_black,
            ):
                break
            dwell = max(0.0, cfg.slide_secs - cfg.fade_secs)
            if not wait(dwell, clock):
                break
            current = next_surf

    pygame.quit()


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
