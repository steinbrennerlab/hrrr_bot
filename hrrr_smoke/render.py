"""Draw one PNG per forecast hour."""

from __future__ import annotations

import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import (
    patheffects,
)
from matplotlib.colorbar import ColorbarBase
from matplotlib.colors import (
    BoundaryNorm,
    LinearSegmentedColormap,
    ListedColormap,
)

from .config import (
    AQI_COLORS,
    AQI_DARK_BANDS,
    AQI_LABELS,
    AQI_LEGEND_LABELS,
    AQI_LEVELS,
    AQI_OVER,
    AQI_SOLID_COLORS,
    Domain,
    band_for,
)
from .grid import SmokeFrame

log = logging.getLogger(__name__)

INK = "#1c1c1e"
MUTED = "#6b6b70"
FAINT = "#9aa3ab"
WATER = "#dbe6ee"
LAND = "#f5f2ed"

# Geometry of the figure, in figure fractions. Fixed placement rather than
# tight_layout so every frame lands on an identical canvas -- GIF needs that,
# and a title that changes width must not shift the map underneath it.
_MAP_BOX = (0.035, 0.155, 0.93, 0.735)
_BAR_BOX = (0.10, 0.080, 0.80, 0.020)

# Width reserved at the top right for "+N h", so the category badge beside it
# can be right-aligned into a fixed slot instead of chasing the text's width.
# Sized for the longest lead the 48-hour cycles produce, plus the badge's own
# rounded padding, which overhangs its anchor.
_LEAD_SLOT = 0.090

# Single-hue magnitude ramp, for reading concentration rather than health
# category. Same breakpoints, so the two palettes are directly comparable.
_MONO = LinearSegmentedColormap.from_list(
    "smoke_mono",
    [
        (0.88, 0.80, 0.72, 0.35),
        (0.76, 0.60, 0.46, 0.60),
        (0.60, 0.42, 0.29, 0.75),
        (0.42, 0.26, 0.17, 0.86),
        (0.24, 0.13, 0.08, 0.92),
    ],
)


def _palette(name: str) -> tuple[ListedColormap, BoundaryNorm]:
    """Discrete colormap + norm over `AQI_LEVELS`, with an unbounded top bin."""
    n_bins = len(AQI_LEVELS) - 1
    if name == "mono":
        colors = [(1.0, 1.0, 1.0, 0.0)] + [
            _MONO(i / (n_bins - 2)) for i in range(n_bins - 1)
        ]
        over = _MONO(1.0)
    else:
        colors = list(AQI_COLORS)
        over = AQI_OVER

    clear = (0.0, 0.0, 0.0, 0.0)
    cmap = ListedColormap(colors).with_extremes(over=over, under=clear, bad=clear)
    return cmap, BoundaryNorm(AQI_LEVELS, cmap.N)


def _basemap(ax, domain: Domain, scale: str = "10m") -> None:
    ax.set_extent(
        [domain.lon_min, domain.lon_max, domain.lat_min, domain.lat_max],
        crs=ccrs.PlateCarree(),
    )
    ax.add_feature(cfeature.LAND.with_scale(scale), facecolor=LAND, zorder=0)
    ax.add_feature(cfeature.OCEAN.with_scale(scale), facecolor=WATER, zorder=0)
    ax.add_feature(cfeature.LAKES.with_scale(scale), facecolor=WATER, zorder=1)
    ax.add_feature(
        cfeature.COASTLINE.with_scale(scale),
        edgecolor="#7d8791",
        linewidth=0.7,
        zorder=5,
    )
    ax.add_feature(
        cfeature.STATES.with_scale(scale), edgecolor=FAINT, linewidth=0.5, zorder=5
    )
    ax.add_feature(
        cfeature.BORDERS.with_scale(scale),
        edgecolor="#7d8791",
        linewidth=0.7,
        linestyle=(0, (4, 2)),
        zorder=5,
    )
    for spine in ax.spines.values():
        spine.set_edgecolor("#c8cdd2")
        spine.set_linewidth(0.8)


