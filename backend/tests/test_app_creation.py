"""FastAPI bootstrap smoke tests."""

from app.main import create_app
from fastapi import FastAPI


def test_create_app_returns_fastapi_application() -> None:
    """The application factory returns a FastAPI instance."""

    assert isinstance(create_app(), FastAPI)
