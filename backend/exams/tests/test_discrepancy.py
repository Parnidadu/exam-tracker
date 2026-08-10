"""EXT-053: the Discrepancy model - type, severity, evidence, lifecycle."""

from datetime import date

import pytest
from django.core.exceptions import ValidationError

from accounts.models import Role, User
from exams.models import (
    Board,
    Discrepancy,
    Exam,
    ExamStage,
    InvalidDiscrepancyTransition,
)

pytestmark = pytest.mark.django_db
T = Discrepancy.Type
S = Discrepancy.Status
Sev = Discrepancy.Severity


@pytest.fixture
def stage():
    board = Board.objects.create(
        name="Union Public Service Commission", code="UPSC", official_url="https://u"
    )
    exam = Exam.objects.create(
        board=board, code="CSE", name="Civil Services Examination", cycle_year=2026, category="x"
    )
    return ExamStage.objects.create(exam=exam, stage_type=ExamStage.StageType.PRELIMS, sequence=1)


@pytest.fixture
def verifier():
    return User.objects.create_user(
        email="verifier@example.gov.in", password="x", role=Role.VERIFIER
    )


def make(stage, verifier, **kwargs):
    defaults = {
        "exam_stage": stage,
        "discrepancy_type": T.POSTPONEMENT,
        "severity": Sev.MEDIUM,
        "description": "Postponed by two weeks per the board's notice.",
        "reported_by": verifier,
    }
    return Discrepancy.objects.create(**(defaults | kwargs))


# --- the types the criterion names -------------------------------------


@pytest.mark.parametrize(
    "value",
    ["postponement", "cancellation", "paper_leak", "key_error", "re_exam", "court_stay"],
)
def test_every_named_type_exists(value):
    assert value in Discrepancy.Type.values


@pytest.mark.parametrize(
    "value,label",
    [
        (T.POSTPONEMENT, "Postponement"),
        (T.CANCELLATION, "Cancellation"),
        (T.PAPER_LEAK, "Paper leak"),
        (T.KEY_ERROR, "Answer key error"),
        (T.RE_EXAM, "Re-examination"),
        (T.COURT_STAY, "Court stay"),
    ],
)
def test_each_named_type_can_be_recorded_and_reads_back(stage, verifier, value, label):
    discrepancy = make(stage, verifier, discrepancy_type=value)

    discrepancy.refresh_from_db()
    assert discrepancy.discrepancy_type == value
    assert discrepancy.get_discrepancy_type_display() == label


def test_an_unnamed_kind_of_problem_has_somewhere_to_go(stage, verifier):
    """Without "other", anything the list does not name has to be filed
    under a type it is not - a centre change recorded as a postponement is
    worse than one recorded as "other", because the first is wrong where
    the second is only vague."""
    discrepancy = make(
        stage,
        verifier,
        discrepancy_type=T.OTHER,
        description="Exam centre relocated at 48 hours' notice.",
    )

    assert discrepancy.discrepancy_type == T.OTHER
    assert discrepancy.description


# --- severity ----------------------------------------------------------


@pytest.mark.parametrize("severity", ["low", "medium", "high", "critical"])
def test_severity_is_recorded_independently_of_type(stage, verifier, severity):
    """A two-day postponement and a cancelled national exam are the same
    type and nothing like the same problem."""
    discrepancy = make(stage, verifier, severity=severity)

    assert discrepancy.severity == severity


# --- evidence ----------------------------------------------------------


def test_evidence_can_be_a_link_and_a_note(stage, verifier):
    """A discrepancy without a source a reader can check is a rumour, and
    this app exists to be the opposite of that."""
    discrepancy = make(
        stage,
        verifier,
        evidence_url="https://www.upsc.gov.in/whats-new/notice",
        evidence_note="Paragraph 3 of the notice dated 15.01.2026.",
    )

    assert discrepancy.evidence_url.startswith("https://")
    assert discrepancy.evidence_note


