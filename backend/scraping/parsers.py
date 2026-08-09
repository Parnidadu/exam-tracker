"""Parser interface and registry.

A parser turns one board's raw HTML into `Observation`s - loose, unmatched
statements of what the page appeared to say. Deliberately *unmatched*:
tying an observation to a specific ExamStage is EXT-050's job, and doing
it here would bury fuzzy-matching rules inside every per-board parser.

Adding a board means writing a Parser subclass and registering it under a
key; nothing else in the pipeline changes.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import date

from exams.models import StatusTrack


class ParserError(Exception):
    """Base class for registry problems."""


class ParserNotFound(ParserError):
    """No parser is registered under the requested key."""


class DuplicateParserKey(ParserError):
    """Two parsers claimed the same key."""


@dataclass(frozen=True)
class Observation:
    """One thing a page appeared to say about one exam.

    A plain value object, not a model. It carries no ExamStage reference
    because the parser cannot know which stage it belongs to - the text on
    a notice board is free-form, and resolving it is EXT-050. Persisting
    observations that fail to match is EXT-051.
    """

    #: Exam name exactly as it appeared, for the matcher to work from.
    exam_name: str
    #: Which status track this speaks to.
    track: str
    #: The observed value, e.g. "conducted" or "declared".
    value: str
    #: Stage wording as seen ("Prelims", "Paper I"), when the page says.
    stage_hint: str = ""
    #: Date the page attributes to the event, if it stated one.
    observed_date: date | None = None
    #: How sure the parser is, 0-1. Feeds StatusTrack.machine_confidence.
    confidence: float = 1.0
    #: Where this came from, so a verifier can check the claim.
    source_url: str = ""
    #: The snippet the parser read it from, for evidence and debugging.
    raw_text: str = ""
    #: Anything board-specific a later stage might want.
    extra: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.track not in StatusTrack.Track.values:
            raise ValueError(
                f"{self.track!r} is not a status track "
                f"(expected one of {StatusTrack.Track.values})."
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be between 0 and 1, got {self.confidence}.")
        if not self.exam_name.strip():
            raise ValueError("exam_name is required - the matcher has nothing to work from.")


class Parser(abc.ABC):
    """Base class every board parser implements.

    Subclasses declare a `key` and implement `parse`. Registration is
    explicit via @register rather than automatic on subclassing, so an
    abstract intermediate or a test double doesn't silently claim a key.
    """

    #: Registry key, matching Source.parser_key.
    key: str = ""

    @abc.abstractmethod
    def parse(self, html: str) -> list[Observation]:
        """Turn a page's HTML into observations.

        Returns an empty list when the page holds nothing relevant - that
        is a normal outcome, not an error.
        """


_REGISTRY: dict[str, type[Parser]] = {}


def register(parser_cls: type[Parser]) -> type[Parser]:
    """Class decorator: add a parser to the registry under its `key`."""
    key = getattr(parser_cls, "key", "")
    if not key:
        raise ParserError(f"{parser_cls.__name__} must define a non-empty `key`.")
    if key in _REGISTRY and _REGISTRY[key] is not parser_cls:
        raise DuplicateParserKey(
            f"{key!r} is already registered to {_REGISTRY[key].__name__}; "
            f"{parser_cls.__name__} cannot claim it too."
        )
    _REGISTRY[key] = parser_cls
    return parser_cls


def get_parser(key: str) -> Parser:
    """Instantiate the parser registered under `key`.

    Raises ParserNotFound naming the available keys - a Source can hold any
    string in parser_key, so a typo surfaces here and the message should
    say what was expected.
    """
    try:
        parser_cls = _REGISTRY[key]
    except KeyError:
        available = ", ".join(sorted(_REGISTRY)) or "none registered"
        raise ParserNotFound(f"No parser registered for {key!r}. Available: {available}.") from None
    return parser_cls()


def registered_keys() -> list[str]:
    return sorted(_REGISTRY)


def is_registered(key: str) -> bool:
    return key in _REGISTRY
