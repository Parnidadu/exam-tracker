"""Normalising the date formats Indian exam boards actually publish.

The format list is taken from the two real page captures committed as
parser fixtures, not from imagination. What those pages actually contain:

    UPSC    21.05.2025   18/07/2022   15th May, 2025   27th November 2025
            October 1st, 2025         15  Sep 2016     November 2025
    IBPS    15.01.2026   14.11.2025   07 Aug 26        15 Nov 25

Two things that shapes:

* **Dots, not slashes.** `DD.MM.YYYY` is the dominant numeric form on both
  boards - there is exactly one slash-separated date across both pages.
  A normaliser built around `DD/MM/YYYY` alone would miss almost every
  numeric date these boards publish.
* **Day-less values are real.** "November 2025" appears on the live UPSC
  page. It is a genuine publication, and it genuinely does not name a day.

The governing rule
------------------
Anything that cannot be read exactly one way is **flagged, never
guessed**. This is the same rule the status model runs on: a wrong date
silently entered as machine data is worse than no date, because no date
is visibly missing while a wrong one looks like an answer.

Concretely, this refuses to:

* pick a day out of "November 2025",
* pick one date out of "15 & 16 March 2026",
* read "03/25/2026" as March 25th - month 25 does not exist, and reading
  it month-first would be inventing a different convention for one value,
* roll "31/02/2026" forward into March,
* decide whether "01-02-45" means 1945 or 2045.

Day-first is assumed for numeric dates, because that is the convention
these boards write in and the acceptance criterion names it. That is a
documented convention, not a guess: `05.06.2026` is the 5th of June.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum

from django.utils import timezone

#: Rejected outside this window. Not env-configurable on purpose: it is a
#: sanity bound on parsing, not something an operator tunes. Boards do
#: publish genuinely old dates - the UPSC capture contains "15 Sep 2016" -
#: so the lower bound is generous.
EARLIEST_PLAUSIBLE_YEAR = 1995
#: How far ahead a date may sit before it looks like a parsing artefact
#: rather than a real announcement.
LATEST_PLAUSIBLE_YEARS_AHEAD = 15


class Ambiguity(Enum):
    """Why a value could not be turned into exactly one date."""

    EMPTY = "empty"
    NOT_A_DATE = "no date found"
    NO_DAY = "names a month but no day"
    MULTIPLE_DATES = "names more than one date"
    INVALID_DATE = "not a real calendar date"
    IMPOSSIBLE_ORDER = "cannot be read day-first"
    IMPLAUSIBLE_YEAR = "year is outside the plausible range"


@dataclass(frozen=True)
class DateResult:
    """Either a date, or the reason there isn't one.

    A result type rather than an exception because the caller - a parser
    building observations - treats "no usable date" as ordinary data, not
    as an error. Exceptions would push routine board wording onto the
    failure path.
    """

    raw: str
    value: date | None = None
    reason: Ambiguity | None = None

    @property
    def ok(self) -> bool:
        return self.value is not None

    @property
    def ambiguous(self) -> bool:
        """True when there was something date-like that we refused to
        guess at - as opposed to no date at all."""
        return self.reason is not None and self.reason not in {
            Ambiguity.EMPTY,
            Ambiguity.NOT_A_DATE,
        }

    def __str__(self) -> str:
        if self.value:
            return self.value.isoformat()
        return f"{self.raw!r}: {self.reason.value if self.reason else 'unparsed'}"


MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

_MONTH_NAMES = "|".join(sorted(MONTHS, key=len, reverse=True))
_ORDINAL = r"(?:st|nd|rd|th)"

#: 15.01.2026 / 18/07/2022 / 07-08-26
_NUMERIC = re.compile(r"\b(\d{1,2})\s*([/.\-])\s*(\d{1,2})\s*\2\s*(\d{2}|\d{4})\b")

#: 15th May, 2025 / 27th November 2025 / 07 Aug 26 / 15  Sep 2016
_DAY_FIRST = re.compile(
    rf"\b(\d{{1,2}}){_ORDINAL}?[\s.\-]+({_MONTH_NAMES})\.?,?[\s\-]+(\d{{2}}|\d{{4}})\b",
    re.IGNORECASE,
)

#: October 1st, 2025 / Jan 5 2026
_MONTH_FIRST = re.compile(
    rf"\b({_MONTH_NAMES})\.?\s+(\d{{1,2}}){_ORDINAL}?,?\s+(\d{{2}}|\d{{4}})\b",
    re.IGNORECASE,
)

#: November 2025 - a month and a year, and no day anywhere.
_MONTH_YEAR = re.compile(rf"\b({_MONTH_NAMES})\.?,?\s+(\d{{4}})\b", re.IGNORECASE)

#: "15 & 16 March", "15 to 17 May", "3 - 5 April". Two day numbers joined
#: by a conjunction in front of a month name.
_DAY_RANGE = re.compile(
    rf"\b\d{{1,2}}{_ORDINAL}?\s*(?:&|and|to|–|—|-)\s*\d{{1,2}}{_ORDINAL}?\s+(?:{_MONTH_NAMES})\b",
    re.IGNORECASE,
)

def _spans_overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]

_WHITESPACE = re.compile(r"[\s   ]+")


def _clean(raw: str) -> str:
    """Collapse whitespace, including the non-breaking spaces that come
    out of HTML. The UPSC page contains "15  Sep 2016" with a double
    space, which a naive pattern misses."""
    return _WHITESPACE.sub(" ", raw or "").strip()


def _resolve_year(digits: str, *, today: date) -> int | None:
    """Expand a two-digit year, or return None when it cannot be resolved.

    Prefers the current century, falling back to the last one only when
    that would put the date implausibly far in the future. A value that
    lands in neither - "45", which is either 1945 or 2045 and is
    implausible both ways - resolves to nothing and gets flagged rather
    than picked.
    """
    if len(digits) == 4:
        return int(digits)

    latest = today.year + LATEST_PLAUSIBLE_YEARS_AHEAD
    this_century = 2000 + int(digits)
    if this_century <= latest:
        return this_century
    last_century = 1900 + int(digits)
    if last_century >= EARLIEST_PLAUSIBLE_YEAR:
        return last_century
    return None


def _build(day: int, month: int, year: int | None, raw: str, *, today: date) -> DateResult:
    if year is None:
        return DateResult(raw=raw, reason=Ambiguity.IMPLAUSIBLE_YEAR)
    if not EARLIEST_PLAUSIBLE_YEAR <= year <= today.year + LATEST_PLAUSIBLE_YEARS_AHEAD:
        return DateResult(raw=raw, reason=Ambiguity.IMPLAUSIBLE_YEAR)
    try:
        return DateResult(raw=raw, value=date(year, month, day))
    except ValueError:
        # 31/02 and friends. Never rolled forward: a date that does not
        # exist is a sign the source or the parse is wrong, and silently
        # producing March 3rd would hide that.
        return DateResult(raw=raw, reason=Ambiguity.INVALID_DATE)


def normalise_date(raw: str, *, today: date | None = None) -> DateResult:
    """Turn a published date string into one date, or say why it cannot.

    Searches within the string rather than requiring it to be a bare date,
    because real parser input is prose - "Result of Online Main
    Examination held on 15.01.2026".
    """
    today = today or timezone.now().date()
    text = _clean(raw)

    if not text:
        return DateResult(raw=raw, reason=Ambiguity.EMPTY)

    # Ranges first. "15 & 16 March 2026" contains a perfectly parseable
    # "16 March 2026", so looking for a single date first would silently
    # return the second day of an exam held over two.
    if _DAY_RANGE.search(text):
        return DateResult(raw=raw, reason=Ambiguity.MULTIPLE_DATES)

    complete = [
        (kind, match)
        for kind, pattern in (
            ("numeric", _NUMERIC),
            ("day_first", _DAY_FIRST),
            ("month_first", _MONTH_FIRST),
        )
        for match in pattern.finditer(text)
    ]

    if len(complete) > 1:
        return DateResult(raw=raw, reason=Ambiguity.MULTIPLE_DATES)

    # A month-and-year that is *not* part of a date already matched. The
    # overlap check matters: "15th May, 2025" ends in "May, 2025", so
    # counting month-year hits blindly would flag every long-form date in
    # prose as naming two dates - and quietly discard a perfectly good one.
    dated_spans = [match.span() for _, match in complete]
    loose_months = [
        match
        for match in _MONTH_YEAR.finditer(text)
        if not any(_spans_overlap(match.span(), span) for span in dated_spans)
    ]

    if loose_months and complete:
        # "Exam held in March 2026; result on 15.04.2026" names two
        # different points in time, and there is no basis for preferring
        # either.
        return DateResult(raw=raw, reason=Ambiguity.MULTIPLE_DATES)
    if len(loose_months) > 1:
        return DateResult(raw=raw, reason=Ambiguity.MULTIPLE_DATES)

    if complete:
        kind, match = complete[0]
        groups = match.groups()
    else:
        kind, groups = "", ()

    if kind == "numeric":
        first, _sep, second, year_digits = groups
        day, month = int(first), int(second)
        if month > 12:
            # Cannot be day-first. Reading it month-first would mean
            # adopting a second convention for a single value - which is
            # exactly the guess this normaliser exists to refuse.
            reason = Ambiguity.IMPOSSIBLE_ORDER if day <= 12 else Ambiguity.INVALID_DATE
            return DateResult(raw=raw, reason=reason)
        return _build(day, month, _resolve_year(year_digits, today=today), raw, today=today)

    if kind == "day_first":
        day_digits, month_name, year_digits = groups
        return _build(
            int(day_digits),
            MONTHS[month_name.lower()],
            _resolve_year(year_digits, today=today),
            raw,
            today=today,
        )

    if kind == "month_first":
        month_name, day_digits, year_digits = groups
        return _build(
            int(day_digits),
            MONTHS[month_name.lower()],
            _resolve_year(year_digits, today=today),
            raw,
            today=today,
        )

    if loose_months:
        # "November 2025" is a real publication that genuinely names no
        # day. Choosing the 1st, or the last, would be inventing one.
        return DateResult(raw=raw, reason=Ambiguity.NO_DAY)

    return DateResult(raw=raw, reason=Ambiguity.NOT_A_DATE)


def parse_date(raw: str, *, today: date | None = None) -> date | None:
    """Convenience wrapper for callers that only want a date or nothing.

    Every reason for returning None is still available through
    `normalise_date`; this exists so a parser assigning
    `Observation.observed_date` does not have to unpack a result it will
    not otherwise use.
    """
    return normalise_date(raw, today=today).value
