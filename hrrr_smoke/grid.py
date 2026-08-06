"""Decode a smoke GRIB record and cut it down to the domain of interest."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import xarray as xr

from .config import KG_M3_TO_UG_M3, Domain


@dataclass(frozen=True)
class SmokeFrame:
    """Near-surface smoke over the domain for one forecast hour."""

    fhr: int
    run: datetime  # cycle initialisation, UTC
    valid: datetime  # forecast valid time, UTC
    lats: np.ndarray  # 2-D
    lons: np.ndarray  # 2-D, degrees east in [-180, 180]
    values: np.ndarray  # 2-D, ug m^-3


def _as_utc(value) -> datetime:
    """numpy datetime64 -> timezone-aware UTC datetime."""
    stamp = np.datetime64(value, "s").astype("int64")
    return datetime.fromtimestamp(stamp, tz=UTC)


def load_frame(path: Path, fhr: int, domain: Domain) -> SmokeFrame:
    """Read one cached GRIB record and clip it to `domain`."""
    # indexpath='' keeps cfgrib from dropping .idx sidecars next to the cache.
    with xr.open_dataset(
        path, engine="cfgrib", backend_kwargs={"indexpath": ""}
    ) as ds:
        # ecCodes has no parameter table entry for HRRR's smoke field, so the
        # variable arrives as `unknown`. There is exactly one data variable in
        # the record, which is what we asked S3 for.
        name = next(iter(ds.data_vars))
        values = ds[name].values.astype("float32")
        lats = ds.latitude.values
        lons = ds.longitude.values
        run = _as_utc(ds.time.values)
        valid = _as_utc(ds.valid_time.values)

    lons = np.where(lons > 180.0, lons - 360.0, lons)

    inside = (
        (lats >= domain.lat_min)
        & (lats <= domain.lat_max)
        & (lons >= domain.lon_min)
        & (lons <= domain.lon_max)
    )
    if not inside.any():
        raise ValueError(f"Domain {domain.name!r} falls outside the HRRR CONUS grid.")

    # The HRRR grid is Lambert conformal, so a lat/lon box is not a rectangle in
    # grid space. Take the bounding rows/columns and keep the array rectangular.
    rows, cols = np.where(inside)
    y0, y1 = rows.min(), rows.max() + 1
    x0, x1 = cols.min(), cols.max() + 1

    return SmokeFrame(
        fhr=fhr,
        run=run,
        valid=valid,
        lats=lats[y0:y1, x0:x1],
        lons=lons[y0:y1, x0:x1],
        values=values[y0:y1, x0:x1] * KG_M3_TO_UG_M3,
    )


def city_series(frames: list[SmokeFrame], lat: float, lon: float) -> list[float]:
    """Smoke at the grid cell nearest a point, one value per frame."""
    if not frames:
        return []
    first = frames[0]
    # Good enough at these latitudes: scale longitude so the nearest-cell search
    # is not biased by degrees of longitude being shorter than degrees of latitude.
    scale = np.cos(np.deg2rad(lat))
    d2 = (first.lats - lat) ** 2 + ((first.lons - lon) * scale) ** 2
    iy, ix = np.unravel_index(np.argmin(d2), d2.shape)
    return [float(f.values[iy, ix]) for f in frames]
