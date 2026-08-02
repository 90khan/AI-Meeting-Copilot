"""Deterministic removal of exact transcript overlap between audio chunks."""

import re
import string

from app.application.exceptions import ApplicationValidationError


class TranscriptDeduplicator:
    """Remove exact normalized token overlap while retaining original text tokens."""

    def __init__(self, *, max_overlap_tokens: int = 24) -> None:
        """Initialize the maximum suffix/prefix overlap considered per call."""

        if (
            not isinstance(max_overlap_tokens, int)
            or isinstance(max_overlap_tokens, bool)
            or max_overlap_tokens <= 0
        ):
            raise ApplicationValidationError(
                "Maximum overlap tokens must be a positive integer."
            )

        self._max_overlap_tokens = max_overlap_tokens

    def deduplicate(
        self,
        *,
        previous_text: str | None,
        current_text: str,
    ) -> str | None:
        """Remove the longest exact prior suffix from the current text prefix."""

        current_trimmed = current_text.strip()
        if not current_trimmed:
            return None
        if previous_text is None or not previous_text.strip():
            return current_trimmed

        previous_tokens = self._normalized_tokens(previous_text)
        current_tokens = self._normalized_current_tokens(current_trimmed)
        if not previous_tokens or not current_tokens:
            return current_trimmed

        maximum_overlap = min(
            self._max_overlap_tokens,
            len(previous_tokens),
            len(current_tokens),
        )
        overlap_size = 0
        current_normalized_tokens = [token[0] for token in current_tokens]
        for candidate_size in range(maximum_overlap, 0, -1):
            if (
                previous_tokens[-candidate_size:]
                == current_normalized_tokens[:candidate_size]
            ):
                overlap_size = candidate_size
                break

        if overlap_size == 0:
            return current_trimmed
        if overlap_size == len(current_tokens):
            return None

        last_overlap_end = current_tokens[overlap_size - 1][2]
        return current_trimmed[last_overlap_end:].strip() or None

    @staticmethod
    def _normalized_tokens(text: str) -> list[str]:
        """Return normalized non-empty whitespace tokens for comparison."""

        return [
            normalized_token
            for token in text.split()
            if (normalized_token := token.lower().strip(string.punctuation))
        ]

    @staticmethod
    def _normalized_current_tokens(text: str) -> list[tuple[str, int, int]]:
        """Keep normalized tokens with original spans for precise retained text."""

        tokens: list[tuple[str, int, int]] = []
        for match in re.finditer(r"\S+", text):
            normalized_token = match.group().lower().strip(string.punctuation)
            if normalized_token:
                tokens.append((normalized_token, match.start(), match.end()))
        return tokens
