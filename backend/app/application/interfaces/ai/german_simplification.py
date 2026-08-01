"""German simplification provider contract."""

from typing import Protocol, runtime_checkable

from app.application.dto.ai.simplification import (
    GermanSimplificationRequest,
    GermanSimplificationResult,
)


@runtime_checkable
class GermanSimplificationProvider(Protocol):
    """Provides asynchronous German text simplification."""

    async def simplify(
        self, request: GermanSimplificationRequest
    ) -> GermanSimplificationResult:
        """Simplify a complete German-text request to the requested level."""
