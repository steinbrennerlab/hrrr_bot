"""Build the published page: the latest animation, rendered rather than downloaded."""

from __future__ import annotations

import html
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from .archive import FORCED, GATED, MANIFEST, RunRecord, load
from .config import AQI_LEGEND_LABELS, AQI_SOLID_COLORS, band_for

if TYPE_CHECKING:  # only for the constructor below; the page never fetches
    from .gate import GateResult

log = logging.getLogger(__name__)

UNMEASURED = ("Unmeasured", "#9aa7b2")
ALT = "Animated near-surface smoke forecast for the Puget Sound"


@dataclass(frozen=True)
class Check:
    """What today's look at the data found, whether or not it produced anything.

    This is deliberately separate from the newest `RunRecord`. They answer
    different questions -- "is it smoky right now?" versus "what does the most
    recent animation show?" -- and on the common case, a clear day following a
    smoky one, the answers differ. Colouring the check from the archived run,
    as the page used to, put a red dot beside the words "clear air".
    """

    city: str
    peak: float
    threshold: float
    threshold_label: str
    triggered: bool
    at: datetime | None = None
    samples: int = 0

    @classmethod
    def from_gate(cls, verdict: GateResult) -> Check:
        return cls(
            city=verdict.city,
            peak=verdict.peak,
            threshold=verdict.threshold,
            threshold_label=verdict.threshold_label,
            triggered=verdict.triggered,
            at=verdict.peak_at,
            samples=verdict.samples,
        )

    def describe(self, tz: ZoneInfo) -> str:
        when = f", peaking {self.at.astimezone(tz):%a %-I %p %Z}" if self.at else ""
        verb = "at or above" if self.triggered else "below"
        return (
            f"{self.city} {self.peak:.1f} µg/m³{when} — {verb} "
            f"{self.threshold_label} ({self.threshold:g} µg/m³)."
        )


def category(value: float | None) -> tuple[str, str]:
    """AQI category name and colour for a concentration."""
    if value is None:
        return UNMEASURED
    index = band_for(value)
    return AQI_LEGEND_LABELS[index].replace("\n", " "), AQI_SOLID_COLORS[index]


def _fmt(when: datetime, tz: ZoneInfo) -> str:
    return when.astimezone(tz).strftime("%a %-d %b %Y, %-I:%M %p %Z")


def _short(when: datetime, tz: ZoneInfo) -> str:
    return when.astimezone(tz).strftime("%a %-d %b, %-I %p %Z")


def _size(path: Path) -> str:
    """Human-sized file size, for a download the reader may be paying for."""
    if not path.exists():
        return ""
    size = path.stat().st_size
    return f"{size / 1e6:.1f} MB" if size >= 1e6 else f"{size / 1e3:.0f} KB"


def _latest(records: list[RunRecord]) -> RunRecord | None:
    ranked = sorted(
        (r for r in records if r.animations), key=lambda r: r.cycle_time, reverse=True
    )
    return ranked[0] if ranked else None


def _intro(check: Check | None) -> str:
    """The three questions a reader arrives with.

    The middle one points at the status banner, which is only drawn when a
    check was actually run -- a manual render without `--gate` has nothing to
    report. Pointing at a banner that is not there would be the same fault this
    page keeps trying not to commit, so the answer changes with it.
    """
    answer = (
        "Checked daily &mdash; the banner below is today&rsquo;s answer."
        if check
        else "Checked daily, and reported here whenever the check has run."
    )
    questions = (
        (
            "Where&rsquo;s the smoke headed?",
            "48 hours of NOAA&rsquo;s HRRR-Smoke, one frame an hour, "
            "in local Pacific time.",
        ),
        ("Is it bad right now?", answer),
        (
            "Why isn&rsquo;t the map from today?",
            "Animations are kept only when Seattle hits Moderate or worse.",
        ),
    )
    items = "".join(f"<li><b>{q}</b> {a}</li>" for q, a in questions)
    return f'<ul class="intro">{items}</ul>'


def _provenance(record: RunRecord) -> str:
    """Why this particular animation exists, in the page's own words."""
    threshold = record.gate_threshold or "the threshold"
    if record.origin == GATED:
        return f"Rendered because {record.reference_city} reached {threshold}."
    if record.origin == FORCED:
        return "Rendered on request, regardless of the threshold."
    return "Archived before this page recorded why a run was rendered."


