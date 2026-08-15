import json
from datetime import UTC, datetime, timedelta

import pytest

from hrrr_smoke.archive import (
    MANIFEST,
    RunRecord,
    adopt_orphans,
    load,
    prune,
    record_run,
    save,
)
from hrrr_smoke.gate import THRESHOLDS

NOW = datetime(2026, 8, 14, 12, tzinfo=UTC)
UNHEALTHY = THRESHOLDS["unhealthy"]  # 55.4


def _record(
    cycle: str, *, peak: float | None, age_days: float, files=None
) -> RunRecord:
    when = NOW - timedelta(days=age_days)
    return RunRecord(
        cycle=cycle,
        cycle_time=when.isoformat(),
        domain="puget",
        reference_city="Seattle",
        peak_ugm3=peak,
        peak_at=when.isoformat(),
        frames=25,
        files=files if files is not None else [f"{cycle}.gif"],
    )


def _touch(tmp_path, record: RunRecord) -> None:
    for name in record.files:
        (tmp_path / name).write_bytes(b"gif")


def test_manifest_round_trips(tmp_path):
    records = [_record("a", peak=12.0, age_days=1), _record("b", peak=None, age_days=9)]
    save(tmp_path, records)
    assert {r.cycle for r in load(tmp_path)} == {"a", "b"}


def test_manifest_is_written_newest_first(tmp_path):
    save(
        tmp_path,
        [_record("old", peak=1.0, age_days=9), _record("new", peak=1.0, age_days=1)],
    )
    blob = json.loads((tmp_path / MANIFEST).read_text())
    assert [r["cycle"] for r in blob["runs"]] == ["new", "old"]


def test_missing_manifest_is_not_an_error(tmp_path):
    assert load(tmp_path) == []


def test_corrupt_manifest_is_reported(tmp_path):
    (tmp_path / MANIFEST).write_text("{not json")
    with pytest.raises(RuntimeError, match="not valid JSON"):
        load(tmp_path)


def test_quiet_old_run_is_pruned(tmp_path):
    quiet = _record("quiet", peak=12.0, age_days=40)
    _touch(tmp_path, quiet)
    save(tmp_path, [quiet])

    removed = prune(tmp_path, keep_days=30, keep_above=UNHEALTHY, now=NOW)

    assert [r.cycle for r in removed] == ["quiet"]
    assert not (tmp_path / "quiet.gif").exists()
    assert load(tmp_path) == []


def test_extreme_old_run_is_kept_forever(tmp_path):
    bad = _record("smoky", peak=180.0, age_days=400)
    _touch(tmp_path, bad)
    save(tmp_path, [bad])

    assert prune(tmp_path, keep_days=30, keep_above=UNHEALTHY, now=NOW) == []
    assert (tmp_path / "smoky.gif").exists()


def test_recent_quiet_run_is_kept(tmp_path):
    recent = _record("recent", peak=2.0, age_days=3)
    _touch(tmp_path, recent)
    save(tmp_path, [recent])

    assert prune(tmp_path, keep_days=30, keep_above=UNHEALTHY, now=NOW) == []
    assert (tmp_path / "recent.gif").exists()


def test_threshold_is_inclusive(tmp_path):
    exactly = _record("edge", peak=UNHEALTHY, age_days=90)
    _touch(tmp_path, exactly)
    save(tmp_path, [exactly])

    assert prune(tmp_path, keep_days=30, keep_above=UNHEALTHY, now=NOW) == []


def test_unmeasured_run_is_never_deleted(tmp_path):
    # Deleting on a guess is the one irreversible mistake here.
    unknown = _record("mystery", peak=None, age_days=400)
    _touch(tmp_path, unknown)
    save(tmp_path, [unknown])

    assert prune(tmp_path, keep_days=30, keep_above=UNHEALTHY, now=NOW) == []
    assert (tmp_path / "mystery.gif").exists()