def _cities(ax, domain: Domain) -> None:
    halo = [patheffects.withStroke(linewidth=2.4, foreground="white")]
    for city in domain.cities:
        ax.plot(
            city.lon,
            city.lat,
            marker="o",
            markersize=3.4,
            markerfacecolor="white",
            markeredgecolor=INK,
            markeredgewidth=0.9,
            transform=ccrs.PlateCarree(),
            zorder=6,
        )
        # Offset in points rather than degrees: the gap between a marker and
        # its name should be the same visual distance on every domain, and a
        # degree is not, once Lambert Conformal has had its way with the edges.
        ax.annotate(
            city.name,
            xy=(city.lon, city.lat),
            xycoords=ccrs.PlateCarree()._as_mpl_transform(ax),
            xytext=(city.dx, city.dy),
            textcoords="offset points",
            ha=city.ha,
            va=city.va,
            fontsize=7.5,
            color=INK,
            zorder=6,
            path_effects=halo,
            annotation_clip=False,
        )


def _legend(fig, cmap, norm) -> None:
    """Discrete scale bar: category names above, breakpoint values below.

    The category name is what makes the colour readable without the legend
    being colour-alone, so it is drawn for every band, not just the extremes.
    """
    cax = fig.add_axes(_BAR_BOX)
    bar = ColorbarBase(
        cax,
        cmap=cmap,
        norm=norm,
        orientation="horizontal",
        boundaries=list(AQI_LEVELS) + [AQI_LEVELS[-1] * 1.6],
        extend="max",
        spacing="uniform",
        ticks=list(AQI_LEVELS[1:]),
    )
    bar.ax.set_xticklabels([f"{v:g}" for v in AQI_LEVELS[1:]], fontsize=7.5)
    bar.ax.tick_params(length=2.5, color=FAINT, labelcolor=MUTED, pad=2)
    bar.outline.set_edgecolor("#c8cdd2")
    bar.outline.set_linewidth(0.6)

    # Bands are drawn with uniform spacing, and the extend arrow occupies one
    # more slot, so each category centre is a simple fraction of the axis.
    # Labels are anchored at their bottom so a two-line name grows upward into
    # the gap under the map rather than down onto the bar.
    for i, label in enumerate(AQI_LEGEND_LABELS):
        cax.text(
            (i + 0.5) / len(AQI_LEGEND_LABELS),
            1.5,
            label,
            transform=cax.transAxes,
            ha="center",
            va="bottom",
            linespacing=1.15,
            fontsize=6.8,
            color=MUTED,
        )
    cax.text(
        0.5,
        -2.3,
        "Near-surface smoke (µg m⁻³, PM2.5-equivalent) · EPA AQI categories",
        transform=cax.transAxes,
        ha="center",
        va="top",
        fontsize=8,
        color=MUTED,
    )


