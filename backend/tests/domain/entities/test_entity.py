"""Tests for the base entity abstraction."""

import pytest
from app.domain.entities import Entity


class ExampleEntity(Entity[str]):
    """Concrete entity used to verify base behavior."""


class OtherEntity(Entity[str]):
    """Different concrete entity used to verify equality boundaries."""


def test_entities_with_the_same_type_and_identity_are_equal() -> None:
    """Entities compare equal when their concrete type and identity match."""

    assert ExampleEntity("entity-1") == ExampleEntity("entity-1")


def test_entities_with_different_identities_are_not_equal() -> None:
    """Entities with distinct identities are not equal."""

    assert ExampleEntity("entity-1") != ExampleEntity("entity-2")


def test_entities_with_different_types_are_not_equal() -> None:
    """Different concrete entity types do not compare equal."""

    assert ExampleEntity("entity-1") != OtherEntity("entity-1")


def test_entities_are_not_hashable() -> None:
    """Mutable entities cannot be used as hash keys."""

    with pytest.raises(TypeError):
        hash(ExampleEntity("entity-1"))
