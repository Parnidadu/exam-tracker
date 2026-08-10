"""EXT-051: the triage queue and the three ways out of it."""

from datetime import timedelta

import pytest
from django.utils import timezone

from exams.models import Board, Exam, ExamStage, StatusTrack
from scraping.matching import Decision, MatchResult, TriageReason, match_observation
from scraping.models import Source, TriageItem
from scraping.parsers import Observation
from scraping.triage import (
    AlreadyResolved,
    TriageError,
    create_exam_and_link,
    dismiss,
    fingerprint,
    link_to_stage,
    pending,
    queue_observation,
    suggested_stages,
)

pytestmark = pytest.mark.django_db
S = ExamStage.StageType


@pytest.fixture
def board():
    return Board.objects.create(
        name="Union Public Service Commission", code="UPSC", official_url="https://www.upsc.gov.in/"
    )


@pytest.fixture
def source(board):
    return Source.objects.create(
        board=board,
        name="UPSC what's new",
        url="https://www.upsc.gov.in/",
        parser_key="upsc_whats_new",
    )


def observation(**kwargs):
    defaults = {
        "exam_name": "Civil Services Examination, 2026",
        "track": StatusTrack.Track.RESULT,
        "value": "declared",
        "confidence": 0.9,
        "source_url": "https://www.upsc.gov.in/whats-new/x",
        "raw_text": "Final Result: Civil Services Examination, 2026",
    }
    return Observation(**(defaults | kwargs))


#: Real wording from the captured UPSC page that the matcher cannot
#: resolve. Using it keeps the "operator knows what the matcher did not"
#: scenario honest instead of engineering a fake miss.
UNMATCHABLE = "CISF AC(EXE) LDCE-2026"


def queue(source, **kwargs) -> TriageItem:
    obs = observation(**kwargs)
    result = match_observation(obs, board=source.board)
    return queue_observation(obs, result, source=source)


# --- getting into the queue --------------------------------------------


def test_an_unmatched_observation_lands_in_the_queue(source):
    item = queue(source)

    assert item.status == TriageItem.Status.PENDING
    assert item.reason == TriageItem.Reason.NO_CANDIDATES
    assert list(pending()) == [item]


def test_the_queue_keeps_what_was_observed(source):
    """By the time an operator opens the queue the parse is long gone. An
    item that cannot show what it saw is not reviewable."""
    item = queue(source, observed_date=None)

    assert item.exam_name == "Civil Services Examination, 2026"
    assert item.track == StatusTrack.Track.RESULT
    assert item.value == "declared"
    assert item.source_url
    assert item.raw_text
    assert item.parser_confidence == pytest.approx(0.9)


def test_a_matched_observation_is_not_queued(source):
    exam = Exam.objects.create(
        board=source.board, code="CSE", name="Civil Services Examination",
        cycle_year=2026, category="x",
    )
    ExamStage.objects.create(exam=exam, stage_type=S.SINGLE, sequence=1)
    obs = observation()
    result = match_observation(obs, board=source.board)
    assert result.decision is Decision.AUTO_LINK

    with pytest.raises(TriageError):
        queue_observation(obs, result, source=source)


def test_the_same_unmatched_observation_recurring_bumps_a_counter(source):
    """The same unresolved notice arrives on every poll. Without this a
    board nobody has configured buries the queue in copies of one problem
    within a day."""
    for _ in range(4):
        queue(source)

    assert TriageItem.objects.count() == 1
    assert TriageItem.objects.get().times_seen == 4


def test_reformatted_text_is_still_the_same_problem(source):
    """The fingerprint is built from what the observation claims, not the
    snippet it was read out of."""
    queue(source, raw_text="Final Result: Civil Services Examination, 2026")
    queue(source, raw_text="FINAL RESULT - Civil Services Examination, 2026 (with name)")

    assert TriageItem.objects.count() == 1


def test_different_observations_are_different_items(source):
    queue(source)
    queue(source, exam_name="Engineering Services Examination, 2026")

    assert TriageItem.objects.count() == 2


def test_the_fingerprint_separates_two_boards_saying_the_same_thing(board):
    other = Board.objects.create(name="Staff Selection Commission", code="SSC", official_url="https://s")
    a = Source.objects.create(board=board, name="a", url="https://a.gov.in", parser_key="x")
    b = Source.objects.create(board=other, name="b", url="https://b.gov.in", parser_key="y")

    assert fingerprint(a, observation()) != fingerprint(b, observation())


