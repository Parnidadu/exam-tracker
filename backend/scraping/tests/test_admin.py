"""The acceptance criterion is about admin, so these drive the real admin
views rather than asserting the model in isolation."""

from datetime import timedelta

import pytest
from django.contrib import admin
from django.utils import timezone
from django_celery_beat.models import PeriodicTask

from scraping.admin import SourceHealthAdmin
from scraping.health import health_for, record_failure, record_success
from scraping.models import Source, SourceHealth, TriageItem
from scraping.schedules import schedule_name


def test_source_is_registered_in_admin():
    assert admin.site.is_registered(Source)


@pytest.mark.django_db
def test_staff_can_create_a_source_without_a_deploy(admin_client, board):
    response = admin_client.post(
        "/admin/scraping/source/add/",
        {
            "board": board.pk,
            "name": "UPSC notices",
            "url": "https://upsc.gov.in/whats-new",
            "fetch_strategy": Source.FetchStrategy.HTTP,
            "parser_key": "upsc_notices",
            "cron": "0 */6 * * *",
            "enabled": "on",
        },
    )

    assert response.status_code == 302
    created = Source.objects.get(name="UPSC notices")
    assert created.url == "https://upsc.gov.in/whats-new"
    assert created.cron == "0 */6 * * *"


@pytest.mark.django_db
def test_staff_can_change_the_url_from_admin(admin_client, source):
    response = admin_client.post(
        f"/admin/scraping/source/{source.pk}/change/",
        {
            "board": source.board.pk,
            "name": source.name,
            "url": "https://upsc.gov.in/notifications",
            "fetch_strategy": source.fetch_strategy,
            "parser_key": source.parser_key,
            "cron": source.cron,
            "enabled": "on",
        },
    )

    assert response.status_code == 302
    source.refresh_from_db()
    assert source.url == "https://upsc.gov.in/notifications"


@pytest.mark.django_db
def test_staff_can_change_the_schedule_from_admin(admin_client, source):
    response = admin_client.post(
        f"/admin/scraping/source/{source.pk}/change/",
        {
            "board": source.board.pk,
            "name": source.name,
            "url": source.url,
            "fetch_strategy": source.fetch_strategy,
            "parser_key": source.parser_key,
            "cron": "*/30 * * * *",
            "enabled": "on",
        },
    )

    assert response.status_code == 302
    source.refresh_from_db()
    assert source.cron == "*/30 * * * *"


@pytest.mark.django_db
def test_admin_rejects_a_malformed_schedule_instead_of_saving_it(admin_client, source):
    response = admin_client.post(
        f"/admin/scraping/source/{source.pk}/change/",
        {
            "board": source.board.pk,
            "name": source.name,
            "url": source.url,
            "fetch_strategy": source.fetch_strategy,
            "parser_key": source.parser_key,
            "cron": "every 6 hours",
            "enabled": "on",
        },
    )

    # 200 means the form came back with errors rather than redirecting on save.
    assert response.status_code == 200
    source.refresh_from_db()
    assert source.cron == "0 */6 * * *"


@pytest.mark.django_db
def test_staff_can_disable_a_source_from_admin(admin_client, source):
    response = admin_client.post(
        f"/admin/scraping/source/{source.pk}/change/",
        {
            "board": source.board.pk,
            "name": source.name,
            "url": source.url,
            "fetch_strategy": source.fetch_strategy,
            "parser_key": source.parser_key,
            "cron": source.cron,
            # `enabled` omitted entirely - that is how an unchecked box posts.
        },
    )

    assert response.status_code == 302
    source.refresh_from_db()
    assert source.enabled is False


# --- EXT-046: the admin toggle is what drives Beat ---------------------


@pytest.mark.django_db
def test_creating_an_enabled_source_in_admin_schedules_it(admin_client, board):
    admin_client.post(
        "/admin/scraping/source/add/",
        {
            "board": board.pk,
            "name": "IBPS CRP updates",
            "url": "https://www.ibps.in/index.php/crp-updates/",
            "fetch_strategy": Source.FetchStrategy.HTTP,
            "parser_key": "ibps_crp_updates",
            "cron": "0 */6 * * *",
            "enabled": "on",
        },
    )

    created = Source.objects.get(name="IBPS CRP updates")
    task = PeriodicTask.objects.get(name=schedule_name(created))
    assert task.enabled
    assert task.args == f"[{created.pk}]"


