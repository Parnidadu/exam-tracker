"""EXT-048: normalising the date formats Indian exam boards publish.

The formats tested here were taken out of the two real page captures
committed as parser fixtures, not invented - see the module docstring in
scraping/dates.py. One test below reads the fixtures directly, so the
format list cannot quietly drift away from what the boards actually
serve.
"""

from datetime import date
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from scraping.dates import Ambiguity, normalise_date, parse_date

#: Fixed so nothing here depends on the day the suite runs. Two-digit
#: years resolve relative to "today", which would otherwise make these
#: tests start failing on their own years later.
TODAY = date(2026, 8, 10)

FIXTURES = Path(__file__).parent / "fixtures"


def parse(raw: str) -> date | None:
    return normalise_date(raw, today=TODAY).value


def reason(raw: str) -> Ambiguity | None:
    return normalise_date(raw, today=TODAY).reason


# --- the three formats the criterion names -----------------------------


@pytest.mark.parametrize(
    "raw",
    ["05/06/2026", "5/6/2026", "05.06.2026", "05-06-2026"],
)
def test_dd_mm_yyyy_in_every_separator_the_boards_use(raw):
    """Dots matter as much as slashes: `DD.MM.YYYY` is the dominant
    numeric form on both captured pages, with exactly one slash-separated
    date across the two."""
    assert parse(raw) == date(2026, 6, 5)


@pytest.mark.parametrize("raw", ["05-06-26", "05/06/26", "05.06.26"])
def test_dd_mm_yy(raw):
    assert parse(raw) == date(2026, 6, 5)


@pytest.mark.parametrize(
    "raw",
    [
        "5 June 2026",
        "5th June 2026",
        "5th June, 2026",
        "05 Jun 2026",
        "5 Jun 26",
        "June 5, 2026",
        "June 5th, 2026",
        "Jun 5 2026",
    ],
)
def test_long_form_month_names_in_either_order(raw):
    assert parse(raw) == date(2026, 6, 5)


def test_the_day_first_convention_is_applied_to_numeric_dates(raw=None):
    """Both components are <= 12, so the value only has one reading under
    a stated convention. The boards write day-first and the criterion
    names DD/MM/YYYY, so this is a documented convention rather than a
    guess - `05.06.2026` is the 5th of June, not the 6th of May."""
    assert parse("05.06.2026") == date(2026, 6, 5)
    assert parse("06.05.2026") == date(2026, 5, 6)


# --- ambiguous input is flagged, not guessed ---------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "15 & 16 March 2026",
        "15 and 16 March 2026",
        "15 to 17 May 2026",
        "3 - 5 April 2026",
        "15th & 16th March 2026",
    ],
)
def test_a_date_range_is_flagged_rather_than_resolved_to_one_end(raw):
    """"15 & 16 March 2026" contains a perfectly parseable "16 March
    2026". Picking it would silently report the second day of a two-day
    exam as the only day."""
    assert parse(raw) is None
    assert reason(raw) is Ambiguity.MULTIPLE_DATES


def test_two_different_dates_in_one_string_are_flagged(raw=None):
    assert reason("Exam on 15.04.2026, result on 20.05.2026") is Ambiguity.MULTIPLE_DATES
    assert reason("Exam held in March 2026; result on 15.04.2026") is Ambiguity.MULTIPLE_DATES
    assert reason("held in March 2026 or April 2026") is Ambiguity.MULTIPLE_DATES


@pytest.mark.parametrize("raw", ["November 2025", "March 2026", "Aug 2026", "Sep 2016"])
def test_a_month_without_a_day_is_flagged_not_filled_in(raw):
    """These are real values from the live UPSC page. Choosing the 1st, or
    the last of the month, would be inventing a day the board never
    published."""
    assert parse(raw) is None
    assert reason(raw) is Ambiguity.NO_DAY


def test_a_value_that_cannot_be_read_day_first_is_flagged(raw=None):
    """"03/25/2026" has no 25th month. Reading it month-first would mean
    adopting a second convention for a single value - the exact guess this
    normaliser exists to refuse."""
    assert parse("03/25/2026") is None
    assert reason("03/25/2026") is Ambiguity.IMPOSSIBLE_ORDER


