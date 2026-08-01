"""Tests for the German simplification provider contract."""

import inspect

from app.application.dto.ai import (
    GermanSimplificationRequest,
    GermanSimplificationResult,
)
from app.application.interfaces.ai import GermanSimplificationProvider


class FakeGermanSimplificationProvider:
    """Minimal structural implementation of the simplification contract."""

    async def simplify(
        self, request: GermanSimplificationRequest
    ) -> GermanSimplificationResult:
        """Return a predictable simplification result for the supplied request."""

        return GermanSimplificationResult(
            original_text=request.text,
            simplified_text=request.text,
            target_level=request.target_level,
        )


def test_german_simplification_provider_method_is_asynchronous() -> None:
    """The provider contract exposes an asynchronous simplify method."""

    assert inspect.iscoroutinefunction(GermanSimplificationProvider.simplify)


def test_fake_provider_structurally_satisfies_the_protocol() -> None:
    """A simplification adapter needs no nominal protocol inheritance."""

    provider: GermanSimplificationProvider = FakeGermanSimplificationProvider()

    assert isinstance(provider, GermanSimplificationProvider)
