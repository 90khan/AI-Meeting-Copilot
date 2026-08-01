"""Tests for German simplification application DTOs."""

from dataclasses import FrozenInstanceError

import pytest
from app.application.dto.ai import (
    GermanLevel,
    GermanSimplificationRequest,
    GermanSimplificationResult,
)
from app.application.exceptions import ApplicationValidationError


def test_german_levels_have_the_expected_string_values() -> None:
    """German levels use the supported CEFR string values."""

    assert [level.value for level in GermanLevel] == ["a2", "b1", "b2", "c1"]
    assert GermanLevel.B1 == "b1"


def test_simplification_request_is_immutable() -> None:
    """Simplification request fields cannot change after construction."""

    request = GermanSimplificationRequest(
        text="Das ist ein komplexer Satz.",
        target_level=GermanLevel.B1,
    )

    with pytest.raises(FrozenInstanceError):
        request.target_level = GermanLevel.A2  # type: ignore[misc]


@pytest.mark.parametrize("text", ["", "   "])
def test_simplification_request_rejects_blank_text(text: str) -> None:
    """Simplification requests require text."""

    with pytest.raises(ApplicationValidationError):
        GermanSimplificationRequest(text=text, target_level=GermanLevel.B1)


def test_simplification_result_is_immutable() -> None:
    """Simplification result fields cannot change after construction."""

    result = GermanSimplificationResult(
        original_text="Das ist ein komplexer Satz.",
        simplified_text="Das ist ein Satz.",
        target_level=GermanLevel.B1,
    )

    with pytest.raises(FrozenInstanceError):
        result.simplified_text = "Ein Satz."  # type: ignore[misc]


@pytest.mark.parametrize(
    ("original_text", "simplified_text"),
    [("", "Ein Satz."), ("Original", "   ")],
)
def test_simplification_result_rejects_blank_text(
    original_text: str, simplified_text: str
) -> None:
    """Simplification results require both original and simplified text."""

    with pytest.raises(ApplicationValidationError):
        GermanSimplificationResult(
            original_text=original_text,
            simplified_text=simplified_text,
            target_level=GermanLevel.B1,
        )
