import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from hrrr_smoke.archive import ADOPTED, FORCED, GATED, RunRecord, save
from hrrr_smoke.site import Check, build, category

TZ = ZoneInfo("America/Los_Angeles")
REPO = "steinbrennerlab/hrrr_bot"
STEM = "hrrr_smoke_puget_20260814_t12z"


FROM = datetime(2026, 8, 14, 12, tzinfo=UTC).isoformat()
TO = datetime(2026, 8, 16, 12, tzinfo=UTC).isoformat()


def _record(stem=STEM, *, files, peak=23.1, peaks=None, origin=GATED, **kw):
    return RunRecord(
        cycle=stem,
        cycle_time=datetime(2026, 8, 14, 12, tzinfo=UTC).isoformat(),
        domain="puget",
        reference_city="Seattle",
        peak_ugm3=peak,
        peak_at=None,
        frames=25,
        files=files,
        city_peaks=peaks or {"Seattle": 23.1, "Tacoma": 9.4},
        origin=origin,
        gate_threshold=kw.pop("gate_threshold", "moderate"),
        valid_from=kw.pop("valid_from", FROM),
        valid_to=kw.pop("valid_to", TO),
        **kw,
    )


def _archive(tmp_path, files, **kw):
    for name in files:
        (tmp_path / name).write_bytes(b"binary")
    save(tmp_path, [_record(files=files, **kw)])
    return tmp_path


def _check(peak=8.7, *, triggered=False):
    return Check(
        city="Seattle",
        peak=peak,
        threshold=9.0,
        threshold_label="moderate",
        triggered=triggered,
        at=datetime(2026, 8, 15, 1, tzinfo=UTC),
        samples=30,
    )


def test_category_maps_to_the_aqi_bands():
    assert category(0.5)[0] == "Clear"
    assert category(5)[0] == "Good"
    assert category(20)[0] == "Moderate"
    assert category(60)[0] == "Unhealthy"
    assert category(900)[0] == "Hazardous"


def test_sensitive_band_names_the_group_it_means():
    assert category(45)[0] == "Sensitive groups"


def test_unmeasured_has_its_own_label():
    assert category(None)[0] == "Unmeasured"


# --- the hero player ---------------------------------------------------------


