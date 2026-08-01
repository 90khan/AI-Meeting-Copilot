"""Application-layer interfaces."""

from app.application.interfaces.ai import SpeechToTextProvider
from app.application.interfaces.unit_of_work import UnitOfWork
from app.application.interfaces.unit_of_work_factory import UnitOfWorkFactory

__all__ = ["SpeechToTextProvider", "UnitOfWork", "UnitOfWorkFactory"]