def test_a_description_is_always_required(stage, verifier):
    """A type and a severity alone tell a candidate nothing about their
    exam."""
    discrepancy = Discrepancy(
        exam_stage=stage,
        discrepancy_type=T.PAPER_LEAK,
        severity=Sev.CRITICAL,
        reported_by=verifier,
        description="",
    )

    with pytest.raises(ValidationError) as excinfo:
        discrepancy.full_clean()

    assert "description" in excinfo.value.error_dict


def test_when_it_happened_is_kept_apart_from_when_it_was_recorded(stage, verifier):
    """A leak often surfaces long after the fact."""
    discrepancy = make(stage, verifier, occurred_on=date(2026, 1, 15))

    assert discrepancy.occurred_on == date(2026, 1, 15)
    assert discrepancy.reported_at.date() != discrepancy.occurred_on


def test_the_date_it_happened_may_genuinely_be_unknown(stage, verifier):
    assert make(stage, verifier).occurred_on is None


# --- lifecycle ---------------------------------------------------------


def test_a_new_discrepancy_starts_as_reported(stage, verifier):
    discrepancy = make(stage, verifier)

    assert discrepancy.status == S.REPORTED
    assert discrepancy.is_open
    assert discrepancy.resolved_at is None


@pytest.mark.parametrize(
    "start,allowed",
    [
        (S.REPORTED, {S.CONFIRMED, S.DISMISSED}),
        (S.CONFIRMED, {S.RESOLVED, S.DISMISSED}),
        (S.RESOLVED, set()),
        (S.DISMISSED, set()),
    ],
)
def test_the_transition_table_is_what_the_model_enforces(start, allowed):
    assert Discrepancy.TRANSITIONS[start] == allowed


def test_a_reported_discrepancy_can_be_confirmed(stage, verifier):
    discrepancy = make(stage, verifier)

    discrepancy.status = S.CONFIRMED
    discrepancy.save()

    discrepancy.refresh_from_db()
    assert discrepancy.status == S.CONFIRMED
    assert discrepancy.is_open


def test_a_confirmed_discrepancy_can_be_resolved(stage, verifier):
    discrepancy = make(stage, verifier)
    discrepancy.status = S.CONFIRMED
    discrepancy.save()

    discrepancy.status = S.RESOLVED
    discrepancy.resolution_note = "Re-conducted on 12 March 2026."
    discrepancy.resolved_by = verifier
    discrepancy.save()

    discrepancy.refresh_from_db()
    assert not discrepancy.is_open
    assert discrepancy.resolved_at is not None


def test_resolving_without_confirming_is_refused(stage, verifier):
    """It would close something nobody ever checked, and "resolved" would
    stop meaning that the claim was real."""
    discrepancy = make(stage, verifier)

    discrepancy.status = S.RESOLVED
    with pytest.raises(InvalidDiscrepancyTransition):
        discrepancy.save()


@pytest.mark.parametrize("terminal", [S.RESOLVED, S.DISMISSED])
def test_a_closed_discrepancy_cannot_be_reopened(stage, verifier, terminal):
    discrepancy = make(stage, verifier)
    discrepancy.status = S.CONFIRMED
    discrepancy.save()
    discrepancy.status = terminal
    discrepancy.resolution_note = "done"
    discrepancy.save()

    discrepancy.status = S.REPORTED
    with pytest.raises(InvalidDiscrepancyTransition):
        discrepancy.save()


def test_a_reported_claim_can_be_dismissed_outright(stage, verifier):
    """Not every report is real, and a false leak claim should not have to
    be confirmed on the way to being thrown out."""
    discrepancy = make(stage, verifier, discrepancy_type=T.PAPER_LEAK)

    discrepancy.status = S.DISMISSED
    discrepancy.resolution_note = "Screenshot was from a coaching mock, not the paper."
    discrepancy.save()

    discrepancy.refresh_from_db()
    assert discrepancy.status == S.DISMISSED
    assert not discrepancy.is_open


