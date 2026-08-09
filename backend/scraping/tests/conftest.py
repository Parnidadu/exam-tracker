import pytest

from exams.models import Board


@pytest.fixture
def board(db):
    return Board.objects.create(
        name="Union Public Service Commission",
        code="UPSC",
        official_url="https://upsc.gov.in",
        timezone="Asia/Kolkata",
    )


@pytest.fixture
def source(board):
    from scraping.models import Source

    return Source.objects.create(
        board=board,
        name="UPSC notices",
        url="https://upsc.gov.in/whats-new",
        parser_key="upsc_notices",
        cron="0 */6 * * *",
    )
