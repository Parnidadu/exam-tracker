"""EXT-054: the verifier-facing discrepancy API."""

import pytest
from rest_framework.test import APIClient

from accounts.models import Role, User
from exams.models import Board, Discrepancy, Exam, ExamStage

pytestmark = pytest.mark.django_db
T = Discrepancy.Type
S = Discrepancy.Status
Sev = Discrepancy.Severity

LIST = "/api/discrepancies/"


@pytest.fixture
def stage():
    board = Board.objects.create(
        name="Union Public Service Commission", code="UPSC", official_url="https://u"
    )
    exam = Exam.objects.create(
        board=board, code="CSE", name="Civil Services Examination", cycle_year=2026, category="x"
    )
    return ExamStage.objects.create(exam=exam, stage_type=ExamStage.StageType.PRELIMS, sequence=1)


def user(role, email):
    return User.objects.create_user(email=email, password="x", role=role)


@pytest.fixture
def verifier():
    return user(Role.VERIFIER, "verifier@example.gov.in")


@pytest.fixture
def client(verifier):
    api = APIClient()
    api.force_authenticate(verifier)
    return api


def payload(stage, **overrides):
    return {
        "exam_stage": stage.pk,
        "discrepancy_type": T.POSTPONEMENT,
        "severity": Sev.MEDIUM,
        "description": "Postponed by two weeks per the board's notice.",
        "evidence_url": "https://www.upsc.gov.in/whats-new/notice",
        **overrides,
    }


def open_one(client, stage, **overrides):
    response = client.post(LIST, payload(stage, **overrides), format="json")
    assert response.status_code == 201, response.data
    return response.data


# --- a verifier can open one -------------------------------------------


def test_a_verifier_can_open_a_discrepancy(client, stage):
    data = open_one(client, stage)

    assert data["status"] == S.REPORTED
    assert data["discrepancy_type"] == T.POSTPONEMENT
    assert data["is_open"] is True
    assert Discrepancy.objects.count() == 1


def test_the_reporter_is_taken_from_the_session_not_the_payload(client, stage, verifier):
    """Who filed a claim like this is not something the client gets to
    assert."""
    impostor = user(Role.ADMIN, "someone.else@example.gov.in")

    data = open_one(client, stage, reported_by=impostor.pk)

    assert data["reported_by"] == verifier.email


def test_the_response_names_the_exam_so_a_list_is_readable(client, stage):
    data = open_one(client, stage)

    assert data["exam_name"] == "Civil Services Examination"
    assert data["board_code"] == "UPSC"
    assert data["stage_type"] == "prelims"


# --- with a required evidence URL --------------------------------------


def test_opening_without_an_evidence_url_is_refused(client, stage):
    """A person filing "the paper leaked" without saying where that came
    from is filing a rumour."""
    body = payload(stage, discrepancy_type=T.PAPER_LEAK, severity=Sev.CRITICAL)
    del body["evidence_url"]

    response = client.post(LIST, body, format="json")

    assert response.status_code == 400
    assert "evidence_url" in response.data
    assert not Discrepancy.objects.exists()


@pytest.mark.parametrize("value", ["", "   ", "not-a-url"])
def test_a_blank_or_malformed_evidence_url_is_refused(client, stage, value):
    response = client.post(LIST, payload(stage, evidence_url=value), format="json")

    assert response.status_code == 400
    assert "evidence_url" in response.data


def test_the_evidence_url_cannot_be_blanked_by_an_update(client, stage):
    data = open_one(client, stage)

    response = client.patch(f"{LIST}{data['id']}/", {"evidence_url": ""}, format="json")

    assert response.status_code == 400
    assert "evidence_url" in response.data


# --- a verifier can update one -----------------------------------------