def test_closing_stamps_the_time_without_the_caller_remembering(stage, verifier):
    """Keeps "closed" and "has a closing time" from drifting apart."""
    discrepancy = make(stage, verifier)
    discrepancy.status = S.CONFIRMED
    discrepancy.save()

    discrepancy.status = S.RESOLVED
    discrepancy.resolution_note = "Stay vacated."
    discrepancy.save()

    assert discrepancy.resolved_at is not None


@pytest.mark.parametrize("terminal", [S.RESOLVED, S.DISMISSED])
def test_closing_without_saying_why_is_refused(stage, verifier, terminal):
    """A dismissed leak claim with no explanation is indistinguishable
    from one nobody bothered to look at."""
    discrepancy = make(stage, verifier)
    discrepancy.status = S.CONFIRMED
    discrepancy.save()

    discrepancy.status = terminal
    with pytest.raises(ValidationError) as excinfo:
        discrepancy.full_clean()

    assert "resolution_note" in excinfo.value.error_dict


def test_saving_without_changing_status_is_always_allowed(stage, verifier):
    """Editing a description on a closed discrepancy is not a transition
    and must not be treated as one."""
    discrepancy = make(stage, verifier)
    discrepancy.status = S.CONFIRMED
    discrepancy.save()
    discrepancy.status = S.RESOLVED
    discrepancy.resolution_note = "done"
    discrepancy.save()

    discrepancy.description = "Corrected wording."
    discrepancy.save()

    discrepancy.refresh_from_db()
    assert discrepancy.description == "Corrected wording."


def test_can_become_answers_the_same_question_as_the_guard(stage, verifier):
    """So a UI can grey out an option instead of offering it and failing."""
    discrepancy = make(stage, verifier)

    assert discrepancy.can_become(S.CONFIRMED)
    assert not discrepancy.can_become(S.RESOLVED)


# --- relationships and audit -------------------------------------------


def test_a_discrepancy_belongs_to_a_stage_not_a_whole_exam(stage, verifier):
    """Stages progress independently - a leak in prelims says nothing
    about mains."""
    make(stage, verifier)

    assert list(stage.discrepancies.all())
    assert stage.exam.stages.count() == 1


def test_a_stage_with_a_discrepancy_cannot_be_deleted_out_from_under_it(stage, verifier):
    from django.db.models import ProtectedError

    make(stage, verifier)

    with pytest.raises(ProtectedError):
        stage.delete()


def test_who_reported_and_who_closed_it_are_both_recorded(stage, verifier):
    admin = User.objects.create_user(email="admin@example.gov.in", password="x", role=Role.ADMIN)
    discrepancy = make(stage, verifier)
    discrepancy.status = S.CONFIRMED
    discrepancy.save()
    discrepancy.status = S.RESOLVED
    discrepancy.resolution_note = "Re-exam held."
    discrepancy.resolved_by = admin
    discrepancy.save()

    discrepancy.refresh_from_db()
    assert discrepancy.reported_by == verifier
    assert discrepancy.resolved_by == admin


def test_every_change_is_kept_in_history(stage, verifier):
    """A claim this serious should not be quietly downgraded."""
    discrepancy = make(stage, verifier, severity=Sev.CRITICAL)
    discrepancy.severity = Sev.LOW
    discrepancy.save()

    severities = list(discrepancy.history.values_list("severity", flat=True))
    assert severities == [Sev.LOW, Sev.CRITICAL]


def test_open_discrepancies_are_queryable_for_the_feed_that_will_need_them(stage, verifier):
    open_one = make(stage, verifier)
    closed = make(stage, verifier, discrepancy_type=T.KEY_ERROR)
    closed.status = S.CONFIRMED
    closed.save()
    closed.status = S.RESOLVED
    closed.resolution_note = "Key revised."
    closed.save()

    still_open = Discrepancy.objects.exclude(status__in=Discrepancy.TERMINAL)

    assert list(still_open) == [open_one]
