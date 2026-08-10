"""EXT-050: matching an Observation to the ExamStage it means.

The end-to-end tests at the bottom run the committed real-page captures
through their parsers and match the results, so the scoring is exercised
against the wording the boards actually publish rather than against
strings chosen to make it pass.
"""

from datetime import timedelta
from pathlib import Path

import pytest
from django.utils import timezone

from exams.models import Board, Exam, ExamStage, StatusTrack
from scraping.board_parsers.ibps import IbpsCrpUpdatesParser
from scraping.board_parsers.upsc import UpscWhatsNewParser
from scraping.matching import (
    Decision,
    TriageReason,
    link_observation,
    match_observation,
    score_candidates,
)
from scraping.parsers import Observation

pytestmark = pytest.mark.django_db

FIXTURES = Path(__file__).parent / "fixtures"
S = ExamStage.StageType


@pytest.fixture
def upsc():
    return Board.objects.create(
        name="Union Public Service Commission", code="UPSC", official_url="https://www.upsc.gov.in/"
    )


def make_exam(board, code, name, year, stages=(S.SINGLE,)):
    exam = Exam.objects.create(
        board=board, code=code, name=name, cycle_year=year, category="civil-services"
    )
    for sequence, stage_type in enumerate(stages, start=1):
        ExamStage.objects.create(exam=exam, stage_type=stage_type, sequence=sequence)
    return exam


@pytest.fixture
def cse(upsc):
    return make_exam(
        upsc, "CSE", "Civil Services Examination", 2026, (S.PRELIMS, S.MAINS, S.INTERVIEW)
    )


def observation(**kwargs):
    defaults = {
        "exam_name": "Civil Services (Main) Examination, 2026",
        "track": StatusTrack.Track.RESULT,
        "value": "declared",
        "stage_hint": "mains",
        "confidence": 0.9,
    }
    return Observation(**(defaults | kwargs))


# --- a confidence score per match --------------------------------------


def test_every_match_carries_a_confidence_score(cse, upsc):
    result = match_observation(observation(), board=upsc)

    assert 0.0 <= result.confidence <= 1.0
    assert result.confidence > 0


def test_a_triaged_observation_still_reports_its_best_score(cse, upsc):
    """A verifier needs to know whether this was a near miss or nothing
    like a match; "unmatched" alone does not say."""
    result = match_observation(observation(exam_name="Something Else Entirely, 2026"), board=upsc)

    assert result.decision is Decision.TRIAGE
    assert result.confidence < 0.85


def test_candidates_carry_the_components_behind_the_score(cse, upsc):
    """A bare number is something a verifier has no way to argue with."""
    candidates = score_candidates(observation(), board=upsc)

    assert candidates
    assert set(candidates[0].components) >= {"name", "code", "exact_code", "year"}


# --- above threshold auto-links ----------------------------------------


def test_a_confident_match_auto_links_to_the_right_stage(cse, upsc):
    result = match_observation(observation(), board=upsc)

    assert result.decision is Decision.AUTO_LINK
    assert result.exam_stage.exam == cse
    assert result.exam_stage.stage_type == S.MAINS


def test_an_exam_code_published_verbatim_is_a_strong_match(upsc):
    exam = make_exam(upsc, "CRP-CSA-XV", "CRP Customer Service Associates XV", 2026)

    result = match_observation(
        observation(exam_name="CRP-CSA-XV", stage_hint=""), board=upsc
    )

    assert result.decision is Decision.AUTO_LINK
    assert result.exam_stage.exam == exam


# --- below threshold goes to triage ------------------------------------


def test_a_weak_match_goes_to_triage_rather_than_linking(upsc):
    """Single-stage on purpose, so the stage resolves and what is being
    tested is the score alone rather than a missing stage."""
    make_exam(upsc, "CGS", "Combined Geo-Scientist Examination", 2026, (S.SINGLE,))

    result = match_observation(
        observation(exam_name="Indian Forest Service Examination, 2026", stage_hint=""),
        board=upsc,
    )

    assert result.decision is Decision.TRIAGE
    assert result.reason is TriageReason.BELOW_THRESHOLD
    assert result.exam_stage is None
    assert 0 < result.confidence < 0.85, "a near miss should still report its score"


def test_nothing_matching_at_all_is_distinguished_from_a_near_miss(upsc):
    result = match_observation(observation(), board=upsc)

    assert result.decision is Decision.TRIAGE
    assert result.reason is TriageReason.NO_CANDIDATES


def test_the_threshold_is_configurable(cse, upsc, settings):
    weak = observation(exam_name="Civil Services Examination", stage_hint="mains")

    settings.MATCH_AUTO_LINK_THRESHOLD = 0.99
    assert match_observation(weak, board=upsc).decision is Decision.TRIAGE

    settings.MATCH_AUTO_LINK_THRESHOLD = 0.5
    assert match_observation(weak, board=upsc).decision is Decision.AUTO_LINK


# --- refusing to guess -------------------------------------------------


