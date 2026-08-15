"""Stitch rendered frames into an animated GIF (and an MP4 when ffmpeg exists)."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from PIL import Image

log = logging.getLogger(__name__)

# How much to shrink each frame before sampling it for the shared palette.
# Nearest-neighbour, so every sampled pixel is a colour that genuinely occurs
# in the frame -- a box filter would average neighbours into colours the map
# never contained and hand the quantiser a blurred version of the palette.
_PALETTE_SUBSAMPLE = 3


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


def _shared_palette(images: list[Image.Image]) -> Image.Image:
    """One 256-colour palette derived from every frame at once.

    Quantising each frame on its own gives each one a palette fitted to its own
    smoke, so a colour that is stable in the data -- the water, the coastline,
    an unchanging band of Moderate -- lands on a slightly different index from
    hour to hour and shimmers as the animation plays. Choosing the palette once,
    from the whole series, makes those pixels bit-identical across frames, which
    also gives the GIF encoder long runs to compress.
    """
    step = _PALETTE_SUBSAMPLE
    thumbs = [
        im.resize((im.width // step, im.height // step), Image.Resampling.NEAREST)
        for im in images
    ]
    width = thumbs[0].width
    strip = Image.new("RGB", (width, sum(t.height for t in thumbs)))
    y = 0
    for thumb in thumbs:
        strip.paste(thumb, (0, y))
        y += thumb.height
    return strip.quantize(colors=256, method=Image.Quantize.MEDIANCUT)


def _optimise(path: Path) -> None:
    """Hand the finished GIF to gifsicle, if it is installed.

    `-O3` only rewrites how the frames are encoded, never their colours, so the
    AQI bands survive it exactly. Purely a bonus: the GIF is already valid and
    correct before this runs, and stays that way if gifsicle is missing.
    """
    gifsicle = shutil.which("gifsicle")
    if not gifsicle:
        log.debug("gifsicle not found; leaving the GIF as Pillow wrote it.")
        return
    before = path.stat().st_size
    try:
        subprocess.run(
            [gifsicle, "-O3", "--careful", "-o", str(path), str(path)],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        log.warning("gifsicle failed (%s); keeping the unoptimised GIF.", exc)
        return
    after = path.stat().st_size
    log.info(
        "gifsicle -O3: %.1f MB -> %.1f MB (%.0f%% smaller)",
        before / 1e6,
        after / 1e6,
        100 * (1 - after / before),
    )


def make_gif(
    frames: list[Path],
    out_path: Path,
    *,
    fps: float = 4.0,
    start_pause: float = 0.7,
    end_pause: float = 1.4,
) -> Path:
    """Write a looping GIF that pauses on its first and last frames.

    A loop with no rest reads as a flicker: by the time you have found the
    forecast hour in the corner it has already moved on. Holding the opening
    frame gives the eye somewhere to start each pass, and holding the last one
    gives the 48-hour outcome a beat before it snaps back.
    """
    if not frames:
        raise ValueError("No frames to animate.")
    images = _uniform_size(frames)
    per_frame = int(round(1000 / fps))
    durations = [per_frame] * len(images)
    durations[0] = max(per_frame, int(start_pause * 1000))
    durations[-1] = max(per_frame, int(end_pause * 1000))

    palette = _shared_palette(images)
    # Dithering trades exact colours for apparent depth, which is the wrong
    # trade here: these are health categories, and a dithered boundary between
    # Moderate and Unhealthy is a lie about where the boundary is.
    quantised = [
        im.quantize(palette=palette, dither=Image.Dither.NONE) for im in images
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    quantised[0].save(
        out_path,
        save_all=True,
        append_images=quantised[1:],
        duration=durations,
        loop=0,
        optimize=True,
        # Every frame shares one palette and covers the whole canvas, so each
        # can simply be left in place for the next to draw over. Saying so
        # explicitly keeps decoders from guessing, which is where the flashes
        # of background between frames come from.
        disposal=1,
    )
    _optimise(out_path)
    log.info(
        "wrote %s (%d frames, %.1f MB)",
        out_path,
        len(images),
        out_path.stat().st_size / 1e6,
    )
    return out_path


def make_poster(frames: list[Path], out_path: Path, *, index: int = 0) -> Path:
    """Copy one frame out as the video's poster image.

    A `<video>` with no poster is a grey rectangle until it has buffered, which
    on a slow connection is most of the time someone spends looking at it.
    """
    if not frames:
        raise ValueError("No frames to choose a poster from.")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(frames[index]) as im:
        im.convert("RGB").save(out_path, format="PNG", optimize=True)
    log.info("wrote %s", out_path)
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