def test_pruning_removes_every_file_of_a_run(tmp_path):
    both = _record("pair", peak=3.0, age_days=90, files=["pair.gif", "pair.mp4"])
    _touch(tmp_path, both)
    save(tmp_path, [both])

    prune(tmp_path, keep_days=30, keep_above=UNHEALTHY, now=NOW)
    assert not (tmp_path / "pair.gif").exists()
    assert not (tmp_path / "pair.mp4").exists()


def test_files_predating_the_manifest_are_adopted_and_spared(tmp_path):
    (tmp_path / "legacy_t12z.gif").write_bytes(b"gif")
    (tmp_path / "legacy_t12z.mp4").write_bytes(b"mp4")

    records = adopt_orphans(tmp_path, [])
    assert len(records) == 1
    assert records[0].peak_ugm3 is None
    assert records[0].files == ["legacy_t12z.gif", "legacy_t12z.mp4"]

    save(tmp_path, records)
    assert prune(tmp_path, keep_days=1, keep_above=UNHEALTHY, now=NOW) == []
    assert (tmp_path / "legacy_t12z.gif").exists()


def test_a_straggler_beside_a_measured_run_is_folded_in_not_orphaned(tmp_path):
    # Regression: a re-render leaves the previous MP4 in place. If that file
    # became its own unmeasured record, half the run would be permanently
    # unprunable and the manifest would list the cycle twice.
    stem = "hrrr_smoke_puget_20260814_t12z"
    (tmp_path / f"{stem}.gif").write_bytes(b"gif")
    (tmp_path / f"{stem}.mp4").write_bytes(b"mp4")

    measured = _record(stem, peak=8.7, age_days=90, files=[f"{stem}.gif"])
    records = adopt_orphans(tmp_path, [measured])

    assert len(records) == 1, "the cycle must not appear twice"
    assert records[0].files == [f"{stem}.gif", f"{stem}.mp4"]
    assert records[0].peak_ugm3 == 8.7

    save(tmp_path, records)
    prune(tmp_path, keep_days=30, keep_above=UNHEALTHY, now=NOW)
    # Quiet and old, so both files go -- not just the one that was recorded.
    assert not (tmp_path / f"{stem}.gif").exists()
    assert not (tmp_path / f"{stem}.mp4").exists()


def test_adopted_runs_are_dated_from_the_filename_not_mtime(tmp_path):
    # A fresh clone stamps every archived file with the checkout time, which
    # would make a year-old run look like it arrived today.
    (tmp_path / "hrrr_smoke_puget_20250115_t06z.gif").write_bytes(b"gif")
    record = adopt_orphans(tmp_path, [])[0]
    assert record.when == datetime(2025, 1, 15, 6, tzinfo=UTC)


def test_adoption_falls_back_to_mtime_for_an_unrecognised_name(tmp_path):
    (tmp_path / "something-else.gif").write_bytes(b"gif")
    record = adopt_orphans(tmp_path, [])[0]
    assert record.when.year >= 2024  # a real timestamp, not a crash


def test_files_for_groups_by_stem(tmp_path):
    from hrrr_smoke.archive import files_for

    (tmp_path / "run_a.gif").write_bytes(b"")
    (tmp_path / "run_a.mp4").write_bytes(b"")
    (tmp_path / "run_b.gif").write_bytes(b"")
    assert files_for(tmp_path, "run_a") == ["run_a.gif", "run_a.mp4"]


def _at_hour(cycle: str, hour: int, *, peak=None, age_days=1) -> RunRecord:
    when = (NOW - timedelta(days=age_days)).replace(hour=hour)
    return RunRecord(
        cycle=cycle,
        cycle_time=when.isoformat(),
        domain="puget",
        reference_city="Seattle",
        peak_ugm3=peak,
        peak_at=None,
        frames=25,
        files=[f"{cycle}.gif"],
    )