def test_two_near_identical_candidates_go_to_triage_even_when_both_score_perfectly(upsc):
    """The coin-toss case. Both score 1.0, and picking either is a guess
    that writes into machine status data."""
    make_exam(upsc, "CGL-A", "Combined Graduate Level Examination", 2026)
    make_exam(upsc, "CGL-B", "Combined Graduate Level Examination", 2026)

    result = match_observation(
        observation(exam_name="Combined Graduate Level Examination, 2026", stage_hint=""),
        board=upsc,
    )

    assert result.decision is Decision.TRIAGE
    assert result.reason is TriageReason.AMBIGUOUS
    assert result.confidence == pytest.approx(1.0)
    assert len(result.candidates) == 2, "triage should be offered both to choose between"


def test_a_consecutive_cycle_is_not_matched_by_substring(upsc):
    """IBPS runs CRP-CSA-XV and CRP-CSA-XVI, and "crp-csa-xv" *is* a
    substring of "crp-csa-xvi". A plain containment test scored last
    cycle's exam a perfect 1.0 for an observation about this one - the
    worst failure a matcher has, because it is confident and wrong."""
    make_exam(upsc, "CRP-CSA-XV", "CRP Customer Service Associates XV", 2026)
    xvi = make_exam(upsc, "CRP-CSA-XVI", "CRP Customer Service Associates XVI", 2026)

    result = match_observation(observation(exam_name="CRP-CSA-XVI", stage_hint=""), board=upsc)

    assert result.decision is Decision.AUTO_LINK
    assert result.exam_stage.exam == xvi


def test_a_different_cycle_year_disqualifies_rather_than_scoring_low(upsc):
    """An observation naming 2025 is not a weak match for the 2026 cycle -
    it is a statement about a different exam."""
    make_exam(upsc, "CSE", "Civil Services Examination", 2026)

    result = match_observation(
        observation(exam_name="Civil Services Examination, 2025", stage_hint=""), board=upsc
    )

    assert result.decision is Decision.TRIAGE
    assert result.reason is TriageReason.NO_CANDIDATES


def test_an_unconfirmed_cycle_scores_below_a_confirmed_one(upsc):
    """A notice omitting the year still matches, but with one fewer thing
    agreeing - an unconfirmed cycle is how an observation lands on last
    year's exam."""
    make_exam(upsc, "CSE", "Civil Services Examination", 2026)

    with_year = match_observation(
        observation(exam_name="Civil Services Examination, 2026", stage_hint=""), board=upsc
    )
    without_year = match_observation(
        observation(exam_name="Civil Services Examination", stage_hint=""), board=upsc
    )

    assert without_year.confidence < with_year.confidence


# --- board scoping -----------------------------------------------------


def test_matching_is_scoped_to_the_board_the_observation_came_from(upsc):
    """Two boards can run exams with near-identical names; the source
    always knows which board it scraped."""
    ssc = Board.objects.create(name="Staff Selection Commission", code="SSC", official_url="https://s")
    make_exam(upsc, "CGL", "Combined Graduate Level Examination", 2026)
    ssc_exam = make_exam(ssc, "CGL", "Combined Graduate Level Examination", 2026)

    result = match_observation(
        observation(exam_name="Combined Graduate Level Examination, 2026", stage_hint=""),
        board=ssc,
    )

    assert result.exam_stage.exam == ssc_exam


def test_without_a_board_two_boards_sharing_a_name_are_ambiguous(upsc):
    ssc = Board.objects.create(name="Staff Selection Commission", code="SSC", official_url="https://s")
    make_exam(upsc, "CGL", "Combined Graduate Level Examination", 2026)
    make_exam(ssc, "CGL", "Combined Graduate Level Examination", 2026)

    result = match_observation(
        observation(exam_name="Combined Graduate Level Examination, 2026", stage_hint="")
    )

    assert result.decision is Decision.TRIAGE
    assert result.reason is TriageReason.AMBIGUOUS


# --- choosing the stage ------------------------------------------------


def test_a_stage_hint_selects_the_stage(cse, upsc):
    result = match_observation(observation(stage_hint="prelims"), board=upsc)

    assert result.exam_stage.stage_type == S.PRELIMS


def test_a_single_stage_exam_needs_no_hint(upsc):
    make_exam(upsc, "CDS", "Combined Defence Services Examination", 2026, (S.SINGLE,))

    result = match_observation(
        observation(exam_name="Combined Defence Services Examination, 2026", stage_hint=""),
        board=upsc,
    )

    assert result.decision is Decision.AUTO_LINK
    assert result.exam_stage.stage_type == S.SINGLE


def test_a_multi_stage_exam_with_no_hint_goes_to_triage(cse, upsc):
    """"Final Result: Civil Services Examination" does not say whether it
    means prelims, mains or interview. Choosing would invent the answer."""
    result = match_observation(
        observation(exam_name="Civil Services Examination, 2026", stage_hint=""), board=upsc
    )

    assert result.decision is Decision.TRIAGE
    assert result.reason is TriageReason.NO_STAGE