class FrameRenderer:
    """Renders every forecast hour onto one reusable figure.

    Projecting the 10 m coastline is far more expensive than drawing the smoke
    field, so the basemap, city labels and legend are built once and only the
    data layer and the header text change between frames. That also guarantees
    a pixel-identical canvas, which is what the GIF needs.
    """

    def __init__(
        self,
        domain: Domain,
        *,
        tz: ZoneInfo,
        palette: str = "aqi",
        dpi: int = 130,
        scale: str = "10m",
    ) -> None:
        """`scale` is the Natural Earth resolution for the coastline.

        Projecting the 10 m shoreline of an inlet-riddled coast is by far the
        slowest thing here, and it is the right cost for a published frame.
        Tests and quick previews, which care about where things land rather
        than how finely the shore is drawn, can drop to "50m".
        """
        self.domain = domain
        self.tz = tz
        self.dpi = dpi
        self.cmap, self.norm = _palette(palette)

        proj = ccrs.LambertConformal(
            central_longitude=(domain.lon_min + domain.lon_max) / 2,
            central_latitude=(domain.lat_min + domain.lat_max) / 2,
            standard_parallels=(domain.lat_min, domain.lat_max),
        )
        self.fig = plt.figure(figsize=(7.4, 7.6), dpi=dpi)
        self.fig.patch.set_facecolor("white")
        self.ax = self.fig.add_axes(_MAP_BOX, projection=proj)

        _basemap(self.ax, domain, scale)
        _cities(self.ax, domain)
        _legend(self.fig, self.cmap, self.norm)

        left = _MAP_BOX[0]
        right = _MAP_BOX[0] + _MAP_BOX[2]
        self._title = self.fig.text(
            left, 0.962, "", fontsize=16, fontweight="bold", color=INK,
            ha="left", va="center",
        )
        self.fig.text(
            left,
            0.929,
            f"HRRR-Smoke near-surface forecast · {domain.name}",
            fontsize=9,
            color=MUTED,
            ha="left",
            va="center",
        )
        self._lead = self.fig.text(
            right, 0.962, "", fontsize=13, color=MUTED, ha="right", va="center"
        )
        # The badge names the category the peak figure below it falls in, so
        # the number is readable without counting bands along the colourbar.
        # Right-aligned in the slot left of "+N h", which is at most five
        # characters wide, so the two can never collide however long the
        # category name gets.
        self._badge = self.fig.text(
            right - _LEAD_SLOT,
            0.962,
            "",
            fontsize=8,
            fontweight="bold",
            ha="right",
            va="center",
        )
        self._meta = self.fig.text(
            right, 0.929, "", fontsize=8.5, color=MUTED, ha="right", va="center"
        )
        self._mesh = None

    def render(self, frame: SmokeFrame, out_path: Path) -> Path:
        """Draw one forecast hour and save it."""
        if self._mesh is not None:
            self._mesh.remove()
        self._mesh = self.ax.pcolormesh(
            frame.lons,
            frame.lats,
            np.ma.masked_less(frame.values, AQI_LEVELS[1]),
            cmap=self.cmap,
            norm=self.norm,
            transform=ccrs.PlateCarree(),
            shading="nearest",
            # One model cell stays one flat block of colour -- this is a
            # forecast on a 3 km grid, and interpolating it would invent detail
            # the model never produced. Antialiasing does not do that: it only
            # decides how a cell's *boundary* lands on the pixel grid. Without
            # it (pcolormesh's default) each quad rounds its edges to whole
            # pixels, neighbours disagree about who owns the shared edge, and
            # the translucent palette turns the resulting hairline gaps into a
            # mesh drawn over the entire map. Every value is unchanged; only
            # the seam between two of them is.
            antialiased=True,
            zorder=2,
        )

        valid = frame.valid.astimezone(self.tz)
        run = frame.run.astimezone(self.tz)
        peak = float(np.nanmax(frame.values))
        band = band_for(peak)
        self._title.set_text(f"{valid:%a %b %-d} · {valid:%-I:%M %p} {valid:%Z}")
        self._lead.set_text(f"+{frame.fhr} h")
        self._badge.set_text(AQI_LABELS[band].upper())
        self._badge.set_color("white" if band in AQI_DARK_BANDS else INK)
        self._badge.set_bbox(
            {
                "boxstyle": "round,pad=0.34",
                "facecolor": AQI_SOLID_COLORS[band],
                "edgecolor": "none",
            }
        )
        self._meta.set_text(
            f"run {run:%-m/%-d %-I %p %Z}  ·  peak {peak:,.0f} µg m⁻³"
        )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        self.fig.savefig(out_path, dpi=self.dpi, facecolor="white")
        log.debug("rendered %s", out_path.name)
        return out_path

    def close(self) -> None:
        plt.close(self.fig)

    def __enter__(self) -> FrameRenderer:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def render_frame(
    frame: SmokeFrame,
    domain: Domain,
    out_path: Path,
    *,
    tz: ZoneInfo,
    palette: str = "aqi",
    dpi: int = 130,
    scale: str = "10m",
) -> Path:
    """Render a single forecast hour. Use `FrameRenderer` for a whole series."""
    with FrameRenderer(domain, tz=tz, palette=palette, dpi=dpi, scale=scale) as r:
        return r.render(frame, out_path)