def test_a_resolved_item_is_not_reopened_by_the_next_scrape(source):
    """A dismissed notice reappears on every poll. Reopening it each time
    would make "dismiss" mean "hide until tomorrow"."""
    item = queue(source)
    dismiss(item, actor="ops@example.gov.in", note="not a tracked exam")

    queue(source)

    item.refresh_from_db()
    assert item.status == TriageItem.Status.DISMISSED
    assert item.times_seen == 2, "recurrence is still counted"


def test_the_queue_puts_the_most_persistent_problems_first(source):
    once = queue(source, exam_name="Seen Once Examination, 2026")
    for _ in range(5):
        often = queue(source, exam_name="Seen Often Examination, 2026")

    assert list(pending()) == [often, once]


# --- action 1: link to an existing stage --------------------------------


@pytest.fixture
def existing_stage(board):
    exam = Exam.objects.create(
        board=board, code="CSE", name="Civil Services Examination", cycle_year=2026, category="x"
    )
    return ExamStage.objects.create(exam=exam, stage_type=S.MAINS, sequence=2)


def test_an_operator_can_link_an_observation_to_a_stage(source, existing_stage):
    item = queue(source, exam_name=UNMATCHABLE)

    resolved, applied = link_to_stage(item, existing_stage, actor="ops@example.gov.in")

    assert resolved.status == TriageItem.Status.LINKED
    assert resolved.exam_stage == existing_stage
    assert resolved.resolved_by == "ops@example.gov.in"
    assert resolved.resolved_at is not None
    assert applied.written


def test_linking_writes_the_observed_value_onto_the_stage(source, existing_stage):
    item = queue(source, exam_name=UNMATCHABLE)

    link_to_stage(item, existing_stage, actor="ops@example.gov.in")

    track = StatusTrack.objects.get(exam_stage=existing_stage, track=StatusTrack.Track.RESULT)
    assert track.machine_value == "declared"
    assert track.machine_confidence == pytest.approx(0.9)


def test_linking_cannot_overwrite_a_fresh_human_verification(source, existing_stage):
    """An operator deciding *which stage* this is about is not an operator
    deciding to overwrite a verification someone made this week."""
    StatusTrack.objects.create(
        exam_stage=existing_stage,
        track=StatusTrack.Track.RESULT,
        human_value="not-declared",
        verified_by="verifier@example.gov.in",
        verified_at=timezone.now() - timedelta(days=1),
    )
    item = queue(source, exam_name=UNMATCHABLE)

    resolved, applied = link_to_stage(item, existing_stage, actor="ops@example.gov.in")

    assert resolved.status == TriageItem.Status.LINKED
    assert not applied.written
    assert applied.conflict is not None
    track = StatusTrack.objects.get(exam_stage=existing_stage, track=StatusTrack.Track.RESULT)
    assert track.human_value == "not-declared"
    assert track.machine_value == ""


# --- action 2: create a new exam ----------------------------------------


def test_an_operator_can_create_a_new_exam_from_an_observation(source, board):
    item = queue(source)

    resolved, stage, applied = create_exam_and_link(
        item,
        board=board,
        code="CSE",
        name="Civil Services Examination",
        cycle_year=2026,
        stage_type=S.SINGLE,
        actor="ops@example.gov.in",
    )

    assert resolved.status == TriageItem.Status.CREATED
    exam = Exam.objects.get(board=board, code="CSE", cycle_year=2026)
    assert stage.exam == exam
    assert stage.stage_type == S.SINGLE
    assert applied.written


def test_creating_an_exam_links_the_observation_to_it(source, board):
    item = queue(source)

    resolved, stage, _ = create_exam_and_link(
        item, board=board, code="CSE", name="Civil Services Examination",
        cycle_year=2026, actor="ops@example.gov.in",
    )

    assert resolved.exam_stage == stage
    track = StatusTrack.objects.get(exam_stage=stage, track=StatusTrack.Track.RESULT)
    assert track.machine_value == "declared"


def test_creating_only_makes_the_one_stage_asked_for(source, board):
    """Which stages an exam has is a domain fact the operator knows and
    this code does not. Inventing prelims/mains/interview would put steps
    on the public timeline that never happen."""
    item = queue(source)

    _, stage, _ = create_exam_and_link(
        item, board=board, code="CSE", name="Civil Services Examination",
        cycle_year=2026, stage_type=S.PRELIMS, actor="ops@example.gov.in",
    )

    assert list(stage.exam.stages.values_list("stage_type", flat=True)) == [S.PRELIMS]


