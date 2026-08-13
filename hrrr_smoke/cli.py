"""Command line entry point: HRRR smoke forecast -> animation."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from .animate import make_gif, make_mp4
from .catalog import Cycle, find_latest_cycle, parse_cycle, select_fhrs
from .config import DEFAULT_TZ, DOMAINS
from .fetch import fetch_all
from .gate import THRESHOLDS, evaluate
from .grid import SmokeFrame, city_series, load_frame
from .render import FrameRenderer

log = logging.getLogger("hrrr_smoke")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hrrr-smoke",
        description="Animate NOAA HRRR near-surface smoke forecasts for Puget Sound.",
    )
    p.add_argument(
        "--domain", choices=sorted(DOMAINS), default="puget", help="map extent"
    )
    p.add_argument(
        "--cycle",
        help="HRRR run to use as YYYYMMDDHH in UTC (default: latest available)",
    )
    p.add_argument(
        "--extended",
        action="store_true",
        help=(
            "only use the 00/06/12/18Z cycles, which forecast out to 48 hours "
            "instead of 18. They finish about 1.8 h after the cycle time."
        ),
    )
    p.add_argument("--start", type=int, default=0, help="first forecast hour")
    p.add_argument("--hours", type=int, default=24, help="last forecast hour")
    p.add_argument("--step", type=int, default=1, help="forecast hour stride")
    p.add_argument("--fps", type=float, default=4.0, help="animation frames per second")
    p.add_argument(
        "--palette",
        choices=("aqi", "mono"),
        default="aqi",
        help="aqi = EPA health categories, mono = single-hue magnitude ramp",
    )
    p.add_argument(
        "--tz",
        default=DEFAULT_TZ,
        help=(
            "IANA timezone for labels (default America/Los_Angeles, which is "
            "PST in winter and PDT in summer). Use Etc/GMT+8 to force PST year-round."
        ),
    )
    p.add_argument("--out", type=Path, default=Path("out"), help="output directory")
    p.add_argument(
        "--archive",
        type=Path,
        help="also copy the finished GIF (and MP4) into this directory",
    )

    gate = p.add_argument_group(
        "gate",
        "Skip rendering unless a reference city has seen, or is forecast to "
        "see, smoke at or above a threshold. Off by default, so a manual run "
        "always produces an animation.",
    )
    gate.add_argument("--gate", action="store_true", help="enable the gate")
    gate.add_argument("--gate-city", default="Seattle", help="reference city")
    gate.add_argument(
        "--gate-threshold",
        choices=sorted(THRESHOLDS),
        default="moderate",
        help="lowest AQI category that counts as worth rendering",
    )
    gate.add_argument(
        "--gate-days",
        type=int,
        default=1,
        help="local days of history to include (1 = yesterday and today)",
    )
    gate.add_argument(
        "--gate-step", type=int, default=1, help="hours between historical samples"
    )
    p.add_argument(
        "--cache", type=Path, default=Path("data/grib"), help="GRIB cache directory"
    )
    p.add_argument("--dpi", type=int, default=130, help="frame resolution")
    p.add_argument("--workers", type=int, default=6, help="parallel downloads")
    p.add_argument("--force", action="store_true", help="re-download cached records")
    p.add_argument("--no-mp4", action="store_true", help="write only the GIF")
    p.add_argument("--quiet", action="store_true", help="warnings and errors only")
    return p


def _github_output(**values: object) -> None:
    """Publish results to the workflow, when running inside GitHub Actions."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={value}\n")


def _archive(paths: list[Path], into: Path) -> list[Path]:
    """Copy finished animations into the keep-forever directory."""
    into.mkdir(parents=True, exist_ok=True)
    copied = []
    for path in paths:
        target = into / path.name
        shutil.copy2(path, target)
        copied.append(target)
        log.info("archived %s", target)
    return copied


