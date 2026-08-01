"""Language-code value types for AI application contracts."""

import re
from dataclasses import dataclass

from app.application.exceptions import ApplicationValidationError

_LANGUAGE_CODE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[a-z]{2,4})?$")


@dataclass(frozen=True, slots=True, kw_only=True)
class LanguageCode:
    """An immutable, normalized ISO-style language code."""

    value: str

    def __post_init__(self) -> None:
        """Normalize and validate the language code."""

        normalized_value = self.value.replace("_", "-").lower()
        if not normalized_value.strip():
            raise ApplicationValidationError("Language code must not be blank.")
        if not _LANGUAGE_CODE_PATTERN.fullmatch(normalized_value):
            raise ApplicationValidationError("Language code is malformed.")

        object.__setattr__(self, "value", normalized_value)

    def __str__(self) -> str:
        """Return the canonical language-code representation."""

        return self.value
