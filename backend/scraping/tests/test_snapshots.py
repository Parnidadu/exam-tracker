"""EXT-042: raw HTML stored with a hash; unchanged content short-circuits."""

import hashlib
from unittest.mock import patch

import pytest
import requests

from scraping.fetch import FetchResult
from scraping.models import Snapshot
from scraping.snapshots import (
    content_hash,
    fetch_and_store,
    latest_snapshot,
    store_snapshot,
)

HTML = "<html><body><h1>UPSC notices</h1></body></html>"


def result(body: str = HTML, status: int = 200, url: str = "https://upsc.test/notices"):
    return FetchResult(
        url=url, status_code=status, content=body.encode(), headers={}
    )


# --- storage and hashing ----------------------------------------------


@pytest.mark.django_db
def test_the_raw_html_is_stored_with_its_hash(source):
    stored = store_snapshot(source, result()).snapshot

    assert stored.content == HTML
    assert stored.content_hash == hashlib.sha256(HTML.encode()).hexdigest()
    assert stored.status_code == 200
    assert stored.url == "https://upsc.test/notices"


def test_the_hash_is_over_raw_bytes_not_decoded_text():
    """Decoding uses errors='replace', which is lossy - two different
    responses could otherwise hash the same and a real change be missed."""
    a = b"\xff\xfe invalid one"
    b = b"\xfe\xff invalid two"

    assert a.decode("utf-8", errors="replace") != b.decode("utf-8", errors="replace")
    assert content_hash(a) != content_hash(b)


@pytest.mark.django_db
def test_different_content_creates_a_new_snapshot(source):
    first = store_snapshot(source, result("<p>one</p>"))
    second = store_snapshot(source, result("<p>two</p>"))

    assert first.changed is True
    assert second.changed is True
    assert Snapshot.objects.filter(source=source).count() == 2


# --- the short-circuit ------------------------------------------------


@pytest.mark.django_db
def test_unchanged_content_reports_that_parsing_can_be_skipped(source):
    store_snapshot(source, result())
    repeat = store_snapshot(source, result())

    assert repeat.changed is False
    assert repeat.should_parse is False


@pytest.mark.django_db
def test_changed_content_reports_that_parsing_is_needed(source):
    store_snapshot(source, result("<p>old</p>"))
    changed = store_snapshot(source, result("<p>new</p>"))

    assert changed.should_parse is True


@pytest.mark.django_db
def test_unchanged_content_does_not_pile_up_duplicate_rows(source):
    """The store's size should track how often a board changes, not how
    often it is polled."""
    for _ in range(5):
        store_snapshot(source, result())

    assert Snapshot.objects.filter(source=source).count() == 1


@pytest.mark.django_db
def test_an_unchanged_fetch_still_records_that_the_source_was_reachable(source):
    first = store_snapshot(source, result()).snapshot
    original_first_seen = first.first_seen_at

    repeat = store_snapshot(source, result()).snapshot

    assert repeat.times_seen == 2
    # first_seen_at marks when this body appeared, so it must not move.
    assert repeat.first_seen_at == original_first_seen
    assert repeat.last_seen_at >= original_first_seen


@pytest.mark.django_db
def test_seeing_the_same_body_repeatedly_counts_each_time(source):
    for _ in range(3):
        store_snapshot(source, result())

    assert Snapshot.objects.get(source=source).times_seen == 3


@pytest.mark.django_db
def test_content_returning_after_a_change_is_recognised_not_re_stored(source):
    """A page that flips back to a previous body must match the row it had
    before rather than inserting a third copy."""
    store_snapshot(source, result("<p>A</p>"))
    store_snapshot(source, result("<p>B</p>"))
    back_to_a = store_snapshot(source, result("<p>A</p>"))

    assert back_to_a.changed is False
    assert Snapshot.objects.filter(source=source).count() == 2


# --- per-source isolation ---------------------------------------------


@pytest.mark.django_db
def test_identical_content_from_two_sources_is_stored_separately(source, board):
    from scraping.models import Source

    other = Source.objects.create(
        board=board, name="other", url="https://ssc.test/n", parser_key="k"
    )

    assert store_snapshot(source, result()).changed is True
    # Same bytes, different source: this one has never seen it before.
    assert store_snapshot(other, result()).changed is True
    assert Snapshot.objects.count() == 2


# --- integration with the fetch layer ---------------------------------


@pytest.mark.django_db
def test_fetch_and_store_short_circuits_on_the_second_poll(source, settings):
    settings.SCRAPER_RATE_LIMIT_SECONDS = 0

    def fake_get(url, *args, **kwargs):
        class R:
            status_code = 200
            text = "User-agent: *\nAllow: /\n" if url.endswith("robots.txt") else HTML
            content = text.encode()
            headers: dict[str, str] = {}

        return R()

    with patch.object(requests.Session, "get", side_effect=fake_get):
        first = fetch_and_store(source)
        second = fetch_and_store(source)

    assert first.should_parse is True
    assert second.should_parse is False
    assert Snapshot.objects.filter(source=source).count() == 1


# --- helpers ----------------------------------------------------------


@pytest.mark.django_db
def test_latest_snapshot_returns_the_most_recently_seen(source):
    store_snapshot(source, result("<p>old</p>"))
    newest = store_snapshot(source, result("<p>new</p>")).snapshot

    assert latest_snapshot(source) == newest


@pytest.mark.django_db
def test_latest_snapshot_is_none_before_anything_is_stored(source):
    assert latest_snapshot(source) is None


@pytest.mark.django_db
def test_deleting_a_source_removes_its_snapshots(source):
    store_snapshot(source, result())
    source.delete()

    assert Snapshot.objects.count() == 0


# --- EXT-051: real boards serve bytes Postgres will not store ---------


def _bytes_result(source, body: bytes) -> FetchResult:
    return FetchResult(url=source.url, status_code=200, content=body, headers={})


@pytest.mark.django_db
def test_a_body_containing_nul_bytes_is_stored_rather_than_exploding(source):
    """The captured UPSC page really does contain NUL bytes. Postgres
    rejects them in a text column, so storing the body verbatim made that
    board fail on every poll - permanently, since an unexpected error is
    recorded as a failure and re-raised."""
    body = b"<html><body>before\x00after</body></html>"

    result = store_snapshot(source, _bytes_result(source, body))

    assert result.changed
    assert "\x00" not in result.snapshot.content
    assert "before" in result.snapshot.content and "after" in result.snapshot.content


@pytest.mark.django_db
def test_the_hash_still_sees_a_difference_the_column_cannot_hold(source):
    """Change detection stays faithful to what was served: two bodies
    differing only in NULs are different pages, even though the stored
    text is identical."""
    plain = b"<html>x</html>"
    with_nul = b"<html>x\x00</html>"

    first = store_snapshot(source, _bytes_result(source, plain))
    second = store_snapshot(source, _bytes_result(source, with_nul))

    assert second.changed
    assert first.snapshot.content_hash != second.snapshot.content_hash


@pytest.mark.django_db
def test_the_real_captured_page_can_be_stored(source):
    """Guards the actual regression rather than a synthetic version of
    it: this is the page a live board serves."""
    from pathlib import Path

    body = (
        Path(__file__).parent / "fixtures" / "upsc_whats_new.html"
    ).read_bytes()
    assert b"\x00" in body, "fixture should still be the unmodified capture"

    result = store_snapshot(source, _bytes_result(source, body))

    assert result.snapshot.pk