def _summarise(frames: list[SmokeFrame], domain, tz: ZoneInfo) -> None:
    """Print the peak hour per labelled city, in local time."""
    if not frames:
        return
    print("\nPeak near-surface smoke by city (µg m⁻³):")
    for name, lat, lon in domain.cities:
        series = city_series(frames, lat, lon)
        if not series:
            continue
        peak = max(series)
        when = frames[series.index(peak)].valid.astimezone(tz)
        print(f"  {name:<14} {peak:6.1f}   at {when:%a %-I %p %Z}")


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(message)s",
    )

    try:
        tz = ZoneInfo(args.tz)
    except Exception:
        log.error("Unknown timezone %r", args.tz)
        return 2

    domain = DOMAINS[args.domain]
    session = requests.Session()
    session.headers["User-Agent"] = "hrrr-bot/1.0 (smoke forecast animation)"

    try:
        cycle: Cycle = (
            parse_cycle(session, args.cycle)
            if args.cycle
            # Asking for the extended cycles is asking for their length, so
            # hold out for a run that has actually published what was asked.
            else find_latest_cycle(
                session,
                extended_only=args.extended,
                min_fhrs=args.hours if args.extended else 12,
            )
        )
    except (RuntimeError, ValueError) as exc:
        log.error("%s", exc)
        return 1

    fhrs = select_fhrs(cycle, args.start, args.hours, args.step)
    if not fhrs:
        log.error(
            "Cycle %s has no forecast hours in the requested range "
            "(published: f%02d-f%02d).",
            cycle,
            min(cycle.fhrs) if cycle.fhrs else 0,
            max(cycle.fhrs) if cycle.fhrs else 0,
        )
        return 1

    log.info(
        "HRRR cycle %s (%s local) · forecast hours f%02d-f%02d · %d frames",
        cycle,
        cycle.run.astimezone(tz).strftime("%-I %p %Z"),
        fhrs[0],
        fhrs[-1],
        len(fhrs),
    )

    cycle_id = f"{cycle.date_str}_t{cycle.hour:02d}z"
    if args.gate:
        try:
            verdict = evaluate(
                session,
                cycle,
                domain,
                args.cache,
                tz=tz,
                city=args.gate_city,
                threshold=args.gate_threshold,
                days=args.gate_days,
                step_hours=args.gate_step,
                workers=args.workers,
            )
        except (RuntimeError, ValueError) as exc:
            log.error("%s", exc)
            return 1

        log.info("gate: %s", verdict.describe(tz))
        if not verdict.triggered:
            log.info("Nothing worth animating; skipping the render.")
            _github_output(
                rendered="false",
                cycle=cycle_id,
                peak=f"{verdict.peak:.1f}",
                summary=verdict.describe(tz),
            )
            return 0

    records = fetch_all(
        session, cycle, fhrs, args.cache, workers=args.workers, force=args.force
    )
    if not records:
        log.error("Nothing downloaded; cannot build an animation.")
        return 1

    frame_dir = args.out / f"frames_{cycle.date_str}_t{cycle.hour:02d}z_{args.domain}"
    frames: list[SmokeFrame] = []
    paths: list[Path] = []
    with FrameRenderer(domain, tz=tz, palette=args.palette, dpi=args.dpi) as renderer:
        for fhr, path in records:
            try:
                frame = load_frame(path, fhr, domain)
            except Exception as exc:  # a truncated cache entry should not be fatal
                log.warning(
                    "f%02d unreadable (%s); delete %s and retry", fhr, exc, path
                )
                continue
            frames.append(frame)
            paths.append(renderer.render(frame, frame_dir / f"f{fhr:03d}.png"))

    if not paths:
        log.error("No frames rendered.")
        return 1
    log.info("rendered %d frames -> %s", len(paths), frame_dir)

    stem = f"hrrr_smoke_{args.domain}_{cycle_id}"
    gif = make_gif(paths, args.out / f"{stem}.gif", fps=args.fps)
    animations = [gif]
    if not args.no_mp4:
        mp4 = make_mp4(paths, args.out / f"{stem}.mp4", fps=args.fps)
        if mp4:
            animations.append(mp4)

    if args.archive:
        gif = _archive(animations, args.archive)[0]

    _summarise(frames, domain, tz)
    _github_output(
        rendered="true",
        cycle=cycle_id,
        gif=gif,
        frames=len(paths),
        summary=f"{len(paths)} frames from HRRR {cycle}",
    )
    print(f"\nAnimation: {gif}")
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
