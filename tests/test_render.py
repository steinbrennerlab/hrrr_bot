from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import numpy as np

from hrrr_smoke.config import AQI_LABELS, AQI_LEVELS, DOMAINS
from hrrr_smoke.grid import SmokeFrame
from hrrr_smoke.render import FrameRenderer, _palette


def test_every_band_and_the_overflow_arrow_has_a_label():
    # Bands between the breakpoints, plus one unbounded band on top.
    assert len(AQI_LABELS) == len(AQI_LEVELS)


def test_palette_bins_values_by_aqi_category():
    cmap, norm = _palette("aqi")
    # A value inside each band maps to that band's index.
    assert norm(0.5) == 0  # clear
    assert norm(5.0) == 1  # good
    assert norm(20.0) == 2  # moderate
    assert norm(45.0) == 3  # sensitive groups
    assert norm(90.0) == 4  # unhealthy
    assert norm(200.0) == 5  # very unhealthy
    # Above the last breakpoint the norm overflows into the "over" colour.
    assert norm(900.0) >= cmap.N


def test_clear_band_is_transparent_so_the_basemap_shows_through():
    cmap, _ = _palette("aqi")
    assert cmap(0)[3] == 0.0


def test_mono_palette_has_the_same_breakpoints():
    _, aqi_norm = _palette("aqi")
    _, mono_norm = _palette("mono")
    assert list(aqi_norm.boundaries) == list(mono_norm.boundaries)


def _frame(fhr: int = 3) -> SmokeFrame:
    domain = DOMAINS["puget"]
    lats, lons = np.meshgrid(
        np.linspace(domain.lat_min, domain.lat_max, 20),
        np.linspace(domain.lon_min, domain.lon_max, 20),
        indexing="ij",
    )
    values = np.linspace(0, 300, 400).reshape(20, 20)
    run = datetime(2026, 8, 6, 12, tzinfo=UTC)
    valid = datetime(2026, 8, 6, 15, tzinfo=UTC)
    return SmokeFrame(
        fhr=fhr, run=run, valid=valid, lats=lats, lons=lons, values=values
    )


def test_renderer_writes_identically_sized_frames(tmp_path):
    from PIL import Image

    tz = ZoneInfo("America/Los_Angeles")
    with FrameRenderer(DOMAINS["puget"], tz=tz, palette="aqi", dpi=60) as renderer:
        sizes = set()
        for fhr in (1, 2, 13):  # 1 PM is a wider title string than 1 AM
            out = renderer.render(_frame(fhr), tmp_path / f"f{fhr:03d}.png")
            assert out.exists() and out.stat().st_size > 0
            sizes.add(Image.open(out).size)
    # A GIF cannot be built from frames whose canvas moves between hours.
    assert len(sizes) == 1
