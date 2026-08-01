"""Shared asynchronous client wrapper for a local Ollama server."""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable
from typing import Protocol, cast

import httpx
from ollama import AsyncClient, ResponseError

from app.application.exceptions import (
    InvalidProviderResponseError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


class _AsyncOllamaClient(Protocol):
    """Private minimum interface required from the official async SDK client."""

    def chat(self, *args: object, **kwargs: object) -> Awaitable[object]:
        """Send a non-streaming Ollama chat request."""


class OllamaClient:
    """Submit non-streaming structured chat requests to one Ollama server."""

    def __init__(
        self,
        *,
        base_url: str,
        request_timeout_seconds: float,
        temperature: float,
        context_length: int,
        keep_alive: str,
        _client: _AsyncOllamaClient | None = None,
    ) -> None:
        """Create one SDK client without contacting the Ollama server."""

        self._temperature = temperature
        self._context_length = context_length
        self._keep_alive = keep_alive
        self._closed = False
        try:
            self._client = _client or cast(
                _AsyncOllamaClient,
                AsyncClient(host=base_url, timeout=request_timeout_seconds),
            )
        except (KeyboardInterrupt, SystemExit):
            raise
        except ProviderError:
            raise
        except Exception as error:
            raise ProviderUnavailableError(
                "Ollama client could not be created."
            ) from error

    async def generate_structured(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, object],
    ) -> dict[str, object]:
        """Generate one schema-constrained JSON object without streaming."""

        if self._closed:
            raise ProviderUnavailableError("Ollama client is closed.")

        try:
            response = await self._client.chat(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                format=schema,
                stream=False,
                keep_alive=self._keep_alive,
                options={
                    "temperature": self._temperature,
                    "num_ctx": self._context_length,
                },
            )
        except asyncio.CancelledError:
            raise
        except (KeyboardInterrupt, SystemExit):
            raise
        except ProviderError:
            raise
        except httpx.TimeoutException as error:
            raise ProviderTimeoutError("Ollama request timed out.") from error
        except TimeoutError as error:
            raise ProviderTimeoutError("Ollama request timed out.") from error
        except httpx.TransportError as error:
            raise ProviderUnavailableError("Ollama server is unavailable.") from error
        except ResponseError as error:
            raise _translate_response_error(error) from error
        except Exception as error:
            raise ProviderUnavailableError("Ollama request failed.") from error

        return _parse_structured_response(response)

    async def close(self) -> None:
        """Close the underlying async SDK client once when it supports closure."""

        if self._closed:
            return

        self._closed = True
        close_method = getattr(self._client, "close", None)
        if callable(close_method):
            close_result = close_method()
            if inspect.isawaitable(close_result):
                await close_result


def _translate_response_error(error: ResponseError) -> ProviderError:
    """Map SDK HTTP response errors to stable application provider errors."""

    if error.status_code in {401, 403}:
        return ProviderAuthenticationError("Ollama authentication failed.")
    if error.status_code == 429:
        return ProviderRateLimitError("Ollama rate limit was exceeded.")
    if error.status_code == 404 or error.status_code >= 500:
        return ProviderUnavailableError(
            "Ollama server or requested model is unavailable."
        )
    return ProviderUnavailableError("Ollama request was rejected.")


def _parse_structured_response(response: object) -> dict[str, object]:
    """Extract and validate a JSON-object assistant response from the SDK result."""

    message = getattr(response, "message", None)
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        raise InvalidProviderResponseError("Ollama returned an empty response.")

    try:
        parsed_content = json.loads(content)
    except json.JSONDecodeError as error:
        raise InvalidProviderResponseError("Ollama returned invalid JSON.") from error

    if not isinstance(parsed_content, dict):
        raise InvalidProviderResponseError(
            "Ollama returned a non-object JSON response."
        )

    return cast(dict[str, object], parsed_content)