@pytest.mark.django_db
def test_unticking_enabled_in_admin_pauses_the_schedule(admin_client, source):
    admin_client.post(
        f"/admin/scraping/source/{source.pk}/change/",
        {
            "board": source.board.pk,
            "name": source.name,
            "url": source.url,
            "fetch_strategy": source.fetch_strategy,
            "parser_key": source.parser_key,
            "cron": source.cron,
            # `enabled` omitted - an unchecked box posts nothing.
        },
    )

    assert PeriodicTask.objects.get(name=schedule_name(source)).enabled is False


@pytest.mark.django_db
def test_changing_the_cron_in_admin_changes_what_beat_will_run(admin_client, source):
    admin_client.post(
        f"/admin/scraping/source/{source.pk}/change/",
        {
            "board": source.board.pk,
            "name": source.name,
            "url": source.url,
            "fetch_strategy": source.fetch_strategy,
            "parser_key": source.parser_key,
            "cron": "*/30 * * * *",
            "enabled": "on",
        },
    )

    crontab = PeriodicTask.objects.get(name=schedule_name(source)).crontab
    assert crontab.minute == "*/30"


@pytest.mark.django_db
def test_the_source_list_shows_what_beat_will_do(admin_client, source):
    """Ticking a box with no visible consequence is not a usable control:
    the operator has to be able to see that it took effect."""
    response = admin_client.get("/admin/scraping/source/")

    assert response.status_code == 200
    assert b"not yet run" in response.content


# --- EXT-047: the source health dashboard ------------------------------


@pytest.mark.django_db
def test_the_health_dashboard_lists_every_source(admin_client, source):
    """Including one that has never run - a newly added source is the one
    most likely to be misconfigured."""
    response = admin_client.get("/admin/scraping/sourcehealth/")

    assert response.status_code == 200
    assert source.name.encode() in response.content
    assert b"not yet run" in response.content


@pytest.mark.django_db
def test_the_dashboard_shows_last_success_last_failure_and_the_streak(admin_client, source):
    record_failure(source, "502 from the board")
    record_failure(source, "502 from the board")

    response = admin_client.get("/admin/scraping/sourcehealth/")
    body = response.content.decode()
    health = health_for(source)

    # All three criterion columns are present as columns, not just as data
    # that happens to exist on the model.
    for column in ("last_success_at", "last_failure_at", "consecutive_failures"):
        assert f"column-{column}" in body

    assert str(health.last_failure_at.year) in body
    assert "502 from the board" in body
    assert ">2<" in body  # the streak itself


@pytest.mark.django_db
def test_a_failing_source_is_visibly_different_from_a_healthy_one(admin_client, source, board):
    other = Source.objects.create(
        board=board, name="healthy one", url="https://x.gov.in/ok", parser_key="ok"
    )
    record_success(other)
    record_failure(source, "down")

    body = admin_client.get("/admin/scraping/sourcehealth/").content.decode()

    assert "failing (1)" in body
    assert ">ok<" in body


@pytest.mark.django_db
def test_a_stale_source_is_called_stale_not_merely_failing(admin_client, source):
    """The two faults are different: a failing source is shouting, a stale
    one has gone quiet."""
    health = health_for(source)
    health.last_success_at = timezone.now() - timedelta(days=30)
    health.save()

    body = admin_client.get("/admin/scraping/sourcehealth/").content.decode()

    assert "stale" in body


@pytest.mark.django_db
def test_the_dashboard_can_be_filtered_to_the_overdue_sources(admin_client, source, board):
    healthy = Source.objects.create(
        board=board, name="healthy one", url="https://x.gov.in/ok", parser_key="ok"
    )
    record_success(healthy)
    stale = health_for(source)
    stale.last_success_at = timezone.now() - timedelta(days=30)
    stale.save()

    body = admin_client.get("/admin/scraping/sourcehealth/?stale=yes").content.decode()

    assert source.name in body
    assert "healthy one" not in body


@pytest.mark.django_db
def test_health_is_read_only(admin_client, source):
    """Every value here is something a run observed; editing one would be
    editing the record of what happened."""
    health = health_for(source)
    response = admin_client.get(f"/admin/scraping/sourcehealth/{health.pk}/change/")

    # Django redirects change -> view when change permission is withheld.
    assert response.status_code in (200, 302)
    assert not SourceHealthAdmin(SourceHealth, admin.site).has_change_permission(None)
    assert not SourceHealthAdmin(SourceHealth, admin.site).has_add_permission(None)


@pytest.mark.django_db
def test_the_source_list_shows_health_beside_the_schedule(admin_client, source):
    """The page an operator is already on when a board looks wrong."""
    record_failure(source, "down")

    body = admin_client.get("/admin/scraping/source/").content.decode()

    assert "failing (1)" in body


