"""Base abstraction for identity-bearing domain entities."""

from typing import Never


class Entity[IdT]:
    """A domain object identified by a stable identity."""

    def __init__(self, entity_id: IdT) -> None:
        """Create an entity with its stable identity."""

        self._id = entity_id

    @property
    def id(self) -> IdT:
        """Return the entity's identity."""

        return self._id

    def __eq__(self, other: object) -> bool:
        """Compare entities by their concrete type and identity."""

        if not isinstance(other, Entity):
            return NotImplemented
        return type(self) is type(other) and self.id == other.id

    def __hash__(self) -> Never:
        """Prevent mutable entities from being used as hash keys."""

        message = f"unhashable type: '{type(self).__name__}'"
        raise TypeError(message)
