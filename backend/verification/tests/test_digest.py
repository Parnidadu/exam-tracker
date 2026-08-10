"""EXT-060: the daily digest of items waiting for a verifier."""

from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone

from accounts.models import Role, User
from exams.models import Board, Exam, ExamStage, StatusTrack
from verification.digest import build_digest, recipients, render, send_digest
from verification.tasks import send_verification_digest

pytestmark = pytest.mark.django_db


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


def queued(stage, track=StatusTrack.Track.RESULT, value="declared"):
    """A track the machine has spoken about and no human has confirmed -
    the highest-priority reason the queue recognises."""
    return StatusTrack.objects.create(
        exam_stage=stage,
        track=track,
        machine_value=value,
        machine_seen_at=timezone.now(),
    )


def stale(stage, track=StatusTrack.Track.CONDUCT):
    """Verified once, long enough ago that effective_status has already
    fallen back to the machine value."""
    return StatusTrack.objects.create(
        exam_stage=stage,
        track=track,
        human_value="conducted",
        verified_by="someone@example.gov.in",
        verified_at=timezone.now() - StatusTrack.STALENESS_WINDOW - timedelta(days=1),
    )


# --- an empty queue sends nothing --------------------------------------


def test_an_empty_queue_sends_nothing(verifier):
    """A daily "0 items" email trains people to delete the message
    unread, and the first day it matters it goes with the rest."""
    result = send_digest()

    assert mail.outbox == []
    assert result["status"] == "skipped"
    assert result["reason"] == "empty_queue"


def test_an_empty_queue_sends_nothing_even_with_verifiers_waiting(verifier, stage):
    """Nothing queued is nothing queued, however many people are
    subscribed."""
    User.objects.create_user(email="second@example.gov.in", password="x", role=Role.VERIFIER)

    send_digest()

    assert mail.outbox == []


def test_the_digest_of_an_empty_queue_is_empty(verifier):
    digest = build_digest()

    assert digest.is_empty
    assert digest.total == 0
    assert digest.items == ()


# --- a daily digest to verifiers ---------------------------------------


def test_a_waiting_item_is_sent_to_the_verifier(verifier, stage):
    queued(stage)

    result = send_digest()

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [verifier.email]
    assert result["status"] == "sent"
    assert result["total"] == 1


def test_every_active_verifier_gets_it(stage):
    for address in ("a@example.gov.in", "b@example.gov.in"):
        User.objects.create_user(email=address, password="x", role=Role.VERIFIER)
    queued(stage)

    send_digest()

    assert sorted(message.to[0] for message in mail.outbox) == [
        "a@example.gov.in",
        "b@example.gov.in",
    ]


def test_each_verifier_gets_their_own_message(stage):
    """One message per recipient rather than one with everyone in To: a
    verifier's colleagues' addresses are not theirs to collect."""
    for address in ("a@example.gov.in", "b@example.gov.in"):
        User.objects.create_user(email=address, password="x", role=Role.VERIFIER)
    queued(stage)

    send_digest()

    assert len(mail.outbox) == 2
    for message in mail.outbox:
        assert len(message.to) == 1
        assert not message.cc
        assert not message.bcc


@pytest.mark.parametrize("role", [Role.VIEWER, Role.ADMIN])
def test_only_verifiers_are_subscribed(stage, role):
    """Admins can verify, but an unsolicited daily email is the fastest
    way to make a system feel like spam. An admin who wants it can be
    given the verifier role."""
    User.objects.create_user(email="other@example.gov.in", password="x", role=role)
    queued(stage)

    send_digest()

    assert mail.outbox == []
    assert recipients() == []


def test_a_deactivated_verifier_is_not_emailed(stage, verifier):
    verifier.is_active = False
    verifier.save()
    queued(stage)

    send_digest()

    assert mail.outbox == []


def test_items_waiting_but_nobody_to_tell_is_reported_distinctly(stage, caplog):
    """A queue building up with nobody assigned to look at it is a
    configuration problem, not a quiet day - and it needs a different fix
    from an empty queue, so it gets a different answer."""
    queued(stage)

    with caplog.at_level("ERROR"):
        result = send_digest()

    assert result["status"] == "skipped"
    assert result["reason"] == "no_recipients"
    assert result["total"] == 1
    assert any("no active verifier" in record.getMessage() for record in caplog.records)


