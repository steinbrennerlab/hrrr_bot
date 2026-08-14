"""Build the published page: the latest animation, rendered rather than downloaded."""

from __future__ import annotations

import html
import logging
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .archive import MANIFEST, RunRecord, load
from .config import AQI_LABELS, AQI_LEVELS

log = logging.getLogger(__name__)

# Solid web equivalents of the map legend, which uses the same breakpoints but
# blends over the basemap.
CATEGORY_COLORS = (
    "#9aa7b2",  # clear
    "#5aa95f",  # good
    "#e0bc16",  # moderate
    "#e07b18",  # sensitive
    "#d62529",  # unhealthy
    "#8f3f97",  # very unhealthy
    "#7e0023",  # hazardous
)


def category(value: float | None) -> tuple[str, str]:
    """AQI category name and colour for a concentration."""
    if value is None:
        return "Unmeasured", "#9aa7b2"
    index = 0
    for i, edge in enumerate(AQI_LEVELS):
        if value >= edge:
            index = i
    return AQI_LABELS[index], CATEGORY_COLORS[index]


def _fmt(when: datetime, tz: ZoneInfo) -> str:
    return when.astimezone(tz).strftime("%a %-d %b %Y, %-I:%M %p %Z")


def _latest(records: list[RunRecord]) -> RunRecord | None:
    ranked = sorted(
        (r for r in records if r.files), key=lambda r: r.cycle_time, reverse=True
    )
    return ranked[0] if ranked else None


def _media(record: RunRecord) -> tuple[str, str]:
    """The rendered animation, plus any secondary link.

    The GIF is the hero rather than the MP4: a `<video>` whose codec the
    browser lacks renders an empty box, and inner fallback content only shows
    when `<video>` itself is unsupported -- so the failure mode is a blank
    page, not a picture. A GIF plays everywhere.
    """
    gif = next((f for f in record.files if f.endswith(".gif")), None)
    mp4 = next((f for f in record.files if f.endswith(".mp4")), None)

    if gif:
        hero = (
            f'<img class="hero" src="{html.escape(gif)}" '
            f'alt="Animated near-surface smoke forecast for the Puget Sound">'
        )
    elif mp4:
        hero = (
            f'<video autoplay loop muted playsinline controls '
            f'src="{html.escape(mp4)}"></video>'
        )
    else:
        return "<p>No animation available.</p>", ""

    extra = ""
    if mp4 and gif:
        extra = f' · <a href="{html.escape(mp4)}">smaller MP4</a>'
    return hero, extra


