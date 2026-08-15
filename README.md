# hrrr_bot

Scrapes NOAA's **HRRR-Smoke** near-surface smoke forecast and turns it into a
short animation over the greater Puget Sound, labelled in local Pacific time.

### 👉 [steinbrennerlab.github.io/hrrr_bot](https://steinbrennerlab.github.io/hrrr_bot/)

The page renders the newest animation inline, with the peak per city and the
recent archive. It refreshes every day — including quiet days, where it reports
that the check ran and found nothing worth animating, so a stale date always
means something is actually broken.

Direct files, if you want the raw animation rather than the page:

| | |
|---|---|
| GIF | https://github.com/steinbrennerlab/hrrr_bot/releases/latest/download/puget_smoke_latest.gif |
| MP4 | https://github.com/steinbrennerlab/hrrr_bot/releases/latest/download/puget_smoke_latest.mp4 |

![example frame](docs/example_frame.png)

## What it pulls

| | |
|---|---|
| Model | HRRR (High-Resolution Rapid Refresh), 3 km CONUS grid |
| Field | `MASSDEN` at `8 m above ground` — near-surface smoke, a PM2.5-equivalent mass concentration |
| Source | `noaa-hrrr-bdp-pds` on S3 (NOAA Big Data Program), surface files `hrrr.tHHz.wrfsfcfFF.grib2` |
| Units | GRIB stores kg m⁻³; everything here is converted to µg m⁻³ |

Each surface file is ~130 MB, but the smoke record is ~800 KB of it. The
scraper reads the file's `.idx` sidecar to find that record's byte offsets and
issues an HTTP range request for just those bytes, so a full 24-hour animation
moves about 20 MB instead of 3 GB.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`cfgrib`/`eccodes` decode the GRIB2 records and `cartopy` draws the coastline.
Cartopy downloads its Natural Earth shapefiles the first time it runs and
caches them afterwards, so the first frame is slower than the rest.

## Use

```bash
# Latest available run, next 24 hours over Puget Sound
.venv/bin/python -m hrrr_smoke

# Two-day outlook from a specific run, one frame per 3 hours
.venv/bin/python -m hrrr_smoke --cycle 2026080612 --hours 48 --step 3

# Wider view that catches smoke before it arrives
.venv/bin/python -m hrrr_smoke --domain cascadia --hours 36 --fps 6
```

Output lands in `out/`:

```
out/hrrr_smoke_puget_20260806_t15z.gif       the animation
out/frames_20260806_t15z_puget/f000.png ...  the individual frames
data/grib/smoke_20260806_t15z_f00.grib2 ...  cached records, reused on re-runs
```

An MP4 — plus a poster still for the page's video — is written alongside the GIF
when `ffmpeg` is on `PATH`; without it you just get the GIF.

The GIF is quantised against **one palette built from every frame at once**,
with dithering off. Per-frame palettes make an unchanging region — the water,
the coastline, a steady band of Moderate — land on a slightly different colour
index each hour and shimmer as the loop plays; one shared palette makes those
pixels bit-identical, which also hands the encoder long runs to compress. On the
48-hour run that is 2.8 MB → 1.3 MB for the same frames. `gifsicle -O3` takes
another ~4% when it is installed, and is skipped silently when it is not.

`out/` and `data/` are ignored by git — they are scratch. Animations worth
keeping get copied into `runs/`, which **is** committed, so the archive
survives across machines and sessions:

```
runs/hrrr_smoke_puget_20260806_t16z.gif
runs/hrrr_smoke_puget_20260806_t16z.mp4   (only where ffmpeg exists)
runs/hrrr_smoke_puget_20260806_t16z.png   the video's poster frame
```

`--archive` copies **every** animation it produced, so on the GitHub runner —
which has ffmpeg — both the GIF and the MP4 are committed. The MP4 is roughly
half the size for the same frames; if the archive ever needs slimming, dropping
the GIF and keeping the MP4 is the cheapest win.

The filename already carries the date and cycle, so re-running the same cycle
overwrites its own file and a new cycle adds one.

Size scales with frame count: 48 hours hourly is ~1.3 MB of GIF and ~0.8 MB of
MP4. `--step 2` halves both and is still perfectly readable for smoke transport.
Git history is not reclaimable, which is what the retention policy below is for.

## Retention

`runs/index.json` records how bad each archived run actually was, which is what
lets the archive forget a quiet Tuesday while keeping the weeks worth looking
back at:

```bash
python -m hrrr_smoke --archive runs --prune-days 30 --prune-keep-above unhealthy
```

