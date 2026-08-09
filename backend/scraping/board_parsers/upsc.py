"""Parser for UPSC's "What's New" board.

Written against a saved copy of the real page (see
tests/fixtures/upsc_whats_new.html). The site is Drupal, and the block is
a view whose every entry looks like:

    <div class="views-row ...">
      <span class="views-field views-field-field-exam-name">
        <span class="field-content">
          <a href="/whats-new/<Exam Name>/<Notice Type>">
            <ul class="arrows"><li>Notice Type: Exam Name</li></ul>
          </a>
        </span>
      </span>
    </div>

The href is the reliable source: it separates exam name from notice type
with a `/`, whereas the visible text joins them with ": " - and several
real exam names contain a colon-free comma but some post titles do
contain colons, so splitting the text is ambiguous.
"""

from __future__ import annotations

import re
from urllib.parse import unquote, urljoin

from bs4 import BeautifulSoup, Tag

from exams.models import StatusTrack
from scraping.parsers import Observation, Parser, register

#: Notice types this parser is willing to draw a conclusion from, mapped
#: to (track, value, confidence).
#:
#: Bare "Notice" and "Addendum Notice" are deliberately absent: their
#: titles say nothing about status, and inventing one would put wrong
#: machine data into a system whose entire purpose is trustworthy status.
NOTICE_MAP: dict[str, tuple[str, str, float]] = {
    "final result": (StatusTrack.Track.RESULT, "declared", 0.9),
    "written result": (StatusTrack.Track.RESULT, "declared", 0.9),
    "written result (with name)": (StatusTrack.Track.RESULT, "declared", 0.9),
    "examination time table": (StatusTrack.Track.CONDUCT, "scheduled", 0.8),
    "interview schedule": (StatusTrack.Track.CONDUCT, "scheduled", 0.8),
    "interview details": (StatusTrack.Track.CONDUCT, "scheduled", 0.8),
}

#: Entries like "02 Posts of Assistant Director..." or "01 Post of Data
#: Processing Assistant..." are recruitment advertisements, not exams.
#: They have no Exam to match against, so emitting them would fill the
#: triage queue with permanently unmatchable rows.
_RECRUITMENT_RE = re.compile(r"^\s*\d+\s+posts?\s+of\b", re.IGNORECASE)

#: Stage wording UPSC uses in exam names, e.g. "... (Main) Examination".
_STAGE_HINTS = (
    ("prelim", "prelims"),
    ("(main", "mains"),
    ("main)", "mains"),
    ("interview", "interview"),
)


def _looks_like_recruitment(name: str) -> bool:
    return bool(_RECRUITMENT_RE.match(name))


def _stage_hint_for(exam_name: str) -> str:
    lowered = exam_name.lower()
    for needle, hint in _STAGE_HINTS:
        if needle in lowered:
            return hint
    return ""


def _split_href(href: str) -> tuple[str, str] | None:
    """Pull (exam name, notice type) out of /whats-new/<name>/<type>."""
    parts = [segment for segment in unquote(href).split("/") if segment]
    if len(parts) < 3 or parts[0].lower() != "whats-new":
        return None
    # The notice type is the last segment; everything between is the name,
    # since a few exam names themselves contain a slash.
    return "/".join(parts[1:-1]).strip(), parts[-1].strip()


@register
class UpscWhatsNewParser(Parser):
    """Reads UPSC's What's New block."""

    key = "upsc_whats_new"
    base_url = "https://www.upsc.gov.in/"

    def parse(self, html: str) -> list[Observation]:
        soup = BeautifulSoup(html, "html.parser")
        observations: list[Observation] = []
        seen: set[tuple[str, str, str]] = set()

        # Iterate rows rather than regexing the document: entries sit in
        # sibling <div class="views-row">, and a document-wide pattern
        # happily pairs one row's link with the next row's text.
        for row in soup.select("div.views-row"):
            observation = self._observation_from_row(row)
            if observation is None:
                continue
            # The same notice can appear twice (e.g. a "with name" variant
            # alongside the plain one); one claim per exam/track/value.
            fingerprint = (observation.exam_name, observation.track, observation.value)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            observations.append(observation)

        return observations

    def _observation_from_row(self, row: Tag) -> Observation | None:
        field = row.select_one("span.views-field-field-exam-name a[href]")
        if field is None:
            return None

        href = field.get("href")
        if not isinstance(href, str):
            return None

        split = _split_href(href)
        if split is None:
            return None
        exam_name, notice_type = split

        if not exam_name or _looks_like_recruitment(exam_name):
            return None

        mapped = NOTICE_MAP.get(notice_type.lower())
        if mapped is None:
            return None
        track, value, confidence = mapped

        return Observation(
            exam_name=exam_name,
            track=track,
            value=value,
            stage_hint=_stage_hint_for(exam_name),
            confidence=confidence,
            source_url=urljoin(self.base_url, href),
            raw_text=" ".join(field.get_text(" ", strip=True).split()),
        )
