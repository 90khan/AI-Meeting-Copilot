"""Application-layer interfaces."""

from app.application.interfaces.ai import (
    GermanSimplificationProvider,
    SpeechToTextProvider,
    TranslationProvider,
)
from app.application.interfaces.unit_of_work import UnitOfWork
from app.application.interfaces.unit_of_work_factory import UnitOfWorkFactory

__all__ = [
    "GermanSimplificationProvider",
    "SpeechToTextProvider",
    "TranslationProvider",
    "UnitOfWork",
    "UnitOfWorkFactory",
]
