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
class Domain:
    """A lat/lon box to cut out of the CONUS grid, plus the cities to label."""

    name: str
    lon_min: float
    lon_max: float
    lat_min: float
    lat_max: float
    cities: tuple[tuple[str, float, float], ...]


PUGET_SOUND = Domain(
    name="Greater Puget Sound",
    lon_min=-124.8,
    lon_max=-120.8,
    lat_min=46.3,
    lat_max=49.2,
    cities=(
        ("Seattle", 47.606, -122.332),
        ("Tacoma", 47.253, -122.444),
        ("Everett", 47.979, -122.202),
        ("Olympia", 47.038, -122.900),
        ("Bellingham", 48.750, -122.479),
        ("Bremerton", 47.567, -122.633),
        ("Mount Vernon", 48.421, -122.334),
        ("Port Angeles", 48.118, -123.431),
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
        ("Seattle", 47.606, -122.332),
        ("Vancouver BC", 49.283, -123.121),
        ("Spokane", 47.659, -117.425),
        ("Portland", 45.512, -122.658),
        ("Yakima", 46.602, -120.505),
        ("Bellingham", 48.750, -122.479),
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
AQI_LABELS = (
    "Clear",
    "Good",
    "Moderate",
    "Sensitive",
    "Unhealthy",
    "Very unhealthy",
    "Hazardous",
)
