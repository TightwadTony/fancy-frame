#!/usr/bin/env python3
"""
Photo frame slideshow with smooth crossfade transitions.

Reads photos from PHOTO_PLAY_DIR, displays them fullscreen with a crossfade
between each slide, and refreshes the photo list periodically to pick up
newly added images.

Environment variables:
  PHOTO_FRAME_SLIDE_SECONDS   — seconds each photo is displayed (default 25)
  PHOTO_FRAME_REFRESH_SECONDS — how often to rescan photo dir (default 300)
"""

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
SPLASH_PATH  = '/opt/photo-frame/assets/splash.jpg'
SLIDE_SECS   = int(os.environ.get('PHOTO_FRAME_SLIDE_SECONDS',   '25'))
REFRESH_SECS = int(os.environ.get('PHOTO_FRAME_REFRESH_SECONDS', '300'))
FADE_SECS    = 1.5
FPS          = 30

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

        surface = pygame.Surface(size)
        surface.fill((0, 0, 0))
        pg = pygame.image.frombuffer(img.tobytes(), (nw, nh), 'RGB')
        surface.blit(pg, ((sw - nw) // 2, (sh - nh) // 2))
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
# Rendering helpers
# ---------------------------------------------------------------------------

def crossfade(
    screen: pygame.Surface,
    old_surf: pygame.Surface,
    new_surf: pygame.Surface,
    duration: float,
    clock: pygame.time.Clock,
) -> bool:
    """
    Blend old_surf → new_surf over `duration` seconds at FPS.
    Returns False if the user requested quit during the transition.
    """
    steps = max(1, int(duration * FPS))
    for i in range(1, steps + 1):
        alpha = int(255 * i / steps)
        screen.blit(old_surf, (0, 0))
        tmp = new_surf.copy()
        tmp.set_alpha(alpha)
        screen.blit(tmp, (0, 0))
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

    # Show splash while the first photo loads.
    splash = load_surface(SPLASH_PATH, size)
    if splash:
        screen.blit(splash, (0, 0))
    else:
        screen.fill((0, 0, 0))
    pygame.display.flip()

    current: pygame.Surface = splash or pygame.Surface(size)
    if not splash:
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

        # Crossfade then dwell.
        if not crossfade(screen, current, next_surf, FADE_SECS, clock):
            break
        current = next_surf

        dwell = max(0.0, SLIDE_SECS - FADE_SECS)
        if not wait(dwell, clock):
            break

    pygame.quit()


if __name__ == '__main__':
    main()
