"""Matching a parser's Observation to the ExamStage it is talking about.

A parser produces free text off a notice board - "Final Result: Civil
Services (Main) Examination, 2026" - and the domain holds structured
rows: an Exam with a code, a name and a cycle year, carrying stages. This
module joins the two, scores how sure it is, and routes on that score:
confident matches are written, everything else is left for a human.

What the score is, and is not
-----------------------------
This is confidence in the **identification** - "the text is talking about
*this* stage". It is deliberately separate from `Observation.confidence`,
which is the parser's confidence in the **reading** - "the text says a
result was declared". They fail independently: a parser can be certain
what a sentence means and still not know which exam it refers to.

So the match score routes, and the parser's confidence is what gets
recorded as `StatusTrack.machine_confidence`. Multiplying them together
would produce one number that answers neither question.

Refusing to guess
-----------------
Two candidates scoring almost the same go to triage **even when both
score highly**. "CRP-PO/MTs-XV" and "CRP-PO/MTs-XVI" are one character
apart and are different exam cycles; picking the better of two
near-identical scores is a coin toss, and a coin toss that writes into
machine status data is worse than a queue item. Same rule the date
normaliser follows.

A year that disagrees disqualifies outright rather than scoring low: an
observation naming 2025 is not a weak match for the 2026 cycle, it is a
statement about a different exam.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum

from django.conf import settings
from django.db import transaction

from exams.models import Board, Exam, ExamStage
from verification.observations import ObservationResult, apply_machine_observation

from .parsers import Observation

DEFAULT_AUTO_LINK_THRESHOLD = 0.85
#: Two candidates within this of each other are treated as indistinguishable.
DEFAULT_AMBIGUITY_MARGIN = 0.05

#: Words that appear in nearly every exam title and so carry almost no
#: identifying signal. Left in, "Examination" alone would make two
#: unrelated exams look similar.
_NOISE = {
    "examination",
    "exam",
    "examinations",
    "recruitment",
    "notice",
    "notification",
    "the",
    "of",
    "for",
    "and",
    "to",
}

#: Stage wording is stripped before comparing names - it identifies the
#: stage, not the exam, and leaving it in makes "CSE (Main)" look like a
#: worse match for "Civil Services Examination" than it is.
_STAGE_WORDS: dict[str, str] = {
    "prelim": ExamStage.StageType.PRELIMS,
    "prelims": ExamStage.StageType.PRELIMS,
    "preliminary": ExamStage.StageType.PRELIMS,
    "main": ExamStage.StageType.MAINS,
    "mains": ExamStage.StageType.MAINS,
    "interview": ExamStage.StageType.INTERVIEW,
    "personality": ExamStage.StageType.INTERVIEW,
    "skill": ExamStage.StageType.SKILL,
    "typing": ExamStage.StageType.SKILL,
}

#: Non-capturing on purpose: a capturing group would make findall return
#: the century rather than the year.
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_NON_WORD = re.compile(r"[^\w]+")


class Decision(Enum):
    AUTO_LINK = "auto_link"
    TRIAGE = "triage"


class TriageReason(Enum):
    """Why a human has to look at this one."""

    NO_CANDIDATES = "no exam for this board matched at all"
    BELOW_THRESHOLD = "best match scored below the auto-link threshold"
    AMBIGUOUS = "two candidates scored too close to choose between"
    NO_STAGE = "matched an exam but could not tell which stage"


@dataclass(frozen=True)
class Candidate:
    """One possible answer, with the score behind it.

    `components` is kept so a triage screen can say *why* something
    scored as it did rather than showing a bare number a verifier has no
    way to argue with.
    """

    exam_stage: ExamStage
    score: float
    components: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class MatchResult:
    observation: Observation
    decision: Decision
    confidence: float
    exam_stage: ExamStage | None = None
    reason: TriageReason | None = None
    #: Best few candidates, best first - what a triage screen offers.
    candidates: tuple[Candidate, ...] = ()

    @property
    def matched(self) -> bool:
        return self.decision is Decision.AUTO_LINK

    def __str__(self) -> str:
        if self.matched:
            return f"{self.exam_stage} ({self.confidence:.2f})"
        detail = self.reason.value if self.reason else "unmatched"
        return f"triage: {detail} ({self.confidence:.2f})"


# --- configuration -----------------------------------------------------


def auto_link_threshold() -> float:
    return float(getattr(settings, "MATCH_AUTO_LINK_THRESHOLD", DEFAULT_AUTO_LINK_THRESHOLD))


def ambiguity_margin() -> float:
    return float(getattr(settings, "MATCH_AMBIGUITY_MARGIN", DEFAULT_AMBIGUITY_MARGIN))


# --- text handling -----------------------------------------------------


def _tokens(text: str) -> list[str]:
    """Lowercase word tokens, with years, stage words and filler removed.

    Years come out because they are compared separately and exactly -
    leaving them in would let "2025" and "2026" contribute *similarity*
    to a pair that should be disqualified.
    """
    cleaned = _YEAR_RE.sub(" ", text.lower())
    words = [word for word in _NON_WORD.split(cleaned) if word]
    return [w for w in words if w not in _NOISE and w not in _STAGE_WORDS]


def _year_in(text: str) -> int | None:
    """The cycle year named in the text, if it names exactly one.

    Two different years in one title ("CDS (II), 2025 for the 2026
    course") identify nothing, so that returns None and the cycle is
    simply treated as unconfirmed rather than half-guessed.
    """
    years = {int(match) for match in _YEAR_RE.findall(text)}
    return years.pop() if len(years) == 1 else None


#: Shorter than this, a code is too generic to treat as proof of
#: identity - "AC" or "II" turn up inside unrelated titles constantly.
_MIN_CODE_LENGTH = 3


def _mentions_code(text: str, code: str) -> bool:
    """Whether the text names this exam code as a whole token.

    A plain substring test is not safe here: IBPS runs consecutive cycles
    called CRP-CSA-XV and CRP-CSA-XVI, and "crp-csa-xv" *is* a substring
    of "crp-csa-xvi". That would score last cycle's exam a perfect 1.0 for
    an observation about this one - the worst possible failure for a
    matcher, because it is both confident and wrong.

    The lookarounds reject a code followed or preceded by another word
    character while still allowing the hyphens and spaces that separate a
    code from a year.
    """
    code = code.strip()
    if len(code) < _MIN_CODE_LENGTH:
        return False
    pattern = rf"(?<!\w){re.escape(code)}(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _similarity(observed: str, candidate: str) -> float:
    """Token overlap and character sequence, combined.

    Token overlap alone treats "Civil Services" and "Services Civil" as
    identical, which is usually what we want for reordered titles;
    sequence ratio alone punishes a title that merely has extra words.
    Neither is sufficient, so both contribute.
    """
    left, right = _tokens(observed), _tokens(candidate)
    if not left or not right:
        return 0.0

    left_set, right_set = set(left), set(right)
    overlap = len(left_set & right_set) / len(left_set | right_set)
    sequence = SequenceMatcher(None, " ".join(left), " ".join(right)).ratio()
    return 0.6 * overlap + 0.4 * sequence


def stage_type_from_hint(hint: str) -> str | None:
    """Map a parser's stage wording onto a StageType, if it said one."""
    for word in _NON_WORD.split(hint.lower()):
        if word in _STAGE_WORDS:
            return _STAGE_WORDS[word]
    return None


# --- scoring -----------------------------------------------------------


def _score_exam(
    observation: Observation, exam: Exam
) -> tuple[float, dict[str, float]] | None:
    """Score one Exam, or None when it is disqualified outright."""
    observed_year = _year_in(observation.exam_name)
    if observed_year is not None and observed_year != exam.cycle_year:
        # Not a weak match - a different cycle of the exam.
        return None

    by_name = _similarity(observation.exam_name, exam.name)
    by_code = _similarity(observation.exam_name, exam.code)
    # A board's own code appearing verbatim is the strongest signal there
    # is: IBPS publishes "CRP-CSA-XV" and that is exactly the exam code.
    exact_code = _mentions_code(observation.exam_name, exam.code)

    score = 1.0 if exact_code else max(by_name, by_code)
    components = {
        "name": round(by_name, 3),
        "code": round(by_code, 3),
        "exact_code": 1.0 if exact_code else 0.0,
        "year": 1.0 if observed_year is not None else 0.0,
    }

    if observed_year is None:
        # The cycle could not be confirmed. Not disqualifying - plenty of
        # notices omit the year - but it is one fewer thing agreeing, and
        # an unconfirmed cycle is exactly how an observation lands on last
        # year's exam.
        score *= 0.9

    return score, components


def _stage_for(observation: Observation, exam: Exam) -> tuple[ExamStage | None, bool]:
    """Pick the stage within a matched exam.

    Returns (stage, unambiguous). A single-stage exam needs no hint. A
    multi-stage exam without one is genuinely undecidable: "Final Result:
    Civil Services Examination" does not say whether it means prelims,
    mains or interview, and choosing would be inventing the answer.
    """
    stages = list(exam.stages.all())
    if not stages:
        return None, False

    wanted = stage_type_from_hint(f"{observation.stage_hint} {observation.exam_name}")
    if wanted:
        for stage in stages:
            if stage.stage_type == wanted:
                return stage, True
        # The page named a stage this exam does not have. Refusing beats
        # falling back to "the only other one".
        return None, False

    if len(stages) == 1:
        return stages[0], True
    return None, False


def score_candidates(observation: Observation, *, board: Board | None = None) -> list[Candidate]:
    """Every plausible ExamStage for this observation, best first.

    Scoping to the observation's own board is the single biggest source
    of precision here, and the caller always knows it - a Source belongs
    to exactly one board.
    """
    exams = Exam.objects.prefetch_related("stages")
    if board is not None:
        exams = exams.filter(board=board)

    candidates: list[Candidate] = []
    for exam in exams:
        scored = _score_exam(observation, exam)
        if scored is None:
            continue
        score, components = scored
        stage, unambiguous = _stage_for(observation, exam)
        if stage is None:
            continue
        candidates.append(
            Candidate(
                exam_stage=stage,
                score=round(score, 4),
                components=components | {"stage_unambiguous": 1.0 if unambiguous else 0.0},
            )
        )

    candidates.sort(key=lambda c: (-c.score, c.exam_stage.pk))
    return candidates


def match_observation(observation: Observation, *, board: Board | None = None) -> MatchResult:
    """Decide which ExamStage an observation belongs to, or that a human
    must."""
    candidates = score_candidates(observation, board=board)

    if not candidates:
        # Distinguish "nothing matched" from "matched an exam but not a
        # stage": the second is a data-completeness problem a verifier can
        # fix by adding the stage, the first usually means a new exam.
        stage_only = _exam_matched_but_stage_unknown(observation, board=board)
        return MatchResult(
            observation=observation,
            decision=Decision.TRIAGE,
            confidence=0.0,
            reason=TriageReason.NO_STAGE if stage_only else TriageReason.NO_CANDIDATES,
        )

    best = candidates[0]
    top = tuple(candidates[:5])

    # Threshold before ambiguity, so the reason given is the honest one.
    # Checked the other way round, two equally *bad* candidates report
    # "could not choose between them" when the truth is that neither was
    # close - and a verifier would waste time comparing two wrong answers.
    # Ambiguity is only interesting when it blocks a link that would
    # otherwise have gone through.
    if best.score < auto_link_threshold():
        return MatchResult(
            observation=observation,
            decision=Decision.TRIAGE,
            confidence=best.score,
            reason=TriageReason.BELOW_THRESHOLD,
            candidates=top,
        )

    if len(candidates) > 1 and (best.score - candidates[1].score) < ambiguity_margin():
        return MatchResult(
            observation=observation,
            decision=Decision.TRIAGE,
            confidence=best.score,
            reason=TriageReason.AMBIGUOUS,
            candidates=top,
        )

    return MatchResult(
        observation=observation,
        decision=Decision.AUTO_LINK,
        confidence=best.score,
        exam_stage=best.exam_stage,
        candidates=top,
    )


def _exam_matched_but_stage_unknown(observation: Observation, *, board: Board | None) -> bool:
    exams = Exam.objects.prefetch_related("stages")
    if board is not None:
        exams = exams.filter(board=board)

    for exam in exams:
        scored = _score_exam(observation, exam)
        if scored is None:
            continue
        if scored[0] >= auto_link_threshold():
            return True
    return False


# --- acting on the decision --------------------------------------------


@transaction.atomic
def link_observation(
    observation: Observation, *, board: Board | None = None
) -> tuple[MatchResult, ObservationResult | None]:
    """Match, and write the observation when the match is confident.

    Writing goes through `apply_machine_observation`, which is the only
    path allowed to touch machine status - so a confident match still
    cannot overwrite a fresh human verification, it records a conflict.
    The matcher does not get to bypass that rule by being sure.

    Below the threshold nothing is written and the result says why. The
    queue those land in is EXT-051; this returns the decision it will be
    built on.
    """
    result = match_observation(observation, board=board)
    if not result.matched or result.exam_stage is None:
        return result, None

    applied = apply_machine_observation(
        exam_stage=result.exam_stage,
        track=observation.track,
        value=observation.value,
        # The parser's confidence in the reading, not the match score -
        # machine_confidence has always meant "how sure are we of this
        # value", and the match score answers a different question.
        confidence=observation.confidence,
    )
    return result, applied