A run is deleted only when **both** are true: it is older than `--prune-days`,
**and** the reference city stayed below `--prune-keep-above`. Anything that
reached the threshold is kept forever.

`unhealthy` (55.4 µg m⁻³) is the default bar. For Seattle, Moderate is an
ordinary summer afternoon, but Unhealthy is a genuine smoke event — rare enough
to be worth archiving, common enough that the archive is not empty.

`--prune-non-extended` additionally drops anything that is not from a 48-hour
`00/06/12/18Z` cycle, whatever its age or severity, so the archive and the page
only ever show the long forecasts. This is the one rule that overrides the two
below: unlike severity, which an old entry may simply never have recorded, the
cycle hour is recoverable with certainty from the run's own timestamp, so it is
not a guess.

Two deliberate safety properties:

- **An unmeasured run is never deleted** by the severity rule. Files archived
  before the manifest existed are adopted with `peak_ugm3: null` and always
  kept. The manifest cannot prove they were quiet, and deleting on a guess is
  the one irreversible mistake available here.
- Pruning runs **only after a new animation is archived**, so a quiet stretch
  never quietly empties the directory.

Severity is taken from the gate's window when the gate ran — that looks back
over the previous day too, so it reflects the episode rather than just the
forecast — and from the animation itself otherwise.

## The published page

`--site DIR` builds a self-contained page — the newest animation rendered
inline, the peak per city, and the recent archive — which the workflow deploys
to GitHub Pages:

```bash
python -m hrrr_smoke --archive runs --site site
```

```
https://steinbrennerlab.github.io/hrrr_bot/
```

It is rebuilt on **every** run, including ones where the gate declines, so the
page reports "we checked, it was clean" rather than silently showing stale
content. A date that stops moving therefore means something really is wrong.

### What the page shows

The **latest forecast** card holds the animation on its own white surface, with
a summary row underneath: model run, forecast window, reference-city peak,
category, and the last check. Below that, the peak per city and the archive.

**Today's check and the latest animation are separate things**, and the page
says so. On a clean day following a smoky one they disagree — the check is
green, the newest animation is red — so the status banner is coloured from the
check's own reading and never from the archived run. The card also states why
that particular animation exists: `origin` in the manifest records whether the
gate passed it, someone forced it, or it predates the manifest, so the page
never claims a run cleared a threshold it was never measured against.

The **MP4 is the player** and the GIF is the fallback. The MP4 is roughly half
the weight, and `<video>` gives real controls, a poster frame while it loads,
and a way to honour `prefers-reduced-motion` — autoplay is added by script only
when the reader has not asked for stillness, since markup cannot take it back.
The GIF still ships for the one case `<video>` handles badly: a browser without
the codec paints an empty box rather than falling through to inner content, so
an `error` listener swaps the GIF in. Its URL sits in `data-src` until then, so
nobody downloads it twice, and a `<noscript>` copy covers scripting being off.

Tables stack into labelled rows below 600 px rather than scrolling sideways.

Pages must be set to deploy from **GitHub Actions** (Settings → Pages → Source).
The workflow passes `enablement: true`, which provisions it automatically where
the token is permitted to.

The workflow also runs on pushes to `main` that touch the workflow, `site.py`
or `archive.py`, so a change to the page publishes itself instead of waiting
for the next morning's cron. Nothing else triggers it, and its own commits only
touch `runs/`, so it cannot retrigger itself.

## The permanent file links

Each successful scheduled run replaces a GitHub Release tagged `latest`, so
these URLs are stable forever:

```
https://github.com/steinbrennerlab/hrrr_bot/releases/latest/download/puget_smoke_latest.gif
https://github.com/steinbrennerlab/hrrr_bot/releases/latest/download/puget_smoke_latest.mp4
```

Release assets live outside git history, so the permanent link costs nothing in
repository size. Committing a `runs/latest.gif` instead would work too, but
every run would add another full copy to history — the thing the retention
policy exists to avoid.

The run also prints when each labelled city peaks:

```
Peak near-surface smoke by city (µg m⁻³):
  Seattle          12.0   at Thu 8 AM PDT
  Tacoma           16.3   at Thu 1 PM PDT
  ...
```

### Options

