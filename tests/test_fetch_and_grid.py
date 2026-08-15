from datetime import UTC, datetime

import numpy as np
import pytest

from hrrr_smoke.config import AQI_LEVELS, KG_M3_TO_UG_M3
from hrrr_smoke.fetch import RecordNotFound, _byte_range
from hrrr_smoke.grid import SmokeFrame, city_series

IDX = "\n".join(
    [
        "1:0:d=2026080612:REFC:entire atmosphere:6 hour fcst:",
        "2:100:d=2026080612:MASSDEN:8 m above ground:6 hour fcst:",
        "3:950:d=2026080612:TMP:2 m above ground:6 hour fcst:",
        "4:1400:d=2026080612:COLMD:entire atmosphere:6 hour fcst:",
    ]
)


def test_byte_range_stops_at_the_next_record():
    assert _byte_range(IDX, "MASSDEN", "8 m above ground") == (100, 949)


def test_byte_range_of_last_record_is_open_ended():
    assert _byte_range(IDX, "COLMD", "entire atmosphere") == (1400, None)


def test_byte_range_is_level_specific():
    # MASSDEN also appears at other levels on some grids; near-surface smoke is
    # specifically the 8 m record.
    with pytest.raises(RecordNotFound):
        _byte_range(IDX, "MASSDEN", "surface")


def test_byte_range_missing_variable():
    with pytest.raises(RecordNotFound):
        _byte_range(IDX, "NOSUCHVAR", "surface")


def test_byte_range_tolerates_blank_lines():
    assert _byte_range(IDX + "\n\n", "MASSDEN", "8 m above ground") == (100, 949)


def _frame(fhr: int, values: np.ndarray) -> SmokeFrame:
    lats, lons = np.meshgrid(
        np.linspace(47.0, 48.0, values.shape[0]),
        np.linspace(-123.0, -122.0, values.shape[1]),
        indexing="ij",
    )
    run = datetime(2026, 8, 6, 12, tzinfo=UTC)
    return SmokeFrame(
        fhr=fhr, run=run, valid=run, lats=lats, lons=lons, values=values
    )


def test_city_series_reads_the_nearest_cell_every_frame():
    base = np.zeros((3, 3))
    base[1, 1] = 40.0  # centre cell: 47.5 N, 122.5 W
    frames = [_frame(0, base), _frame(1, base * 2)]
    assert city_series(frames, 47.5, -122.5) == [40.0, 80.0]


def test_city_series_is_empty_without_frames():
    assert city_series([], 47.6, -122.3) == []


def test_unit_conversion_factor():
    # GRIB stores kg m^-3; 1e-8 kg m^-3 is 10 ug m^-3.
    assert 1e-8 * KG_M3_TO_UG_M3 == pytest.approx(10.0)


def test_aqi_levels_are_increasing():
    assert list(AQI_LEVELS) == sorted(AQI_LEVELS)


def test_the_domain_box_is_marked_on_the_frame():
    """The clipped array is rectangular in grid space, not in lat/lon.

    A Lambert grid's rows and columns are not parallels and meridians, so the
    bounding rows/columns of a lat/lon box overshoot it -- on the Puget Sound
    domain by about a third of the cells, far enough north to reach fires in
    British Columbia. The frame carries which cells are genuinely inside so a
    summary can leave the rest out.
    """
    from hrrr_smoke.config import DOMAINS

    domain = DOMAINS["puget"]
    lats, lons = np.meshgrid(
        np.linspace(domain.lat_min - 1.0, domain.lat_max + 1.0, 12),
        np.linspace(domain.lon_min, domain.lon_max, 12),
        indexing="ij",
    )
    values = np.full((12, 12), 5.0)
    inside = (lats >= domain.lat_min) & (lats <= domain.lat_max)
    values[0, 0] = 4521.0  # a fire well north of the box
    assert not inside[0, 0]

    frame = SmokeFrame(
        fhr=0,
        run=datetime(2026, 8, 14, 12, tzinfo=UTC),
        valid=datetime(2026, 8, 14, 12, tzinfo=UTC),
        lats=lats,
        lons=lons,
        values=values,
        inside=inside,
    )
    assert frame.peak == 5.0
    assert values.max() == 4521.0  # ...still present, and still drawn


def test_a_frame_with_no_mask_counts_every_cell():
    """Hand-built frames are plain rectangles; nothing to exclude."""
    frame = SmokeFrame(
        fhr=0,
        run=datetime(2026, 8, 14, 12, tzinfo=UTC),
        valid=datetime(2026, 8, 14, 12, tzinfo=UTC),
        lats=np.zeros((2, 2)),
        lons=np.zeros((2, 2)),
        values=np.array([[1.0, 9.0], [3.0, 4.0]]),
    )
    assert frame.peak == 9.0