def _media(record: RunRecord) -> str:
    """The rendered animation.

    The MP4 leads: it is roughly half the GIF's weight, it can be paused and
    scrubbed, and it can be told not to move for a reader who has asked for
    that. Its one failure mode is a browser without the codec, which renders an
    empty box rather than falling back -- a `video`'s inner content only shows
    when `video` itself is unknown, which no current browser does. So the GIF
    is emitted alongside it, holding its URL in `data-src` so nobody downloads
    three megabytes they will not see, and the script swaps it in on error.
    """
    gif = next((f for f in record.files if f.endswith(".gif")), None)
    mp4 = next((f for f in record.files if f.endswith(".mp4")), None)
    poster = next((f for f in record.files if f.endswith(".png")), None)

    if not mp4:
        if not gif:
            return "<p>No animation available.</p>"
        return f'<img class="hero" src="{html.escape(gif)}" alt="{ALT}">'

    attrs = 'class="hero" id="hero" loop muted playsinline controls preload="metadata"'
    if poster:
        attrs += f' poster="{html.escape(poster)}"'
    parts = [
        f"<video {attrs}>",
        f'<source src="{html.escape(mp4)}" type="video/mp4">',
        "</video>",
    ]
    if gif:
        parts.append(
            f'<img class="hero" id="hero-gif" data-src="{html.escape(gif)}" '
            f'alt="{ALT}" hidden>'
        )
        parts.append(
            f'<noscript><img class="hero" src="{html.escape(gif)}" alt="{ALT}">'
            "</noscript>"
        )
    return "".join(parts)


def _downloads(record: RunRecord, out_dir: Path) -> str:
    links = []
    for name in record.animations:
        kind = "MP4 video" if name.endswith(".mp4") else "Animated GIF"
        size = _size(out_dir / name)
        links.append(
            f'<a href="{html.escape(name)}" download>Download {kind}'
            + (f' <span class="muted">({size})</span>' if size else "")
            + "</a>"
        )
    return " · ".join(links)


def _summary(record: RunRecord, tz: ZoneInfo, check: Check | None) -> str:
    """The compact fact row under the map."""
    label, color = category(record.peak_ugm3)
    peak = "—" if record.peak_ugm3 is None else f"{record.peak_ugm3:.1f} µg/m³"

    window = "—"
    covers = record.covers
    if covers:
        start, end = covers
        window = f"{_short(start, tz)} → {_short(end, tz)}"
    elif record.frames:
        window = f"{record.frames} hourly frames"

    items = [
        ("Model run", html.escape(_fmt(record.when, tz))),
        ("Forecast window", html.escape(window)),
        (f"{html.escape(record.reference_city)} peak", peak),
        (
            "Category",
            f'<span class="chip" style="background:{color}"></span>'
            f"{html.escape(label)}",
        ),
    ]
    if check:
        items.append(
            (
                "Last check",
                f'<span class="chip" style="background:{category(check.peak)[1]}">'
                f"</span>"
                + html.escape(f"{check.city} {check.peak:.1f} µg/m³"),
            )
        )
    cells = "".join(
        f'<div class="fact"><dt>{key}</dt><dd>{value}</dd></div>'
        for key, value in items
    )
    return f'<dl class="facts">{cells}</dl>'


def _cell(label: str, value: str, *, num: bool = False) -> str:
    """One body cell, carrying the column name it loses in the stacked layout.

    The value is wrapped so it stays a single flex item there: a bare chip
    beside bare text is two items, and the row's space-between pushes them to
    opposite ends with the swatch stranded in the middle.
    """
    cls = ' class="num"' if num else ""
    return f'<td{cls} data-label="{html.escape(label)}"><span>{value}</span></td>'


def _chip(label: str, color: str) -> str:
    return f'<span class="chip" style="background:{color}"></span>{html.escape(label)}'


