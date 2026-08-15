"""Decide whether a cycle is worth animating.

The archive is only interesting on smoky days, so an animation is built only
when a reference city has actually seen -- or is forecast to see -- smoke at or
above a chosen AQI category during the current or previous local day.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .catalog import Cycle
from .config import AQI_LABELS, AQI_LEVELS, City, Domain
from .fetch import fetch_record
from .grid import SmokeFrame, city_series, load_frame

log = logging.getLogger(__name__)

# Lower bound of each AQI category, keyed by a name usable on the command line.
# "moderate" is 9 ug/m^3 under the 2024 PM2.5 breakpoints.
THRESHOLDS = {
    label.lower().replace(" ", "-"): level
    for label, level in zip(AQI_LABELS, AQI_LEVELS, strict=True)
}


@dataclass(frozen=True)
class GateResult:
    """What the reference city saw across the window, and whether that clears."""

    city: str
    threshold_label: str
    threshold: float
    peak: float
    peak_at: datetime | None
    samples: int

    @property
    def triggered(self) -> bool:
        return self.peak >= self.threshold

    def describe(self, tz: ZoneInfo) -> str:
        when = f" at {self.peak_at.astimezone(tz):%a %-I %p %Z}" if self.peak_at else ""
        verdict = "clears" if self.triggered else "below"
        return (
            f"{self.city} peak {self.peak:.1f} µg/m³{when} "
            f"({self.samples} samples) — {verdict} {self.threshold_label} "
            f"({self.threshold:g} µg/m³)"
        )


def find_city(domain: Domain, name: str) -> City:
    """Look up a labelled city on the domain by name, case-insensitively."""
    for city in domain.cities:
        if city.name.lower() == name.lower():
            return city
    known = ", ".join(c.name for c in domain.cities)
    raise ValueError(
        f"{name!r} is not a city on the {domain.name} domain. Try: {known}"
    )


def window(now: datetime, tz: ZoneInfo, days: int = 1) -> tuple[datetime, datetime]:
    """UTC bounds of local midnight `days` ago through the end of today.

    `days=1` is "the current or previous day": yesterday 00:00 local through
    today 23:59 local.
    """
    local = now.astimezone(tz)
    start = (local - timedelta(days=days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = local.replace(hour=23, minute=59, second=59, microsecond=0)
    return start.astimezone(UTC), end.astimezone(UTC)


def sample_points(
    cycle: Cycle,
    start: datetime,
    end: datetime,
    now: datetime,
    step_hours: int = 1,
) -> list[tuple[Cycle, int]]:
    """`(cycle, fhr)` pairs whose valid times cover the window.

    The current run only reaches forward, so hours already past are read from
    each hour's own analysis (`f00` of the cycle initialised at that hour) and
    the remainder of today comes from the current run's forecast.
    """
    points: list[tuple[Cycle, int]] = []

    cutoff = min(now, end)
    stamp = start.replace(minute=0, second=0, microsecond=0)
    while stamp <= cutoff:
        points.append((Cycle(run=stamp, fhrs=(0,)), 0))
        stamp += timedelta(hours=step_hours)

    for fhr in cycle.fhrs:
        valid = cycle.run + timedelta(hours=fhr)
        if cutoff < valid <= end:
            points.append((cycle, fhr))

    return points


def evaluate(
    session: requests.Session,
    cycle: Cycle,
    domain: Domain,
    cache_dir: Path,
    *,
    tz: ZoneInfo,
    city: str = "Seattle",
    threshold: str = "moderate",
    days: int = 1,
    step_hours: int = 1,
    workers: int = 6,
    now: datetime | None = None,
) -> GateResult:
    """Read the reference city's smoke across the window and compare it."""
    now = now or datetime.now(UTC)
    found = find_city(domain, city)
    level = THRESHOLDS[threshold]

    start, end = window(now, tz, days)
    points = sample_points(cycle, start, end, now, step_hours)
    log.info(
        "gate: sampling %s from %s to %s (%d points)",
        found.name,
        start.astimezone(tz).strftime("%a %-I %p %Z"),
        end.astimezone(tz).strftime("%a %-I %p %Z"),
        len(points),
    )

    def one(point: tuple[Cycle, int]) -> SmokeFrame | None:
        point_cycle, fhr = point
        try:
            path = fetch_record(session, point_cycle, fhr, cache_dir)
            return load_frame(path, fhr, domain)
        except Exception as exc:  # a gap in the record should not veto the run
            log.debug("gate sample %s f%02d skipped: %s", point_cycle, fhr, exc)
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        frames = [f for f in pool.map(one, points) if f is not None]

    if not frames:
        raise RuntimeError(
            "Gate read no smoke records; refusing to decide whether to render."
        )

    values = city_series(frames, found.lat, found.lon)
    peak_at_index = max(range(len(values)), key=values.__getitem__)
    return GateResult(
        city=found.name,
        threshold_label=threshold,
        threshold=level,
        peak=values[peak_at_index],
        peak_at=frames[peak_at_index].valid,
        samples=len(frames),
    )
