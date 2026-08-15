"""Layout regressions on the map itself.

The frames are the product, and the way they go wrong is by crowding: a label
lands on a neighbour's marker, or a legend name runs into the one beside it,
and nothing raises. These measure the drawn geometry rather than eyeballing a
PNG.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from hrrr_smoke.config import AQI_LEGEND_LABELS, AQI_LEVELS, DOMAINS
from hrrr_smoke.grid import SmokeFrame
from hrrr_smoke.render import FrameRenderer

TZ = ZoneInfo("America/Los_Angeles")


def _frame(peak: float = 300.0) -> SmokeFrame:
    domain = DOMAINS["puget"]
    lats, lons = np.meshgrid(
        np.linspace(domain.lat_min, domain.lat_max, 24),
        np.linspace(domain.lon_min, domain.lon_max, 24),
        indexing="ij",
    )
    values = np.linspace(0, peak, 576).reshape(24, 24)
    run = datetime(2026, 8, 14, 12, tzinfo=UTC)
    return SmokeFrame(
        fhr=13, run=run, valid=run, lats=lats, lons=lons, values=values
    )


@pytest.fixture(scope="module", params=sorted(DOMAINS))
def drawn(request, tmp_path_factory):
    """A rendered figure, with its text laid out and measurable.

    Built once per domain, at the coarse coastline: label geometry comes from
    the projection and the offsets, not from how finely the shore is drawn, so
    the 10 m basemap would buy nothing here but a minute and a half.
    """
    domain = DOMAINS[request.param]
    out = tmp_path_factory.mktemp("frames") / "f.png"
    with FrameRenderer(domain, tz=TZ, dpi=110, scale="50m") as renderer:
        renderer.render(_frame(), out)
        renderer.fig.canvas.draw()
        yield domain, renderer


def _boxes(renderer, names):
    canvas = renderer.fig.canvas.get_renderer()
    found = {}
    for text in renderer.ax.texts:
        if text.get_text() in names:
            found[text.get_text()] = text.get_window_extent(canvas)
    return found


def _overlap(a, b) -> float:
    dx = min(a.x1, b.x1) - max(a.x0, b.x0)
    dy = min(a.y1, b.y1) - max(a.y0, b.y0)
    return dx * dy if dx > 0 and dy > 0 else 0.0


def test_no_two_city_labels_overlap(drawn):
    domain, renderer = drawn
    boxes = _boxes(renderer, {c.name for c in domain.cities})
    assert len(boxes) == len(domain.cities)

    names = sorted(boxes)
    for i, one in enumerate(names):
        for other in names[i + 1 :]:
            assert not _overlap(boxes[one], boxes[other]), (
                f"{one} and {other} labels overlap"
            )


def test_no_city_label_covers_another_citys_marker(drawn):
    """The original defect: 'Bremerton' ran straight over Seattle's dot."""
    import cartopy.crs as ccrs

    domain, renderer = drawn
    boxes = _boxes(renderer, {c.name for c in domain.cities})

    for city in domain.cities:
        x, y = renderer.ax.projection.transform_point(
            city.lon, city.lat, ccrs.PlateCarree()
        )
        px, py = renderer.ax.transData.transform((x, y))
        for name, box in boxes.items():
            if name == city.name:
                continue
            inside = box.x0 <= px <= box.x1 and box.y0 <= py <= box.y1
            assert not inside, f"{name!r}'s label covers {city.name}'s marker"


def test_a_labels_offset_places_it_on_the_side_it_asked_for(drawn):
    """A city that throws its name west must actually end up west of its dot."""
    import cartopy.crs as ccrs

    domain, renderer = drawn
    boxes = _boxes(renderer, {c.name for c in domain.cities})
    for city in domain.cities:
        x, y = renderer.ax.projection.transform_point(
            city.lon, city.lat, ccrs.PlateCarree()
        )
        px, _ = renderer.ax.transData.transform((x, y))
        box = boxes[city.name]
        if city.ha == "right":
            assert box.x1 <= px + 1, f"{city.name} was meant to sit left of its marker"
        elif city.ha == "left":
            assert box.x0 >= px - 1, f"{city.name} was meant to sit right of its marker"


def test_every_legend_band_is_named_and_none_collide(drawn):
    _, renderer = drawn
    cax = renderer.fig.axes[-1]
    canvas = renderer.fig.canvas.get_renderer()
    labels = [t for t in cax.texts if t.get_text() in AQI_LEGEND_LABELS]
    assert len(labels) == len(AQI_LEGEND_LABELS)

    boxes = sorted((t.get_window_extent(canvas) for t in labels), key=lambda b: b.x0)
    for left, right in zip(boxes, boxes[1:], strict=False):
        assert left.x1 <= right.x0, "legend labels run into each other"


def test_the_sensitive_band_names_the_group_it_means(drawn):
    _, renderer = drawn
    cax = renderer.fig.axes[-1]
    assert any("groups" in t.get_text() for t in cax.texts)


@pytest.fixture(scope="module")
def puget(tmp_path_factory):
    """One reusable renderer -- redrawing the basemap per case costs seconds."""
    out = tmp_path_factory.mktemp("header") / "f.png"
    with FrameRenderer(DOMAINS["puget"], tz=TZ, dpi=90, scale="50m") as renderer:
        yield renderer, out


def test_the_header_reports_the_lead_and_the_in_box_peak(puget):
    renderer, out = puget
    renderer.render(_frame(300.0), out)
    assert renderer._lead.get_text() == "+13 h"
    assert "peak 300 µg m⁻³" in renderer._meta.get_text()


def test_a_fire_outside_the_domain_is_not_reported_as_the_peak(puget):
    """The BC wildfire problem, in miniature.

    The array has to stay rectangular in grid space, so its corners reach past
    the lat/lon box the map advertises. A fire sitting out there must not be
    printed as this region's peak.
    """
    renderer, out = puget
    base = _frame(50.0)
    inside = np.ones_like(base.values, dtype=bool)
    values = base.values.copy()
    inside[0, 0] = False
    values[0, 0] = 4521.0  # the real one, at 49.8 N -- north of the domain

    renderer.render(
        SmokeFrame(
            fhr=13,
            run=base.run,
            valid=base.valid,
            lats=base.lats,
            lons=base.lons,
            values=values,
            inside=inside,
        ),
        out,
    )
    meta = renderer._meta.get_text()
    assert "4,521" not in meta, "an out-of-box fire was reported as the peak"
    assert "peak 50 µg m⁻³" in meta


def test_the_smoke_layer_is_drawn_over_the_basemap_and_under_the_labels(puget):
    renderer, out = puget
    renderer.render(_frame(), out)
    mesh = renderer._mesh
    assert 0 < mesh.get_zorder() < 5
    # Trace smoke stays transparent so clean air recedes into the basemap.
    assert mesh.norm.boundaries[1] == AQI_LEVELS[1]
