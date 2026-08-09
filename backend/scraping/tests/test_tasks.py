"""EXT-046: the task a source's schedule fires."""

import pytest

from exams.models import Board
from scraping.fetch import FetchFailed, RobotsDisallowed
from scraping.health import health_for
from scraping.models import Snapshot, Source
from scraping.tasks import scrape_source

pytestmark = pytest.mark.django_db


@pytest.fixture
def source():
    board = Board.objects.create(name="Union Public Service Commission", code="UPSC")
    return Source.objects.create(
        board=board,
        name="UPSC what's new",
        url="https://www.upsc.gov.in/",
        parser_key="upsc_whats_new",
    )


def test_a_run_stores_a_snapshot(source, monkeypatch):
    monkeypatch.setattr(
        "scraping.tasks.fetch_and_store",
        lambda s: _stub_result(s, changed=True),
    )

    result = scrape_source(source.pk)

    assert result["status"] == "ok"
    assert result["changed"] is True
    assert result["should_parse"] is True


def test_an_unchanged_page_reports_that_there_is_nothing_to_parse(source, monkeypatch):
    monkeypatch.setattr(
        "scraping.tasks.fetch_and_store",
        lambda s: _stub_result(s, changed=False),
    )

    result = scrape_source(source.pk)

    assert result["status"] == "ok"
    assert result["should_parse"] is False


def test_a_disabled_source_is_skipped_even_if_the_task_was_already_queued(source):
    """Disabling turns the PeriodicTask off, but a tick already sitting in
    the broker would otherwise still run."""
    source.enabled = False
    source.save()

    assert scrape_source(source.pk)["status"] == "disabled"


def test_a_source_deleted_between_scheduling_and_running_does_not_raise(source):
    """Beat can queue a tick moments before the row is deleted. Raising
    here would turn a routine race into a paging error."""
    pk = source.pk
    source.delete()

    assert scrape_source(pk)["status"] == "missing"


@pytest.mark.parametrize(
    "error",
    [RobotsDisallowed("robots.txt disallows it"), FetchFailed("502 from the board")],
)
def test_a_board_being_unreachable_is_reported_not_raised(source, monkeypatch, error):
    """A board being down is a normal operating condition. Raising would
    mark the periodic task failed on every tick and drown the genuinely
    unexpected errors."""

    def boom(_source):
        raise error

    monkeypatch.setattr("scraping.tasks.fetch_and_store", boom)

    result = scrape_source(source.pk)

    assert result["status"] == "failed"
    assert result["error"]


def test_the_task_can_be_called_the_way_celery_calls_it(source, monkeypatch):
    """`.apply()` goes through Celery's own machinery - serialisation
    included - which a direct call skips."""
    monkeypatch.setattr(
        "scraping.tasks.fetch_and_store",
        lambda s: _stub_result(s, changed=True),
    )

    outcome = scrape_source.apply(args=[source.pk])

    assert outcome.successful()
    assert outcome.result["status"] == "ok"


def _stub_result(source, *, changed):
    """Builds a real SnapshotResult without going near the network."""
    from scraping.snapshots import SnapshotResult

    snapshot = Snapshot.objects.create(
        source=source,
        url=source.url,
        content_hash="a" * 64,
        content="<html></html>",
        status_code=200,
    )
    return SnapshotResult(snapshot=snapshot, changed=changed)


# --- EXT-047: runs feed source health ---------------------------------


def test_a_successful_run_is_recorded_on_the_source_health(source, monkeypatch):
    monkeypatch.setattr(
        "scraping.tasks.fetch_and_store",
        lambda s: _stub_result(s, changed=True),
    )

    scrape_source(source.pk)

    assert health_for(source).last_success_at is not None


def test_a_failed_run_increments_the_failure_count(source, monkeypatch):
    def boom(_source):
        raise FetchFailed("502 from the board")

    monkeypatch.setattr("scraping.tasks.fetch_and_store", boom)

    result = scrape_source(source.pk)

    health = health_for(source)
    assert health.consecutive_failures == 1
    assert health.last_error == "502 from the board"
    # Surfaced in the task result too, so a run's outcome is legible
    # without opening admin.
    assert result["consecutive_failures"] == 1


def test_recovery_clears_the_streak(source, monkeypatch):
    def boom(_source):
        raise FetchFailed("down")

    monkeypatch.setattr("scraping.tasks.fetch_and_store", boom)
    scrape_source(source.pk)
    scrape_source(source.pk)
    assert health_for(source).consecutive_failures == 2

    monkeypatch.setattr(
        "scraping.tasks.fetch_and_store",
        lambda s: _stub_result(s, changed=True),
    )
    scrape_source(source.pk)

    assert health_for(source).consecutive_failures == 0


def test_a_skipped_disabled_source_is_not_counted_as_a_failure(source):
    source.enabled = False
    source.save()

    scrape_source(source.pk)

    assert health_for(source).consecutive_failures == 0


def test_an_unexpected_error_is_recorded_as_a_failure_and_still_raised(source, monkeypatch):
    """Health that ignored unexpected errors would show a source as fine
    while it broke on every tick - but swallowing them would hide the
    traceback, so it is recorded and re-raised."""

    def boom(_source):
        raise ValueError("something nobody predicted")

    monkeypatch.setattr("scraping.tasks.fetch_and_store", boom)

    with pytest.raises(ValueError):
        scrape_source(source.pk)

    health = health_for(source)
    assert health.consecutive_failures == 1
    assert "ValueError" in health.last_error