def test_a_stage_the_exam_does_not_have_is_refused(upsc):
    """Falling back to "the only other stage" would be a guess."""
    make_exam(upsc, "CDS", "Combined Defence Services Examination", 2026, (S.SINGLE,))

    result = match_observation(
        observation(exam_name="Combined Defence Services Examination, 2026", stage_hint="prelims"),
        board=upsc,
    )

    assert result.decision is Decision.TRIAGE


# --- acting on the decision --------------------------------------------


def test_a_confident_match_writes_the_machine_value(cse, upsc):
    result, applied = link_observation(observation(), board=upsc)

    assert result.decision is Decision.AUTO_LINK
    assert applied is not None and applied.written

    track = StatusTrack.objects.get(exam_stage=result.exam_stage, track=StatusTrack.Track.RESULT)
    assert track.machine_value == "declared"


def test_the_recorded_confidence_is_the_parser_s_not_the_match_score(cse, upsc):
    """They answer different questions: the parser's confidence is how
    sure it is the text *says* this, the match score is how sure we are
    which stage it is about. machine_confidence has always meant the
    former."""
    result, _ = link_observation(observation(confidence=0.8), board=upsc)

    track = StatusTrack.objects.get(exam_stage=result.exam_stage, track=StatusTrack.Track.RESULT)
    assert track.machine_confidence == pytest.approx(0.8)
    assert result.confidence != pytest.approx(0.8)


def test_a_triaged_observation_writes_nothing(cse, upsc):
    result, applied = link_observation(
        observation(exam_name="Nothing Like This Exam, 2026"), board=upsc
    )

    assert result.decision is Decision.TRIAGE
    assert applied is None
    assert not StatusTrack.objects.exclude(machine_value="").exists()


def test_a_confident_match_still_cannot_overwrite_a_fresh_verification(cse, upsc):
    """The matcher does not get to bypass CLAUDE.md's core rule by being
    sure. It writes through apply_machine_observation, which records a
    conflict instead."""
    stage = cse.stages.get(stage_type=S.MAINS)
    StatusTrack.objects.create(
        exam_stage=stage,
        track=StatusTrack.Track.RESULT,
        human_value="not-declared",
        verified_by="verifier@example.gov.in",
        verified_at=timezone.now() - timedelta(days=1),
    )

    result, applied = link_observation(observation(), board=upsc)

    assert result.decision is Decision.AUTO_LINK
    assert applied is not None
    assert not applied.written, "a fresh human value must survive a confident match"
    assert applied.conflict is not None

    track = StatusTrack.objects.get(exam_stage=stage, track=StatusTrack.Track.RESULT)
    assert track.machine_value == ""
    assert track.human_value == "not-declared"


# --- against the real captured pages ------------------------------------


def test_real_upsc_observations_match_the_exams_an_operator_would_enter(upsc):
    """Exam names as an operator realistically enters them - copied from
    the board's own wording - matched against what the parser really
    pulls off the captured page."""
    make_exam(upsc, "CSE", "Civil Services Examination", 2026, (S.PRELIMS, S.MAINS, S.INTERVIEW))
    make_exam(upsc, "CAPF", "Central Armed Police Forces (ACs) Examination", 2025)
    make_exam(upsc, "ESE", "Engineering Services Examination", 2026, (S.PRELIMS, S.MAINS))

    html = (FIXTURES / "upsc_whats_new.html").read_text(encoding="utf-8", errors="replace")
    results = {
        o.exam_name: match_observation(o, board=upsc) for o in UpscWhatsNewParser().parse(html)
    }

    linked = {name: r for name, r in results.items() if r.matched}
    assert len(linked) >= 3, f"expected several confident matches, got {len(linked)}"

    civil = next(r for name, r in results.items() if name.startswith("Civil Services"))
    assert civil.matched
    assert civil.exam_stage.stage_type == S.MAINS

    # An exam nobody has entered yet must not be forced onto a neighbour.
    cisf = next(r for name, r in results.items() if name.startswith("CISF"))
    assert not cisf.matched


def test_real_ibps_observations_match_by_exam_code(upsc):
    ibps = Board.objects.create(
        name="Institute of Banking Personnel Selection", code="IBPS", official_url="https://i"
    )
    make_exam(ibps, "CRP-CSA-XV", "CRP Customer Service Associates XV", 2026, (S.PRELIMS, S.MAINS))

    html = (FIXTURES / "ibps_crp_updates.html").read_text(encoding="utf-8", errors="replace")
    results = [match_observation(o, board=ibps) for o in IbpsCrpUpdatesParser().parse(html)]

    linked = [r for r in results if r.matched]
    assert len(linked) == 1, "only the seeded cycle should link"
    assert linked[0].exam_stage.stage_type == S.MAINS
    # The other cycles have no exam row, so they wait for a human.
    assert all(r.reason is not None for r in results if not r.matched)
