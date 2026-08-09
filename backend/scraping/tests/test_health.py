"""EXT-047: recording and judging source health."""

from datetime import UTC, datetime, timedelta

import pytest
from django.utils import timezone

from exams.models import Board
from scraping.health import (
    health_for,
    is_stale,
    missed_runs,
    record_failure,
    record_success,
    report_stale_sources,
    stale_sources,
    touch,
)
from scraping.models import Source, SourceHealth

pytestmark = pytest.mark.django_db


@pytest.fixture
def board():
    return Board.objects.create(name="Union Public Service Commission", code="UPSC")


@pytest.fixture
def source(board):
    return Source.objects.create(
        board=board,
        name="UPSC what's new",
        url="https://www.upsc.gov.in/",
        parser_key="upsc_whats_new",
        cron="0 * * * *",  # hourly
        enabled=True,
    )


# --- a health row exists per source ------------------------------------


def test_every_new_source_gets_a_health_row(source):
    """The dashboard has to list a source from the moment it exists - a
    newly added source is the one most likely to be misconfigured."""
    assert SourceHealth.objects.filter(source=source).exists()


def test_a_brand_new_source_reads_as_not_yet_run(source):
    health = health_for(source)

    assert health.last_success_at is None
    assert health.last_failure_at is None
    assert health.consecutive_failures == 0
    assert health.has_ever_succeeded is False


# --- the three things the dashboard must show --------------------------


def test_a_success_records_when_it_last_worked(source):
    before = timezone.now()

    health = record_success(source)

    assert health.last_success_at is not None
    assert health.last_success_at >= before


def test_a_failure_records_when_it_last_broke_and_why(source):
    health = record_failure(source, "502 from the board")

    assert health.last_failure_at is not None
    assert health.last_error == "502 from the board"


def test_consecutive_failures_accumulate(source):
    for _ in range(3):
        record_failure(source, "still down")

    assert health_for(source).consecutive_failures == 3


def test_a_success_clears_the_failure_streak(source):
    record_failure(source, "down")
    record_failure(source, "down")

    health = record_success(source)

    assert health.consecutive_failures == 0
    assert health.last_error == ""


def test_a_success_does_not_erase_the_last_failure(source):
    """Knowing a source recovered is useful; knowing it broke this morning
    and recovered is more useful."""
    record_failure(source, "brief outage")
    failed_at = health_for(source).last_failure_at

    record_success(source)

    assert health_for(source).last_failure_at == failed_at


def test_a_skipped_run_counts_as_neither_success_nor_failure(source):
    """Pausing a source deliberately is not a fault, and counting it as
    one would make a tidy admin action look like a broken board."""
    touch(source)
    health = health_for(source)

    assert health.last_checked_at is not None
    assert health.last_success_at is None
    assert health.last_failure_at is None
    assert health.consecutive_failures == 0


# --- staleness ---------------------------------------------------------


def test_a_source_that_just_succeeded_is_not_stale(source):
    record_success(source)

    assert is_stale(source, health_for(source)) is False


def test_a_source_that_has_missed_too_many_runs_is_stale(source):
    """The quiet failure: no exception was ever raised, the source simply
    stopped being scraped."""
    health = record_success(source)
    health.last_success_at = timezone.now() - timedelta(hours=10)
    health.save()

    assert is_stale(source, health) is True


def test_staleness_is_judged_against_that_source_s_own_schedule(board):
    """"No success in 10 hours" is a crisis for an hourly source and
    completely normal for a weekly one. A single global timeout would call
    the weekly source broken every week."""
    hourly = Source.objects.create(
        board=board, name="hourly", url="https://x.gov.in/h", parser_key="a", cron="0 * * * *"
    )
    weekly = Source.objects.create(
        board=board, name="weekly", url="https://x.gov.in/w", parser_key="b", cron="0 6 * * 1"
    )
    ten_hours_ago = timezone.now() - timedelta(hours=10)

    for source in (hourly, weekly):
        health = health_for(source)
        health.last_success_at = ten_hours_ago
        health.save()

    assert is_stale(hourly, health_for(hourly)) is True
    assert is_stale(weekly, health_for(weekly)) is False


