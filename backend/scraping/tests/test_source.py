import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import ProtectedError

from scraping.models import Source


@pytest.mark.django_db
def test_source_stores_everything_a_scrape_run_needs(source, board):
    source.refresh_from_db()

    assert source.board == board
    assert source.url == "https://upsc.gov.in/whats-new"
    assert source.fetch_strategy == Source.FetchStrategy.HTTP
    assert source.parser_key == "upsc_notices"
    assert source.cron == "0 */6 * * *"
    assert source.enabled is True


@pytest.mark.django_db
def test_a_source_defaults_to_enabled_and_plain_http(board):
    created = Source.objects.create(
        board=board, name="n", url="https://x.test/", parser_key="k"
    )
    assert created.enabled is True
    assert created.fetch_strategy == Source.FetchStrategy.HTTP


@pytest.mark.django_db
def test_disabling_a_source_keeps_its_configuration(source):
    """`enabled` exists so a board can be paused without losing its URL,
    parser and schedule."""
    source.enabled = False
    source.save()
    source.refresh_from_db()

    assert source.enabled is False
    assert source.url == "https://upsc.gov.in/whats-new"
    assert source.cron == "0 */6 * * *"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "expression",
    ["0 * * * *", "*/15 * * * *", "0 9,21 * * *", "0 0 1 * *", "30 2 * * 1-5"],
)
def test_valid_cron_expressions_are_accepted(board, expression):
    source = Source(
        board=board, name="n", url="https://x.test/", parser_key="k", cron=expression
    )
    source.full_clean()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "expression",
    ["not a cron", "0 * * *", "99 * * * *", "* * * * * * *", "@hourly-ish"],
)
def test_invalid_cron_expressions_are_rejected(board, expression):
    """A typo in admin must fail at save time - otherwise the source simply
    never runs and nobody finds out."""
    source = Source(
        board=board, name="n", url="https://x.test/", parser_key="k", cron=expression
    )
    with pytest.raises(ValidationError):
        source.full_clean()


@pytest.mark.django_db
def test_the_same_url_cannot_be_registered_twice_for_one_parser(source, board):
    with pytest.raises(IntegrityError):
        Source.objects.create(
            board=board,
            name="duplicate",
            url=source.url,
            parser_key=source.parser_key,
        )


@pytest.mark.django_db
def test_a_board_with_sources_cannot_be_deleted(source, board):
    """Deleting a board out from under its scrape config would orphan the
    schedule silently."""
    with pytest.raises(ProtectedError):
        board.delete()


@pytest.mark.django_db
def test_source_changes_are_recorded_in_history(source):
    """Sources are edited in admin rather than reviewed in a PR, so the
    audit trail is the only record of who changed a URL or schedule."""
    source.cron = "0 3 * * *"
    source.save()

    versions = list(source.history.all())
    assert len(versions) == 2
    assert versions[0].cron == "0 3 * * *"
    assert versions[1].cron == "0 */6 * * *"


@pytest.mark.django_db
def test_source_str_identifies_the_board_and_feed(source):
    assert str(source) == "UPSC - UPSC notices"
