"""Tests for exact transcript-overlap de-duplication."""

import pytest
from app.application.exceptions import ApplicationValidationError
from app.application.services import TranscriptDeduplicator


@pytest.mark.parametrize("max_overlap_tokens", [0, -1, True])
def test_constructor_requires_a_positive_integer(max_overlap_tokens: int) -> None:
    """The overlap cap must be a positive non-boolean integer."""

    with pytest.raises(ApplicationValidationError, match="positive integer"):
        TranscriptDeduplicator(max_overlap_tokens=max_overlap_tokens)


def test_blank_current_text_returns_none() -> None:
    """Whitespace-only current text has no transcript content to retain."""

    assert (
        TranscriptDeduplicator().deduplicate(
            previous_text="Earlier text",
            current_text="  \n ",
        )
        is None
    )


@pytest.mark.parametrize("previous_text", [None, "  "])
def test_missing_previous_text_preserves_trimmed_current_text(
    previous_text: str | None,
) -> None:
    """The first chunk retains its original casing and punctuation."""

    assert (
        TranscriptDeduplicator().deduplicate(
            previous_text=previous_text,
            current_text="  Hello, World!  ",
        )
        == "Hello, World!"
    )


def test_full_exact_duplicate_returns_none() -> None:
    """A fully duplicated normalized current chunk is discarded."""

    assert (
        TranscriptDeduplicator().deduplicate(
            previous_text="Hello, World!",
            current_text="hello world",
        )
        is None
    )


def test_duplicated_prefix_is_removed_with_original_remainder_preserved() -> None:
    """Only the original current prefix is removed from a partial duplicate."""

    assert (
        TranscriptDeduplicator().deduplicate(
            previous_text="We will ship next week",
            current_text="SHIP next week — Please Review!",
        )
        == "— Please Review!"
    )


def test_longest_exact_overlap_is_selected() -> None:
    """The longest matching prior suffix wins over shorter valid overlaps."""

    assert (
        TranscriptDeduplicator().deduplicate(
            previous_text="One two three four",
            current_text="two three four five",
        )
        == "five"
    )


def test_overlap_is_limited_by_configured_token_cap() -> None:
    """The configured cap prevents comparison beyond its maximum token count."""

    assert (
        TranscriptDeduplicator(max_overlap_tokens=2).deduplicate(
            previous_text="one two one two one two",
            current_text="one two one two new",
        )
        == "one two new"
    )


def test_case_and_surrounding_punctuation_do_not_prevent_matching() -> None:
    """Comparison ignores case and surrounding ASCII punctuation only."""

    assert (
        TranscriptDeduplicator().deduplicate(
            previous_text="HELLO, world!",
            current_text="hello WORLD... Again.",
        )
        == "Again."
    )


def test_no_overlap_preserves_original_current_text() -> None:
    """Unrelated chunks preserve the trimmed original current input exactly."""

    assert (
        TranscriptDeduplicator().deduplicate(
            previous_text="Earlier words",
            current_text="  Fresh  Text!  ",
        )
        == "Fresh  Text!"
    )


def test_repeated_words_are_handled_deterministically() -> None:
    """Repeated words still use the longest exact suffix/prefix match."""

    assert (
        TranscriptDeduplicator().deduplicate(
            previous_text="go go go",
            current_text="go go now",
        )
        == "now"
    )


def test_inputs_are_not_modified() -> None:
    """String inputs remain unchanged after comparison and de-duplication."""

    previous_text = "Hello, World!"
    current_text = "WORLD! Keep This."

    TranscriptDeduplicator().deduplicate(
        previous_text=previous_text,
        current_text=current_text,
    )

    assert previous_text == "Hello, World!"
    assert current_text == "WORLD! Keep This."


def test_partial_tokens_are_not_matched() -> None:
    """Exact token matching never treats one token as another token's prefix."""

    assert (
        TranscriptDeduplicator().deduplicate(
            previous_text="hello",
            current_text="helloworld continues",
        )
        == "helloworld continues"
    )
