"""The GIF is the artifact people actually download, so its shape is a contract.

These check the properties that break silently: a frame whose palette drifted,
a duration that stopped pausing, a canvas that moved, a file that quietly grew
past what is reasonable to serve on a phone.
"""

import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from hrrr_smoke.animate import make_gif, make_poster

FPS = 4.0
PER_FRAME_MS = 250

# What one 48-hour animation may weigh before it stops being something you can
# open on a phone. The real ones land near 1-3 MB; this only catches a
# regression that multiplies that.
MAX_GIF_BYTES = 12_000_000


# The synthetic frame: a fixed left half, and a band that moves across the
# right. Named so a test can look at one without the other.
SIZE = (160, 90)
STATIC = (0, 0, 80, 90)
# A rectangle well inside the moving band on frame 0, which is one flat colour.
FLAT = ((90, 20), (110, 60))
BANDS = ("#e0bc16", "#d62529", "#8f3f97", "#5aa95f", "#e07b18", "#7e0023")


@pytest.fixture
def frames(tmp_path) -> list[Path]:
    """A short series that changes over a fixed background, like the real map.

    The fixed half is a colour *gradient* rather than a flat fill, on purpose.
    A quantiser fitting a palette to one frame at a time has to spend its 256
    slots on whichever colours that frame contains, so a rich static region
    lands slightly differently as the moving band's colours compete with it --
    which is exactly the shimmer these guard against. Two flat colours would
    survive per-frame quantisation unchanged and prove nothing.
    """
    paths = []
    for i, band in enumerate(BANDS):
        im = Image.new("RGB", (160, 90))
        pixels = im.load()
        for x in range(STATIC[2]):
            for y in range(STATIC[3]):
                pixels[x, y] = (60 + x * 2, 90 + y, 200 - x)
        draw = ImageDraw.Draw(im)
        draw.rectangle((80, 0, 160, 90), fill="#f5f2ed")
        draw.rectangle((85 + i * 10, 10, 115 + i * 10, 70), fill=band)
        path = tmp_path / f"f{i:03d}.png"
        im.save(path)
        paths.append(path)
    return paths


def _static_renderings(path: Path) -> int:
    """How many different ways the unchanging region got drawn."""
    seen = set()
    with Image.open(path) as im:
        for i in range(im.n_frames):
            im.seek(i)
            seen.add(im.convert("RGB").crop(STATIC).tobytes())
    return len(seen)


def test_gif_holds_its_first_and_last_frames(frames, tmp_path):
    gif = make_gif(frames, tmp_path / "a.gif", fps=FPS, start_pause=0.7, end_pause=1.4)

    with Image.open(gif) as im:
        durations = []
        for i in range(im.n_frames):
            im.seek(i)
            durations.append(im.info["duration"])

    assert durations[0] == 700
    assert durations[-1] == 1400
    assert durations[1:-1] == [PER_FRAME_MS] * (len(frames) - 2)


def test_a_pause_shorter_than_a_frame_does_not_speed_it_up(frames, tmp_path):
    gif = make_gif(frames, tmp_path / "a.gif", fps=FPS, start_pause=0.0, end_pause=0.0)
    with Image.open(gif) as im:
        im.seek(0)
        assert im.info["duration"] == PER_FRAME_MS
        im.seek(im.n_frames - 1)
        assert im.info["duration"] == PER_FRAME_MS


def test_gif_keeps_every_frame_and_the_canvas_size(frames, tmp_path):
    gif = make_gif(frames, tmp_path / "a.gif", fps=FPS)
    with Image.open(gif) as im:
        assert im.n_frames == len(frames)
        assert im.size == SIZE


def test_unchanging_pixels_are_identical_across_frames(frames, tmp_path):
    """The anti-shimmer promise, measured where a reader would see it break.

    Asserted on decoded pixels rather than on how the palette is stored: a
    shared palette can legitimately be written as one global colour table or as
    identical local ones, and which you get depends on Pillow's optimiser and
    on whether gifsicle ran. What must not vary is the picture.
    """
    gif = make_gif(frames, tmp_path / "a.gif", fps=FPS)
    assert _static_renderings(gif) == 1


def test_per_frame_palettes_would_fail_that(frames, tmp_path):
    """The negative control, so the test above cannot pass vacuously."""
    images = [Image.open(p).convert("RGB") for p in frames]
    quantised = [im.quantize(colors=256) for im in images]
    out = tmp_path / "per-frame.gif"
    quantised[0].save(
        out,
        save_all=True,
        append_images=quantised[1:],
        duration=250,
        loop=0,
        optimize=True,
    )
    assert _static_renderings(out) == len(frames)


def test_the_palette_is_shared_even_without_gifsicle(frames, tmp_path, monkeypatch):
    """gifsicle is optional, so it must not be what makes this true."""
    real = shutil.which
    monkeypatch.setattr(
        shutil, "which", lambda name: None if name == "gifsicle" else real(name)
    )
    gif = make_gif(frames, tmp_path / "a.gif", fps=FPS)
    assert _static_renderings(gif) == 1


def test_frames_are_not_dithered(frames, tmp_path):
    """A dithered band boundary misreports where a health category begins."""
    gif = make_gif(frames, tmp_path / "a.gif", fps=FPS)
    with Image.open(gif) as im:
        im.seek(0)
        rgb = im.convert("RGB")
        block = [
            rgb.getpixel((x, y))
            for x in range(FLAT[0][0], FLAT[1][0])
            for y in range(FLAT[0][1], FLAT[1][1])
        ]
    assert len(set(block)) == 1


def test_gif_stays_small_enough_to_open_on_a_phone(frames, tmp_path):
    gif = make_gif(frames, tmp_path / "a.gif", fps=FPS)
    assert gif.stat().st_size <= MAX_GIF_BYTES


def test_gif_needs_frames(tmp_path):
    with pytest.raises(ValueError):
        make_gif([], tmp_path / "a.gif")


def test_poster_is_a_still_of_the_chosen_frame(frames, tmp_path):
    poster = make_poster(frames, tmp_path / "p.png", index=0)
    with Image.open(poster) as im:
        assert im.format == "PNG"
        assert im.size == SIZE
        assert im.convert("RGB").getpixel(FLAT[0]) == (224, 188, 22)  # #e0bc16


def test_poster_needs_frames(tmp_path):
    with pytest.raises(ValueError):
        make_poster([], tmp_path / "p.png")
