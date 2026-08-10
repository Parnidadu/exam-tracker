"""EXT-052: machine status changes emit an event and queue follow-up work."""

from datetime import timedelta

import pytest
from django.utils import timezone

from exams.models import Board, Exam, ExamStage, StatusTrack
from verification.models import StatusChange
from verification.observations import apply_machine_observation
from verification.tasks import process_status_change

pytestmark = pytest.mark.django_db
S = ExamStage.StageType


@pytest.fixture
def stage():
    board = Board.objects.create(
        name="Union Public Service Commission", code="UPSC", official_url="https://u"
    )
    exam = Exam.objects.create(
        board=board, code="CSE", name="Civil Services Examination", cycle_year=2026, category="x"
    )
    return ExamStage.objects.create(exam=exam, stage_type=S.MAINS, sequence=2)


@pytest.fixture
def track(stage):
    return StatusTrack.objects.create(exam_stage=stage, track=StatusTrack.Track.RESULT)


def observe(stage, value, confidence=0.9):
    return apply_machine_observation(
        exam_stage=stage, track=StatusTrack.Track.RESULT, value=value, confidence=confidence
    )


# --- a machine status change writes a StatusChange row -----------------


def test_a_machine_status_change_writes_a_row(stage, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        observe(stage, "declared")

    change = StatusChange.objects.get()
    assert change.new_value == "declared"
    assert change.status_track.exam_stage == stage


def test_a_first_observation_counts_as_a_change(stage, django_capture_on_commit_callbacks):
    """A status appeared where there was none, which is exactly what a
    verifier should look at."""
    with django_capture_on_commit_callbacks(execute=True):
        observe(stage, "declared")

    change = StatusChange.objects.get()
    assert change.previous_value == ""
    assert change.new_value == "declared"


def test_a_transition_records_both_ends(stage, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        observe(stage, "scheduled", confidence=0.8)
        observe(stage, "declared", confidence=0.9)

    latest = StatusChange.objects.first()
    assert (latest.previous_value, latest.new_value) == ("scheduled", "declared")
    assert latest.previous_confidence == pytest.approx(0.8)
    assert latest.new_confidence == pytest.approx(0.9)


def test_the_observation_time_is_kept_separately_from_the_detection_time(
    stage, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        observe(stage, "declared")

    change = StatusChange.objects.get()
    assert change.observed_at is not None
    assert change.detected_at is not None


# --- what is deliberately not a change ---------------------------------


def test_re_observing_the_same_value_emits_nothing(stage, django_capture_on_commit_callbacks):
    """Most polls confirm what is already there. A row per source per run
    would tell a verifier nothing about what moved."""
    with django_capture_on_commit_callbacks(execute=True):
        for _ in range(5):
            observe(stage, "declared")

    assert StatusChange.objects.count() == 1


def test_confidence_moving_on_its_own_is_not_a_status_change(
    stage, django_capture_on_commit_callbacks
):
    """The value is the status. 0.8 to 0.9 on "declared" is not a
    transition."""
    with django_capture_on_commit_callbacks(execute=True):
        observe(stage, "declared", confidence=0.8)
        observe(stage, "declared", confidence=0.99)

    assert StatusChange.objects.count() == 1


def test_a_human_verification_is_not_a_machine_change(track, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        track.human_value = "declared"
        track.verified_by = "verifier@example.gov.in"
        track.verified_at = timezone.now()
        track.save()

    assert not StatusChange.objects.exists()


def test_creating_an_empty_track_emits_nothing(stage, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        StatusTrack.objects.create(exam_stage=stage, track=StatusTrack.Track.CONDUCT)

    assert not StatusChange.objects.exists()


def test_a_refused_write_emits_nothing(stage, django_capture_on_commit_callbacks):
    """A machine observation contradicting a fresh human value is not
    written, so nothing changed and there is nothing to log. The conflict
    record is the artefact there, not a change."""
    StatusTrack.objects.create(
        exam_stage=stage,
        track=StatusTrack.Track.RESULT,
        human_value="not-declared",
        verified_by="verifier@example.gov.in",
        verified_at=timezone.now() - timedelta(days=1),
    )

    with django_capture_on_commit_callbacks(execute=True):
        applied = observe(stage, "declared")

    assert not applied.written
    assert not StatusChange.objects.exists()


# --- caught however the value is written -------------------------------


def test_a_direct_model_write_is_caught_too(track, django_capture_on_commit_callbacks):
    """The model already carries a save-time backstop so its rule holds
    for a management command or the admin, not only for callers who
    remember the helper. A change log that saw one caller would quietly
    under-report - and an under-reporting log is worse than none, because
    it looks complete."""
    with django_capture_on_commit_callbacks(execute=True):
        track.machine_value = "declared"
        track.machine_seen_at = timezone.now()
        track.save()

    assert StatusChange.objects.count() == 1


# --- and enqueues a verification task ----------------------------------


def test_a_change_enqueues_a_verification_task(stage, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        observe(stage, "declared")

    assert callbacks, "the follow-up task should be queued on commit"
    assert StatusChange.objects.get().verification_task_id


def test_the_task_is_not_enqueued_until_the_write_commits(
    stage, django_capture_on_commit_callbacks
):
    """Enqueueing inline would hand the worker an id for an uncommitted
    row, and would produce work for a change that a rollback undoes."""
    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        observe(stage, "declared")
        assert StatusChange.objects.exists(), "the row is written straight away"
        assert not StatusChange.objects.get().verification_task_id

    assert len(callbacks) == 1


def test_a_rolled_back_write_queues_nothing(stage, django_capture_on_commit_callbacks):
    from django.db import transaction

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        try:
            with transaction.atomic():
                observe(stage, "declared")
                raise RuntimeError("something later in the run failed")
        except RuntimeError:
            pass

    assert not StatusChange.objects.exists()
    assert not callbacks


# --- what the task does ------------------------------------------------


def test_the_task_marks_a_change_that_needs_a_verifier(stage, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        observe(stage, "declared")

    change = StatusChange.objects.get()
    change.refresh_from_db()

    assert change.needs_verification is True
    assert change.queue_reason == "machine_changed"
    assert change.processed_at is not None


def test_the_task_does_not_flag_a_machine_agreeing_with_a_human(
    stage, django_capture_on_commit_callbacks
):
    """A scraper confirming what a verifier already said is a real
    transition worth logging, but it puts the track in front of nobody.
    Marking it as needing verification would pad the queue with
    agreement."""
    StatusTrack.objects.create(
        exam_stage=stage,
        track=StatusTrack.Track.RESULT,
        human_value="declared",
        verified_by="verifier@example.gov.in",
        verified_at=timezone.now(),
    )

    with django_capture_on_commit_callbacks(execute=True):
        applied = observe(stage, "declared")

    assert applied.written
    change = StatusChange.objects.get()
    assert change.needs_verification is False
    assert change.queue_reason == ""


def test_unprocessed_is_distinguishable_from_processed_and_clear(stage):
    """Null means the task has not run; False means it ran and found
    nothing to verify. Collapsing them would make a stuck worker look like
    a quiet queue."""
    with_no_task = StatusChange.objects.create(
        status_track=StatusTrack.objects.create(
            exam_stage=stage, track=StatusTrack.Track.INTEGRITY
        ),
        new_value="clean",
    )

    assert with_no_task.needs_verification is None


def test_the_task_survives_the_change_being_deleted(stage, django_capture_on_commit_callbacks):
    """Beat and the worker are separate processes; a stage deleted in
    between is a normal race, not something to page anyone over."""
    with django_capture_on_commit_callbacks(execute=True):
        observe(stage, "declared")
    change_id = StatusChange.objects.get().pk
    StatusChange.objects.all().delete()

    assert process_status_change(change_id)["status"] == "missing"


def test_the_task_can_be_called_the_way_celery_calls_it(
    stage, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        observe(stage, "declared")
    change = StatusChange.objects.get()

    outcome = process_status_change.apply(args=[change.pk])

    assert outcome.successful()
    assert outcome.result["needs_verification"] is True


# --- history -----------------------------------------------------------


def test_successive_changes_are_all_kept_newest_first(
    stage, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        observe(stage, "scheduled")
        observe(stage, "conducted")
        observe(stage, "declared")

    assert [c.new_value for c in StatusChange.objects.all()] == [
        "declared",
        "conducted",
        "scheduled",
    ]
