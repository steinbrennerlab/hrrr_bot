from datetime import UTC, datetime

import pytest

from hrrr_smoke.catalog import Cycle, _list_surface_fhrs, find_latest_cycle, select_fhrs


def _cycle(hour: int, fhrs=range(0, 49)) -> Cycle:
    run = datetime(2026, 8, 6, hour, tzinfo=UTC)
    return Cycle(run=run, fhrs=tuple(fhrs))


class FakeResponse:
    def __init__(self, body: bytes):
        self.content = body

    def raise_for_status(self) -> None:
        pass


class FakeSession:
    """Stands in for requests.Session, serving canned S3 listings."""

    def __init__(self, listings: dict[str, bytes]):
        self.listings = listings
        self.calls: list[str] = []

    def get(self, url, params=None, **kwargs):
        prefix = (params or {}).get("prefix", "")
        self.calls.append(prefix)
        return FakeResponse(self.listings.get(prefix, _listing([])))


def _listing(keys: list[str], truncated: bool = False) -> bytes:
    contents = "".join(f"<Contents><Key>{k}</Key></Contents>" for k in keys)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
        f"<IsTruncated>{'true' if truncated else 'false'}</IsTruncated>"
        f"{contents}</ListBucketResult>"
    ).encode()


def _keys(date: str, hour: int, fhrs, with_idx=True) -> list[str]:
    out = []
    for f in fhrs:
        stem = f"hrrr.{date}/conus/hrrr.t{hour:02d}z.wrfsfcf{f:02d}.grib2"
        out.append(stem)
        if with_idx:
            out.append(stem + ".idx")
    return out


def test_grib_url():
    assert _cycle(12).grib_url(6).endswith(
        "hrrr.20260806/conus/hrrr.t12z.wrfsfcf06.grib2"
    )


def test_extended_cycles_run_to_48_others_to_18():
    assert _cycle(12).max_fhr == 48
    assert _cycle(13).max_fhr == 18


def test_select_fhrs_clamps_to_cycle_length():
    # A 13Z cycle only publishes 18 hours, even if 36 are requested.
    assert select_fhrs(_cycle(13), 0, 36, 1)[-1] == 18
    assert select_fhrs(_cycle(12), 0, 36, 1)[-1] == 36


def test_select_fhrs_honours_step_and_start():
    assert select_fhrs(_cycle(12), 6, 18, 6) == [6, 12, 18]


def test_select_fhrs_skips_unpublished_hours():
    cycle = _cycle(12, fhrs=[0, 1, 2, 5])
    assert select_fhrs(cycle, 0, 6, 1) == [0, 1, 2, 5]


def test_listing_requires_both_grib_and_index():
    prefix = "hrrr.20260806/conus/hrrr.t12z.wrfsfcf"
    keys = _keys("20260806", 12, [0, 1])
    keys.append("hrrr.20260806/conus/hrrr.t12z.wrfsfcf02.grib2")  # no .idx yet
    session = FakeSession({prefix: _listing(keys)})
    run = datetime(2026, 8, 6, 12, tzinfo=UTC)
    assert _list_surface_fhrs(session, run) == (0, 1)


def test_find_latest_cycle_skips_a_barely_started_run():
    now = datetime(2026, 8, 6, 15, 30, tzinfo=UTC)
    listings = {
        # 15Z has only just started writing output.
        "hrrr.20260806/conus/hrrr.t15z.wrfsfcf": _listing(
            _keys("20260806", 15, [0, 1])
        ),
        "hrrr.20260806/conus/hrrr.t14z.wrfsfcf": _listing(
            _keys("20260806", 14, range(0, 19))
        ),
    }
    cycle = find_latest_cycle(FakeSession(listings), min_fhrs=12, now=now)
    assert cycle.hour == 14
    assert len(cycle.fhrs) == 19


