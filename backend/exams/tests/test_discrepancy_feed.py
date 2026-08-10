"""EXT-055: the public discrepancy feed, and what it refuses to show."""

import pytest
from rest_framework.test import APIClient

from accounts.models import Role, User
from exams.models import Board, Discrepancy, Exam, ExamStage

pytestmark = pytest.mark.django_db
T = Discrepancy.Type
S = Discrepancy.Status
Sev = Discrepancy.Severity

FEED = "/api/discrepancy-feed/"


@pytest.fixture
def board():
    return Board.objects.create(
        name="Union Public Service Commission", code="UPSC", official_url="https://u"
    )


@pytest.fixture
def stage(board):
    exam = Exam.objects.create(
        board=board, code="CSE", name="Civil Services Examination", cycle_year=2026, category="x"
    )
    return ExamStage.objects.create(exam=exam, stage_type=ExamStage.StageType.PRELIMS, sequence=1)


@pytest.fixture
def verifier():
    return User.objects.create_user(
        email="verifier@example.gov.in", password="x", role=Role.VERIFIER
    )


def make(stage, verifier, status=S.REPORTED, **kwargs):
    """Builds a discrepancy already in the state under test.

    Walked through the real transitions rather than written straight to
    the target status, so these fixtures cannot describe a state the
    lifecycle would never produce.
    """
    discrepancy = Discrepancy.objects.create(
        exam_stage=stage,
        discrepancy_type=kwargs.pop("discrepancy_type", T.POSTPONEMENT),
        severity=kwargs.pop("severity", Sev.MEDIUM),
        description=kwargs.pop("description", "Postponed by two weeks."),
        evidence_url="https://www.upsc.gov.in/whats-new/notice",
        reported_by=verifier,
        **kwargs,
    )
    if status == S.REPORTED:
        return discrepancy

    if status in {S.CONFIRMED, S.RESOLVED}:
        discrepancy.status = S.CONFIRMED
        discrepancy.save()
    if status == S.RESOLVED:
        discrepancy.status = S.RESOLVED
        discrepancy.resolution_note = "Re-conducted on 12 March."
        discrepancy.resolved_by = verifier
        discrepancy.save()
    if status == S.DISMISSED:
        discrepancy.status = S.DISMISSED
        discrepancy.resolution_note = "Screenshot was from a coaching mock."
        discrepancy.resolved_by = verifier
        discrepancy.save()
    return discrepancy


def feed(client=None):
    return (client or APIClient()).get(FEED)


# --- only resolved or confirmed are public -----------------------------


@pytest.mark.parametrize("status", [S.CONFIRMED, S.RESOLVED])
def test_a_checked_discrepancy_is_public(stage, verifier, status):
    discrepancy = make(stage, verifier, status=status)

    response = feed()

    assert response.status_code == 200
    assert [row["id"] for row in response.data["results"]] == [discrepancy.pk]


def test_an_unchecked_claim_stays_internal(stage, verifier):
    """`reported` is an allegation nobody has verified, naming a real
    board and a real exam. Publishing it is how this becomes the rumour
    mill it exists to replace."""
    make(stage, verifier, status=S.REPORTED, discrepancy_type=T.PAPER_LEAK, severity=Sev.CRITICAL)

    assert feed().data["results"] == []


def test_a_dismissed_claim_stays_internal(stage, verifier):
    """Someone checked this one and found it untrue. Republishing "we
    looked into the leak allegation" is how a rumour outlives its own
    correction."""
    make(stage, verifier, status=S.DISMISSED, discrepancy_type=T.PAPER_LEAK)

    assert feed().data["results"] == []


def test_the_feed_shows_only_the_checked_ones_when_all_four_exist(stage, verifier):
    reported = make(stage, verifier, status=S.REPORTED)
    confirmed = make(stage, verifier, status=S.CONFIRMED, discrepancy_type=T.KEY_ERROR)
    resolved = make(stage, verifier, status=S.RESOLVED, discrepancy_type=T.RE_EXAM)
    dismissed = make(stage, verifier, status=S.DISMISSED, discrepancy_type=T.PAPER_LEAK)

    visible = {row["id"] for row in feed().data["results"]}

    assert visible == {confirmed.pk, resolved.pk}
    assert reported.pk not in visible
    assert dismissed.pk not in visible


def test_the_rule_has_one_definition(stage, verifier):
    """The feed reads Discrepancy.PUBLIC rather than repeating the list,
    so there is exactly one answer to "is this public"."""
    assert Discrepancy.PUBLIC == {S.CONFIRMED, S.RESOLVED}


def test_a_caller_cannot_ask_for_drafts(stage, verifier):
    """There is no `status` filter on this endpoint on purpose - a query
    parameter that could widen visibility is a visibility rule with a
    loophole."""
    make(stage, verifier, status=S.REPORTED, discrepancy_type=T.PAPER_LEAK)

    for params in ({"status": "reported"}, {"status": "dismissed"}, {"status__in": "reported"}):
        assert APIClient().get(FEED, params).data["results"] == []