@pytest.mark.parametrize("raw", ["31/02/2026", "31.04.2026", "30 February 2026"])
def test_an_impossible_calendar_date_is_flagged_not_rolled_forward(raw):
    """Silently producing 3 March from 31 February would hide the fact
    that either the board or the parse is wrong."""
    assert parse(raw) is None
    assert reason(raw) is Ambiguity.INVALID_DATE


def test_a_two_digit_year_that_could_be_either_century_is_flagged(raw=None):
    """"45" is 1945 or 2045 and implausible either way. Picking one would
    put a date decades out into machine data."""
    assert parse("01-02-45") is None
    assert reason("01-02-45") is Ambiguity.IMPLAUSIBLE_YEAR


def test_two_digit_years_that_are_not_ambiguous_still_resolve(raw=None):
    assert parse("07 Aug 26") == date(2026, 8, 7)
    assert parse("15 Nov 25") == date(2025, 11, 15)
    assert parse("01-02-99") == date(1999, 2, 1)


# --- non-dates ---------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["", "   ", "to be announced", "postponed until further notice", "2026", "Notice"],
)
def test_text_that_names_no_date_yields_no_date(raw):
    assert parse(raw) is None


def test_no_date_at_all_is_distinguished_from_a_refused_guess(raw=None):
    """A caller triaging its input needs to tell "this line was never
    about a date" from "this line named a date I would not guess at"."""
    assert normalise_date("to be announced", today=TODAY).ambiguous is False
    assert normalise_date("", today=TODAY).ambiguous is False
    assert normalise_date("15 & 16 March 2026", today=TODAY).ambiguous is True
    assert normalise_date("November 2025", today=TODAY).ambiguous is True


# --- real-world shapes -------------------------------------------------


def test_dates_are_found_inside_prose(raw=None):
    """Parsers pass whole headings, not bare dates."""
    assert parse("Result of Online Main Examination held on 15.01.2026") == date(2026, 1, 15)
    assert parse("Declared on 15th May, 2025 (Thursday)") == date(2025, 5, 15)


def test_prose_containing_a_joining_word_is_not_mistaken_for_a_range(raw=None):
    """A regression guard. "15th May, 2025" ends in "May, 2025", so
    counting month-year matches without checking whether they overlap an
    already-matched date flagged every long-form date in a sentence
    containing "to" or "and" - discarding good dates."""
    assert parse("Result to be declared on 15th May, 2025") == date(2025, 5, 15)
    assert parse("Candidates are advised to appear on 27th November 2025") == date(2025, 11, 27)
    assert parse("Interview scheduled and confirmed for 5 June 2026") == date(2026, 6, 5)


def test_html_whitespace_does_not_defeat_the_match(raw=None):
    """"15  Sep 2016" - a real double space on the UPSC page - plus the
    non-breaking spaces that come out of scraped markup."""
    assert parse("15  Sep 2016") == date(2016, 9, 15)
    assert parse(" 15.01.2026 ") == date(2026, 1, 15)
    assert parse("15 Jan 2026") == date(2026, 1, 15)


def test_parse_date_is_the_shorthand_for_callers_wanting_only_a_date(raw=None):
    assert parse_date("15.01.2026", today=TODAY) == date(2026, 1, 15)
    assert parse_date("November 2025", today=TODAY) is None


# --- grounded in the real captures -------------------------------------


def test_every_date_the_captured_pages_publish_is_handled(raw=None):
    """Reads the committed fixtures and asserts the normaliser produces a
    date or a *specific* reason for every date-like string on them.

    This is the test that stops the format list drifting: if a board's
    markup is recaptured and a new format appears, this fails rather than
    silently dropping those dates.
    """
    import re

    date_like = re.compile(
        r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b"
        r"|\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]{2,8}\.?,?\s+\d{2,4}\b",
    )

    seen = 0
    for fixture in sorted(FIXTURES.glob("*.html")):
        text = BeautifulSoup(
            fixture.read_text(encoding="utf-8", errors="replace"), "html.parser"
        ).get_text(" ", strip=True)

        for candidate in set(date_like.findall(text)):
            result = normalise_date(candidate, today=TODAY)
            seen += 1
            assert result.ok, f"{candidate!r} from {fixture.name}: {result}"

    assert seen >= 15, "expected the captured pages to carry real dates"
