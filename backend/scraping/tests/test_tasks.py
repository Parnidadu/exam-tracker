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


# --- EXT-051: a changed page is parsed, matched and routed -------------


def _upsc_source(db_board=None):
    from exams.models import Board

    board = db_board or Board.objects.create(
        name="Union Public Service Commission", code="UPSC", official_url="https://www.upsc.gov.in/"
    )
    return Source.objects.create(
        board=board,
        name="UPSC what's new",
        url="https://www.upsc.gov.in/",
        parser_key="upsc_whats_new",
    )


def _serve_real_page(monkeypatch, source, *, changed=True):
    """Serves the captured UPSC page, with a fresh content hash each call.

    A distinct hash per call is what a real board looks like when it adds
    a notice: the body changed, so the page is re-parsed, and the notices
    that were already there come round again. Reusing one hash would
    instead violate the (source, content_hash) constraint.
    """
    from itertools import count
    from pathlib import Path

    from scraping.snapshots import SnapshotResult, storable_text

    html = storable_text(
        (Path(__file__).parent / "fixtures" / "upsc_whats_new.html").read_text(
            encoding="utf-8", errors="replace"
        )
    )
    counter = count(1)

    def fake(_source):
        snapshot = Snapshot.objects.create(
            source=_source, url=_source.url,
            content_hash=f"{next(counter):064d}",
            content=html, status_code=200,
        )
        return SnapshotResult(snapshot=snapshot, changed=changed)

    monkeypatch.setattr("scraping.tasks.fetch_and_store", fake)


def test_a_changed_page_is_parsed_and_its_observations_routed(monkeypatch):
    """End to end on the real captured page: nothing is configured, so
    every observation should land in triage rather than vanish."""
    from scraping.models import TriageItem

    source = _upsc_source()
    _serve_real_page(monkeypatch, source)

    result = scrape_source(source.pk)

    assert result["parsed"] is True
    assert result["observations"] > 0
    assert result["queued_for_triage"] == result["observations"]
    assert TriageItem.objects.count() == result["observations"]


def test_a_configured_exam_is_linked_instead_of_queued(monkeypatch):
    from exams.models import Exam, ExamStage
    from scraping.models import TriageItem

    source = _upsc_source()
    exam = Exam.objects.create(
        board=source.board, code="CSE", name="Civil Services Examination",
        cycle_year=2026, category="x",
    )
    ExamStage.objects.create(exam=exam, stage_type=ExamStage.StageType.MAINS, sequence=2)
    _serve_real_page(monkeypatch, source)

    result = scrape_source(source.pk)

    assert result["linked"] >= 1
    assert TriageItem.objects.count() == result["queued_for_triage"]


def test_an_unchanged_page_is_not_reparsed(monkeypatch):
    """EXT-042's short-circuit: most polls find nothing new, and
    re-parsing an identical page would re-run the matcher over
    observations already dealt with."""
    from scraping.models import TriageItem

    source = _upsc_source()
    _serve_real_page(monkeypatch, source, changed=False)

    result = scrape_source(source.pk)

    assert result["parsed"] is False
    assert not TriageItem.objects.exists()


def test_a_parser_key_naming_nothing_does_not_fail_the_run(monkeypatch):
    """Source.parser_key is free text by design, so this is a config
    mistake fixed in admin - not a reason to lose a stored snapshot."""
    source = _upsc_source()
    source.parser_key = "no_such_parser"
    source.save()
    _serve_real_page(monkeypatch, source)

    result = scrape_source(source.pk)

    assert result["status"] == "ok"
    assert result["parsed"] is False
    assert result["parser_missing"] == "no_such_parser"


def test_a_parser_that_raises_does_not_fail_the_run(monkeypatch):
    source = _upsc_source()
    _serve_real_page(monkeypatch, source)

    class Exploding:
        def parse(self, html):
            raise ValueError("board redesigned its markup")

    monkeypatch.setattr("scraping.tasks.get_parser", lambda key: Exploding())

    result = scrape_source(source.pk)

    assert result["status"] == "ok", "the snapshot is already stored; losing it loses the evidence"
    assert result["parsed"] is False
    assert result["parser_failed"]


def test_notices_that_persist_across_page_changes_do_not_multiply_queue_items(monkeypatch):
    """A board adding one notice re-parses the whole page, so every
    unresolved notice already there comes round again. Without the
    fingerprint the queue would grow by a full page every time the board
    posted anything."""
    from scraping.models import TriageItem

    source = _upsc_source()
    _serve_real_page(monkeypatch, source)

    first = scrape_source(source.pk)
    scrape_source(source.pk)

    assert TriageItem.objects.count() == first["observations"]
    assert TriageItem.objects.filter(times_seen=2).count() == first["observations"]
