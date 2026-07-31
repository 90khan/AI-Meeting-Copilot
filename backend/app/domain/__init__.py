"""Framework-independent domain package."""

from app.domain.exceptions import (
    DomainError,
    InvalidStateTransitionError,
    InvariantViolationError,
    ValidationError,
)

__all__ = [
    "DomainError",
    "InvalidStateTransitionError",
    "InvariantViolationError",
    "ValidationError",
]
