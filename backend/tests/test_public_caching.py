"""EXT-034: short-TTL caching on public reads, busted by verification writes."""

import json
from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from accounts.models import Role, User
from config.caching import bump_public_cache_version, public_cache_version
from exams.models import Board, Exam, ExamStage, StatusTrack
from verification.models import VerificationRecord


@pytest.fixture
def board(db):
    return Board.objects.create(
        name="Union Public Service Commission",
        code="UPSC",
        official_url="https://upsc.gov.in",
        timezone="Asia/Kolkata",
    )


@pytest.fixture
def exam(board):
    return Exam.objects.create(
        board=board, code="CSE", name="Civil Services", cycle_year=2026, category="c"
    )


@pytest.fixture
def stage(exam):
    return ExamStage.objects.create(
        exam=exam, stage_type=ExamStage.StageType.PRELIMS, sequence=1
    )


@pytest.fixture
def verifier(db):
    return User.objects.create_user(
        email="verifier@example.com", password="pw", role=Role.VERIFIER
    )


@pytest.mark.django_db
def test_public_read_is_cached(client, exam):
    assert client.get("/api/exams/")["X-Cache"] == "MISS"
    assert client.get("/api/exams/")["X-Cache"] == "HIT"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    ["/api/exams/", "/api/boards/", "/api/calendar/"],
)
def test_each_public_endpoint_is_cached(client, exam, path):
    assert client.get(path)["X-Cache"] == "MISS"
    assert client.get(path)["X-Cache"] == "HIT"


@pytest.mark.django_db
def test_exam_detail_is_cached(client, exam):
    url = f"/api/exams/{exam.slug}/"
    assert client.get(url)["X-Cache"] == "MISS"
    assert client.get(url)["X-Cache"] == "HIT"


@pytest.mark.django_db
def test_a_cached_response_serves_the_same_body(client, exam):
    first = client.get("/api/exams/")
    second = client.get("/api/exams/")
    assert second["X-Cache"] == "HIT"
    assert json.loads(second.content) == json.loads(first.content)


@pytest.mark.django_db
def test_different_query_parameters_are_cached_separately(client, exam, board):
    Exam.objects.create(
        board=board, code="CDS", name="Defence", cycle_year=2026, category="d"
    )
    client.get("/api/exams/", {"search": "civil"})

    # A different filter must not be served the previous filter's results.
    other = client.get("/api/exams/", {"search": "defence"})
    assert other["X-Cache"] == "MISS"
    assert [e["code"] for e in json.loads(other.content)["results"]] == ["CDS"]


@pytest.mark.django_db
def test_parameter_order_does_not_split_the_cache(client, exam):
    client.get("/api/exams/?board=UPSC&search=civil")
    repeat = client.get("/api/exams/?search=civil&board=UPSC")
    assert repeat["X-Cache"] == "HIT"


@pytest.mark.django_db
def test_verification_write_invalidates_the_cache(
    client, stage, verifier, django_capture_on_commit_callbacks
):
    assert client.get("/api/exams/")["X-Cache"] == "MISS"
    assert client.get("/api/exams/")["X-Cache"] == "HIT"

    client.force_login(verifier)
    # Invalidation deliberately runs via transaction.on_commit, which never
    # fires under the test's rolled-back transaction unless captured here.
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            f"/api/stages/{stage.pk}/verify/",
            data=json.dumps({"track": "conduct", "value": "conducted"}),
            content_type="application/json",
        )
    assert response.status_code == 201

    client.logout()
    assert client.get("/api/exams/")["X-Cache"] == "MISS"


@pytest.mark.django_db
def test_a_verification_write_is_visible_immediately_not_after_the_ttl(
    client, exam, stage, verifier, django_capture_on_commit_callbacks
):
    """The point of invalidation: a freshly verified status shows up on the
    next public read, not up to a TTL later."""
    url = f"/api/exams/{exam.slug}/"
    before = json.loads(client.get(url).content)
    assert before["stages"][0]["status_tracks"] == []

    client.force_login(verifier)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(
            f"/api/stages/{stage.pk}/verify/",
            data=json.dumps({"track": "conduct", "value": "conducted"}),
            content_type="application/json",
        )
    client.logout()

    after = json.loads(client.get(url).content)
    assert after["stages"][0]["status_tracks"][0]["human_value"] == "conducted"


@pytest.mark.django_db
def test_history_cache_does_not_leak_the_actor_to_anonymous_visitors(
    client, exam, stage, verifier
):
    """EXT-027 hides the actor from anonymous callers. A shared cache entry
    would hand a signed-in response to the next anonymous reader."""
    VerificationRecord.objects.create(
        exam_stage=stage,
        track=StatusTrack.Track.CONDUCT,
        value="conducted",
        actor=verifier,
    )
    url = f"/api/exams/{exam.slug}/verifications/"

    client.force_login(verifier)
    signed_in = json.loads(client.get(url).content)["results"][0]
    assert signed_in["actor"] == verifier.email

    client.logout()
    anonymous = json.loads(client.get(url).content)["results"][0]
    assert anonymous["actor"] is None


@pytest.mark.django_db
def test_history_cache_does_not_hide_the_actor_from_signed_in_users(
    client, exam, stage, verifier
):
    """The same leak in the other direction: an anonymous response must not
    be served to a signed-in verifier."""
    VerificationRecord.objects.create(
        exam_stage=stage,
        track=StatusTrack.Track.CONDUCT,
        value="conducted",
        actor=verifier,
    )
    url = f"/api/exams/{exam.slug}/verifications/"

    assert json.loads(client.get(url).content)["results"][0]["actor"] is None

    client.force_login(verifier)
    assert json.loads(client.get(url).content)["results"][0]["actor"] == verifier.email


@pytest.mark.django_db
def test_write_requests_are_never_cached(client, stage, verifier):
    client.force_login(verifier)
    for _ in range(2):
        response = client.post(
            f"/api/stages/{stage.pk}/verify/",
            data=json.dumps({"track": "conduct", "value": "conducted"}),
            content_type="application/json",
        )
        assert response.status_code == 201
        assert response.get("X-Cache") != "HIT"

    # Both writes landed; the second was not short-circuited by a cache hit.
    assert VerificationRecord.objects.count() == 2


@pytest.mark.django_db
def test_errors_are_not_cached(client):
    """A 404 must not stick around for a TTL after the exam is created."""
    assert client.get("/api/exams/does-not-exist/").status_code == 404
    assert cache.get("public:1:anon:/api/exams/does-not-exist/?") is None


@pytest.mark.django_db
def test_ttl_is_short(settings):
    assert settings.PUBLIC_CACHE_TTL <= 60


def test_bumping_the_version_changes_it():
    start = public_cache_version()
    assert bump_public_cache_version() == start + 1
    assert public_cache_version() == start + 1


@pytest.mark.django_db
def test_stale_verification_still_resolves_correctly_through_the_cache(
    client, exam, stage
):
    """Caching must not freeze effective_status across the staleness
    boundary in a way that outlives the TTL."""
    StatusTrack.objects.create(
        exam_stage=stage,
        track=StatusTrack.Track.CONDUCT,
        machine_value="conducted",
        human_value="postponed",
        verified_at=timezone.now() - StatusTrack.STALENESS_WINDOW - timedelta(days=1),
    )
    body = json.loads(client.get(f"/api/exams/{exam.slug}/").content)
    assert body["stages"][0]["status_tracks"][0]["effective_status"] == "conducted"