| Flag | Default | Notes |
|---|---|---|
| `--domain` | `puget` | `puget` or `cascadia` |
| `--cycle` | latest | HRRR run as `YYYYMMDDHH` **in UTC** |
| `--start` / `--hours` / `--step` | `0` / `24` / `1` | forecast-hour range and stride |
| `--fps` | `4` | animation speed |
| `--palette` | `aqi` | `aqi` health categories, or `mono` single-hue magnitude |
| `--tz` | `America/Los_Angeles` | any IANA zone |
| `--dpi` | `130` | frame resolution |
| `--force` | off | re-download instead of using the cache |
| `--no-mp4` | off | GIF only |
| `--archive DIR` | off | also copy the finished animation into `DIR` |
| `--gate` | off | skip rendering on clean days — see below |

## The gate

Rendering every day would fill `runs/` with animations of clean air. `--gate`
renders only when a reference city has actually seen, or is forecast to see,
smoke at or above an AQI category:

```bash
python -m hrrr_smoke --gate --gate-city Seattle --gate-threshold moderate
```

The window is **the current or previous local day** — yesterday 00:00 through
tonight 23:59. Because a forecast run cannot reach backwards, the hours already
past are read from each hour's own analysis (`f00` of the cycle initialised at
that hour) and the rest of today comes from the current run's forecast. That
costs roughly 48 extra range requests, about 40 MB.

When the gate does not trip, the command logs why and exits 0 — a clean day is
a success, not a failure.

| Gate flag | Default | Notes |
|---|---|---|
| `--gate-city` | `Seattle` | any city labelled on the domain |
| `--gate-threshold` | `moderate` | `good`, `moderate`, `sensitive`, `unhealthy`, `very-unhealthy`, `hazardous` |
| `--gate-days` | `1` | local days of history (`1` = yesterday and today, `0` = today only) |
| `--gate-step` | `1` | hours between historical samples |

## Scheduled runs

`.github/workflows/smoke.yml` runs the gated pipeline daily at **14:30 UTC**
(7:30 AM PDT / 6:30 AM PST) with `--extended --hours 48`, and commits any
animation to `runs/`.

That time is chosen off the cycle schedule rather than the clock: the 12Z long
run finishes about 13:47Z, so 14:30 leaves ~40 minutes of margin. GitHub's cron
often fires late, which is only ever safer here — `--extended` holds out for a
cycle that has published all 48 hours, so a late run still gets a complete
forecast instead of a half-written one.

To follow every long cycle instead of one a day, add the other three:

```yaml
- cron: "30 2,8,14,20 * * *"   # 00Z, 06Z, 12Z and 18Z runs
```

The runner has `ffmpeg`, so scheduled runs commit an MP4 into `runs/` alongside
the GIF, and upload both as a workflow artifact.

Use **Run workflow** to trigger it by hand; `force` renders even on a clean day,
`threshold` overrides the category, and `step` trades frames for file size.

Two things worth knowing:

- GitHub only runs `schedule` triggers from the **default branch**. This
  repository's default is `main`, so the workflow is live there — a copy of it
  on any other branch does nothing.
- Scheduled workflows are auto-disabled after 60 days of repository inactivity.
  Whether the workflow's own commits reset that clock is not something we have
  confirmed, so check in occasionally if the archive matters.

Observed cron drift has run 26–58 minutes late, which is why the schedule is set
off the cycle completion time with margin rather than at the hour.

## A note on "Pacific Standard Time"

The default `America/Los_Angeles` gives **local** Pacific time — PST (UTC−8) in
winter and PDT (UTC−7) during daylight saving, which is what a clock in Seattle
reads. Frames are labelled with whichever is in effect, so a summer animation
says `PDT`. If you specifically want fixed UTC−8 year-round, pass
`--tz Etc/GMT+8` (the sign is inverted in POSIX-style zone names — `GMT+8` is
UTC−8).

## Which runs have what

HRRR runs every hour, but the runs are not all the same length:

| Cycles | Forecast length | Complete by |
|---|---|---|
| `00/06/12/18Z` | **48 hours** | cycle + ~1.8 h |
| every other hour | 18 hours | cycle + ~1.4 h |

`--hours` is clamped to whatever the chosen cycle supports, so asking for 48
hours from a 15Z run silently gives you 18.

`--extended` restricts the search to the four long cycles:

```bash
python -m hrrr_smoke --extended --hours 48
```

Because asking for the extended cycles is really asking for their length, this
also holds out for a run that has published all the hours requested. If the
newest long cycle is still integrating, the search falls back to the previous
one rather than animating a half-written forecast — at 13:24Z the 12Z run has
only reached f24, so `--extended --hours 48` returns 06Z with its full f00–f48.

NCEP uploads files as the model integrates, so the newest cycle on S3 is
usually incomplete. By default the scraper walks back from the current hour and
takes the newest run that has published at least 12 forecast hours, rather than
building an animation out of the three frames that happen to exist yet. A
forecast hour only counts as available once **both** the GRIB file and its
`.idx` are present — without the index there is no way to range-request the
record.