def test_missed_runs_counts_the_schedule_not_elapsed_time(source):
    """Fixed timestamps rather than `now()` offsets: an hourly cron counts
    the top-of-hour marks crossed, so the answer would otherwise depend on
    what minute the suite happened to run at."""
    health = health_for(source)
    health.last_success_at = datetime(2026, 8, 9, 8, 30, tzinfo=UTC)
    health.save()

    # Fires at 09:00, 10:00, 11:00 - three marks crossed by 11:15.
    assert missed_runs(source, health, now=datetime(2026, 8, 9, 11, 15, tzinfo=UTC)) == 3
    # 12:00 makes it four, which is over the threshold.
    assert missed_runs(source, health, now=datetime(2026, 8, 9, 12, 0, tzinfo=UTC)) == 4


def test_counting_stops_once_the_threshold_is_passed(source):
    """A source last successful a year ago on a per-minute cron must not
    walk half a million cron iterations to answer a yes/no question."""
    source.cron = "* * * * *"
    source.save()
    health = health_for(source)
    health.last_success_at = timezone.now() - timedelta(days=365)
    health.save()

    assert missed_runs(source, health) == 4  # threshold (3) + 1, then it stops
    assert is_stale(source, health) is True


def test_a_source_that_has_never_run_is_not_instantly_stale(source):
    """Otherwise every source is born broken and the dashboard cries wolf
    the moment someone adds one."""
    assert is_stale(source, health_for(source)) is False


def test_a_source_that_has_never_succeeded_goes_stale_from_when_it_was_added(source):
    health = health_for(source)
    health.last_checked_at = timezone.now() - timedelta(hours=10)
    health.save()

    assert is_stale(source, health) is True
    assert health.has_ever_succeeded is False


# --- the stale list ----------------------------------------------------


def test_stale_sources_lists_only_the_overdue_ones(board):
    healthy = Source.objects.create(
        board=board, name="fine", url="https://x.gov.in/1", parser_key="a", cron="0 * * * *"
    )
    overdue = Source.objects.create(
        board=board, name="quiet", url="https://x.gov.in/2", parser_key="b", cron="0 * * * *"
    )
    record_success(healthy)
    stale_health = health_for(overdue)
    stale_health.last_success_at = timezone.now() - timedelta(days=2)
    stale_health.save()

    assert [source for source, _ in stale_sources()] == [overdue]


def test_a_disabled_source_is_never_reported_stale(source):
    """A source paused on purpose is not a fault. Reporting it as one
    trains operators to ignore the list."""
    health = health_for(source)
    health.last_success_at = timezone.now() - timedelta(days=30)
    health.save()
    source.enabled = False
    source.save()

    assert stale_sources() == []


# --- alerting ----------------------------------------------------------


def test_crossing_the_failure_threshold_raises_an_alert(source, caplog):
    """ERROR because Sentry's logging integration turns ERROR into an
    event - so this is a real alert, not a line in a file nobody reads."""
    with caplog.at_level("ERROR"):
        for _ in range(4):
            record_failure(source, "board is down")

    alerts = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(alerts) == 1
    assert "failed 4 times in a row" in alerts[0].getMessage()


def test_a_source_that_stays_down_does_not_alert_on_every_run(source, caplog):
    """One alert per run for as long as a board is down is how alerting
    gets muted."""
    with caplog.at_level("ERROR"):
        for _ in range(12):
            record_failure(source, "board is down")

    assert len([r for r in caplog.records if r.levelname == "ERROR"]) == 1


def test_a_brief_wobble_does_not_alert(source, caplog):
    with caplog.at_level("ERROR"):
        record_failure(source, "one blip")
        record_success(source)

    assert [r for r in caplog.records if r.levelname == "ERROR"] == []


def test_stale_sources_are_alerted_too(source, caplog):
    """The counterpart: failures shout, staleness is silent, and only this
    catches the silent one."""
    health = health_for(source)
    health.last_success_at = timezone.now() - timedelta(days=2)
    health.save()

    with caplog.at_level("ERROR"):
        reported = report_stale_sources()

    assert [s for s, _ in reported] == [source]
    assert any("is stale" in r.getMessage() for r in caplog.records)


# --- concurrency -------------------------------------------------------


def test_two_workers_failing_at_once_do_not_lose_an_increment(source):
    """Read-modify-write would have both read the same value and written
    the same result, counting two failures as one."""
    record_failure(source, "first")
    stale_copy = SourceHealth.objects.get(source=source)

    record_failure(source, "second")
    # A worker holding the pre-increment instance still increments the row.
    record_failure(stale_copy.source, "third")

    assert health_for(source).consecutive_failures == 3
