"""Keep a manifest of archived runs, and drop the unremarkable old ones.

Every archived animation gets an entry in `index.json` recording how bad the
smoke actually was. That is what lets the archive forget a quiet Tuesday while
keeping the weeks people will want to look back at.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

log = logging.getLogger(__name__)

MANIFEST = "index.json"
VERSION = 1

# Archived names end in the cycle they came from, e.g. `..._20260814_t12z`.
_STEM_CYCLE = re.compile(r"_(\d{8})_t(\d{2})z$")


@dataclass
class RunRecord:
    """One archived animation."""

    cycle: str  # e.g. "20260814_t12z"
    cycle_time: str  # ISO 8601 UTC, the run's initialisation
    domain: str
    reference_city: str
    peak_ugm3: float | None  # None when the severity was never measured
    peak_at: str | None
    frames: int
    files: list[str] = field(default_factory=list)
    # City -> peak ug/m^3 over the animation, for the published page. Absent on
    # entries written before the page existed, hence the default.
    city_peaks: dict[str, float] = field(default_factory=dict)

    @property
    def when(self) -> datetime:
        return datetime.fromisoformat(self.cycle_time)

    def is_archival(self, keep_above: float) -> bool:
        """Bad enough to keep regardless of age.

        An unmeasured run counts as archival: the manifest cannot prove it was
        quiet, and deleting on a guess is the one irreversible mistake here.
        """
        return self.peak_ugm3 is None or self.peak_ugm3 >= keep_above


def _path(archive_dir: Path) -> Path:
    return archive_dir / MANIFEST


def load(archive_dir: Path) -> list[RunRecord]:
    """Read the manifest, tolerating its absence."""
    path = _path(archive_dir)
    if not path.exists():
        return []
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path} is not valid JSON: {exc}") from exc
    return [RunRecord(**entry) for entry in blob.get("runs", [])]


def save(archive_dir: Path, records: list[RunRecord]) -> Path:
    """Write the manifest newest-first, formatted for a readable diff."""
    archive_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records, key=lambda r: r.cycle_time, reverse=True)
    path = _path(archive_dir)
    path.write_text(
        json.dumps(
            {"version": VERSION, "runs": [asdict(r) for r in ordered]},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _stem_time(stem: str, paths: list[Path]) -> datetime:
    """When an adopted run was initialised.

    The filename is authoritative; modification time is only a fallback,
    because a fresh clone stamps every archived file with the checkout time.
    """
    match = _STEM_CYCLE.search(stem)
    if match:
        return datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H").replace(
            tzinfo=UTC
        )
    return datetime.fromtimestamp(min(p.stat().st_mtime for p in paths), tz=UTC)


def files_for(archive_dir: Path, stem: str) -> list[str]:
    """Every archived file belonging to one run, by shared filename stem."""
    return sorted(
        p.name
        for p in archive_dir.glob(f"{stem}.*")
        if p.is_file() and p.name != MANIFEST
    )


def adopt_orphans(archive_dir: Path, records: list[RunRecord]) -> list[RunRecord]:
    """Give unmanaged files an entry so the manifest lists the whole directory.

    Animations archived before the manifest existed have no measured peak, so
    they are recorded with `peak_ugm3: null` and are never pruned automatically.

    A run's records are keyed by filename stem, so a straggler beside a run we
    already measured -- an MP4 left from an earlier render of the same cycle,
    say -- is folded into that record rather than becoming a second, unmeasured
    entry that would keep half the run permanently unprunable.
    """
    by_stem = {record.cycle: record for record in records}
    known = {name for record in records for name in record.files}
    orphans = sorted(
        p
        for p in archive_dir.glob("*")
        if p.is_file() and p.name != MANIFEST and p.name not in known
    )
    if not orphans:
        return records

    grouped: dict[str, list[Path]] = {}
    for path in orphans:
        grouped.setdefault(path.stem, []).append(path)

    for stem, paths in grouped.items():
        names = sorted(p.name for p in paths)
        existing = by_stem.get(stem)
        if existing is not None:
            existing.files = sorted(set(existing.files) | set(names))
            continue

        records.append(
            RunRecord(
                cycle=stem,
                cycle_time=_stem_time(stem, paths).isoformat(),
                domain="unknown",
                reference_city="unknown",
                peak_ugm3=None,
                peak_at=None,
                frames=0,
                files=names,
            )
        )
        log.info("adopted unmanaged archive entry %s", stem)
    return records


def record_run(archive_dir: Path, record: RunRecord) -> list[RunRecord]:
    """Add or replace a run's entry, and adopt anything unmanaged alongside it."""
    records = [r for r in load(archive_dir) if r.cycle != record.cycle]
    records.append(record)
    records = adopt_orphans(archive_dir, records)
    save(archive_dir, records)
    return records


def prune(
    archive_dir: Path,
    *,
    keep_days: int,
    keep_above: float,
    now: datetime | None = None,
) -> list[RunRecord]:
    """Delete runs older than `keep_days` that never reached `keep_above`.

    Returns the records that were removed. Anything at or above the threshold
    is kept forever -- those are the events worth looking back at.
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=keep_days)

    records = adopt_orphans(archive_dir, load(archive_dir))
    kept: list[RunRecord] = []
    removed: list[RunRecord] = []

    for record in records:
        if record.is_archival(keep_above) or record.when >= cutoff:
            kept.append(record)
        else:
            removed.append(record)

    for record in removed:
        for name in record.files:
            target = archive_dir / name
            target.unlink(missing_ok=True)
            log.info("pruned %s (peak %.1f µg/m³)", name, record.peak_ugm3 or 0.0)

    if removed:
        save(archive_dir, kept)
    return removed
