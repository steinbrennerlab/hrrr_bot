"""Pull the single near-surface smoke GRIB record out of each HRRR surface file."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from .catalog import Cycle
from .config import SMOKE_LEVEL, SMOKE_VARIABLE

log = logging.getLogger(__name__)


class RecordNotFound(RuntimeError):
    """The cycle's index has no near-surface smoke record."""


def _byte_range(idx_text: str, variable: str, level: str) -> tuple[int, int | None]:
    """Start/end byte offsets of `variable` at `level` in the GRIB file.

    Each `.idx` line is `num:offset:date:var:level:fcst:`. A record runs from
    its own offset to the byte before the next record starts; the final record
    runs to the end of the file, which we express as an open-ended range.
    """
    records = [line.split(":") for line in idx_text.splitlines() if line.strip()]
    for i, rec in enumerate(records):
        if len(rec) > 4 and rec[3] == variable and rec[4] == level:
            start = int(rec[1])
            end = int(records[i + 1][1]) - 1 if i + 1 < len(records) else None
            return start, end
    raise RecordNotFound(f"No {variable} at {level!r} in index")


def fetch_record(
    session: requests.Session,
    cycle: Cycle,
    fhr: int,
    cache_dir: Path,
    *,
    force: bool = False,
) -> Path:
    """Download the smoke record for one forecast hour, caching it on disk."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"smoke_{cycle.date_str}_t{cycle.hour:02d}z_f{fhr:02d}.grib2"
    if out.exists() and out.stat().st_size > 0 and not force:
        log.debug("f%02d cached", fhr)
        return out

    url = cycle.grib_url(fhr)
    idx = session.get(url + ".idx", timeout=60)
    idx.raise_for_status()
    start, end = _byte_range(idx.text, SMOKE_VARIABLE, SMOKE_LEVEL)

    span = f"bytes={start}-{'' if end is None else end}"
    resp = session.get(url, headers={"Range": span}, timeout=180)
    resp.raise_for_status()
    if resp.status_code != 206:
        size_mb = len(resp.content) / 1e6
        raise RuntimeError(
            f"S3 ignored the range request for f{fhr:02d} "
            f"(status {resp.status_code}); refusing a {size_mb:.0f} MB download."
        )

    tmp = out.with_suffix(".part")
    tmp.write_bytes(resp.content)
    tmp.replace(out)
    log.info("f%02d downloaded (%.0f KB)", fhr, len(resp.content) / 1024)
    return out


def fetch_all(
    session: requests.Session,
    cycle: Cycle,
    fhrs: list[int],
    cache_dir: Path,
    *,
    workers: int = 6,
    force: bool = False,
) -> list[tuple[int, Path]]:
    """Fetch every requested forecast hour, skipping any that fail."""

    def one(fhr: int) -> tuple[int, Path] | None:
        try:
            return fhr, fetch_record(session, cycle, fhr, cache_dir, force=force)
        except (requests.RequestException, RecordNotFound, RuntimeError) as exc:
            log.warning("f%02d skipped: %s", fhr, exc)
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, fhrs))

    return sorted((r for r in results if r is not None), key=lambda r: r[0])
