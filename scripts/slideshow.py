#!/usr/bin/env python3
"""
Photo frame slideshow with smooth transitions (crossfade, fade-to-black, wipe).

Reads photos from PHOTO_PLAY_DIR, displays them fullscreen with a crossfade
between each slide, and refreshes the photo list periodically to pick up
newly added images.

Environment variables:
  PHOTO_FRAME_SLIDE_SECONDS   — seconds each photo is displayed (default 25)
  PHOTO_FRAME_REFRESH_SECONDS — how often to rescan photo dir (default 300)
"""

from __future__ import annotations

import io
import os
import sys
import random
import time
import threading

import pygame
from PIL import Image, ImageOps

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PHOTO_DIR    = '/var/lib/photo-frame/playable-photos'
SLIDE_SECS   = int(os.environ.get('PHOTO_FRAME_SLIDE_SECONDS',   '25'))
REFRESH_SECS = int(os.environ.get('PHOTO_FRAME_REFRESH_SECONDS', '300'))
FADE_SECS    = 1.5
FPS          = 30
KB_ZOOM_MIN  = 1.02   # minimum zoom
KB_ZOOM_MAX  = 1.20   # maximum zoom

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tif', '.tiff'}

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
    )


def load_surface(path: str, size: tuple[int, int]) -> pygame.Surface | None:
    """
    Open an image file with Pillow, scale it to fit size while preserving
    aspect ratio (letter/pillarboxed on black), and return a pygame Surface.
    Returns None on any error so callers can skip bad files.
    """
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

        surface = pygame.Surface(size)
        surface.fill((0, 0, 0))
        surface.blit(pg_img, ((sw - nw) // 2, (sh - nh) // 2))
        return surface

    except Exception as exc:
        print(f'slideshow: skipping {path}: {exc}', file=sys.stderr)
        return None

# ---------------------------------------------------------------------------
# Background preloader
# ---------------------------------------------------------------------------

class Preloader:
    """
    Loads the next image on a background thread so the crossfade can start
    immediately without blocking on disk I/O or Pillow decode time.
    """

    def __init__(self, size: tuple[int, int]) -> None:
        self._size    = size
        self._path:    str | None           = None
        self._surface: pygame.Surface | None = None
        self._thread:  threading.Thread | None = None
        self._lock    = threading.Lock()

    def request(self, path: str) -> None:
        with self._lock:
            self._path    = path
            self._surface = None
        t = threading.Thread(target=self._load, args=(path,), daemon=True)
        t.start()
        self._thread = t

    def _load(self, path: str) -> None:
        surface = load_surface(path, self._size)
        with self._lock:
            if self._path == path:
                self._surface = surface

    def get(self, timeout: float = 10.0) -> pygame.Surface | None:
        if self._thread:
            self._thread.join(timeout)
        with self._lock:
            return self._surface

# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------

BLACK = (0, 0, 0)


def _pump(clock: pygame.time.Clock) -> bool:
    """Tick clock and drain event queue. Returns False if quit requested."""
    clock.tick(FPS)
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
) -> bool:
    """Alpha-blend old_surf → new_surf."""
    new_frame = new_surf
    steps = max(1, int(duration * FPS))
    for i in range(1, steps + 1):
        alpha = int(255 * i / steps)
        screen.blit(old_surf, (0, 0))
        tmp = new_frame.copy()
        tmp.set_alpha(alpha)
        screen.blit(tmp, (0, 0))
        pygame.display.flip()
        if not _pump(clock):
            return False
    return True


def fade_to_black(
    screen: pygame.Surface,
    old_surf: pygame.Surface,
    new_surf: pygame.Surface,
    duration: float,
    clock: pygame.time.Clock,
) -> bool:
    """Fade old image to black, then fade new image in."""
    new_frame = new_surf
    half = duration / 2
    steps = max(1, int(half * FPS))
    black = pygame.Surface(screen.get_size())
    black.fill(BLACK)

    # Fade out
    for i in range(1, steps + 1):
        alpha = int(255 * i / steps)
        screen.blit(old_surf, (0, 0))
        black.set_alpha(alpha)
        screen.blit(black, (0, 0))
        pygame.display.flip()
        if not _pump(clock):
            return False

    # Fade in
    for i in range(1, steps + 1):
        alpha = int(255 * i / steps)
        screen.blit(black, (0, 0))
        tmp = new_frame.copy()
        tmp.set_alpha(alpha)
        screen.blit(tmp, (0, 0))
        pygame.display.flip()
        if not _pump(clock):
            return False

    return True


def wipe(
    screen: pygame.Surface,
    old_surf: pygame.Surface,
    new_surf: pygame.Surface,
    duration: float,
    clock: pygame.time.Clock,
) -> bool:
    """Hard edge sweeps across revealing the new image."""
    sw, sh = screen.get_size()
    new_frame = new_surf
    steps = max(1, int(duration * FPS))
    direction = random.choice(('left', 'right', 'top', 'bottom'))

    for i in range(1, steps + 1):
        progress = i / steps
        screen.blit(old_surf, (0, 0))

        if direction == 'left':
            w = int(sw * progress)
            screen.blit(new_frame, (0, 0), (0, 0, w, sh))
        elif direction == 'right':
            w = int(sw * progress)
            x = sw - w
            screen.blit(new_frame, (x, 0), (x, 0, w, sh))
        elif direction == 'top':
            h = int(sh * progress)
            screen.blit(new_frame, (0, 0), (0, 0, sw, h))
        else:  # bottom
            h = int(sh * progress)
            y = sh - h
            screen.blit(new_frame, (0, y), (0, y, sw, h))

        pygame.display.flip()
        if not _pump(clock):
            return False

    return True


def transition(
    screen: pygame.Surface,
    old_surf: pygame.Surface,
    new_kb: KenBurns,
    duration: float,
    clock: pygame.time.Clock,
) -> bool:
    """Capture the Ken Burns t=0 frame then run a random transition into it."""
    new_frame = pygame.Surface(screen.get_size())
    new_kb.blit_at(new_frame, 0.0)
    fn = random.choice((crossfade, fade_to_black, wipe))
    return fn(screen, old_surf, new_frame, duration, clock)


class KenBurns:
    """
    Pre-scales the image once to a random zoom level, then pans smoothly
    across it during the dwell. The dwell loop is a pure blit with an
    offset — no per-frame scaling, which is essential for a Pi Zero 2W.

    Zoom variety comes from each image getting a different random zoom
    level, which looks identical to intra-image zoom at this motion speed.
    """

    def __init__(self, surf: pygame.Surface, screen_size: tuple[int, int]) -> None:
        sw, sh = screen_size

        # Pick one random zoom level for this image and pre-scale once.
        zoom = random.uniform(KB_ZOOM_MIN, KB_ZOOM_MAX)
        zw = int(sw * zoom)
        zh = int(sh * zoom)
        self._zoomed = pygame.transform.scale(surf, (zw, zh))

        # Random start/end pan offsets within the overflow area.
        max_ox = max(0, zw - sw)
        max_oy = max(0, zh - sh)
        self._ox_a = random.randint(0, max_ox)
        self._oy_a = random.randint(0, max_oy)
        self._ox_b = random.randint(0, max_ox)
        self._oy_b = random.randint(0, max_oy)

    def blit_at(self, screen: pygame.Surface, t: float) -> None:
        """Blit the current pan position (smoothstepped t ∈ [0, 1]) to screen."""
        e = t * t * (3 - 2 * t)
        ox = int(self._ox_a + (self._ox_b - self._ox_a) * e)
        oy = int(self._oy_a + (self._oy_b - self._oy_a) * e)
        screen.blit(self._zoomed, (-ox, -oy))


def ken_burns_dwell(
    screen: pygame.Surface,
    kb: KenBurns,
    seconds: float,
    clock: pygame.time.Clock,
) -> bool:
    """Pan the pre-scaled KenBurns surface across the screen for `seconds`."""
    start = time.monotonic()
    end   = start + seconds
    while True:
        now = time.monotonic()
        if now >= end:
            break
        t = min(1.0, (now - start) / seconds)
        kb.blit_at(screen, t)
        pygame.display.flip()
        clock.tick(FPS)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return False
    return True


def wait(seconds: float, clock: pygame.time.Clock) -> bool:
    """
    Idle for `seconds`, processing events. Returns False if quit requested.
    """
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return False
        clock.tick(FPS)
    return True

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    pygame.init()
    pygame.mouse.set_visible(False)

    screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
    pygame.display.set_caption('Photo Frame')
    size  = screen.get_size()
    clock = pygame.time.Clock()

    screen.fill((0, 0, 0))
    pygame.display.flip()

    current: pygame.Surface = pygame.Surface(size)
    current.fill((0, 0, 0))

    photos:       list[str] = []
    idx:          int       = 0
    last_refresh: float     = 0.0
    preloader   = Preloader(size)
    preloaded_path: str | None = None

    while True:
        # Refresh photo list periodically.
        now = time.monotonic()
        if now - last_refresh >= REFRESH_SECS or not photos:
            fresh = list_photos(PHOTO_DIR)
            if fresh and fresh != photos:
                photos = fresh
                random.shuffle(photos)
                idx = 0
            last_refresh = now

        if not photos:
            if not wait(5, clock):
                break
            continue

        # Pick the next photo; reshuffle when the list is exhausted.
        path = photos[idx % len(photos)]
        idx += 1
        if idx >= len(photos):
            random.shuffle(photos)
            idx = 0

        # Use the preloaded surface when it matches, otherwise load inline.
        if path == preloaded_path:
            next_surf = preloader.get()
        else:
            next_surf = load_surface(path, size)

        if next_surf is None:
            continue

        # Kick off preload of the image after this one.
        next_path = photos[idx % len(photos)]
        if next_path != preloaded_path:
            preloader.request(next_path)
            preloaded_path = next_path

        # Build Ken Burns state for the incoming image. The transition renders
        # it at t=0 so there's no snap when the dwell starts.
        next_kb = KenBurns(next_surf, size)

        # Transition into the new image, then Ken Burns dwell.
        if not transition(screen, current, next_kb, FADE_SECS, clock):
            break

        dwell = max(0.0, SLIDE_SECS - FADE_SECS)
        if not ken_burns_dwell(screen, next_kb, dwell, clock):
            break

        # Capture the final pan position as the base for the next transition.
        current = pygame.Surface(size)
        next_kb.blit_at(current, 1.0)

    pygame.quit()


if __name__ == '__main__':
    main()
