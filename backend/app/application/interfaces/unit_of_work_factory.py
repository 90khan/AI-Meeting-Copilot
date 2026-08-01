"""Factory contract for application Unit of Work instances."""

from typing import Protocol

from app.application.interfaces.unit_of_work import UnitOfWork


class UnitOfWorkFactory(Protocol):
    """Create an independent Unit of Work for one application operation."""

    def __call__(self) -> UnitOfWork:
        """Create a new Unit of Work instance."""

        ...
