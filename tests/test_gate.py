from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from hrrr_smoke.catalog import Cycle
from hrrr_smoke.config import DOMAINS
from hrrr_smoke.gate import (
    THRESHOLDS,
    GateResult,
    find_city,
    sample_points,
    window,
)

TZ = ZoneInfo("America/Los_Angeles")
PUGET = DOMAINS["puget"]


def _cycle(hour: int = 15, day: int = 6, fhrs=range(0, 19)) -> Cycle:
    return Cycle(run=datetime(2026, 8, day, hour, tzinfo=UTC), fhrs=tuple(fhrs))


def test_moderate_is_the_2024_pm25_breakpoint():
    assert THRESHOLDS["moderate"] == 9.0


def test_every_aqi_category_is_selectable():
    assert set(THRESHOLDS) == {
        "clear",
        "good",
        "moderate",
        "sensitive",
        "unhealthy",
        "very-unhealthy",
        "hazardous",
    }


def test_thresholds_are_category_lower_bounds():
    assert THRESHOLDS["good"] < THRESHOLDS["moderate"] < THRESHOLDS["unhealthy"]


def test_window_spans_yesterday_midnight_to_tonight():
    now = datetime(2026, 8, 6, 17, tzinfo=UTC)  # Thu 10 AM PDT
    start, end = window(now, TZ, days=1)
    assert start.astimezone(TZ).strftime("%a %H:%M") == "Wed 00:00"
    assert end.astimezone(TZ).strftime("%a %H:%M") == "Thu 23:59"


def test_window_days_zero_is_today_only():
    now = datetime(2026, 8, 6, 17, tzinfo=UTC)
    start, end = window(now, TZ, days=0)
    assert start.astimezone(TZ).strftime("%a %H:%M") == "Thu 00:00"
    assert end.astimezone(TZ).strftime("%a %H:%M") == "Thu 23:59"


def test_window_crossing_a_month_boundary():
    now = datetime(2026, 9, 1, 17, tzinfo=UTC)
    start, _ = window(now, TZ, days=1)
    assert start.astimezone(TZ).strftime("%b %d") == "Aug 31"


def test_past_hours_come_from_their_own_analysis():
    now = datetime(2026, 8, 6, 17, tzinfo=UTC)
    start, end = window(now, TZ, days=1)
    points = sample_points(_cycle(), start, end, now, step_hours=1)

    past = [(c, f) for c, f in points if c.run + timedelta(hours=f) <= now]
    # The current run cannot reach backwards, so history is read as f00 of the
    # cycle initialised at each hour.
    assert all(fhr == 0 for _, fhr in past)
    hours_back = int((now - start).total_seconds() // 3600)
    assert {c.run for c, _ in past} == {
        start + timedelta(hours=i) for i in range(hours_back + 1)
    }


def test_future_hours_come_from_the_current_run():
    now = datetime(2026, 8, 6, 17, tzinfo=UTC)
    cycle = _cycle()
    start, end = window(now, TZ, days=1)
    points = sample_points(cycle, start, end, now, step_hours=1)

    future = [(c, f) for c, f in points if c.run + timedelta(hours=f) > now]
    assert future, "the rest of today should be covered by the forecast"
    assert all(c.run == cycle.run for c, _ in future)


def test_samples_never_cover_the_same_valid_time_twice():
    now = datetime(2026, 8, 6, 17, tzinfo=UTC)
    start, end = window(now, TZ, days=1)
    points = sample_points(_cycle(), start, end, now, step_hours=1)
    valid = [c.run + timedelta(hours=f) for c, f in points]
    assert len(valid) == len(set(valid))


def test_samples_stay_inside_the_window():
    now = datetime(2026, 8, 6, 17, tzinfo=UTC)
    start, end = window(now, TZ, days=1)
    points = sample_points(_cycle(), start, end, now, step_hours=1)
    valid = [c.run + timedelta(hours=f) for c, f in points]
    assert min(valid) >= start
    assert max(valid) <= end


def test_step_hours_thins_the_history():
    now = datetime(2026, 8, 6, 17, tzinfo=UTC)
    start, end = window(now, TZ, days=1)
    hourly = sample_points(_cycle(), start, end, now, step_hours=1)
    coarse = sample_points(_cycle(), start, end, now, step_hours=3)
    assert len(coarse) < len(hourly)


def test_a_forecast_beyond_tonight_is_not_sampled():
    # A 48-hour run reaches into the day after tomorrow; the gate only asks
    # about the current and previous day.
    now = datetime(2026, 8, 6, 17, tzinfo=UTC)
    start, end = window(now, TZ, days=1)
    points = sample_points(_cycle(fhrs=range(0, 49)), start, end, now, step_hours=1)
    assert all(c.run + timedelta(hours=f) <= end for c, f in points)


def _result(peak: float) -> GateResult:
    return GateResult(
        city="Seattle",
        threshold_label="moderate",
        threshold=9.0,
        peak=peak,
        peak_at=datetime(2026, 8, 6, 15, tzinfo=UTC),
        samples=48,
    )


def test_trigger_is_inclusive_of_the_breakpoint():
    assert _result(9.0).triggered
    assert _result(12.0).triggered
    assert not _result(8.9).triggered


def test_describe_reports_the_verdict_and_local_time():
    text = _result(12.0).describe(TZ)
    assert "Seattle peak 12.0" in text
    assert "clears moderate" in text
    assert "PDT" in text
    assert "below" not in text


def test_find_city_is_case_insensitive():
    assert find_city(PUGET, "seattle")[0] == "Seattle"


def test_find_city_lists_options_when_unknown():
    with pytest.raises(ValueError, match="Seattle"):
        find_city(PUGET, "Spokane")  # on the cascadia domain, not puget
