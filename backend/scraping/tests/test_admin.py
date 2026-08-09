"""The acceptance criterion is about admin, so these drive the real admin
views rather than asserting the model in isolation."""

import pytest
from django.contrib import admin
from django_celery_beat.models import PeriodicTask

from scraping.models import Source
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
