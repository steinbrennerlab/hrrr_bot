"""The GIF is the artifact people actually download, so its shape is a contract.

These check the properties that break silently: a frame whose palette drifted,
a duration that stopped pausing, a canvas that moved, a file that quietly grew
past what is reasonable to serve on a phone.
"""

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


@pytest.fixture
def frames(tmp_path) -> list[Path]:
    """A short series that changes over a fixed background, like the real map."""
    paths = []
    for i in range(6):
        im = Image.new("RGB", (120, 90), "#f5f2ed")
        draw = ImageDraw.Draw(im)
        draw.rectangle((0, 60, 120, 90), fill="#dbe6ee")  # unchanging "water"
        draw.rectangle((10 + i * 8, 10, 40 + i * 8, 40), fill="#e0bc16")  # "smoke"
        path = tmp_path / f"f{i:03d}.png"
        im.save(path)
        paths.append(path)
    return paths


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
        assert im.size == (120, 90)


def _local_colour_tables(path: Path) -> int:
    """How many frames carry their own palette instead of using the global one.

    Walked out of the bytes because that is where the property lives: Pillow
    reports a frame with no local table as having no palette at all, which
    reads the same as a frame it simply has not decoded yet.
    """
    data = path.read_bytes()
    packed = data[10]
    i = 13 + (3 << ((packed & 0x07) + 1) if packed & 0x80 else 0)
    count = 0

    def skip_sub_blocks(j: int) -> int:
        while data[j]:
            j += data[j] + 1
        return j + 1

    while i < len(data) and data[i] != 0x3B:  # 0x3B = trailer
        if data[i] == 0x21:  # extension: label, then sub-blocks
            i = skip_sub_blocks(i + 2)
        elif data[i] == 0x2C:  # image descriptor
            flags = data[i + 9]
            if flags & 0x80:
                count += 1
                i += 10 + (3 << ((flags & 0x07) + 1))
            else:
                i += 10
            i = skip_sub_blocks(i + 1)  # LZW minimum code size, then data
        else:
            break
    return count


def test_every_frame_shares_one_palette(frames, tmp_path):
    """Per-frame palettes are what make a static region shimmer as it loops."""
    gif = make_gif(frames, tmp_path / "a.gif", fps=FPS)
    assert _local_colour_tables(gif) == 0


def test_unchanging_pixels_are_identical_across_frames(frames, tmp_path):
    """The other half of the same promise, measured on the pixels."""
    gif = make_gif(frames, tmp_path / "a.gif", fps=FPS)
    with Image.open(gif) as im:
        water = set()
        for i in range(im.n_frames):
            im.seek(i)
            water.add(im.convert("RGB").getpixel((5, 80)))
    assert len(water) == 1


def test_frames_are_not_dithered(frames, tmp_path):
    """A dithered band boundary misreports where a health category begins."""
    gif = make_gif(frames, tmp_path / "a.gif", fps=FPS)
    with Image.open(gif) as im:
        im.seek(0)
        block = [
            im.convert("RGB").getpixel((x, y))
            for x in range(14, 36)
            for y in range(14, 36)
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
        assert im.size == (120, 90)
        assert im.convert("RGB").getpixel((20, 20)) == (224, 188, 22)


def test_poster_needs_frames(tmp_path):
    with pytest.raises(ValueError):
        make_poster([], tmp_path / "p.png")