# --- what the digest says ----------------------------------------------


def test_the_subject_carries_the_count(verifier, stage):
    queued(stage)
    queued(stage, track=StatusTrack.Track.CONDUCT, value="scheduled")

    send_digest()

    assert "2 items to verify" in mail.outbox[0].subject


def test_the_subject_reads_naturally_for_one_item(verifier, stage):
    queued(stage)

    send_digest()

    assert "1 item to verify" in mail.outbox[0].subject


def test_the_body_agrees_with_itself_grammatically(verifier, stage):
    """"1 item are waiting" is the sort of thing nobody reports and
    everybody notices."""
    queued(stage)
    send_digest()
    assert "1 item is waiting" in mail.outbox[0].body

    mail.outbox.clear()
    queued(stage, track=StatusTrack.Track.CONDUCT, value="scheduled")
    send_digest()
    assert "2 items are waiting" in mail.outbox[0].body


def test_the_body_names_the_exam_and_why_it_is_queued(verifier, stage):
    queued(stage)

    send_digest()
    body = mail.outbox[0].body

    # The exam's name, not just its code: someone skimming this at 07:00
    # is looking for "Civil Services Examination", not "UPSC CSE 2026".
    assert "Civil Services Examination" in body
    assert "UPSC" in body
    assert "2026" in body
    assert "prelims" in body
    assert "Machine observation contradicts the record" in body


def test_reasons_are_summarised_by_the_queue_s_own_priority(verifier, stage):
    """Three contradictions matter more than forty stale verifications; a
    digest sorted by count would bury them."""
    queued(stage)
    stale(stage)

    digest = build_digest()

    assert list(digest.counts) == ["machine_changed", "stale_verification"]


def test_a_long_queue_is_summarised_rather_than_listed_in_full(verifier, stage, settings):
    settings.VERIFICATION_DIGEST_MAX_ITEMS = 2
    for index in range(4):
        other = ExamStage.objects.create(
            exam=stage.exam, stage_type=ExamStage.StageType.MAINS, sequence=index + 2
        )
        queued(other)

    send_digest()
    body = mail.outbox[0].body

    assert "...and 2 more." in body


def test_the_body_links_to_the_console_when_one_is_configured(verifier, stage, settings):
    settings.FRONTEND_BASE_URL = "https://exams.example.gov.in/"
    queued(stage)

    send_digest()

    assert "https://exams.example.gov.in/verify" in mail.outbox[0].body


def test_no_link_is_offered_when_there_is_no_frontend_url(verifier, stage, settings):
    """Better than emitting a broken one."""
    settings.FRONTEND_BASE_URL = ""
    queued(stage)

    send_digest()

    assert "Work the queue" not in mail.outbox[0].body


def test_render_is_pure_so_the_body_can_be_checked_without_sending(stage):
    queued(stage)

    subject, body = render(build_digest())

    assert subject
    assert body
    assert mail.outbox == []


# --- the scheduled task ------------------------------------------------


def test_the_task_sends_the_digest(verifier, stage):
    queued(stage)

    result = send_verification_digest()

    assert result["status"] == "sent"
    assert len(mail.outbox) == 1


def test_the_task_sends_nothing_on_an_empty_queue(verifier):
    result = send_verification_digest()

    assert result["status"] == "skipped"
    assert mail.outbox == []


def test_the_task_can_be_called_the_way_celery_calls_it(verifier, stage):
    queued(stage)

    outcome = send_verification_digest.apply()

    assert outcome.successful()
    assert outcome.result["status"] == "sent"


def test_the_digest_is_on_beat_s_schedule(settings):
    """It is a fixed system job, so it lives in CELERY_BEAT_SCHEDULE
    rather than being derived from a Source row."""
    entry = settings.CELERY_BEAT_SCHEDULE["verification-digest"]

    assert entry["task"] == "verification.tasks.send_verification_digest"
    assert entry["schedule"].hour == {7}
    assert entry["schedule"].minute == {0}


def test_the_schedule_does_not_collide_with_the_source_derived_ones():
    """scraping.schedules prunes only its own prefix, so a fixed entry
    cannot be deleted by a source sync."""
    from scraping.schedules import NAME_PREFIX

    assert not "verification-digest".startswith(NAME_PREFIX)
