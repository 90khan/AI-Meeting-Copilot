"""Tests for AI language-code value objects."""

from dataclasses import FrozenInstanceError

import pytest
from app.application.dto.ai import LanguageCode
from app.application.exceptions import ApplicationValidationError


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("DE", "de"),
        ("de_de", "de-de"),
        ("tr-TR", "tr-tr"),
        ("en", "en"),
    ],
)
def test_language_code_normalizes_to_a_canonical_value(
    source: str,
    expected: str,
) -> None:
    """Language codes lower-case letters and canonicalize separators."""

    language_code = LanguageCode(value=source)

    assert language_code.value == expected
    assert str(language_code) == expected


def test_language_codes_compare_by_their_canonical_value() -> None:
    """Equivalent source formats become equal immutable values."""

    assert LanguageCode(value="DE_de") == LanguageCode(value="de-DE")


def test_language_code_is_immutable() -> None:
    """A validated language code cannot be changed."""

    language_code = LanguageCode(value="de")

    with pytest.raises(FrozenInstanceError):
        language_code.value = "tr"


@pytest.mark.parametrize("value", ["", "   ", "d", "de--DE", "de_DE_TR", "de!"])
def test_language_code_rejects_blank_and_malformed_values(value: str) -> None:
    """Only simple ISO-style language-code forms are accepted."""

    with pytest.raises(ApplicationValidationError):
        LanguageCode(value=value)
