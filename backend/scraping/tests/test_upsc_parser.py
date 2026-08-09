"""EXT-044: the UPSC parser, tested against a saved copy of the real page.

The fixture is a genuine capture of https://www.upsc.gov.in/ - not
hand-written markup - so these tests assert against the structure the
board actually serves, including its quirks.
"""

from pathlib import Path

import pytest

from exams.models import StatusTrack
from scraping.board_parsers.upsc import UpscWhatsNewParser
from scraping.parsers import Observation, get_parser

FIXTURE = Path(__file__).parent / "fixtures" / "upsc_whats_new.html"


@pytest.fixture(scope="module")
def html() -> str:
    return FIXTURE.read_text(encoding="utf-8", errors="replace")


@pytest.fixture
def observations(html) -> list[Observation]:
    return UpscWhatsNewParser().parse(html)


# --- the fixture itself -----------------------------------------------


def test_the_fixture_is_a_real_capture_not_hand_written_markup(html):
    """Guards the value of every other test here: if the fixture were
    trimmed to a toy snippet, passing would stop meaning anything."""
    assert len(html) > 50_000
    assert "www.upsc.gov.in" in html
    assert 'class="views-row' in html


# --- results ----------------------------------------------------------


def test_results_are_parsed_from_the_live_page(observations):
    results = [o for o in observations if o.track == StatusTrack.Track.RESULT]

    assert results, "expected at least one result notice on the captured page"
    assert all(o.value == "declared" for o in results)
    assert any("Combined Defence Services" in o.exam_name for o in results)


def test_a_final_result_names_the_exam_without_the_notice_type(observations):
    match = next(o for o in observations if "Central Armed Police Forces" in o.exam_name)

    # "Final Result: " must not survive into the name the matcher sees.
    assert match.exam_name == "Central Armed Police Forces (ACs) Examination, 2025"
    assert match.track == StatusTrack.Track.RESULT
    assert match.value == "declared"


def test_written_result_variants_do_not_produce_duplicate_claims(observations):
    """The page carries both "Written Result" and "Written Result (with
    name)" for the same exam; that is one fact, not two."""
    ies = [o for o in observations if o.exam_name.startswith("Indian Economic Service")]

    assert len(ies) == 1


# --- notifications / scheduling ---------------------------------------


def test_scheduling_notices_are_parsed_as_conduct_observations(observations):
    conduct = [o for o in observations if o.track == StatusTrack.Track.CONDUCT]

    assert conduct, "expected at least one timetable or interview notice"
    assert all(o.value == "scheduled" for o in conduct)


def test_an_examination_time_table_schedules_the_exam(observations):
    match = next(o for o in observations if "Civil Services (Main)" in o.exam_name)

    assert match.track == StatusTrack.Track.CONDUCT
    assert match.value == "scheduled"


# --- what is deliberately ignored -------------------------------------


def test_vague_notices_are_skipped_rather_than_guessed_at(observations):
    """"Notice: Engineering Services (Main) Examination, 2026" says
    nothing about status. Emitting a status from it would put invented
    machine data into the system."""
    engineering = [o for o in observations if "Engineering Services" in o.exam_name]

    # It appears only via its result notice, never via the bare "Notice".
    assert all(o.track == StatusTrack.Track.RESULT for o in engineering)


def test_recruitment_advertisements_are_not_treated_as_exams(observations):
    """Entries like "02 Posts of Assistant Director..." have no Exam to
    match, so they would sit in the triage queue forever."""
    assert not any(
        o.exam_name.lower().startswith(("01 post", "02 posts", "03 posts", "19 posts"))
        for o in observations
    )
    assert not any(" Posts of " in o.exam_name for o in observations)


# --- observation quality ----------------------------------------------


def test_every_observation_is_usable_downstream(observations):
    assert observations
    for o in observations:
        assert o.exam_name.strip()
        assert o.track in StatusTrack.Track.values
        assert 0.0 <= o.confidence <= 1.0
        # A verifier has to be able to go and look at the claim.
        assert o.source_url.startswith("https://www.upsc.gov.in/")
        assert o.raw_text.strip()


def test_stage_wording_in_the_exam_name_becomes_a_stage_hint(observations):
    mains = next(o for o in observations if "(Main)" in o.exam_name)
    assert mains.stage_hint == "mains"


def test_results_are_more_confident_than_scheduling_notices(observations):
    """A "Final Result" states a fact; a timetable states an intention."""
    result = next(o for o in observations if o.track == StatusTrack.Track.RESULT)
    conduct = next(o for o in observations if o.track == StatusTrack.Track.CONDUCT)

    assert result.confidence > conduct.confidence


# --- registry integration ---------------------------------------------


def test_the_parser_is_reachable_through_the_registry():
    """EXT-043's autodiscovery should have picked this up with no central
    list to update."""
    assert isinstance(get_parser("upsc_whats_new"), UpscWhatsNewParser)


# --- robustness -------------------------------------------------------


def test_an_unrelated_page_yields_nothing_rather_than_raising():
    """A board redesign or an error page must not crash a scrape run."""
    assert UpscWhatsNewParser().parse("<html><body><p>Down for maintenance</p></body></html>") == []


def test_empty_html_yields_nothing():
    assert UpscWhatsNewParser().parse("") == []


def test_a_row_with_a_malformed_link_is_skipped_not_fatal():
    html = """
    <div class="views-row">
      <span class="views-field views-field-field-exam-name">
        <span class="field-content"><a href="/not-whats-new/whatever">x</a></span>
      </span>
    </div>
    """
    assert UpscWhatsNewParser().parse(html) == []