## Reading the map

Colours are the EPA's PM2.5 AQI categories (2024 breakpoints), so a colour maps
to a health category rather than an arbitrary scale. Every band is named in the
legend, so the map is not readable by colour alone. Below 1 µg m⁻³ the overlay
is fully transparent and the basemap shows through — clean air recedes instead
of painting the whole region green.

The `peak` figure in the header counts **only cells inside the domain's lat/lon
box**. That distinction matters more than it sounds: the HRRR grid is Lambert
conformal, so a lat/lon box is not a rectangle in grid space, and the clipped
array has to stay rectangular — about a third of the cells it returns fall
outside the box, with the corners reaching into British Columbia. On the
2026-08-14 12Z run the raw maximum was a 4,521 µg m⁻³ BC wildfire at 49.8 N,
seventy kilometres north of the domain's edge, on a map where every labelled
city was Good. Those cells are still *drawn*, so smoke visibly arrives from
outside rather than appearing at the boundary; they are simply not counted when
summarising the region.

Each model cell is drawn as one flat block of colour — this is a 3 km forecast,
and interpolating it would invent detail the model never produced. The smoke
layer is antialiased all the same, which is not the same thing: it decides how a
cell's *boundary* lands on the pixel grid, not what is inside it. Without it
each quad rounds its edges to whole pixels, neighbours disagree about who owns
the shared edge, and the translucent palette turns the hairline gaps into a mesh
drawn over the entire map.

City labels carry a per-city offset and alignment in `config.py`, in points
rather than degrees so the gap is the same on every domain. That is a judgement
about one particular map — Bremerton's name is long enough to run over Seattle's
marker 20 km away, so it is thrown west over Hood Canal — and it lives beside
the coordinates rather than in the renderer.

| µg m⁻³ | Category |
|---|---|
| < 1 | Clear |
| 1 – 9 | Good |
| 9 – 35.4 | Moderate |
| 35.4 – 55.4 | Unhealthy for sensitive groups |
| 55.4 – 125.4 | Unhealthy |
| 125.4 – 225.4 | Very unhealthy |
| > 225.4 | Hazardous |

HRRR-Smoke is a forecast of smoke transported from detected fires; it is not a
measurement, and it does not include non-smoke PM2.5. For observations, see
[AirNow](https://www.airnow.gov) or [WA Ecology](https://enviwa.ecology.wa.gov).

## Tests

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest tests -q
```

The suite covers cycle selection, `.idx` byte-range parsing, grid subsetting,
retention and the colour bands; it stubs out S3 so it runs offline. On top of
that it guards the things that break silently:

- **The GIF's shape** (`test_animate.py`) — frame timings, canvas size, a
  file-size ceiling, and that no frame carries its own colour table, walked out
  of the GIF bytes because Pillow reports "no local palette" and "not decoded
  yet" identically.
- **Map layout** (`test_labels.py`) — that no city label overlaps another, that
  none covers a neighbour's marker, that legend bands do not collide, and that
  a fire outside the domain box is not reported as the region's peak. These
  measure the drawn geometry; they fail on the old universal label offset.
- **The page** (`test_site.py`) — that a clean check is not coloured by a smoky
  archive, that a forced run does not claim it cleared the gate, that the GIF is
  not eagerly fetched behind the video, and that every cell carries the label
  the stacked layout needs.

`test_page_layout.py` drives the built page in Chromium for the two things HTML
assertions cannot see — whether it overflows a phone, and whether the video/GIF
handoff actually fires — and takes screenshots at 390 px and 1280 px. It skips
where Playwright or a browser is missing, so it is optional:

```bash
.venv/bin/pip install playwright && .venv/bin/playwright install chromium
.venv/bin/python -m pytest tests/test_page_layout.py -q
```

Set `PLAYWRIGHT_CHROMIUM_EXECUTABLE` if a Chromium is installed somewhere
Playwright does not look.

## Layout

```
hrrr_smoke/
  catalog.py   which cycle exists, which forecast hours it published
  fetch.py     .idx parsing + range requests + on-disk cache
  grid.py      GRIB decode, lat/lon clip to the domain
  render.py    per-frame map drawing
  animate.py   frames -> GIF / MP4 / poster
  gate.py      whether a cycle is worth animating at all
  archive.py   the runs/ manifest and its retention rules
  site.py      the published page
  cli.py       argument handling
  config.py    domains, cities, label offsets, palettes
```
