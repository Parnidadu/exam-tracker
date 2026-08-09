"""Raw snapshot storage with content hashing.

Parsing a board's notice page is the expensive part of a scrape run, and
most runs find nothing new. Hashing the raw body first means an unchanged
page is recognised and dropped before a parser ever sees it.
"""

import hashlib
from dataclasses import dataclass

import requests
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .fetch import FetchResult, fetch
from .models import Snapshot, Source


def content_hash(content: bytes) -> str:
    """sha256 of the raw bytes.

    Hashing bytes rather than decoded text on purpose: decoding with
    errors="replace" is lossy, so two different responses could otherwise
    hash identically and a real change would be missed.
    """
    return hashlib.sha256(content).hexdigest()


@dataclass(frozen=True)
class SnapshotResult:
    snapshot: Snapshot
    #: False when this exact body has been stored before.
    changed: bool

    @property
    def should_parse(self) -> bool:
        """Whether a parser needs to run. This is the short-circuit."""
        return self.changed


@transaction.atomic
def store_snapshot(source: Source, result: FetchResult) -> SnapshotResult:
    """Record a fetched body, or note that it is unchanged.

    Returns a SnapshotResult whose `should_parse` is False when the body
    matches one already stored for this source, so callers can skip
    parsing without comparing anything themselves.
    """
    digest = content_hash(result.content)

    # select_for_update so two workers fetching the same source cannot both
    # decide the content is new and race to insert it.
    existing = (
        Snapshot.objects.select_for_update()
        .filter(source=source, content_hash=digest)
        .first()
    )

    if existing is not None:
        # F() so the counter is incremented in SQL. Reading the value and
        # writing back current+1 would lose increments when two workers
        # poll the same source at once.
        Snapshot.objects.filter(pk=existing.pk).update(
            last_seen_at=timezone.now(),
            times_seen=F("times_seen") + 1,
            status_code=result.status_code,
        )
        existing.refresh_from_db()
        return SnapshotResult(snapshot=existing, changed=False)

    snapshot = Snapshot.objects.create(
        source=source,
        url=result.url,
        content_hash=digest,
        content=result.text,
        status_code=result.status_code,
    )
    return SnapshotResult(snapshot=snapshot, changed=True)


def fetch_and_store(
    source: Source, session: requests.Session | None = None
) -> SnapshotResult:
    """Fetch a source politely (EXT-041) and store the result.

    The natural place the short-circuit is consumed: callers act on
    `result.should_parse` rather than diffing HTML themselves.
    """
    return store_snapshot(source, fetch(source.url, session=session))


def latest_snapshot(source: Source) -> Snapshot | None:
    return Snapshot.objects.filter(source=source).order_by("-last_seen_at", "-id").first()
