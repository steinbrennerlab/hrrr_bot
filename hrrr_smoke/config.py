"""Domains, palettes and other tunables for the HRRR near-surface smoke maps."""

from __future__ import annotations

from dataclasses import dataclass

# HRRR-Smoke output lives on the NOAA Big Data Program mirror, which supports
# HTTP range requests. That is what lets us pull a single ~800 KB GRIB record
# out of a ~130 MB file.
S3_BUCKET_URL = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"

# Near-surface smoke in the HRRR surface files. `MASSDEN` also exists for dust
# on some grids, so the level is part of the identity of the record.
SMOKE_VARIABLE = "MASSDEN"
SMOKE_LEVEL = "8 m above ground"

# GRIB stores this as kg m^-3; every published smoke product uses ug m^-3.
KG_M3_TO_UG_M3 = 1e9

# Cycles that carry the 48-hour forecast. Every other cycle stops at 18 hours.
EXTENDED_CYCLES = (0, 6, 12, 18)
EXTENDED_MAX_FHR = 48
STANDARD_MAX_FHR = 18

DEFAULT_TZ = "America/Los_Angeles"


@dataclass(frozen=True)
class City:
    """A labelled point on a domain.

    The label offset is in typographic points from the marker, not degrees, so
    it means the same thing however wide the domain is and however the
    projection stretches near the edges. The default sits the name up and to
    the right; cities that would collide with a neighbour override it, which is
    a judgement about one particular map and belongs here beside the
    coordinates rather than in the renderer.
    """

    name: str
    lat: float
    lon: float
    dx: float = 5.0
    dy: float = 3.0
    ha: str = "left"
    va: str = "bottom"


@dataclass(frozen=True)
class Domain:
    """A lat/lon box to cut out of the CONUS grid, plus the cities to label."""

    name: str
    lon_min: float
    lon_max: float
    lat_min: float
    lat_max: float
    cities: tuple[City, ...]


# Label placement notes, for the crowded middle of the Sound: Bremerton and
# Seattle are 20 km apart and Bremerton's name is long enough to run straight
# over Seattle's marker, so it is thrown west over Hood Canal. Tacoma drops
# below its marker to stay out of Seattle's column, and Everett rises above
# its own so it does not chase Mount Vernon down the I-5 corridor.
PUGET_SOUND = Domain(
    name="Greater Puget Sound",
    lon_min=-124.8,
    lon_max=-120.8,
    lat_min=46.3,
    lat_max=49.2,
    cities=(
        City("Seattle", 47.606, -122.332, dx=6.0, dy=4.0),
        City("Tacoma", 47.253, -122.444, dx=6.0, dy=-5.0, va="top"),
        City("Everett", 47.979, -122.202, dx=6.0, dy=4.0),
        City("Olympia", 47.038, -122.900, dx=-6.0, dy=-5.0, ha="right", va="top"),
        City("Bellingham", 48.750, -122.479, dx=6.0, dy=3.0),
        City("Bremerton", 47.567, -122.633, dx=-6.0, dy=-1.0, ha="right", va="center"),
        City("Mount Vernon", 48.421, -122.334, dx=6.0, dy=3.0),
        City("Port Angeles", 48.118, -123.431, dx=0.0, dy=-6.0, ha="center", va="top"),
    ),
)

# A wider view that picks up smoke arriving from the Cascades, the Columbia
# Basin and southern British Columbia before it reaches the Sound.
CASCADIA = Domain(
    name="Cascadia",
    lon_min=-125.5,
    lon_max=-116.5,
    lat_min=44.5,
    lat_max=50.0,
    cities=(
        City("Seattle", 47.606, -122.332, dx=6.0, dy=-5.0, va="top"),
        City("Vancouver BC", 49.283, -123.121, dx=-6.0, dy=3.0, ha="right"),
        City("Spokane", 47.659, -117.425, dx=-6.0, dy=3.0, ha="right"),
        City("Portland", 45.512, -122.658, dx=6.0, dy=3.0),
        City("Yakima", 46.602, -120.505, dx=6.0, dy=3.0),
        City("Bellingham", 48.750, -122.479, dx=6.0, dy=3.0),
    ),
)

DOMAINS = {"puget": PUGET_SOUND, "cascadia": CASCADIA}

# EPA AQI categories for PM2.5 (2024 breakpoints), which is how a smoke
# concentration is actually read by a person deciding whether to go outside.
# The lowest bin is split so that trace smoke is still visible: below
# 1 ug/m^3 the map stays transparent, so clean air recedes to the basemap.
# The top category is unbounded and is drawn as the colourbar's extend arrow.
AQI_LEVELS = (0.0, 1.0, 9.0, 35.4, 55.4, 125.4, 225.4)
AQI_COLORS = (
    (0.00, 0.00, 0.00, 0.00),  # < 1      clear
    (0.42, 0.75, 0.44, 0.45),  # 1-9      good, trace smoke
    (0.99, 0.87, 0.24, 0.62),  # 9-35     moderate
    (0.98, 0.55, 0.14, 0.75),  # 35-55    unhealthy for sensitive groups
    (0.90, 0.16, 0.18, 0.82),  # 55-125   unhealthy
    (0.56, 0.25, 0.59, 0.88),  # 125-225  very unhealthy
)
AQI_OVER = (0.49, 0.00, 0.14, 0.92)  # > 225    hazardous

# Opaque equivalents of the same bands, for the chips on the page, which sit on
# a flat background instead of blending over the basemap.
AQI_SOLID_COLORS = (
    "#9aa7b2",  # clear
    "#5aa95f",  # good
    "#e0bc16",  # moderate
    "#e07b18",  # sensitive groups
    "#d62529",  # unhealthy
    "#8f3f97",  # very unhealthy
    "#7e0023",  # hazardous
)


def band_for(value: float) -> int:
    """Index of the AQI band a concentration falls in."""
    index = 0
    for i, edge in enumerate(AQI_LEVELS):
        if value >= edge:
            index = i
    return index
# Short names, used as the command-line threshold keys ("--gate-threshold
# sensitive") and anywhere a category has to fit in a table cell.
AQI_LABELS = (
    "Clear",
    "Good",
    "Moderate",
    "Sensitive",
    "Unhealthy",
    "Very unhealthy",
    "Hazardous",
)

# What a reader should actually see. "Sensitive" alone says nothing; the EPA
# category is "unhealthy for sensitive groups". The newlines are where a label
# is allowed to wrap under a colourbar band, which is the only place these are
# tight enough to need it -- the page collapses them back to spaces.
AQI_LEGEND_LABELS = (
    "Clear",
    "Good",
    "Moderate",
    "Sensitive\ngroups",
    "Unhealthy",
    "Very\nunhealthy",
    "Hazardous",
)
