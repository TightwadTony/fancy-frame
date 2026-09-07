#!/usr/bin/env python3
"""Exercise a GPU-backed crossfade on the frame's real display stack.

This is deliberately separate from the production slideshow. It is the
go/no-go test for the OpenGL renderer: load two photos once, upload them as
textures, and measure presentation timing while the GPU blends them.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
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
except ImportError as exc:
    raise SystemExit(
        "PyOpenGL is required. Install the python3-opengl package on the frame."
    ) from exc


BLACK = (0, 0, 0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", type=Path, help="first image")
    parser.add_argument("second", type=Path, help="second image")
    parser.add_argument("--duration", type=float, default=1.5, help="seconds per crossfade")
    parser.add_argument("--fps", type=int, default=60, help="target presentation rate")
    parser.add_argument("--cycles", type=int, default=10, help="forward/back crossfade pairs")
    parser.add_argument("--windowed", action="store_true", help="use a 1280x720 window")
    args = parser.parse_args()
    if args.duration <= 0:
        parser.error("--duration must be greater than zero")
    if args.fps <= 0:
        parser.error("--fps must be greater than zero")
    if args.cycles <= 0:
        parser.error("--cycles must be greater than zero")
    for image_path in (args.first, args.second):
        if not image_path.is_file():
            parser.error(f"image does not exist: {image_path}")
    return args


def display_ready_rgb(path: Path, size: tuple[int, int]) -> bytes:
    """Decode and letterbox an image once before it is uploaded to the GPU."""
    sw, sh = size
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        image.thumbnail(size, Image.LANCZOS)
        canvas = Image.new("RGB", size, BLACK)
        canvas.paste(image, ((sw - image.width) // 2, (sh - image.height) // 2))
        return canvas.tobytes()


def upload_texture(pixels: bytes, size: tuple[int, int]) -> int:
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
        size[0],
        size[1],
        0,
        GL_RGB,
        GL_UNSIGNED_BYTE,
        pixels,
    )
    return texture


def draw_texture(texture: int, alpha: float) -> None:
    glBindTexture(GL_TEXTURE_2D, texture)
    glColor4f(1.0, 1.0, 1.0, alpha)
    glBegin(GL_TRIANGLE_STRIP)
    # Pixel row zero comes from the top of the Pillow image, so it maps to the
    # top vertices. Inverting V here would display the texture upside down.
    glTexCoord2f(0.0, 0.0)
    glVertex2f(-1.0, 1.0)
    glTexCoord2f(1.0, 0.0)
    glVertex2f(1.0, 1.0)
    glTexCoord2f(0.0, 1.0)
    glVertex2f(-1.0, -1.0)
    glTexCoord2f(1.0, 1.0)
    glVertex2f(1.0, -1.0)
    glEnd()


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)
    return ordered[index]


def crossfade(old_texture: int, new_texture: int, duration: float, fps: int) -> tuple[bool, list[float]]:
    clock = pygame.time.Clock()
    frame_intervals: list[float] = []
    started = time.monotonic()
    previous_present: float | None = None

    while True:
        now = time.monotonic()
        progress = min(1.0, (now - started) / duration)
        eased = progress * progress * (3.0 - 2.0 * progress)

        glClear(GL_COLOR_BUFFER_BIT)
        draw_texture(old_texture, 1.0)
        draw_texture(new_texture, eased)
        pygame.display.flip()

        presented = time.monotonic()
        if previous_present is not None:
            frame_intervals.append(presented - previous_present)
        previous_present = presented

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False, frame_intervals
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                return False, frame_intervals

        if progress >= 1.0:
            return True, frame_intervals
        clock.tick_busy_loop(fps)


def configure_projection(size: tuple[int, int]) -> None:
    glViewport(0, 0, size[0], size[1])
    glMatrixMode(GL_PROJECTION)
    glLoadIdentity()
    glOrtho(-1.0, 1.0, -1.0, 1.0, -1.0, 1.0)
    glMatrixMode(GL_MODELVIEW)
    glLoadIdentity()
    glClearColor(0.0, 0.0, 0.0, 1.0)
    glEnable(GL_TEXTURE_2D)
    glEnable(GL_BLEND)
    glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)


def main() -> int:
    args = parse_args()
    pygame.init()
    pygame.mouse.set_visible(False)

    flags = pygame.OPENGL | pygame.DOUBLEBUF
    requested_size = (1280, 720) if args.windowed else (0, 0)
    if not args.windowed:
        flags |= pygame.FULLSCREEN

    try:
        try:
            display = pygame.display.set_mode(requested_size, flags, vsync=1)
        except TypeError:
            display = pygame.display.set_mode(requested_size, flags)
        size = display.get_size()
        configure_projection(size)
        vendor = (glGetString(GL_VENDOR) or b'unknown').decode('utf-8', errors='replace')
        renderer = (glGetString(GL_RENDERER) or b'unknown').decode('utf-8', errors='replace')
        version = (glGetString(GL_VERSION) or b'unknown').decode('utf-8', errors='replace')
        print(f"gpu-probe: OpenGL vendor={vendor} renderer={renderer} version={version}")

        first = upload_texture(display_ready_rgb(args.first, size), size)
        second = upload_texture(display_ready_rgb(args.second, size), size)
        intervals: list[float] = []
        keep_running = True

        for _ in range(args.cycles):
            keep_running, samples = crossfade(first, second, args.duration, args.fps)
            intervals.extend(samples)
            if not keep_running:
                break
            keep_running, samples = crossfade(second, first, args.duration, args.fps)
            intervals.extend(samples)
            if not keep_running:
                break

        target_interval = 1.0 / args.fps
        late = sum(value > target_interval * 1.5 for value in intervals)
        late_percent = (late / len(intervals) * 100.0) if intervals else 0.0
        print(
            "gpu-probe: "
            f"size={size[0]}x{size[1]} target={args.fps}fps samples={len(intervals)} "
            f"p50={percentile(intervals, 0.50) * 1000:.2f}ms "
            f"p95={percentile(intervals, 0.95) * 1000:.2f}ms "
            f"max={max(intervals, default=0.0) * 1000:.2f}ms "
            f"late={late_percent:.2f}%"
        )

        glDeleteTextures([first, second])
        return 0 if keep_running else 130
    finally:
        pygame.quit()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"gpu-probe: failed: {exc}", file=sys.stderr)
        raise