def test_a_verifier_can_correct_the_details(client, stage):
    data = open_one(client, stage)

    response = client.patch(
        f"{LIST}{data['id']}/",
        {
            "severity": Sev.HIGH,
            "description": "Postponed by six weeks, not two.",
            "evidence_note": "Corrigendum dated 20.01.2026.",
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["severity"] == Sev.HIGH
    assert response.data["description"] == "Postponed by six weeks, not two."


def test_an_update_cannot_walk_the_lifecycle_sideways(client, stage):
    """A PATCH that happens to include `status` must not step past the
    transition rules by accident."""
    data = open_one(client, stage)

    response = client.patch(f"{LIST}{data['id']}/", {"status": S.RESOLVED}, format="json")

    assert response.status_code == 200
    assert response.data["status"] == S.REPORTED
    assert Discrepancy.objects.get().status == S.REPORTED


# --- a verifier can resolve one ----------------------------------------


def transition(client, discrepancy_id, **body):
    return client.post(f"{LIST}{discrepancy_id}/transition/", body, format="json")


def test_a_verifier_can_confirm_then_resolve(client, stage, verifier):
    data = open_one(client, stage)

    confirmed = transition(client, data["id"], status=S.CONFIRMED)
    assert confirmed.status_code == 200
    assert confirmed.data["status"] == S.CONFIRMED

    resolved = transition(
        client, data["id"], status=S.RESOLVED, resolution_note="Re-conducted on 12 March."
    )

    assert resolved.status_code == 200
    assert resolved.data["status"] == S.RESOLVED
    assert resolved.data["is_open"] is False
    assert resolved.data["resolved_by"] == verifier.email
    assert resolved.data["resolved_at"]


def test_resolving_without_confirming_is_refused_with_the_moves_that_would_work(client, stage):
    """A 400 naming the legal moves, not the model's exception surfacing
    as a 500."""
    data = open_one(client, stage)

    response = transition(client, data["id"], status=S.RESOLVED, resolution_note="done")

    assert response.status_code == 400
    # The message must name the values a client could actually send, not
    # Python enum reprs.
    assert "'confirmed'" in str(response.data["status"])
    assert "Discrepancy.Status" not in str(response.data["status"])
    assert Discrepancy.objects.get().status == S.REPORTED


def test_closing_without_saying_why_is_refused(client, stage):
    data = open_one(client, stage)
    transition(client, data["id"], status=S.CONFIRMED)

    response = transition(client, data["id"], status=S.RESOLVED)

    assert response.status_code == 400
    assert "resolution_note" in response.data


def test_a_claim_that_was_not_real_can_be_dismissed(client, stage):
    data = open_one(client, stage, discrepancy_type=T.PAPER_LEAK, severity=Sev.CRITICAL)

    response = transition(
        client,
        data["id"],
        status=S.DISMISSED,
        resolution_note="Screenshot was from a coaching mock, not the paper.",
    )

    assert response.status_code == 200
    assert response.data["status"] == S.DISMISSED


def test_a_closed_discrepancy_cannot_be_reopened(client, stage):
    data = open_one(client, stage)
    transition(client, data["id"], status=S.CONFIRMED)
    transition(client, data["id"], status=S.RESOLVED, resolution_note="done")

    response = transition(client, data["id"], status=S.CONFIRMED)

    assert response.status_code == 400


def test_resolving_may_carry_the_evidence_for_the_resolution(client, stage):
    """The notice announcing the re-exam, or the order vacating the stay.
    The original is not lost - Discrepancy carries history."""
    data = open_one(client, stage)
    transition(client, data["id"], status=S.CONFIRMED)

    response = transition(
        client,
        data["id"],
        status=S.RESOLVED,
        resolution_note="Re-conducted.",
        evidence_url="https://www.upsc.gov.in/whats-new/re-exam",
    )

    assert response.status_code == 200
    assert response.data["evidence_url"].endswith("/re-exam")
    assert Discrepancy.objects.get().history.count() >= 3


def test_the_response_says_what_may_happen_next(client, stage):
    """So the UI offers exactly the transitions that will succeed rather
    than offering all of them and reporting a failure afterwards."""
    data = open_one(client, stage)
    assert data["available_transitions"] == ["confirmed", "dismissed"]

    confirmed = transition(client, data["id"], status=S.CONFIRMED)
    assert confirmed.data["available_transitions"] == ["dismissed", "resolved"]

    resolved = transition(client, data["id"], status=S.RESOLVED, resolution_note="done")
    assert resolved.data["available_transitions"] == []


# --- listing and filtering ---------------------------------------------


def test_the_list_can_be_narrowed_to_what_is_still_open(client, stage):
    open_item = open_one(client, stage)
    closed = open_one(client, stage, discrepancy_type=T.KEY_ERROR)
    transition(client, closed["id"], status=S.CONFIRMED)
    transition(client, closed["id"], status=S.RESOLVED, resolution_note="Key revised.")

    response = client.get(LIST, {"open": "true"})

    assert [row["id"] for row in response.data["results"]] == [open_item["id"]]


def test_the_list_can_be_filtered_by_type(client, stage):
    open_one(client, stage)
    open_one(client, stage, discrepancy_type=T.KEY_ERROR)

    response = client.get(LIST, {"discrepancy_type": "key_error"})

    assert [row["discrepancy_type"] for row in response.data["results"]] == ["key_error"]


def test_the_list_can_be_filtered_by_status(client, stage):
    open_one(client, stage)
    confirmed = open_one(client, stage, discrepancy_type=T.KEY_ERROR)
    transition(client, confirmed["id"], status=S.CONFIRMED)

    response = client.get(LIST, {"status": "confirmed"})

    assert [row["id"] for row in response.data["results"]] == [confirmed["id"]]


def test_the_list_can_be_scoped_to_one_exam(client, stage):
    open_one(client, stage)

    response = client.get(LIST, {"exam": stage.exam.slug})
    assert response.data["count"] == 1

    assert client.get(LIST, {"exam": "no-such-exam"}).data["count"] == 0


# --- who may see any of this -------------------------------------------


def test_an_unverified_claim_is_not_public(stage, client):
    """A discrepancy starts as `reported` - an unchecked claim naming a
    real board and a real exam. Publishing those would make this a rumour
    mill, which is the opposite of what the app is for."""
    open_one(client, stage, discrepancy_type=T.PAPER_LEAK, severity=Sev.CRITICAL)

    response = APIClient().get(LIST)

    assert response.status_code in (401, 403)


def test_a_viewer_cannot_read_or_open_discrepancies(stage, client):
    open_one(client, stage)
    viewer = APIClient()
    viewer.force_authenticate(user(Role.VIEWER, "viewer@example.gov.in"))

    assert viewer.get(LIST).status_code == 403
    assert viewer.post(LIST, payload(stage), format="json").status_code == 403


def test_an_admin_may_do_everything_a_verifier_can(stage, client):
    data = open_one(client, stage)
    admin = APIClient()
    admin.force_authenticate(user(Role.ADMIN, "admin@example.gov.in"))

    assert admin.get(LIST).status_code == 200
    assert transition(admin, data["id"], status=S.CONFIRMED).status_code == 200


def test_an_anonymous_caller_cannot_transition_anything(stage, client):
    data = open_one(client, stage)

    response = APIClient().post(f"{LIST}{data['id']}/transition/", {"status": S.CONFIRMED})

    assert response.status_code in (401, 403)
    assert Discrepancy.objects.get().status == S.REPORTED
