"""EXT-061: alerting when a planned date passes with nothing recorded."""

from datetime import timedelta

import pytest
from django.core import mail
from django.db import IntegrityError
from django.utils import timezone

from accounts.models import Role, User
from exams.models import Board, Exam, ExamStage, StatusTrack
from verification.alerts import (
    send_elapsed_date_alerts,
    stages_with_elapsed_dates,
)
from verification.models import ElapsedDateAlert
from verification.tasks import send_elapsed_date_alerts as alert_task

pytestmark = pytest.mark.django_db

TODAY = timezone.now().date()
PAST = TODAY - timedelta(days=10)
FUTURE = TODAY + timedelta(days=10)


@pytest.fixture
def exam():
    board = Board.objects.create(
        name="Union Public Service Commission", code="UPSC", official_url="https://u"
    )
    return Exam.objects.create(
        board=board, code="CSE", name="Civil Services Examination", cycle_year=2026, category="x"
    )


@pytest.fixture
def verifier():
    return User.objects.create_user(
        email="verifier@example.gov.in", password="x", role=Role.VERIFIER
    )


def stage(exam, planned=PAST, sequence=1, stage_type=ExamStage.StageType.PRELIMS):
    return ExamStage.objects.create(
        exam=exam, stage_type=stage_type, sequence=sequence, planned_start_date=planned
    )


# --- the condition -----------------------------------------------------


def test_a_date_that_passed_with_nothing_recorded_alerts(exam, verifier):
    overdue = stage(exam)

    result = send_elapsed_date_alerts()

    assert result["status"] == "sent"
    assert result["stages"] == 1
    assert ElapsedDateAlert.objects.get().exam_stage == overdue


def test_a_stage_with_no_status_track_at_all_still_alerts(exam, verifier):
    """The most complete "no update" there is - and the one the
    verification queue misses, because that query runs over StatusTrack
    rows and this stage has none."""
    overdue = stage(exam)
    assert not StatusTrack.objects.filter(exam_stage=overdue).exists()

    assert list(stages_with_elapsed_dates()) == [overdue]


def test_a_stage_with_an_empty_conduct_track_alerts(exam, verifier):
    """A row exists but says nothing, which is still no update."""
    overdue = stage(exam)
    StatusTrack.objects.create(exam_stage=overdue, track=StatusTrack.Track.CONDUCT)

    assert list(stages_with_elapsed_dates()) == [overdue]


def test_a_date_still_in_the_future_does_not_alert(exam, verifier):
    stage(exam, planned=FUTURE)

    assert list(stages_with_elapsed_dates()) == []
    assert send_elapsed_date_alerts()["status"] == "skipped"


def test_a_stage_with_no_planned_date_does_not_alert(exam, verifier):
    """Nothing was promised, so nothing was missed."""
    ExamStage.objects.create(
        exam=exam, stage_type=ExamStage.StageType.MAINS, sequence=2, planned_start_date=None
    )

    assert list(stages_with_elapsed_dates()) == []


def test_a_machine_observation_counts_as_an_update(exam, verifier):
    """The scraper saw something, so the date did not pass unremarked."""
    overdue = stage(exam)
    StatusTrack.objects.create(
        exam_stage=overdue,
        track=StatusTrack.Track.CONDUCT,
        machine_value="conducted",
        machine_seen_at=timezone.now(),
    )

    assert list(stages_with_elapsed_dates()) == []


def test_an_update_on_a_different_track_does_not_count(exam, verifier):
    """A declared result says nothing about whether the exam was held."""
    overdue = stage(exam)
    StatusTrack.objects.create(
        exam_stage=overdue,
        track=StatusTrack.Track.RESULT,
        machine_value="declared",
        machine_seen_at=timezone.now(),
    )

    assert list(stages_with_elapsed_dates()) == [overdue]


# --- fires once per stage ----------------------------------------------


def test_a_second_run_does_not_alert_again(exam, verifier):
    """A daily job with no memory would re-alert every morning for as long
    as the stage stayed unattended - which is precisely the stage nobody
    is attending to."""
    stage(exam)

    first = send_elapsed_date_alerts()
    mail.outbox.clear()
    second = send_elapsed_date_alerts()

    assert first["stages"] == 1
    assert second["status"] == "skipped"
    assert mail.outbox == []
    assert ElapsedDateAlert.objects.count() == 1


def test_running_it_many_times_still_alerts_once(exam, verifier):
    stage(exam)

    for _ in range(5):
        send_elapsed_date_alerts()

    assert ElapsedDateAlert.objects.count() == 1
    assert len(mail.outbox) == 1


def test_once_per_stage_is_a_database_constraint_not_a_convention(exam, verifier):
    """So a second code path cannot quietly reintroduce the repeat."""
    overdue = stage(exam)
    ElapsedDateAlert.objects.create(exam_stage=overdue, planned_date=PAST)

    with pytest.raises(IntegrityError):
        ElapsedDateAlert.objects.create(exam_stage=overdue, planned_date=PAST)


def test_each_overdue_stage_gets_its_own_record(exam, verifier):
    first = stage(exam, sequence=1)
    second = stage(exam, sequence=2, stage_type=ExamStage.StageType.MAINS)

    send_elapsed_date_alerts()

    assert ElapsedDateAlert.objects.count() == 2
    assert {a.exam_stage_id for a in ElapsedDateAlert.objects.all()} == {first.pk, second.pk}
    # One email for the run, though - a separate message per stage on the
    # same morning is a mailbox nobody reads.
    assert len(mail.outbox) == 1


