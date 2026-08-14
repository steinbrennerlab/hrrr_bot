from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from hrrr_smoke.archive import RunRecord, save
from hrrr_smoke.site import build, category

TZ = ZoneInfo("America/Los_Angeles")
REPO = "steinbrennerlab/hrrr_bot"


def _record(stem="hrrr_smoke_puget_20260814_t12z", *, files, peak=23.1, peaks=None):
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
    )


def _archive(tmp_path, files):
    for name in files:
        (tmp_path / name).write_bytes(b"binary")
    save(tmp_path, [_record(files=files)])
    return tmp_path


def test_category_maps_to_the_aqi_bands():
    assert category(0.5)[0] == "Clear"
    assert category(5)[0] == "Good"
    assert category(20)[0] == "Moderate"
    assert category(60)[0] == "Unhealthy"
    assert category(900)[0] == "Hazardous"


def test_unmeasured_has_its_own_label():
    assert category(None)[0] == "Unmeasured"


def test_page_embeds_the_gif_rather_than_linking_it(tmp_path):
    stem = "hrrr_smoke_puget_20260814_t12z"
    src = _archive(tmp_path, [f"{stem}.gif", f"{stem}.mp4"])
    out = tmp_path / "site"

    page = build(src, out, tz=TZ, repo=REPO).read_text()

    # An <img> renders; an <a href> would only download.
    assert f'<img class="hero" src="{stem}.gif"' in page
    # A <video> whose codec is missing renders an empty box, so the MP4 is
    # offered as a link instead of being the hero.
    assert "<video" not in page
    assert f'<a href="{stem}.mp4">' in page


def test_media_is_copied_beside_the_page(tmp_path):
    stem = "hrrr_smoke_puget_20260814_t12z"
    src = _archive(tmp_path, [f"{stem}.gif", f"{stem}.mp4"])
    out = tmp_path / "site"

    build(src, out, tz=TZ, repo=REPO)
    assert (out / f"{stem}.gif").exists()
    assert (out / f"{stem}.mp4").exists()
    assert (out / "index.json").exists()


def test_video_is_used_only_when_there_is_no_gif(tmp_path):
    stem = "hrrr_smoke_puget_20260814_t12z"
    src = _archive(tmp_path, [f"{stem}.mp4"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    assert "<video" in page


def test_city_table_is_rendered_and_sorted_worst_first(tmp_path):
    src = _archive(tmp_path, ["hrrr_smoke_puget_20260814_t12z.gif"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    assert "Peak by city" in page
    assert page.index("Seattle") < page.index("Tacoma")


def test_times_are_shown_in_local_pacific_time(tmp_path):
    src = _archive(tmp_path, ["hrrr_smoke_puget_20260814_t12z.gif"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    # 12Z on 14 Aug is 5 AM PDT.
    assert "5:00 AM PDT" in page


def test_status_banner_appears_when_given(tmp_path):
    src = _archive(tmp_path, ["hrrr_smoke_puget_20260814_t12z.gif"])
    page = build(
        src, tmp_path / "site", tz=TZ, repo=REPO, status="Seattle peak 8.7 below"
    ).read_text()
    assert "Today&rsquo;s check" in page
    assert "Seattle peak 8.7 below" in page


def test_an_empty_archive_still_produces_a_page(tmp_path):
    save(tmp_path, [])
    page = build(tmp_path, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    assert "No animation has been produced yet" in page
    assert "<html" in page


def test_frame_count_is_omitted_when_unknown(tmp_path):
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


def test_page_is_theme_aware_and_responsive(tmp_path):
    src = _archive(tmp_path, ["hrrr_smoke_puget_20260814_t12z.gif"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    assert "prefers-color-scheme: dark" in page
    assert "width=device-width" in page
    # Wide tables must scroll inside their own box rather than the body.
    assert 'class="scroll"' in page


def test_archive_links_point_at_the_repository(tmp_path):
    src = _archive(tmp_path, ["hrrr_smoke_puget_20260814_t12z.gif"])
    page = build(src, tmp_path / "site", tz=TZ, repo=REPO).read_text()
    assert f"https://github.com/{REPO}/raw/main/runs/" in page
