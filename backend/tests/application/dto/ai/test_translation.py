"""Tests for translation application DTOs."""

from dataclasses import FrozenInstanceError

import pytest
from app.application.dto.ai import LanguageCode, TranslationRequest, TranslationResult
from app.application.exceptions import ApplicationValidationError


def test_translation_request_is_immutable() -> None:
    """Translation request fields cannot change after construction."""

    request = TranslationRequest(
        text="Hallo",
        source_language=LanguageCode(value="de"),
        target_language=LanguageCode(value="en"),
    )

    with pytest.raises(FrozenInstanceError):
        request.text = "Merhaba"  # type: ignore[misc]


@pytest.mark.parametrize("text", ["", "   "])
def test_translation_request_rejects_blank_text(text: str) -> None:
    """Translation requests require source text."""

    with pytest.raises(ApplicationValidationError):
        TranslationRequest(
            text=text,
            source_language=None,
            target_language=LanguageCode(value="en"),
        )


def test_translation_request_rejects_identical_source_and_target_languages() -> None:
    """A translation request must change languages when source is known."""

    with pytest.raises(ApplicationValidationError):
        TranslationRequest(
            text="Hallo",
            source_language=LanguageCode(value="DE"),
            target_language=LanguageCode(value="de"),
        )


def test_translation_request_allows_an_unknown_source_language() -> None:
    """The provider may detect the source language when it is not supplied."""

    request = TranslationRequest(
        text="Hallo",
        source_language=None,
        target_language=LanguageCode(value="en"),
    )

    assert request.source_language is None
    assert request.preserve_formatting is True


def test_translation_result_is_immutable() -> None:
    """Translation result fields cannot change after construction."""

    result = TranslationResult(
        translated_text="Hello",
        source_language=LanguageCode(value="de"),
        target_language=LanguageCode(value="en"),
    )

    with pytest.raises(FrozenInstanceError):
        result.translated_text = "Hi"  # type: ignore[misc]


@pytest.mark.parametrize("translated_text", ["", "   "])
def test_translation_result_rejects_blank_text(translated_text: str) -> None:
    """Translation results require returned text."""

    with pytest.raises(ApplicationValidationError):
        TranslationResult(
            translated_text=translated_text,
            source_language=LanguageCode(value="de"),
            target_language=LanguageCode(value="en"),
        )


def test_translation_result_rejects_identical_languages() -> None:
    """A translation result must describe a language change."""

    with pytest.raises(ApplicationValidationError):
        TranslationResult(
            translated_text="Hallo",
            source_language=LanguageCode(value="de"),
            target_language=LanguageCode(value="de"),
        )