def test_a_new_stage_going_overdue_later_still_alerts(exam, verifier):
    """"Once per stage" must not mean "once, ever"."""
    stage(exam, sequence=1)
    send_elapsed_date_alerts()
    mail.outbox.clear()

    stage(exam, sequence=2, stage_type=ExamStage.StageType.MAINS)
    result = send_elapsed_date_alerts()

    assert result["stages"] == 1
    assert len(mail.outbox) == 1


# --- suppressed after verification -------------------------------------


def test_a_verified_stage_is_not_alerted_about(exam, verifier):
    overdue = stage(exam)
    StatusTrack.objects.create(
        exam_stage=overdue,
        track=StatusTrack.Track.CONDUCT,
        human_value="conducted",
        verified_by=verifier.email,
        verified_at=timezone.now(),
    )

    assert list(stages_with_elapsed_dates()) == []
    assert send_elapsed_date_alerts()["status"] == "skipped"
    assert mail.outbox == []


def test_verifying_suppresses_an_alert_that_would_otherwise_fire(exam, verifier):
    """The sequence that matters: overdue, then a human deals with it
    before the job next runs."""
    overdue = stage(exam)
    assert list(stages_with_elapsed_dates()) == [overdue]

    StatusTrack.objects.create(
        exam_stage=overdue,
        track=StatusTrack.Track.CONDUCT,
        human_value="postponed",
        verified_by=verifier.email,
        verified_at=timezone.now(),
    )

    assert send_elapsed_date_alerts()["status"] == "skipped"
    assert not ElapsedDateAlert.objects.exists()


def test_a_stale_verification_still_counts_as_having_been_dealt_with(exam, verifier):
    """This alert is about silence, not freshness. A verification that has
    aged out is the staleness queue's business (EXT-023), and alerting
    again here would report the same stage under two different names."""
    overdue = stage(exam)
    StatusTrack.objects.create(
        exam_stage=overdue,
        track=StatusTrack.Track.CONDUCT,
        human_value="conducted",
        verified_by=verifier.email,
        verified_at=timezone.now() - StatusTrack.STALENESS_WINDOW - timedelta(days=30),
    )

    assert list(stages_with_elapsed_dates()) == []


# --- what is sent ------------------------------------------------------


def test_the_alert_goes_to_verifiers(exam, verifier):
    stage(exam)

    send_elapsed_date_alerts()

    assert [message.to for message in mail.outbox] == [[verifier.email]]


def test_the_message_says_which_exam_and_which_date(exam, verifier):
    stage(exam)

    send_elapsed_date_alerts()
    body = mail.outbox[0].body

    assert "Civil Services Examination" in body
    assert "UPSC" in body
    assert "prelims" in body
    assert str(PAST) in body


def test_the_subject_agrees_with_itself(exam, verifier):
    stage(exam, sequence=1)
    send_elapsed_date_alerts()
    assert "1 exam date passed" in mail.outbox[0].subject

    mail.outbox.clear()
    stage(exam, sequence=2, stage_type=ExamStage.StageType.MAINS)
    stage(exam, sequence=3, stage_type=ExamStage.StageType.INTERVIEW)
    send_elapsed_date_alerts()
    assert "2 exam dates passed" in mail.outbox[0].subject


def test_every_overdue_stage_is_logged_individually(exam, verifier, caplog):
    """One log record per stage, so an on-call operator sees one event per
    exam rather than one event per morning."""
    stage(exam, sequence=1)
    stage(exam, sequence=2, stage_type=ExamStage.StageType.MAINS)

    with caplog.at_level("ERROR"):
        send_elapsed_date_alerts()

    overdue_records = [r for r in caplog.records if "passed with no update" in r.getMessage()]
    assert len(overdue_records) == 2


def test_the_condition_is_still_recorded_when_there_is_nobody_to_email(exam, caplog):
    """An alert nobody is subscribed to is a configuration problem, and
    losing the record of it would hide the overdue exam as well."""
    overdue = stage(exam)

    with caplog.at_level("ERROR"):
        result = send_elapsed_date_alerts()

    assert result["status"] == "logged"
    assert mail.outbox == []
    assert ElapsedDateAlert.objects.get().exam_stage == overdue
    assert ElapsedDateAlert.objects.get().notified == 0
    assert any("no active verifier" in r.getMessage() for r in caplog.records)


def test_the_planned_date_that_triggered_it_is_kept(exam, verifier):
    """The stage's own date may move afterwards; what fired the alert
    should still be answerable."""
    overdue = stage(exam)

    send_elapsed_date_alerts()
    overdue.planned_start_date = FUTURE
    overdue.save()

    assert ElapsedDateAlert.objects.get().planned_date == PAST


# --- the scheduled task ------------------------------------------------


def test_the_task_runs_the_alert(exam, verifier):
    stage(exam)

    result = alert_task()

    assert result["status"] == "sent"
    assert len(mail.outbox) == 1


def test_the_task_sends_nothing_when_nothing_is_overdue(exam, verifier):
    stage(exam, planned=FUTURE)

    assert alert_task()["status"] == "skipped"
    assert mail.outbox == []


def test_the_alert_is_on_beat_s_schedule(settings):
    entry = settings.CELERY_BEAT_SCHEDULE["elapsed-date-alerts"]

    assert entry["task"] == "verification.tasks.send_elapsed_date_alerts"
    assert entry["schedule"].hour == {6}


def test_it_runs_before_the_digest(settings):
    """So a date that passed unremarked is already recorded by the time
    the day's queue summary goes out."""
    alert = settings.CELERY_BEAT_SCHEDULE["elapsed-date-alerts"]["schedule"]
    digest = settings.CELERY_BEAT_SCHEDULE["verification-digest"]["schedule"]

    assert min(alert.hour) < min(digest.hour)
