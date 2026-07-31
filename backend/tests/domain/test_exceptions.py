"""Tests for the domain exception hierarchy."""

import pytest
from app.domain import (
    DomainError,
    InvalidStateTransitionError,
    InvariantViolationError,
    ValidationError,
)


@pytest.mark.parametrize(
    "exception_type",
    [ValidationError, InvariantViolationError, InvalidStateTransitionError],
)
def test_domain_exceptions_inherit_from_domain_error(
    exception_type: type[DomainError],
) -> None:
    """Specialized domain exceptions inherit from the common base type."""

    assert issubclass(exception_type, DomainError)


@pytest.mark.parametrize(
    "exception_type",
    [
        DomainError,
        ValidationError,
        InvariantViolationError,
        InvalidStateTransitionError,
    ],
)
def test_domain_exceptions_can_be_raised(
    exception_type: type[DomainError],
) -> None:
    """Every domain exception behaves as a normal Python exception."""

    with pytest.raises(exception_type):
        raise exception_type()


@pytest.mark.parametrize(
    "exception_type",
    [
        DomainError,
        ValidationError,
        InvariantViolationError,
        InvalidStateTransitionError,
    ],
)
def test_custom_domain_exception_message_is_preserved(
    exception_type: type[DomainError],
) -> None:
    """A custom message is retained verbatim."""

    assert str(exception_type("specific failure")) == "specific failure"


@pytest.mark.parametrize(
    ("exception_type", "expected_message"),
    [
        (DomainError, "A domain error occurred."),
        (ValidationError, "Domain validation failed."),
        (InvariantViolationError, "A domain invariant was violated."),
        (InvalidStateTransitionError, "The requested state transition is invalid."),
    ],
)
def test_domain_exception_uses_default_message(
    exception_type: type[DomainError], expected_message: str
) -> None:
    """Each exception supplies a useful default message."""

    assert str(exception_type()) == expected_message
