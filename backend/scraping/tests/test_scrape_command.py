"""EXT-066: the operator command the runbook depends on.

Diagnosing a broken source otherwise means opening a Django shell and
calling the task by hand, which is not a step a second person can follow
unaided.
"""

import pytest
from django.core.management import CommandError, call_command

from exams.models import Board
from scraping.models import Snapshot, Source
from scraping.snapshots import SnapshotResult

pytestmark = pytest.mark.django_db


@pytest.fixture
def source():
    board = Board.objects.create(
        name="Union Public Service Commission", code="UPSC", official_url="https://u"
    )
    return Source.objects.create(
        board=board,
        name="UPSC what's new",
        url="https://www.upsc.gov.in/",
        parser_key="upsc_whats_new",
    )


@pytest.fixture
def no_network(monkeypatch):
    def fake(src):
        snapshot = Snapshot.objects.create(
            source=src, url=src.url, content_hash="c" * 64,
            content="<html></html>", status_code=200,
        )
        return SnapshotResult(snapshot=snapshot, changed=True)

    monkeypatch.setattr("scraping.tasks.fetch_and_store", fake)


def test_it_lists_sources_with_their_health(source, capsys):
    """Step one of the runbook: which source, and what state is it in."""
    call_command("scrape_source", "--list")

    out = capsys.readouterr().out
    assert str(source.pk) in out
    assert "UPSC" in out
    assert "never run" in out


def test_the_list_marks_a_paused_source(source, capsys):
    source.enabled = False
    source.save()

    call_command("scrape_source", "--list")

    assert "PAUSED" in capsys.readouterr().out


def test_running_it_bare_lists_rather_than_erroring(source, capsys):
    """Someone who does not know the id yet should get the ids, not a
    usage error."""
    call_command("scrape_source")

    assert str(source.pk) in capsys.readouterr().out


def test_it_scrapes_and_prints_the_outcome(source, no_network, capsys):
    call_command("scrape_source", str(source.pk))

    out = capsys.readouterr().out
    assert "status" in out
    assert "ok" in out


def test_an_unknown_id_says_how_to_find_the_right_one(source):
    """An error that tells the reader their next move, rather than a
    traceback."""
    with pytest.raises(CommandError, match="--list"):
        call_command("scrape_source", "9999")


def test_a_failing_scrape_is_reported_not_hidden(source, monkeypatch, capsys):
    from scraping.fetch import FetchFailed

    def boom(_src):
        raise FetchFailed("502 from the board")

    monkeypatch.setattr("scraping.tasks.fetch_and_store", boom)

    call_command("scrape_source", str(source.pk))

    out = capsys.readouterr().out
    assert "failed" in out
    assert "502 from the board" in out


def test_it_says_so_when_nothing_is_configured(capsys):
    call_command("scrape_source", "--list")

    assert "No sources configured" in capsys.readouterr().out