def test_only_the_48_hour_cycles_count_as_extended():
    assert _at_hour("a", 0).is_extended
    assert _at_hour("a", 6).is_extended
    assert _at_hour("a", 12).is_extended
    assert _at_hour("a", 18).is_extended
    for hour in (1, 5, 14, 15, 17, 23):
        assert not _at_hour("a", hour).is_extended


def test_extended_only_drops_short_cycles_whatever_their_age(tmp_path):
    short = _at_hour("short", 14, peak=5.0, age_days=0)  # today, would be kept
    long_ = _at_hour("long", 12, peak=5.0, age_days=0)
    for record in (short, long_):
        _touch(tmp_path, record)
    save(tmp_path, [short, long_])

    removed = prune(
        tmp_path, keep_days=30, keep_above=UNHEALTHY, extended_only=True, now=NOW
    )
    assert [r.cycle for r in removed] == ["short"]
    assert not (tmp_path / "short.gif").exists()
    assert (tmp_path / "long.gif").exists()


def test_extended_only_drops_short_cycles_even_when_extreme(tmp_path):
    # The cycle hour is recoverable with certainty, so this is not a guess and
    # it outranks the keep-the-severe rule.
    bad = _at_hour("short_but_bad", 15, peak=400.0, age_days=0)
    _touch(tmp_path, bad)
    save(tmp_path, [bad])

    removed = prune(
        tmp_path, keep_days=30, keep_above=UNHEALTHY, extended_only=True, now=NOW
    )
    assert [r.cycle for r in removed] == ["short_but_bad"]


def test_extended_only_drops_short_cycles_even_when_unmeasured(tmp_path):
    unknown = _at_hour("short_unknown", 17, peak=None, age_days=0)
    _touch(tmp_path, unknown)
    save(tmp_path, [unknown])

    removed = prune(
        tmp_path, keep_days=30, keep_above=UNHEALTHY, extended_only=True, now=NOW
    )
    assert [r.cycle for r in removed] == ["short_unknown"]


def test_extended_only_still_honours_the_severity_rule_for_long_cycles(tmp_path):
    quiet_old = _at_hour("long_quiet", 12, peak=5.0, age_days=90)
    bad_old = _at_hour("long_bad", 6, peak=300.0, age_days=90)
    for record in (quiet_old, bad_old):
        _touch(tmp_path, record)
    save(tmp_path, [quiet_old, bad_old])

    removed = prune(
        tmp_path, keep_days=30, keep_above=UNHEALTHY, extended_only=True, now=NOW
    )
    assert [r.cycle for r in removed] == ["long_quiet"]
    assert (tmp_path / "long_bad.gif").exists()


def test_short_cycles_survive_when_the_flag_is_off(tmp_path):
    short = _at_hour("short", 14, peak=5.0, age_days=0)
    _touch(tmp_path, short)
    save(tmp_path, [short])

    assert prune(tmp_path, keep_days=30, keep_above=UNHEALTHY, now=NOW) == []
    assert (tmp_path / "short.gif").exists()


def test_the_manifest_itself_is_never_adopted(tmp_path):
    save(tmp_path, [])
    assert adopt_orphans(tmp_path, []) == []


def test_recording_the_same_cycle_twice_replaces_it(tmp_path):
    record_run(tmp_path, _record("puget_20260814_t12z", peak=5.0, age_days=0))
    record_run(tmp_path, _record("puget_20260814_t12z", peak=61.0, age_days=0))

    records = load(tmp_path)
    assert len(records) == 1
    assert records[0].peak_ugm3 == 61.0


def test_a_rerun_that_got_worse_becomes_archival(tmp_path):
    quiet = _record("puget_x", peak=5.0, age_days=90)
    _touch(tmp_path, quiet)
    record_run(tmp_path, quiet)
    assert prune(tmp_path, keep_days=30, keep_above=UNHEALTHY, now=NOW)

    worse = _record("puget_x", peak=200.0, age_days=90)
    _touch(tmp_path, worse)
    record_run(tmp_path, worse)
    assert prune(tmp_path, keep_days=30, keep_above=UNHEALTHY, now=NOW) == []
