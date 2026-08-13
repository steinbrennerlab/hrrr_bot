"""Work out which HRRR cycle to use and which forecast hours it has published."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import requests

from .config import (
    EXTENDED_CYCLES,
    EXTENDED_MAX_FHR,
    S3_BUCKET_URL,
    STANDARD_MAX_FHR,
)

_S3_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
_SFC_KEY = re.compile(r"hrrr\.t(\d{2})z\.wrfsfcf(\d{2})\.grib2$")


@dataclass(frozen=True)
class Cycle:
    """One HRRR run, identified by its initialisation time in UTC."""

    run: datetime
    fhrs: tuple[int, ...]

    @property
    def date_str(self) -> str:
        return self.run.strftime("%Y%m%d")

    @property
    def hour(self) -> int:
        return self.run.hour

    @property
    def max_fhr(self) -> int:
        return EXTENDED_MAX_FHR if self.hour in EXTENDED_CYCLES else STANDARD_MAX_FHR

    def grib_url(self, fhr: int) -> str:
        return (
            f"{S3_BUCKET_URL}/hrrr.{self.date_str}/conus/"
            f"hrrr.t{self.hour:02d}z.wrfsfcf{fhr:02d}.grib2"
        )

    def __str__(self) -> str:
        return f"{self.run:%Y-%m-%d %HZ}"


def _list_surface_fhrs(session: requests.Session, run: datetime) -> tuple[int, ...]:
    """Forecast hours whose surface file *and* index are both on S3 for `run`."""
    prefix = f"hrrr.{run:%Y%m%d}/conus/hrrr.t{run.hour:02d}z.wrfsfcf"
    fhrs: set[int] = set()
    have_idx: set[int] = set()
    token: str | None = None

    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        resp = session.get(S3_BUCKET_URL, params=params, timeout=60)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        for node in root.findall("s3:Contents/s3:Key", _S3_NS):
            key = node.text or ""
            if key.endswith(".idx"):
                match = _SFC_KEY.search(key[: -len(".idx")])
                if match:
                    have_idx.add(int(match.group(2)))
            else:
                match = _SFC_KEY.search(key)
                if match:
                    fhrs.add(int(match.group(2)))

        if (root.findtext("s3:IsTruncated", "", _S3_NS) or "").lower() != "true":
            break
        token = root.findtext("s3:NextContinuationToken", None, _S3_NS)
        if not token:
            break

    # Without the .idx we would have to download the whole 130 MB file, so a
    # forecast hour only counts as available once its index has landed too.
    return tuple(sorted(fhrs & have_idx))


def find_latest_cycle(
    session: requests.Session,
    *,
    min_fhrs: int = 12,
    look_back: int | None = None,
    now: datetime | None = None,
    extended_only: bool = False,
) -> Cycle:
    """Newest cycle that has published at least `min_fhrs` forecast hours.

    NCEP posts files as the model integrates, so the newest cycle on S3 is
    usually incomplete for the first hour or so. Walking backwards avoids
    building an animation out of the three frames that happen to exist yet.

    `extended_only` restricts the search to the cycles that forecast out to 48
    hours. Those finish about 1.8 hours after their initialisation time, so a
    caller wanting a complete long run should not start looking before then.
    """
    now = now or datetime.now(UTC)
    top = now.replace(minute=0, second=0, microsecond=0)
    if look_back is None:
        # Extended cycles are six hours apart, so an eight-hour walk back can
        # straddle at most one of them -- give the search room for two.
        look_back = 14 if extended_only else 8
    best: Cycle | None = None

    for back in range(look_back):
        run = top - timedelta(hours=back)
        if extended_only and run.hour not in EXTENDED_CYCLES:
            continue
        fhrs = _list_surface_fhrs(session, run)
        if not fhrs:
            continue
        cycle = Cycle(run=run, fhrs=fhrs)
        if best is None:
            best = cycle
        if len(fhrs) >= min_fhrs:
            return cycle

    if best is None:
        kind = (
            "extended (" + "/".join(f"{h:02d}" for h in EXTENDED_CYCLES) + "Z) "
            if extended_only
            else ""
        )
        raise RuntimeError(
            f"No HRRR {kind}surface files found on S3 in the last "
            f"{look_back} hours."
        )
    return best


def parse_cycle(
    session: requests.Session, run_str: str, *, allow_missing: bool = False
) -> Cycle:
    """Build a `Cycle` from an explicit `YYYYMMDDHH` string."""
    try:
        run = datetime.strptime(run_str, "%Y%m%d%H").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(f"--cycle must look like YYYYMMDDHH, got {run_str!r}") from exc

    fhrs = _list_surface_fhrs(session, run)
    if not fhrs and not allow_missing:
        raise RuntimeError(f"HRRR cycle {run:%Y-%m-%d %HZ} has no surface files on S3.")
    return Cycle(run=run, fhrs=fhrs)


def select_fhrs(cycle: Cycle, start: int, stop: int, step: int) -> list[int]:
    """Requested forecast hours, intersected with what the cycle actually has."""
    stop = min(stop, cycle.max_fhr)
    wanted = range(start, stop + 1, step)
    return [f for f in wanted if f in set(cycle.fhrs)]