def _city_rows(record: RunRecord) -> str:
    if not record.city_peaks:
        return ""
    rows = []
    for name, peak in sorted(
        record.city_peaks.items(), key=lambda kv: kv[1], reverse=True
    ):
        label, color = category(peak)
        rows.append(
            f"<tr><td>{html.escape(name)}</td>"
            f'<td class="num">{peak:.1f} µg/m³</td>'
            f'<td><span class="chip" style="background:{color}"></span>'
            f"{html.escape(label)}</td></tr>"
        )
    return (
        '<h2>Peak by city</h2><table><thead><tr><th>City</th>'
        '<th class="num">Peak</th><th>Category</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def _archive_rows(records: list[RunRecord], repo: str, tz: ZoneInfo) -> str:
    ranked = sorted(
        (r for r in records if r.files), key=lambda r: r.cycle_time, reverse=True
    )[:30]
    if not ranked:
        return ""
    base = f"https://github.com/{repo}/raw/main/runs"
    rows = []
    for record in ranked:
        label, color = category(record.peak_ugm3)
        peak = "—" if record.peak_ugm3 is None else f"{record.peak_ugm3:.1f} µg/m³"
        links = " ".join(
            f'<a href="{base}/{html.escape(f)}">{f.rsplit(".", 1)[1]}</a>'
            for f in record.files
        )
        rows.append(
            f"<tr><td>{_fmt(record.when, tz)}</td>"
            f'<td class="num">{peak}</td>'
            f'<td><span class="chip" style="background:{color}"></span>'
            f"{html.escape(label)}</td><td>{links}</td></tr>"
        )
    return (
        "<h2>Archive</h2><p class=\"muted\">Quiet runs are removed after 30 days; "
        "anything that reached Unhealthy is kept for good.</p>"
        '<table><thead><tr><th>Run</th><th class="num">Seattle peak</th>'
        "<th>Category</th><th>Files</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


_CSS = """
:root{
  --bg:#ffffff; --panel:#f6f7f9; --ink:#15181c; --muted:#666e78;
  --line:#e2e6ea; --accent:#2f6f9f;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#14171a; --panel:#1d2126; --ink:#e8ebee; --muted:#9aa3ad;
    --line:#2b3138; --accent:#7bb7e0;
  }
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
.wrap{max-width:940px;margin:0 auto;padding:2rem 1.1rem 4rem}
h1{font-size:1.6rem;margin:0 0 .2rem}
h2{font-size:1.05rem;margin:2.2rem 0 .6rem;letter-spacing:.01em}
.muted{color:var(--muted)}
.sub{color:var(--muted);margin:0 0 1.4rem}
.status{display:flex;gap:.65rem;align-items:flex-start;background:var(--panel);
  border:1px solid var(--line);border-radius:10px;padding:.8rem 1rem;margin:0 0 1.4rem}
.status .dot{width:11px;height:11px;border-radius:50%;flex:0 0 auto;margin-top:.42rem}
figure{margin:0}
video,img.hero,figure img{width:100%;height:auto;display:block;
  border:1px solid var(--line);border-radius:10px;background:var(--panel)}
figcaption{color:var(--muted);font-size:.87rem;margin-top:.55rem}
table{border-collapse:collapse;width:100%;font-size:.93rem}
th,td{text-align:left;padding:.42rem .6rem;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:600;font-size:.8rem;text-transform:uppercase;
  letter-spacing:.04em}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.chip{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:.45rem}
a{color:var(--accent)}
.scroll{overflow-x:auto}
footer{margin-top:3rem;padding-top:1.2rem;border-top:1px solid var(--line);
  color:var(--muted);font-size:.87rem}
"""


def build(
    archive_dir: Path,
    out_dir: Path,
    *,
    tz: ZoneInfo,
    repo: str,
    status: str | None = None,
    checked: datetime | None = None,
) -> Path:
    """Write `out_dir/index.html` plus the latest animation beside it."""
    records = load(archive_dir)
    latest = _latest(records)
    out_dir.mkdir(parents=True, exist_ok=True)

    hero = "<p>No animation has been produced yet.</p>"
    extra = ""
    caption = ""
    cities = ""
    if latest:
        for name in latest.files:
            source = archive_dir / name
            if source.exists():
                shutil.copy2(source, out_dir / name)
        hero, extra = _media(latest)
        caption = f"HRRR run initialised {_fmt(latest.when, tz)}"
        if latest.frames:
            caption += f" · {latest.frames} frames"
        cities = _city_rows(latest)

    stamp = checked or datetime.now(tz)
    label, color = category(latest.peak_ugm3 if latest else None)
    banner = ""
    if status:
        banner = (
            f'<div class="status"><span class="dot" style="background:{color}"></span>'
            f"<div><strong>Today&rsquo;s check</strong><br>{html.escape(status)}</div>"
            "</div>"
        )

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Puget Sound smoke forecast</title>
<style>{_CSS}</style></head><body><div class="wrap">
<h1>Puget Sound smoke forecast</h1>
<p class="sub">Near-surface smoke from NOAA&rsquo;s HRRR-Smoke model, in local
Pacific time. Checked daily; a new animation appears only when Seattle reaches
Moderate or worse.</p>
{banner}
<figure>{hero}<figcaption>{html.escape(caption)}{extra}</figcaption></figure>
<div class="scroll">{cities}</div>
<div class="scroll">{_archive_rows(records, repo, tz)}</div>
<footer>
Built from <a href="https://github.com/{html.escape(repo)}">{html.escape(repo)}</a>
· data from the <a href="https://registry.opendata.aws/noaa-hrrr-pds/">NOAA Big
Data Program</a> · colours are EPA PM2.5 AQI categories (2024 breakpoints).
HRRR-Smoke is a forecast, not a measurement — for observations see
<a href="https://www.airnow.gov">AirNow</a>.
<br>Page generated {_fmt(stamp, tz)}.
</footer>
</div></body></html>
"""
    target = out_dir / "index.html"
    target.write_text(page, encoding="utf-8")

    # Publish the manifest too, so the archive is machine-readable from the page.
    manifest = archive_dir / MANIFEST
    if manifest.exists():
        shutil.copy2(manifest, out_dir / MANIFEST)

    log.info("built %s", target)
    return target
