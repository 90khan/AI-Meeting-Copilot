"""Domain-specific exception types."""

from typing import ClassVar


class DomainError(Exception):
    """Base exception for domain-level failures."""

    DEFAULT_MESSAGE: ClassVar[str] = "A domain error occurred."

    def __init__(self, message: str | None = None) -> None:
        """Initialize the exception with a custom or default message."""

        super().__init__(message if message is not None else self.DEFAULT_MESSAGE)


class ValidationError(DomainError):
    """Raised when domain input fails validation."""

    DEFAULT_MESSAGE: ClassVar[str] = "Domain validation failed."


class InvariantViolationError(DomainError):
    """Raised when a domain invariant is violated."""

    DEFAULT_MESSAGE: ClassVar[str] = "A domain invariant was violated."


class InvalidStateTransitionError(DomainError):
    """Raised when a domain state transition is invalid."""

    DEFAULT_MESSAGE: ClassVar[str] = "The requested state transition is invalid."