def test_confirming_makes_one_appear_without_waiting_for_the_cache(
    stage, verifier, django_capture_on_commit_callbacks
):
    """The public reads are cached (EXT-034). Without busting that on a
    transition, a confirmed postponement stays invisible for a full TTL -
    the one moment a candidate checking whether their exam moved cares.

    The callbacks are executed explicitly because the bump is deferred to
    commit on purpose: retiring the cache inside the transaction would
    throw it away even on a rollback, and a concurrent read could then
    repopulate it from pre-commit state and look fresh for a full TTL.
    """
    discrepancy = make(stage, verifier, status=S.REPORTED)
    assert feed().data["results"] == [], "nothing to see yet, and now cached"

    staff = APIClient()
    staff.force_authenticate(verifier)
    with django_capture_on_commit_callbacks(execute=True):
        staff.post(f"/api/discrepancies/{discrepancy.pk}/transition/", {"status": S.CONFIRMED})

    assert [row["id"] for row in feed().data["results"]] == [discrepancy.pk]


def test_dismissing_removes_one_from_the_feed(
    stage, verifier, django_capture_on_commit_callbacks
):
    """A claim withdrawn must actually disappear, not linger in a cached
    page until the TTL happens to lapse."""
    discrepancy = make(stage, verifier, status=S.CONFIRMED, discrepancy_type=T.PAPER_LEAK)
    assert feed().data["results"]

    staff = APIClient()
    staff.force_authenticate(verifier)
    with django_capture_on_commit_callbacks(execute=True):
        staff.post(
            f"/api/discrepancies/{discrepancy.pk}/transition/",
            {"status": S.DISMISSED, "resolution_note": "Not substantiated."},
            format="json",
        )

    assert feed().data["results"] == []


# --- what the feed says ------------------------------------------------


def test_the_feed_names_the_exam_and_links_the_evidence(stage, verifier):
    make(stage, verifier, status=S.CONFIRMED)

    row = feed().data["results"][0]

    assert row["exam_name"] == "Civil Services Examination"
    assert row["board_code"] == "UPSC"
    assert row["stage_type"] == "prelims"
    assert row["exam_slug"]
    assert row["evidence_url"].startswith("https://")


def test_the_type_arrives_ready_to_display(stage, verifier):
    make(stage, verifier, status=S.CONFIRMED, discrepancy_type=T.KEY_ERROR)

    row = feed().data["results"][0]

    assert row["discrepancy_type"] == "key_error"
    assert row["type_label"] == "Answer key error"


def test_a_resolved_one_says_what_was_done_about_it(stage, verifier):
    """For a resolved discrepancy this is the part a candidate actually
    needs - "re-conducted on 12 March" is more use than the bare fact that
    something went wrong."""
    make(stage, verifier, status=S.RESOLVED)

    row = feed().data["results"][0]

    assert row["resolution_note"] == "Re-conducted on 12 March."
    assert row["resolved_at"]


def test_no_staff_identity_reaches_the_public(stage, verifier):
    """A discrepancy is a more contentious record than a verification, and
    naming the person who filed it serves nobody the feed exists for."""
    make(stage, verifier, status=S.RESOLVED)

    row = feed().data["results"][0]

    assert "reported_by" not in row
    assert "resolved_by" not in row
    assert verifier.email not in str(row)


def test_newest_first(stage, verifier):
    older = make(stage, verifier, status=S.CONFIRMED)
    newer = make(stage, verifier, status=S.CONFIRMED, discrepancy_type=T.KEY_ERROR)

    assert [row["id"] for row in feed().data["results"]] == [newer.pk, older.pk]


# --- filtering ---------------------------------------------------------


def test_the_feed_can_be_scoped_to_one_exam(stage, verifier, board):
    make(stage, verifier, status=S.CONFIRMED)
    other_exam = Exam.objects.create(
        board=board, code="CDS", name="Combined Defence Services", cycle_year=2026, category="x"
    )
    other_stage = ExamStage.objects.create(
        exam=other_exam, stage_type=ExamStage.StageType.SINGLE, sequence=1
    )
    make(other_stage, verifier, status=S.CONFIRMED)

    response = APIClient().get(FEED, {"exam": stage.exam.slug})

    assert response.data["count"] == 1
    assert response.data["results"][0]["exam_slug"] == stage.exam.slug


def test_the_feed_can_be_filtered_by_type(stage, verifier):
    make(stage, verifier, status=S.CONFIRMED)
    make(stage, verifier, status=S.CONFIRMED, discrepancy_type=T.COURT_STAY)

    response = APIClient().get(FEED, {"discrepancy_type": "court_stay"})

    assert [row["discrepancy_type"] for row in response.data["results"]] == ["court_stay"]


def test_filtering_still_cannot_reveal_a_draft(stage, verifier):
    make(stage, verifier, status=S.REPORTED, discrepancy_type=T.COURT_STAY)

    response = APIClient().get(FEED, {"discrepancy_type": "court_stay"})

    assert response.data["results"] == []


# --- who may read it ---------------------------------------------------


def test_the_feed_is_open_to_anyone(stage, verifier):
    """Unlike the verifier's own list, which needs a role - this is the
    half of the record the public is meant to have."""
    make(stage, verifier, status=S.CONFIRMED)

    assert feed().status_code == 200


def test_a_signed_in_verifier_sees_the_same_public_feed(stage, verifier):
    """The feed is not a second, richer view for staff - they have their
    own endpoint. A shared cache key would otherwise serve one audience's
    response to the other."""
    make(stage, verifier, status=S.REPORTED, discrepancy_type=T.PAPER_LEAK)
    make(stage, verifier, status=S.CONFIRMED)

    staff = APIClient()
    staff.force_authenticate(verifier)

    assert len(feed(staff).data["results"]) == 1