def test_find_latest_cycle_falls_back_to_partial_run():
    now = datetime(2026, 8, 6, 15, 30, tzinfo=UTC)
    listings = {
        "hrrr.20260806/conus/hrrr.t15z.wrfsfcf": _listing(
            _keys("20260806", 15, [0, 1])
        )
    }
    cycle = find_latest_cycle(FakeSession(listings), min_fhrs=12, look_back=3, now=now)
    assert cycle.hour == 15  # partial beats nothing


def test_extended_only_skips_the_18_hour_cycles():
    # 14Z is newer, but only the 00/06/12/18Z runs reach 48 hours.
    now = datetime(2026, 8, 6, 14, 30, tzinfo=UTC)
    listings = {
        "hrrr.20260806/conus/hrrr.t14z.wrfsfcf": _listing(
            _keys("20260806", 14, range(0, 19))
        ),
        "hrrr.20260806/conus/hrrr.t12z.wrfsfcf": _listing(
            _keys("20260806", 12, range(0, 49))
        ),
    }
    cycle = find_latest_cycle(
        FakeSession(listings), extended_only=True, min_fhrs=48, now=now
    )
    assert cycle.hour == 12
    assert cycle.max_fhr == 48
    assert max(cycle.fhrs) == 48


def test_extended_only_never_lists_a_short_cycle():
    now = datetime(2026, 8, 6, 14, 30, tzinfo=UTC)
    session = FakeSession({})
    with pytest.raises(RuntimeError):
        find_latest_cycle(session, extended_only=True, now=now)
    # The walk back must not spend requests on cycles that cannot be 48 hours.
    assert all("t12z" in p or "t06z" in p or "t18z" in p or "t00z" in p
               for p in session.calls)


def test_extended_only_falls_back_when_the_newest_run_is_incomplete():
    # 12Z is still integrating (f00-f24 only); 06Z has the full 48 hours.
    now = datetime(2026, 8, 6, 13, 30, tzinfo=UTC)
    listings = {
        "hrrr.20260806/conus/hrrr.t12z.wrfsfcf": _listing(
            _keys("20260806", 12, range(0, 25))
        ),
        "hrrr.20260806/conus/hrrr.t06z.wrfsfcf": _listing(
            _keys("20260806", 6, range(0, 49))
        ),
    }
    cycle = find_latest_cycle(
        FakeSession(listings), extended_only=True, min_fhrs=48, now=now
    )
    assert cycle.hour == 6
    assert max(cycle.fhrs) == 48


def test_extended_lookback_reaches_a_second_cycle():
    # Six hours apart means the default eight-hour walk back would find only
    # one extended cycle; the search must reach further.
    now = datetime(2026, 8, 6, 13, 0, tzinfo=UTC)
    listings = {
        "hrrr.20260806/conus/hrrr.t06z.wrfsfcf": _listing(
            _keys("20260806", 6, range(0, 49))
        ),
    }
    cycle = find_latest_cycle(
        FakeSession(listings), extended_only=True, min_fhrs=48, now=now
    )
    assert cycle.hour == 6


def test_default_search_still_prefers_the_newest_short_cycle():
    now = datetime(2026, 8, 6, 14, 30, tzinfo=UTC)
    listings = {
        "hrrr.20260806/conus/hrrr.t14z.wrfsfcf": _listing(
            _keys("20260806", 14, range(0, 19))
        ),
        "hrrr.20260806/conus/hrrr.t12z.wrfsfcf": _listing(
            _keys("20260806", 12, range(0, 49))
        ),
    }
    cycle = find_latest_cycle(FakeSession(listings), now=now)
    assert cycle.hour == 14


def test_find_latest_cycle_raises_when_bucket_is_empty():
    with pytest.raises(RuntimeError, match="No HRRR surface files"):
        find_latest_cycle(
            FakeSession({}),
            look_back=2,
            now=datetime(2026, 8, 6, 15, tzinfo=UTC),
        )
