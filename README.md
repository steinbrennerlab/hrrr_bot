# hrrr_bot

Scrapes NOAA's **HRRR-Smoke** near-surface smoke forecast and turns it into a
short animation over the greater Puget Sound, labelled in local Pacific time.

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

An MP4 is written alongside the GIF when `ffmpeg` is on `PATH`; without it you
just get the GIF.

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

## A note on "Pacific Standard Time"

The default `America/Los_Angeles` gives **local** Pacific time — PST (UTC−8) in
winter and PDT (UTC−7) during daylight saving, which is what a clock in Seattle
reads. Frames are labelled with whichever is in effect, so a summer animation
says `PDT`. If you specifically want fixed UTC−8 year-round, pass
`--tz Etc/GMT+8` (the sign is inverted in POSIX-style zone names — `GMT+8` is
UTC−8).

## Which runs have what

HRRR runs every hour. The `00/06/12/18Z` cycles forecast out to 48 hours;
every other cycle stops at 18. `--hours` is clamped to whatever the chosen
cycle supports.

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

The suite covers cycle selection, `.idx` byte-range parsing, grid subsetting
and the colour bands; it stubs out S3 so it runs offline.

## Layout

```
hrrr_smoke/
  catalog.py   which cycle exists, which forecast hours it published
  fetch.py     .idx parsing + range requests + on-disk cache
  grid.py      GRIB decode, lat/lon clip to the domain
  render.py    per-frame map drawing
  animate.py   frames -> GIF / MP4
  cli.py       argument handling
  config.py    domains, cities, palettes
```
