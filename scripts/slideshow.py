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
    new_kb: KenBurns,
    duration: float,
    clock: pygame.time.Clock,
) -> bool:
    """Alpha-blend old_surf → new image at its Ken Burns t=0 frame."""
    new_frame = new_kb.frame_at(0)
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
    new_kb: KenBurns,
    duration: float,
    clock: pygame.time.Clock,
) -> bool:
    """Fade old image to black, then fade new image in at Ken Burns t=0."""
    new_frame = new_kb.frame_at(0)
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
    new_kb: KenBurns,
    duration: float,
    clock: pygame.time.Clock,
) -> bool:
    """Hard edge sweeps across revealing the new image at Ken Burns t=0."""
    sw, sh = screen.get_size()
    new_frame = new_kb.frame_at(0)
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
    """Pick a random transition and run it."""
    fn = random.choice((crossfade, fade_to_black, wipe))
    return fn(screen, old_surf, new_kb, duration, clock)


class KenBurns:
    """
    Pre-bakes a zoomed surface at load time so the dwell loop is pure blit —
    no per-frame scaling, which is too slow for a Pi Zero 2W.

    The image is scaled up to KB_ZOOM_MAX once; each frame we blit a
    screen-sized rect from a slowly moving position within it.
    """

    def __init__(self, surf: pygame.Surface, screen_size: tuple[int, int]) -> None:
        sw, sh = screen_size
        self._sw, self._sh = sw, sh

        # Pre-scale to the maximum zoom level once.
        big_w = int(sw * KB_ZOOM_MAX)
        big_h = int(sh * KB_ZOOM_MAX)
        self._big = pygame.transform.scale(surf, (big_w, big_h))

        # Random start/end zoom expressed as fraction of the big surface.
        za = random.uniform(KB_ZOOM_MIN, KB_ZOOM_MAX)
        zb = random.uniform(KB_ZOOM_MIN, KB_ZOOM_MAX)
        if random.random() < 0.5:
            za, zb = zb, za  # occasionally zoom out

        # Convert zoom levels to crop-rect sizes within the big surface.
        self._wa = int(sw * sw / (big_w / za))  # width of crop at zoom a
        self._ha = int(sh * sh / (big_h / za))
        self._wb = int(sw * sw / (big_w / zb))
        self._hb = int(sh * sh / (big_h / zb))

        # Random pan: top-left corner of the crop rect, clamped to big surface.
        self._xa = random.randint(0, max(0, big_w - self._wa))
        self._ya = random.randint(0, max(0, big_h - self._ha))
        self._xb = random.randint(0, max(0, big_w - self._wb))
        self._yb = random.randint(0, max(0, big_h - self._hb))

    def frame_at(self, t: float) -> pygame.Surface:
        """Return a screen-sized surface for eased progress t ∈ [0, 1]."""
        # Smoothstep easing.
        e = t * t * (3 - 2 * t)
        x = int(self._xa + (self._xb - self._xa) * e)
        y = int(self._ya + (self._yb - self._ya) * e)
        w = int(self._wa + (self._wb - self._wa) * e)
        h = int(self._ha + (self._hb - self._ha) * e)
        # Crop from the big surface then scale to screen size.
        crop = self._big.subsurface(
            pygame.Rect(x, y, min(w, self._big.get_width() - x),
                               min(h, self._big.get_height() - y))
        )
        return pygame.transform.scale(crop, (self._sw, self._sh))


def ken_burns_dwell(
    screen: pygame.Surface,
    kb: KenBurns,
    seconds: float,
    clock: pygame.time.Clock,
) -> bool:
    """Animate a KenBurns instance for `seconds`."""
    start = time.monotonic()
    end   = start + seconds
    while True:
        now = time.monotonic()
        if now >= end:
            break
        t = min(1.0, (now - start) / seconds)
        screen.blit(kb.frame_at(t), (0, 0))
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

        # Transition (renders new image at Ken Burns t=0) then dwell.
        if not transition(screen, current, next_kb, FADE_SECS, clock):
            break
        current = next_kb.frame_at(0)

        dwell = max(0.0, SLIDE_SECS - FADE_SECS)
        if not ken_burns_dwell(screen, next_kb, dwell, clock):
            break

        # Capture the last Ken Burns frame as the base for the next transition.
        current = next_kb.frame_at(1.0)

    pygame.quit()


if __name__ == '__main__':
    main()
