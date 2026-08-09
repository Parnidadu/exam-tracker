"""EXT-045: the IBPS parser, and proof that the interface generalises.

The fixture is a genuine capture of
https://www.ibps.in/index.php/crp-updates/ - a structurally different
site from UPSC's, which is the point of a second board.
"""

from datetime import date
from pathlib import Path

import pytest

from exams.models import StatusTrack
from scraping.board_parsers.ibps import IbpsCrpUpdatesParser
from scraping.board_parsers.upsc import UpscWhatsNewParser
from scraping.parsers import Observation, Parser, get_parser

FIXTURES = Path(__file__).parent / "fixtures"
IBPS_FIXTURE = FIXTURES / "ibps_crp_updates.html"
UPSC_FIXTURE = FIXTURES / "upsc_whats_new.html"


@pytest.fixture(scope="module")
def html() -> str:
    return IBPS_FIXTURE.read_text(encoding="utf-8", errors="replace")


@pytest.fixture
def observations(html) -> list[Observation]:
    return IbpsCrpUpdatesParser().parse(html)


# --- the fixture ------------------------------------------------------


def test_the_fixture_is_a_real_capture(html):
    assert len(html) > 50_000
    assert "ibps.in" in html
    assert "detail-second-heading" in html


# --- results ----------------------------------------------------------


def test_a_result_notice_is_parsed(observations):
    result = next(o for o in observations if o.track == StatusTrack.Track.RESULT)

    assert result.value == "declared"
    assert result.exam_name == "CRP-CSA-XV"
    assert result.stage_hint == "mains"


def test_the_entry_date_is_captured(observations):
    """UPSC's board carries no dates, so this field goes untested until a
    board that publishes them arrives - which is part of what a second
    parser is for."""
    result = next(o for o in observations if o.track == StatusTrack.Track.RESULT)

    assert result.observed_date == date(2026, 7, 17)
    assert all(o.observed_date is not None for o in observations)


# --- notifications ----------------------------------------------------


def test_a_recruitment_notification_schedules_the_cycle(observations):
    scheduled = [o for o in observations if o.track == StatusTrack.Track.CONDUCT]

    assert scheduled
    assert all(o.value == "scheduled" for o in scheduled)
    assert any(o.exam_name == "CRP-CSA-XVI" for o in scheduled)


# --- name normalisation -----------------------------------------------


def test_inconsistent_spacing_in_the_exam_code_is_normalised(observations):
    """The live page writes the same cycle as "CRP-CSA-XVI", "CRP- CSA-XV"
    and "CRP-PO/MTs -XVI". The matcher should not have to cope with that."""
    names = {o.exam_name for o in observations}

    assert "CRP-CSA-XV" in names
    assert "CRP-PO/MTs-XVI" in names
    assert not any("  " in name or name.endswith("-") for name in names)


# --- what is deliberately ignored -------------------------------------


@pytest.mark.parametrize(
    "phrase",
    ["Window Notification", "Apply Online", "Updated Vacancies", "Corrigendum", "Notice dated"],
)
def test_wording_that_states_no_status_is_skipped(observations, phrase):
    """Same rule as the UPSC parser: never invent a status from a headline
    that does not state one."""
    assert not any(o.raw_text.lower().startswith(phrase.lower()) for o in observations)


def test_housekeeping_notices_without_an_exam_are_ignored(observations):
    """ISO certification, trademark cautions and equipment tenders name no
    exam, so there is nothing to attach an observation to."""
    for noise in ("ISO 9001", "Trade Mark", "Workstation", "Fraudulent Websites"):
        assert not any(noise.lower() in o.raw_text.lower() for o in observations)


# --- registry ---------------------------------------------------------


def test_the_parser_is_reachable_through_the_registry():
    assert isinstance(get_parser("ibps_crp_updates"), IbpsCrpUpdatesParser)


# --- robustness -------------------------------------------------------


def test_an_unrelated_page_yields_nothing_rather_than_raising():
    assert IbpsCrpUpdatesParser().parse("<html><body><p>Maintenance</p></body></html>") == []


def test_empty_html_yields_nothing():
    assert IbpsCrpUpdatesParser().parse("") == []


def test_an_entry_with_an_unparseable_date_still_yields_the_observation():
    """A malformed date should cost the date, not the whole observation."""
    html = """
    <a href="https://www.ibps.in/x">
      <div class="detail-list">
        <div class="detail-first-heading">not a date</div>
        <div class="detail-second-heading">Result of Online Main Examination for CRP-CSA-XV</div>
      </div>
    </a>
    """
    observations = IbpsCrpUpdatesParser().parse(html)

    assert len(observations) == 1
    assert observations[0].observed_date is None
    assert observations[0].value == "declared"


# --- the point of the ticket: the interface generalises ---------------

BOARDS = [
    (UpscWhatsNewParser, UPSC_FIXTURE),
    (IbpsCrpUpdatesParser, IBPS_FIXTURE),
]


@pytest.mark.parametrize("parser_cls,fixture", BOARDS, ids=["upsc", "ibps"])
def test_both_boards_satisfy_the_same_parser_contract(parser_cls, fixture):
    """Two structurally unrelated sites - Drupal views keyed by href, and
    WordPress prose with inline exam codes - drive the same interface with
    no board-specific handling outside their own modules."""
    parser = parser_cls()

    assert isinstance(parser, Parser)
    assert parser.key

    observations = parser.parse(fixture.read_text(encoding="utf-8", errors="replace"))

    assert observations, f"{parser.key} found nothing in its own fixture"
    for o in observations:
        assert isinstance(o, Observation)
        assert o.exam_name.strip()
        assert o.track in StatusTrack.Track.values
        assert 0.0 <= o.confidence <= 1.0
        assert o.source_url.startswith("https://")
        assert o.raw_text.strip()


@pytest.mark.parametrize("parser_cls", [p for p, _ in BOARDS], ids=["upsc", "ibps"])
def test_every_board_handles_an_unrelated_page_the_same_way(parser_cls):
    """Whatever the site, a redesign or an error page must degrade to "no
    observations" rather than take a scrape run down."""
    assert parser_cls().parse("<html><body>nothing here</body></html>") == []


def test_the_two_boards_are_genuinely_different_implementations():
    """If the second parser were a copy of the first, this ticket would
    prove nothing about the interface."""
    upsc = UpscWhatsNewParser()
    ibps = IbpsCrpUpdatesParser()

    assert upsc.key != ibps.key
    assert upsc.base_url != ibps.base_url
    # Each finds its own board's entries and nothing in the other's.
    assert ibps.parse(UPSC_FIXTURE.read_text(encoding="utf-8", errors="replace")) == []
    assert upsc.parse(IBPS_FIXTURE.read_text(encoding="utf-8", errors="replace")) == []