# --- EXT-051: the triage queue, driven through the real admin views ----


@pytest.fixture
def triage_item(source):
    from scraping.matching import match_observation
    from scraping.parsers import Observation
    from scraping.triage import queue_observation

    obs = Observation(
        exam_name="CISF AC(EXE) LDCE-2026",
        track="result",
        value="declared",
        confidence=0.9,
        source_url="https://www.upsc.gov.in/whats-new/x",
        raw_text="Final Result: CISF AC(EXE) LDCE-2026",
    )
    return queue_observation(obs, match_observation(obs, board=source.board), source=source)


@pytest.mark.django_db
def test_the_triage_queue_is_listed_in_admin(admin_client, triage_item):
    response = admin_client.get("/admin/scraping/triageitem/")

    assert response.status_code == 200
    assert b"CISF AC(EXE) LDCE-2026" in response.content
    # All three ways out are offered on the row itself.
    for label in (b"Link", b"Create exam", b"Dismiss"):
        assert label in response.content


@pytest.mark.django_db
def test_an_operator_can_link_an_observation_to_a_stage_from_admin(
    admin_client, triage_item, board
):
    from exams.models import Exam, ExamStage

    exam = Exam.objects.create(
        board=board, code="CISF-LDCE", name="CISF AC LDCE", cycle_year=2026, category="x"
    )
    stage = ExamStage.objects.create(exam=exam, stage_type=ExamStage.StageType.SINGLE, sequence=1)

    response = admin_client.post(
        f"/admin/scraping/triageitem/{triage_item.pk}/link/",
        {"exam_stage": stage.pk, "note": "obviously the LDCE"},
    )

    assert response.status_code == 302
    triage_item.refresh_from_db()
    assert triage_item.status == TriageItem.Status.LINKED
    assert triage_item.exam_stage == stage
    assert triage_item.resolved_by


@pytest.mark.django_db
def test_an_operator_can_create_a_new_exam_from_admin(admin_client, triage_item, board):
    from exams.models import Exam, ExamStage

    response = admin_client.post(
        f"/admin/scraping/triageitem/{triage_item.pk}/create-exam/",
        {
            "board": board.pk,
            "code": "CISF-LDCE",
            "name": "CISF AC(EXE) LDCE",
            "cycle_year": 2026,
            "stage_type": ExamStage.StageType.SINGLE,
            "note": "new cycle nobody had entered",
        },
    )

    assert response.status_code == 302
    exam = Exam.objects.get(board=board, code="CISF-LDCE", cycle_year=2026)
    triage_item.refresh_from_db()
    assert triage_item.status == TriageItem.Status.CREATED
    assert triage_item.exam_stage.exam == exam


@pytest.mark.django_db
def test_an_operator_can_dismiss_from_admin(admin_client, triage_item):
    response = admin_client.post(
        f"/admin/scraping/triageitem/{triage_item.pk}/dismiss/",
        {"note": "departmental promotion exam, not tracked"},
    )

    assert response.status_code == 302
    triage_item.refresh_from_db()
    assert triage_item.status == TriageItem.Status.DISMISSED
    assert triage_item.resolution_note == "departmental promotion exam, not tracked"


@pytest.mark.django_db
def test_the_create_form_prefills_the_year_it_read(admin_client, triage_item):
    """Prefill only - the operator confirms it, so a wrong guess costs a
    keystroke rather than creating an exam under the wrong cycle."""
    response = admin_client.get(f"/admin/scraping/triageitem/{triage_item.pk}/create-exam/")

    assert response.status_code == 200
    assert b'value="2026"' in response.content


@pytest.mark.django_db
def test_a_triage_item_cannot_be_edited_as_a_form(admin_client, triage_item):
    """Retyping exam_name would change the fingerprint and split one
    recurring problem into two queue items."""
    from scraping.admin import TriageItemAdmin

    assert not TriageItemAdmin(TriageItem, admin.site).has_change_permission(None)
    assert not TriageItemAdmin(TriageItem, admin.site).has_add_permission(None)


@pytest.mark.django_db
def test_resolving_an_already_resolved_item_warns_rather_than_double_applying(
    admin_client, triage_item
):
    admin_client.post(f"/admin/scraping/triageitem/{triage_item.pk}/dismiss/", {"note": "first"})

    response = admin_client.post(
        f"/admin/scraping/triageitem/{triage_item.pk}/dismiss/", {"note": "second"}, follow=True
    )

    triage_item.refresh_from_db()
    assert triage_item.resolution_note == "first"
    assert b"already dismissed" in response.content