def test_mp4_is_the_player_and_the_gif_is_held_in_reserve(tmp_path):
    src = _archive(tmp_path, [f"{STEM}.gif", f"{STEM}.mp4", f"{STEM}.png"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()

    assert f'<source src="{STEM}.mp4"' in page
    assert f'poster="{STEM}.png"' in page
    # The GIF is present for the fallback but must not be fetched up front:
    # it is roughly twice the MP4's weight.
    assert f'data-src="{STEM}.gif"' in page
    assert f'<img class="hero" id="hero-gif" data-src="{STEM}.gif"' in page
    assert not re.search(r'<img[^>]*\ssrc="[^"]*\.gif"', page.split("<noscript>")[0])


def test_a_failed_video_swaps_in_the_gif(tmp_path):
    src = _archive(tmp_path, [f"{STEM}.gif", f"{STEM}.mp4", f"{STEM}.png"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()

    # A missing codec paints an empty box and never reaches inner content, so
    # the swap has to be driven by the error event -- captured, because it
    # fires on <source> and does not bubble.
    assert "addEventListener('error',fallback,true)" in page
    assert "if(v.error)fallback()" in page
    assert "gif.src=gif.dataset.src" in page


def test_scriptless_readers_still_see_the_animation(tmp_path):
    src = _archive(tmp_path, [f"{STEM}.gif", f"{STEM}.mp4", f"{STEM}.png"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    assert f'<noscript><img class="hero" src="{STEM}.gif"' in page


def test_the_gif_is_the_player_when_there_is_no_mp4(tmp_path):
    src = _archive(tmp_path, [f"{STEM}.gif"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    assert "<video" not in page
    assert f'<img class="hero" src="{STEM}.gif"' in page


def test_media_is_copied_beside_the_page(tmp_path):
    src = _archive(tmp_path, [f"{STEM}.gif", f"{STEM}.mp4", f"{STEM}.png"])
    out = tmp_path / "site"

    build(src, out, tz=TZ, repo=REPO)
    for ext in ("gif", "mp4", "png"):
        assert (out / f"{STEM}.{ext}").exists()
    assert (out / "index.json").exists()


# --- today's check, which is not the latest animation ------------------------


def test_a_clean_check_is_not_coloured_by_a_smoky_archive(tmp_path):
    """The bug this guards: a green day drawn red because yesterday was bad."""
    src = _archive(tmp_path, [f"{STEM}.gif"], peak=180.0)  # very unhealthy
    page = build(
        src, tmp_path / "site", tz=TZ, repo=REPO, check=_check(3.2)
    ).read_text()

    dot = re.search(r'<span class="dot" style="background:([^"]+)"', page)
    assert dot, page
    # 3.2 ug/m3 is Good, and must be drawn as Good whatever the archive holds.
    assert dot.group(1) == category(3.2)[1]
    assert dot.group(1) != category(180.0)[1]


def test_a_smoky_check_is_coloured_from_its_own_reading(tmp_path):
    src = _archive(tmp_path, [f"{STEM}.gif"], peak=2.0)
    page = build(
        src, tmp_path / "site", tz=TZ, repo=REPO, check=_check(70.0, triggered=True)
    ).read_text()

    dot = re.search(r'<span class="dot" style="background:([^"]+)"', page)
    assert dot.group(1) == category(70.0)[1]
    assert "Smoke at or above the threshold" in page


def test_the_check_reports_its_own_numbers(tmp_path):
    src = _archive(tmp_path, [f"{STEM}.gif"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO, check=_check()).read_text()
    assert "Today&rsquo;s check" in page
    assert "Below the threshold" in page
    assert "Seattle 8.7 µg/m³" in page


def test_no_check_means_no_banner(tmp_path):
    src = _archive(tmp_path, [f"{STEM}.gif"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    assert "Today&rsquo;s check" not in page


# --- the latest-forecast card ------------------------------------------------


def test_the_card_summarises_the_run(tmp_path):
    src = _archive(tmp_path, [f"{STEM}.gif", f"{STEM}.mp4", f"{STEM}.png"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO, check=_check()).read_text()

    assert "Latest forecast" in page
    for heading in ("Model run", "Forecast window", "Seattle peak", "Category"):
        assert f"<dt>{heading}</dt>" in page
    # The window's ends, in local time: 12Z 14 Aug -> 12Z 16 Aug.
    assert "Fri 14 Aug, 5 AM PDT → Sun 16 Aug, 5 AM PDT" in page
    # The check keeps its own cell, distinct from the run's peak.
    assert "<dt>Last check</dt>" in page


def test_downloads_are_labelled_with_their_size(tmp_path):
    src = tmp_path / "runs"
    src.mkdir()
    for name, size in ((f"{STEM}.gif", 2_800_000), (f"{STEM}.mp4", 1_270_000)):
        (src / name).write_bytes(b"\0" * size)
    save(src, [_record(files=[f"{STEM}.gif", f"{STEM}.mp4"])])

    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    assert "Download MP4 video" in page
    assert "Download Animated GIF" in page
    assert "(2.8 MB)" in page and "(1.3 MB)" in page


def test_a_gated_run_says_why_it_was_kept(tmp_path):
    src = _archive(tmp_path, [f"{STEM}.gif"], origin=GATED)
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    assert "Rendered because Seattle reached moderate." in page


def test_a_forced_run_does_not_claim_it_cleared_the_gate(tmp_path):
    """The 8.7 µg/m³ run: below Moderate, on the page because someone asked."""
    src = _archive(tmp_path, [f"{STEM}.gif"], peak=8.7, origin=FORCED)
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    assert "Rendered on request, regardless of the threshold." in page
    assert "reached" not in page.split("Archive</h2>")[0].split('class="note"')[1]


def test_an_adopted_run_admits_it_does_not_know(tmp_path):
    src = _archive(tmp_path, [f"{STEM}.gif"], origin=ADOPTED)
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    assert "Archived before this page recorded why a run was rendered." in page


def test_the_poster_is_not_offered_as_a_download(tmp_path):
    src = _archive(tmp_path, [f"{STEM}.gif", f"{STEM}.mp4", f"{STEM}.png"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    assert "Download PNG" not in page
    # ...nor listed as a file in the archive table.
    assert ">PNG<" not in page


# --- motion, layout and the rest ---------------------------------------------


def test_reduced_motion_is_respected(tmp_path):
    src = _archive(tmp_path, [f"{STEM}.gif", f"{STEM}.mp4", f"{STEM}.png"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()

    assert "prefers-reduced-motion:reduce" in page
    # Autoplay is added at runtime, so it can be withheld. If it were in the
    # markup no media query could take it back.
    assert "autoplay" not in page.split("<script>")[0]
    assert "prefers-reduced-motion: reduce" in page.split("<script>")[1]


def test_the_player_always_has_visible_controls(tmp_path):
    src = _archive(tmp_path, [f"{STEM}.gif", f"{STEM}.mp4", f"{STEM}.png"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    assert re.search(r"<video[^>]*\scontrols", page)


def test_tables_stack_instead_of_scrolling_on_a_phone(tmp_path):
    src = _archive(tmp_path, [f"{STEM}.gif"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()

    assert "@media (max-width:600px)" in page
    assert "content:attr(data-label)" in page
    # Every body cell needs the label the stacked layout shows in place of a
    # column header.
    for cell in re.findall(r"<tbody>.*?</tbody>", page, re.S):
        assert cell.count("<td") == cell.count("data-label")


def test_city_table_is_rendered_and_sorted_worst_first(tmp_path):
    src = _archive(tmp_path, [f"{STEM}.gif"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    assert "Peak by city" in page
    assert page.index("Seattle") < page.index("Tacoma")


def test_times_are_shown_in_local_pacific_time(tmp_path):
    src = _archive(tmp_path, [f"{STEM}.gif"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    # 12Z on 14 Aug is 5 AM PDT.
    assert "5:00 AM PDT" in page


def test_an_empty_archive_still_produces_a_page(tmp_path):
    save(tmp_path, [])
    page = build(tmp_path, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    assert "No animation has been produced yet" in page
    assert "<html" in page


def test_a_legacy_record_loads_and_renders(tmp_path):
    """Entries written before the manifest grew these fields must still work."""
    stem = "legacy_20260101_t00z"
    (tmp_path / f"{stem}.gif").write_bytes(b"x")
    save(
        tmp_path,
        [
            RunRecord(
                cycle=stem,
                cycle_time=datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
                domain="unknown",
                reference_city="unknown",
                peak_ugm3=None,
                peak_at=None,
                frames=0,
                files=[f"{stem}.gif"],
            )
        ],
    )
    page = build(tmp_path, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    assert "0 frames" not in page
    assert "Unmeasured" in page


def test_page_is_theme_aware_and_responsive(tmp_path):
    src = _archive(tmp_path, [f"{STEM}.gif"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    assert "prefers-color-scheme: dark" in page
    assert "width=device-width" in page
    assert 'class="scroll"' in page


def test_archive_links_point_at_the_repository(tmp_path):
    src = _archive(tmp_path, [f"{STEM}.gif"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    assert f"https://github.com/{REPO}/raw/main/runs/" in page


def test_the_archive_peak_column_is_labelled_once_for_the_whole_table(tmp_path):
    """An adopted row must not label the column 'Unknown peak'."""
    for name in (f"{STEM}.gif", "legacy_20260101_t00z.gif"):
        (tmp_path / name).write_bytes(b"binary")
    save(
        tmp_path,
        [
            _record(files=[f"{STEM}.gif"]),
            RunRecord(
                cycle="legacy_20260101_t00z",
                cycle_time=datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
                domain="unknown",
                reference_city="unknown",
                peak_ugm3=None,
                peak_at=None,
                frames=0,
                files=["legacy_20260101_t00z.gif"],
            ),
        ],
    )
    page = build(tmp_path, tmp_path / "site", tz=TZ, repo=REPO).read_text()

    assert "Unknown peak" not in page
    assert page.count('data-label="Seattle peak"') == 2
    assert '<th class="num">Seattle peak</th>' in page