def _city_rows(record: RunRecord) -> str:
    if not record.city_peaks:
        return ""
    rows = []
    for name, peak in sorted(
        record.city_peaks.items(), key=lambda kv: kv[1], reverse=True
    ):
        label, color = category(peak)
        rows.append(
            "<tr>"
            + _cell("City", html.escape(name))
            + _cell("Peak", f"{peak:.1f} µg/m³", num=True)
            + _cell("Category", _chip(label, color))
            + "</tr>"
        )
    return (
        '<h2>Peak by city</h2><div class="scroll"><table><thead><tr><th>City</th>'
        '<th class="num">Peak</th><th>Category</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _archive_rows(records: list[RunRecord], repo: str, tz: ZoneInfo) -> str:
    ranked = sorted(
        (r for r in records if r.animations), key=lambda r: r.cycle_time, reverse=True
    )[:30]
    if not ranked:
        return ""
    base = f"https://github.com/{repo}/raw/main/runs"
    # One label for the whole column, taken from the newest run that recorded a
    # reference city. Per-row labels would read "Unknown peak" on the adopted
    # entries while the header above them still said "Seattle peak".
    named = next(
        (r.reference_city for r in ranked if r.reference_city not in ("", "unknown")),
        "Reference city",
    )
    peak_column = f"{named} peak"
    rows = []
    for record in ranked:
        label, color = category(record.peak_ugm3)
        peak = "—" if record.peak_ugm3 is None else f"{record.peak_ugm3:.1f} µg/m³"
        links = " ".join(
            f'<a href="{base}/{html.escape(f)}">{f.rsplit(".", 1)[1].upper()}</a>'
            for f in record.animations
        )
        rows.append(
            "<tr>"
            + _cell("Run", _fmt(record.when, tz))
            + _cell(peak_column, peak, num=True)
            + _cell("Category", _chip(label, color))
            + _cell("Files", links)
            + "</tr>"
        )
    return (
        '<h2>Archive</h2><p class="muted">Quiet runs are removed after 30 days; '
        "anything that reached Unhealthy is kept for good.</p>"
        '<div class="scroll"><table><thead><tr><th>Run</th>'
        f'<th class="num">{html.escape(peak_column)}</th>'
        "<th>Category</th><th>Files</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


_CSS = """
:root{
  --bg:#ffffff; --panel:#f6f7f9; --card:#ffffff; --ink:#15181c; --muted:#666e78;
  --line:#e2e6ea; --accent:#2f6f9f; --map:#ffffff;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#14171a; --panel:#1d2126; --card:#1a1e22; --ink:#e8ebee; --muted:#9aa3ad;
    --line:#2b3138; --accent:#7bb7e0; --map:#ffffff;
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

/* The standfirst: the three questions a reader arrives with. A list without
   bullet glyphs, so it reads as part of the header rather than as content --
   the bold question already does the work a marker would. */
.intro{list-style:none;margin:.7rem 0 1.4rem;padding:0;color:var(--muted);
  font-size:.95rem;line-height:1.45}
/* Enough gap that a question wrapping onto two or three lines on a phone
   still reads as one item rather than running into the next. */
.intro li{margin:0 0 .45rem}
.intro b{color:var(--ink);font-weight:600}
.status{display:flex;gap:.65rem;align-items:flex-start;background:var(--panel);
  border:1px solid var(--line);border-radius:10px;padding:.8rem 1rem;margin:0 0 1.4rem}
.status .dot{width:11px;height:11px;border-radius:50%;flex:0 0 auto;margin-top:.42rem}

/* Latest forecast card: the map sits on its own white surface in both themes,
   because the frames are drawn on white and a dark card would ring them. */
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
  overflow:hidden;margin:0}
.card .surface{background:var(--map);padding:0;line-height:0}
video.hero,img.hero{width:100%;height:auto;display:block;background:var(--map)}
.card .foot{padding:.85rem 1rem 1rem;border-top:1px solid var(--line)}
.facts{display:grid;gap:.7rem 1.4rem;margin:0 0 .7rem;
  grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
.fact{margin:0}
.facts dt{color:var(--muted);font-size:.72rem;text-transform:uppercase;
  letter-spacing:.05em;font-weight:600}
.facts dd{margin:.12rem 0 0;font-size:.95rem;font-variant-numeric:tabular-nums}
.card .note{color:var(--muted);font-size:.85rem;margin:0}
.card .note a{white-space:nowrap}

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

/* On a phone a four-column table is a horizontal scroll nobody finds. Stack
   each row into its own labelled block instead. */
@media (max-width:600px){
  .wrap{padding:1.3rem .9rem 3rem}
  .scroll{overflow-x:visible}
  table,tbody,tr,td{display:block;width:100%}
  thead{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0)}
  tr{border:1px solid var(--line);border-radius:9px;padding:.35rem .6rem;
    margin-bottom:.6rem}
  td{border:0;padding:.2rem 0;display:flex;align-items:baseline;
    justify-content:space-between;gap:1rem}
  td>span{text-align:right}
  td::before{content:attr(data-label);color:var(--muted);font-size:.78rem;
    text-transform:uppercase;letter-spacing:.04em;font-weight:600;
    flex:0 0 auto;white-space:nowrap}
}

/* Someone who has asked their system to stop animations should not be handed a
   looping map. The video keeps its controls, so it is still watchable -- on
   purpose rather than at it. */
@media (prefers-reduced-motion:reduce){
  video.hero{animation:none}
}
"""

# Two runtime decisions that markup cannot express.
#
# Autoplay is one: `<video autoplay>` cannot be conditioned on a media query,
# so the attribute is left off and added back only for a reader who has not
# asked their system to stop animations. Everyone still gets the controls.
#
# The GIF swap is the other. A missing codec is not a parse error, so the
# browser shows an empty box and never reaches the `video`'s inner content;
# only an `error` event says so. It is listened for in the capture phase
# because the event fires on the `<source>` and does not bubble, and `v.error`
# is checked once directly in case it already fired during parsing.
_MOTION_JS = """
(function(){
  var v=document.getElementById('hero');
  if(!v)return;
  var gif=document.getElementById('hero-gif');
  var swapped=false;
  function fallback(){
    if(swapped||!gif)return;
    swapped=true;
    gif.src=gif.dataset.src;
    gif.hidden=false;
    v.remove();
  }
  v.addEventListener('error',fallback,true);
  if(v.error)fallback();
  if(window.matchMedia('(prefers-reduced-motion: reduce)').matches)return;
  v.setAttribute('autoplay','');
  var p=v.play();
  if(p&&p.catch)p.catch(function(){});
})();
"""


def build(
    archive_dir: Path,
    out_dir: Path,
    *,
    tz: ZoneInfo,
    repo: str,
    check: Check | None = None,
    checked: datetime | None = None,
) -> Path:
    """Write `out_dir/index.html` plus the latest animation beside it."""
    records = load(archive_dir)
    latest = _latest(records)
    out_dir.mkdir(parents=True, exist_ok=True)

    card = (
        '<div class="card"><div class="foot">'
        '<p class="note">No animation has been produced yet.</p>'
        "</div></div>"
    )
    cities = ""
    if latest:
        for name in latest.files:
            source = archive_dir / name
            if source.exists():
                shutil.copy2(source, out_dir / name)
        card = (
            '<div class="card">'
            f'<div class="surface">{_media(latest)}</div>'
            '<div class="foot">'
            f"{_summary(latest, tz, check)}"
            f'<p class="note">{html.escape(_provenance(latest))} '
            f"{_downloads(latest, out_dir)}</p>"
            "</div></div>"
        )
        cities = _city_rows(latest)

    stamp = checked or datetime.now(tz)
    banner = ""
    if check:
        # Coloured from the check's own reading, never from the archived run.
        _, color = category(check.peak)
        headline = (
            "Smoke at or above the threshold"
            if check.triggered
            else "Below the threshold"
        )
        banner = (
            f'<div class="status"><span class="dot" style="background:{color}"></span>'
            f"<div><strong>Today&rsquo;s check</strong> — {html.escape(headline)}<br>"
            f"{html.escape(check.describe(tz))}</div></div>"
        )

    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Puget Sound smoke forecast</title>
<style>{_CSS}</style></head><body><div class="wrap">
<h1>Puget Sound smoke forecast</h1>
{_intro(check)}
{banner}
<h2>Latest forecast</h2>
{card}
{cities}
{_archive_rows(records, repo, tz)}
<footer>
Built from <a href="https://github.com/{html.escape(repo)}">{html.escape(repo)}</a>
· data from the <a href="https://registry.opendata.aws/noaa-hrrr-pds/">NOAA Big
Data Program</a> · colours are EPA PM2.5 AQI categories (2024 breakpoints).
HRRR-Smoke is a forecast, not a measurement — for observations see
<a href="https://www.airnow.gov">AirNow</a>.
<br>Page generated {_fmt(stamp, tz)}.
</footer>
</div><script>{_MOTION_JS}</script></body></html>
"""
    target = out_dir / "index.html"
    target.write_text(page, encoding="utf-8")

    # Publish the manifest too, so the archive is machine-readable from the page.
    manifest = archive_dir / MANIFEST
    if manifest.exists():
        shutil.copy2(manifest, out_dir / MANIFEST)

    log.info("built %s", target)
    return target
