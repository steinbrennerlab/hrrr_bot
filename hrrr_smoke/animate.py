"""Stitch rendered frames into an animated GIF (and an MP4 when ffmpeg exists)."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)


def _uniform_size(paths: list[Path]) -> list[Image.Image]:
    """Open frames and pad them to a common size.

    `bbox_inches='tight'` can vary the canvas by a pixel or two between frames,
    which GIF will not tolerate.
    """
    images = [Image.open(p).convert("RGB") for p in paths]
    width = max(im.width for im in images)
    height = max(im.height for im in images)
    out = []
    for im in images:
        if im.size == (width, height):
            out.append(im)
            continue
        canvas = Image.new("RGB", (width, height), "white")
        canvas.paste(im, ((width - im.width) // 2, (height - im.height) // 2))
        out.append(canvas)
    return out


def make_gif(
    frames: list[Path], out_path: Path, *, fps: float = 4.0, end_pause: float = 1.2
) -> Path:
    """Write a looping GIF, holding the last frame briefly before it restarts."""
    if not frames:
        raise ValueError("No frames to animate.")
    images = _uniform_size(frames)
    per_frame = int(round(1000 / fps))
    durations = [per_frame] * len(images)
    durations[-1] = max(per_frame, int(end_pause * 1000))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(
        out_path,
        save_all=True,
        append_images=images[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )
    log.info(
        "wrote %s (%d frames, %.1f MB)",
        out_path,
        len(images),
        out_path.stat().st_size / 1e6,
    )
    return out_path


def make_mp4(frames: list[Path], out_path: Path, *, fps: float = 4.0) -> Path | None:
    """Write an MP4 if ffmpeg is on PATH; return None if it is not."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        log.info("ffmpeg not found; skipping MP4 (the GIF is still written).")
        return None
    if not frames:
        raise ValueError("No frames to animate.")

    listing = out_path.parent / "frames.txt"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    listing.write_text(
        "".join(f"file '{p.resolve()}'\nduration {1 / fps:.4f}\n" for p in frames)
        + f"file '{frames[-1].resolve()}'\n"
    )
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(listing),
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    listing.unlink(missing_ok=True)
    log.info("wrote %s", out_path)
    return out_path
