"""Parser for IBPS's "CRP Updates" board.

Written against a saved copy of the real page (see
tests/fixtures/ibps_crp_updates.html). Deliberately the second board, and
deliberately a structurally different one - it exercises the Parser
contract in ways UPSC's page never does:

    UPSC                              IBPS
    ------------------------------    ------------------------------
    Drupal views                      WordPress / Elementor
    exam + notice type encoded in     free text in a sibling <div>,
      the href path                     with the exam code inline
    no dates on the board             an explicit date per entry

Each entry looks like:

    <a href="...">
      <div class="detail-section"><div class="detail-list">
        <div class="detail-first-heading">17 Jul 26</div>
        <div class="detail-second-heading">Result of Online Main
            Examination for CRP- CSA-XV (Provisional Allotment...)</div>
      </div></div>
    </a>

So the exam has to be recovered from prose rather than read off a URL,
and the date is real data rather than absent - which is the point: the
same Observation contract carries both boards without either parser
knowing about the other.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from exams.models import StatusTrack
from scraping.dates import parse_date
from scraping.parsers import Observation, Parser, register

#: Leading phrases worth drawing a conclusion from, in priority order.
#: Same conservative rule as the UPSC parser: anything whose wording does
#: not actually state a status is skipped rather than guessed at.
_RULES: list[tuple[re.Pattern[str], str, str, float]] = [
    (
        re.compile(r"\bresult\s+of\b|\bresult\s+for\b|\bfinal\s+result\b", re.IGNORECASE),
        StatusTrack.Track.RESULT,
        "declared",
        0.9,
    ),
    (
        # "Notification for Common Recruitment Process for CRP-X" is IBPS
        # formally announcing that a cycle will run.
        re.compile(r"^\s*notification\s+for\s+common\s+recruitment\s+process\b", re.IGNORECASE),
        StatusTrack.Track.CONDUCT,
        "scheduled",
        0.8,
    ),
]

#: Wording that names a CRP but says nothing about its status. Checked
#: before the rules above so a "Corrigendum ... Result ..." headline can
#: never be read as a result declaration.
_SKIP = re.compile(
    r"^\s*(window notification|apply online|updated vacancies|corrigendum|notice dated)\b",
    re.IGNORECASE,
)

#: IBPS names cycles as CRP-<stream>-<roman>, but the live page is
#: inconsistent about spacing: "CRP- CSA-XV", "CRP-PO/MTs -XVI".
_CRP_RE = re.compile(r"\bCRP\s*-?\s*([A-Za-z/]+?)\s*-\s*([IVXLC]+)\b")

_STAGE_HINTS = (
    ("prelim", "prelims"),
    ("main", "mains"),
    ("interview", "interview"),
)


def _normalise_crp(match: re.Match[str]) -> str:
    """Collapse the page's inconsistent spacing into one canonical name."""
    stream = match.group(1).strip().rstrip("-")
    roman = match.group(2).strip()
    return f"CRP-{stream}-{roman}"


def _stage_hint_for(text: str) -> str:
    lowered = text.lower()
    for needle, hint in _STAGE_HINTS:
        if needle in lowered:
            return hint
    return ""


@register
class IbpsCrpUpdatesParser(Parser):
    """Reads IBPS's CRP Updates board."""

    key = "ibps_crp_updates"
    base_url = "https://www.ibps.in/"

    def parse(self, html: str) -> list[Observation]:
        soup = BeautifulSoup(html, "html.parser")
        observations: list[Observation] = []
        seen: set[tuple[str, str, str]] = set()

        for entry in soup.select("div.detail-list"):
            observation = self._observation_from_entry(entry)
            if observation is None:
                continue
            fingerprint = (observation.exam_name, observation.track, observation.value)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            observations.append(observation)

        return observations

    def _observation_from_entry(self, entry: Tag) -> Observation | None:
        heading = entry.select_one("div.detail-second-heading")
        if heading is None:
            return None
        text = " ".join(heading.get_text(" ", strip=True).split())
        if not text or _SKIP.match(text):
            return None

        crp = _CRP_RE.search(text)
        if crp is None:
            # Housekeeping notices - ISO certification, trademark cautions,
            # workstation tenders - name no exam, so there is nothing to
            # attach an observation to.
            return None
        exam_name = _normalise_crp(crp)

        for pattern, track, value, confidence in _RULES:
            if pattern.search(text):
                break
        else:
            return None

        observed_date = None
        date_div = entry.select_one("div.detail-first-heading")
        if date_div is not None:
            # EXT-048: the shared normaliser rather than a strptime
            # format local to this parser. IBPS writes "17 Jul 26"
            # today, but a board changing its date format should not
            # need a code change in the parser that reads it.
            observed_date = parse_date(date_div.get_text(strip=True))

        link = entry.find_parent("a", href=True)
        href = link["href"] if isinstance(link, Tag) else ""
        source_url = urljoin(self.base_url, str(href)) if href else self.base_url

        return Observation(
            exam_name=exam_name,
            track=track,
            value=value,
            stage_hint=_stage_hint_for(text),
            observed_date=observed_date,
            confidence=confidence,
            source_url=source_url,
            raw_text=text,
        )