def test_creating_reuses_an_exam_that_already_exists(source, board, existing_stage):
    """Two operators resolving two notices about the same new cycle must
    not produce two exams."""
    item = queue(source, exam_name=UNMATCHABLE)

    _, stage, _ = create_exam_and_link(
        item, board=board, code="CSE", name="Civil Services Examination",
        cycle_year=2026, stage_type=S.MAINS, actor="ops@example.gov.in",
    )

    assert Exam.objects.filter(board=board, code="CSE", cycle_year=2026).count() == 1
    assert stage == existing_stage


# --- action 3: dismiss ---------------------------------------------------


def test_an_operator_can_dismiss_an_observation(source):
    item = queue(source)

    resolved = dismiss(item, actor="ops@example.gov.in", note="recruitment advert, not an exam")

    assert resolved.status == TriageItem.Status.DISMISSED
    assert resolved.resolution_note == "recruitment advert, not an exam"
    assert resolved.resolved_at is not None


def test_dismissing_writes_no_status_data(source):
    item = queue(source)

    dismiss(item, actor="ops@example.gov.in")

    assert not StatusTrack.objects.exists()


def test_a_dismissed_item_leaves_the_queue(source):
    item = queue(source)
    dismiss(item, actor="ops@example.gov.in")

    assert list(pending()) == []


# --- concurrency ---------------------------------------------------------


@pytest.mark.parametrize(
    "action",
    [
        lambda item, stage: dismiss(item, actor="second@example.gov.in"),
        lambda item, stage: link_to_stage(item, stage, actor="second@example.gov.in"),
    ],
)
def test_two_operators_cannot_resolve_the_same_item_twice(source, existing_stage, action):
    """Without this the second operator's action silently overwrites the
    first, and the observation is applied to a stage twice."""
    item = queue(source, exam_name=UNMATCHABLE)
    dismiss(item, actor="first@example.gov.in")

    with pytest.raises(AlreadyResolved):
        action(item, existing_stage)


# --- what the operator is offered ---------------------------------------


def test_the_matcher_s_shortlist_is_kept_for_the_operator(source, board):
    """A near-miss should hand over the candidates it was choosing
    between, rather than making the operator search from scratch."""
    for code in ("CGL-A", "CGL-B"):
        exam = Exam.objects.create(
            board=board, code=code, name="Combined Graduate Level Examination",
            cycle_year=2026, category="x",
        )
        ExamStage.objects.create(exam=exam, stage_type=S.SINGLE, sequence=1)

    obs = observation(exam_name="Combined Graduate Level Examination, 2026")
    result = match_observation(obs, board=board)
    assert result.reason is TriageReason.AMBIGUOUS

    item = queue_observation(obs, result, source=source)

    assert len(item.candidates) == 2
    assert {entry["exam_stage_id"] for entry in item.candidates}
    assert len(suggested_stages(item)) == 2


def test_a_stage_deleted_since_is_simply_absent_from_the_shortlist(source, board):
    for code in ("CGL-A", "CGL-B"):
        exam = Exam.objects.create(
            board=board, code=code, name="Combined Graduate Level Examination",
            cycle_year=2026, category="x",
        )
        ExamStage.objects.create(exam=exam, stage_type=S.SINGLE, sequence=1)

    obs = observation(exam_name="Combined Graduate Level Examination, 2026")
    item = queue_observation(obs, match_observation(obs, board=board), source=source)
    assert len(item.candidates) == 2

    ExamStage.objects.all().delete()

    assert suggested_stages(item) == []


def test_the_stored_reason_survives_the_exam_list_changing(source, board):
    """An item should still say why it landed here when it did, even after
    someone adds the exam it was looking for."""
    item = queue(source)
    assert item.reason == TriageItem.Reason.NO_CANDIDATES

    exam = Exam.objects.create(
        board=board, code="CSE", name="Civil Services Examination", cycle_year=2026, category="x"
    )
    ExamStage.objects.create(exam=exam, stage_type=S.SINGLE, sequence=1)

    item.refresh_from_db()
    assert item.reason == TriageItem.Reason.NO_CANDIDATES


def test_only_a_triage_decision_can_be_queued(source):
    result = MatchResult(
        observation=observation(), decision=Decision.AUTO_LINK, confidence=1.0
    )

    with pytest.raises(TriageError):
        queue_observation(observation(), result, source=source)
