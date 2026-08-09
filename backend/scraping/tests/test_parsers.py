"""EXT-043: the Parser base class contract and the key registry."""

from datetime import date

import pytest

from exams.models import StatusTrack
from scraping import parsers
from scraping.parsers import (
    DuplicateParserKey,
    Observation,
    Parser,
    ParserError,
    ParserNotFound,
    get_parser,
    is_registered,
    register,
    registered_keys,
)


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Each test gets a clean registry.

    The registry is module-level global state, so without this a parser
    registered by one test leaks into the next and key-collision tests
    start reporting phantom conflicts.
    """
    original = dict(parsers._REGISTRY)
    parsers._REGISTRY.clear()
    yield
    parsers._REGISTRY.clear()
    parsers._REGISTRY.update(original)


# --- the base class contract ------------------------------------------


def test_a_parser_must_implement_parse():
    """The interface is enforced, not merely documented."""

    class Incomplete(Parser):
        key = "incomplete"

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]


def test_a_parser_returns_observations_from_html():
    @register
    class Fake(Parser):
        key = "fake"

        def parse(self, html: str) -> list[Observation]:
            return [
                Observation(
                    exam_name="Civil Services Examination",
                    track=StatusTrack.Track.CONDUCT,
                    value="conducted",
                )
            ]

    observations = get_parser("fake").parse("<html/>")

    assert len(observations) == 1
    assert isinstance(observations[0], Observation)
    assert observations[0].exam_name == "Civil Services Examination"


def test_a_page_with_nothing_relevant_returns_an_empty_list():
    """Finding nothing is a normal outcome, not an error."""

    @register
    class Empty(Parser):
        key = "empty"

        def parse(self, html: str) -> list[Observation]:
            return []

    assert get_parser("empty").parse("<html/>") == []


# --- the registry -----------------------------------------------------


def test_a_parser_is_retrieved_by_its_key():
    @register
    class Upsc(Parser):
        key = "upsc_notices"

        def parse(self, html: str) -> list[Observation]:
            return []

    assert isinstance(get_parser("upsc_notices"), Upsc)
    assert is_registered("upsc_notices") is True
    assert "upsc_notices" in registered_keys()


def test_an_unknown_key_raises_and_names_what_is_available():
    """Source.parser_key is free text, so a typo lands here - the message
    has to say what was expected."""

    @register
    class Known(Parser):
        key = "known_parser"

        def parse(self, html: str) -> list[Observation]:
            return []

    with pytest.raises(ParserNotFound) as exc:
        get_parser("typo_parser")

    assert "typo_parser" in str(exc.value)
    assert "known_parser" in str(exc.value)


def test_an_unknown_key_is_clear_even_when_nothing_is_registered():
    with pytest.raises(ParserNotFound) as exc:
        get_parser("anything")

    assert "none registered" in str(exc.value)


def test_two_parsers_cannot_claim_the_same_key():
    """Silently overwriting would mean a board is scraped by whichever
    module imported last."""

    @register
    class First(Parser):
        key = "shared"

        def parse(self, html: str) -> list[Observation]:
            return []

    with pytest.raises(DuplicateParserKey):

        @register
        class Second(Parser):
            key = "shared"

            def parse(self, html: str) -> list[Observation]:
                return []


def test_registering_the_same_class_twice_is_harmless():
    """Module re-import shouldn't look like a collision."""

    class Once(Parser):
        key = "idempotent"

        def parse(self, html: str) -> list[Observation]:
            return []

    register(Once)
    register(Once)

    assert registered_keys() == ["idempotent"]


def test_a_parser_without_a_key_is_rejected():
    with pytest.raises(ParserError):

        @register
        class Keyless(Parser):
            def parse(self, html: str) -> list[Observation]:
                return []


def test_get_parser_returns_a_fresh_instance_each_call():
    """Parsers are cheap; sharing one instance across concurrent scrape
    runs would invite accidental state between boards."""

    @register
    class Stateless(Parser):
        key = "stateless"

        def parse(self, html: str) -> list[Observation]:
            return []

    assert get_parser("stateless") is not get_parser("stateless")


# --- Observation validation -------------------------------------------


def test_an_observation_carries_what_the_matcher_and_verifier_need():
    observation = Observation(
        exam_name="Civil Services Examination",
        track=StatusTrack.Track.RESULT,
        value="declared",
        stage_hint="Prelims",
        observed_date=date(2026, 6, 1),
        confidence=0.8,
        source_url="https://upsc.test/notices",
        raw_text="Result declared for CSE Prelims 2026",
    )

    assert observation.stage_hint == "Prelims"
    assert observation.observed_date == date(2026, 6, 1)
    assert observation.confidence == 0.8
    assert observation.source_url == "https://upsc.test/notices"


def test_an_observation_rejects_a_track_that_is_not_a_status_track():
    """Tracks must line up with StatusTrack, or nothing downstream can
    apply the observation."""
    with pytest.raises(ValueError):
        Observation(exam_name="x", track="rumour", value="y")


@pytest.mark.parametrize("confidence", [-0.1, 1.5])
def test_an_observation_rejects_confidence_outside_zero_to_one(confidence):
    """machine_confidence is validated to 0-1 on StatusTrack; catching it
    here means a bad parser fails at its own boundary."""
    with pytest.raises(ValueError):
        Observation(
            exam_name="x",
            track=StatusTrack.Track.CONDUCT,
            value="y",
            confidence=confidence,
        )


def test_an_observation_requires_an_exam_name():
    with pytest.raises(ValueError):
        Observation(exam_name="   ", track=StatusTrack.Track.CONDUCT, value="y")


def test_observations_are_immutable():
    """A parser's output is a statement of what the page said; later
    stages annotate around it rather than editing it."""
    observation = Observation(
        exam_name="x", track=StatusTrack.Track.CONDUCT, value="conducted"
    )

    with pytest.raises(Exception):
        observation.value = "cancelled"  # type: ignore[misc]


# --- autodiscovery ----------------------------------------------------


def test_ready_imports_parser_modules_so_they_self_register(tmp_path):
    """Drops a real parser module into the package, runs the app's own
    ready(), and checks it registered - asserting the mechanism rather
    than that the attributes exist."""
    import sys
    from pathlib import Path

    from django.apps import apps as django_apps

    from scraping import board_parsers

    source = '''
from scraping.parsers import Parser, register


@register
class TmpParser(Parser):
    key = "tmp_autodiscovered"

    def parse(self, html):
        return []
'''
    module_path = Path(board_parsers.__path__[0]) / "_tmp_autodiscover.py"
    module_path.write_text(source, encoding="utf-8")

    try:
        assert is_registered("tmp_autodiscovered") is False

        django_apps.get_app_config("scraping").ready()

        assert is_registered("tmp_autodiscovered") is True
        assert isinstance(get_parser("tmp_autodiscovered"), Parser)
    finally:
        module_path.unlink(missing_ok=True)
        sys.modules.pop(f"{board_parsers.__name__}._tmp_autodiscover", None)
