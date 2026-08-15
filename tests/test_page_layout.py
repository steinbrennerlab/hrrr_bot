"""Drive the published page in a real browser.

Everything else about the page is asserted against its HTML, which cannot see
the two things that actually break it: whether it overflows a phone, and
whether the video/GIF handoff works in an engine that has opinions about
codecs. These need a browser, so they skip where there is not one -- the page
is still covered by `test_site.py` either way.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from hrrr_smoke.archive import GATED, RunRecord, save
from hrrr_smoke.site import Check, build

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
from playwright.sync_api import Error as PlaywrightError  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

TZ = ZoneInfo("America/Los_Angeles")
REPO = "steinbrennerlab/hrrr_bot"
STEM = "hrrr_smoke_puget_20260814_t12z"

PHONE = {"width": 390, "height": 844}  # iPhone 14
DESKTOP = {"width": 1280, "height": 900}


@pytest.fixture(scope="module")
def browser():
    # Set PLAYWRIGHT_CHROMIUM_EXECUTABLE where a Chromium is already installed
    # but not where this Playwright build expects to find it.
    executable = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    try:
        with sync_playwright() as pw:
            try:
                launched = pw.chromium.launch(executable_path=executable)
            except PlaywrightError as exc:  # no browser binary in this env
                pytest.skip(f"chromium unavailable: {exc}")
            yield launched
            launched.close()
    except NotImplementedError as exc:  # pragma: no cover - platform guard
        pytest.skip(str(exc))


@pytest.fixture(scope="module")
def page_url(tmp_path_factory) -> str:
    """A built page with media beside it, all of it generated here.

    Self-contained on purpose: `out/` is scratch and is not in the repository,
    so depending on a previously rendered frame would mean these quietly
    skipped everywhere except a machine that had just run the pipeline.
    """
    src = tmp_path_factory.mktemp("runs")
    Image.new("RGB", (962, 988), "#f5f2ed").save(src / f"{STEM}.png")
    # Deliberately not real video: what is under test is the layout and the
    # fallback, and an undecodable source exercises the fallback path. The
    # sizes are the real ones, so the download labels read as they will.
    (src / f"{STEM}.mp4").write_bytes(b"\0" * 807_000)
    (src / f"{STEM}.gif").write_bytes(b"\0" * 1_290_000)
    save(
        src,
        [
            RunRecord(
                cycle=STEM,
                cycle_time=datetime(2026, 8, 14, 12, tzinfo=UTC).isoformat(),
                domain="puget",
                reference_city="Seattle",
                peak_ugm3=23.1,
                peak_at=None,
                frames=49,
                files=[f"{STEM}.gif", f"{STEM}.mp4", f"{STEM}.png"],
                city_peaks={"Seattle": 23.1, "Tacoma": 9.4, "Everett": 41.2},
                origin=GATED,
                gate_threshold="moderate",
                valid_from=datetime(2026, 8, 14, 12, tzinfo=UTC).isoformat(),
                valid_to=datetime(2026, 8, 16, 12, tzinfo=UTC).isoformat(),
            )
        ],
    )
    out = tmp_path_factory.mktemp("site")
    check = Check(
        city="Seattle",
        peak=8.7,
        threshold=9.0,
        threshold_label="moderate",
        triggered=False,
        at=datetime(2026, 8, 15, 1, tzinfo=UTC),
        samples=30,
    )
    build(src, out, tz=TZ, repo=REPO, check=check)
    return (out / "index.html").as_uri()


def _shot(browser, url, viewport, name, artifacts: Path):
    page = browser.new_page(viewport=viewport)
    page.goto(url)
    page.wait_for_load_state("networkidle")
    artifacts.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(artifacts / f"{name}.png"), full_page=True)
    return page


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("screenshots")


@pytest.mark.parametrize(
    ("name", "viewport"), [("phone", PHONE), ("desktop", DESKTOP)]
)
def test_the_page_never_scrolls_sideways(browser, page_url, artifacts, name, viewport):
    page = _shot(browser, page_url, viewport, name, artifacts)
    try:
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - "
            "document.documentElement.clientWidth"
        )
        assert overflow <= 0, f"{name} viewport scrolls {overflow}px sideways"
    finally:
        page.close()


def test_tables_stack_on_a_phone(browser, page_url, artifacts):
    page = _shot(browser, page_url, PHONE, "phone-tables", artifacts)
    try:
        # Stacked rows: the header is taken out of flow and each cell carries
        # its own label, so no table is wider than the screen.
        widest = page.evaluate(
            "() => Math.max(...[...document.querySelectorAll('table')]"
            ".map(t => t.scrollWidth))"
        )
        assert widest <= PHONE["width"]
        assert page.locator("thead").first.is_hidden() or page.evaluate(
            "() => getComputedStyle(document.querySelector('thead'))"
            ".position === 'absolute'"
        )
    finally:
        page.close()


def test_tables_keep_their_headers_on_a_desktop(browser, page_url, artifacts):
    page = _shot(browser, page_url, DESKTOP, "desktop-tables", artifacts)
    try:
        assert page.locator("thead th", has_text="Category").first.is_visible()
    finally:
        page.close()


def test_an_unplayable_video_is_replaced_by_the_gif(browser, page_url, artifacts):
    """The empty-box failure the markup alone cannot cover."""
    page = browser.new_page(viewport=DESKTOP)
    try:
        page.goto(page_url)
        page.wait_for_selector("#hero-gif:not([hidden])", timeout=10_000)
        assert page.locator("video#hero").count() == 0
        src = page.get_attribute("#hero-gif", "src")
        assert src and src.endswith(".gif")
    finally:
        page.close()


def test_a_reader_who_asked_for_stillness_is_not_autoplayed(browser, page_url):
    page = browser.new_page(viewport=DESKTOP, reduced_motion="reduce")
    try:
        page.goto(page_url)
        page.wait_for_load_state("networkidle")
        # The video may have been swapped for the GIF; either way nothing
        # should have been told to start playing on its own.
        assert (
            page.evaluate(
                "() => {const v=document.querySelector('video');"
                "return v ? v.hasAttribute('autoplay') : false;}"
            )
            is False
        )
    finally:
        page.close()


def test_the_card_and_the_check_are_both_on_the_page(browser, page_url):
    page = browser.new_page(viewport=DESKTOP)
    try:
        page.goto(page_url)
        assert page.locator(".status", has_text="Today’s check").is_visible()
        assert page.locator(".card .facts").is_visible()
        # The check's colour comes from 8.7 µg/m³, not from the 23.1 run.
        dot = page.evaluate(
            "() => getComputedStyle(document.querySelector('.status .dot'))"
            ".backgroundColor"
        )
        assert dot == "rgb(90, 169, 95)"  # Good, the band 8.7 falls in
    finally:
        page.close()


def test_screenshots_were_written(artifacts, browser, page_url):
    _shot(browser, page_url, PHONE, "phone", artifacts).close()
    _shot(browser, page_url, DESKTOP, "desktop", artifacts).close()
    shots = sorted(p.name for p in artifacts.glob("*.png"))
    assert "phone.png" in shots and "desktop.png" in shots
    assert json.dumps(shots)  # keep the names visible in a failure report
