"""EXT-046: the task a source's schedule fires."""

import pytest

from exams.models import Board
from scraping.fetch import FetchFailed, RobotsDisallowed
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
