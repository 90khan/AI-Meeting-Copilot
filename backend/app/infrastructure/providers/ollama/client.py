"""Shared asynchronous client wrapper for a local Ollama server."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
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
from app.core.logging import get_logger
from app.core.throughput_diagnostics import (
    begin_ollama_request,
    emit_throughput,
    finish_ollama_request,
)

_LOGGER = get_logger(__name__)


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
        self._active_request_count = 0
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

        request_started_at = time.monotonic()
        self._active_request_count += 1
        active_request_count = begin_ollama_request()
        outcome = "completed"
        failure_reason: str | None = None
        emit_throughput(
            "ollama_started",
            active_requests=active_request_count,
        )
        try:
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
                outcome = "cancelled"
                raise
            except (KeyboardInterrupt, SystemExit):
                outcome = "failed"
                raise
            except ProviderError:
                outcome = "failed"
                failure_reason = "provider_internal_error"
                raise
            except httpx.TimeoutException as error:
                outcome = "failed"
                failure_reason = "timeout"
                raise ProviderTimeoutError("Ollama request timed out.") from error
            except TimeoutError as error:
                outcome = "failed"
                failure_reason = "timeout"
                raise ProviderTimeoutError("Ollama request timed out.") from error
            except httpx.TransportError as error:
                outcome = "failed"
                failure_reason = "connection_failed"
                raise ProviderUnavailableError(
                    "Ollama server is unavailable."
                ) from error
            except ConnectionError as error:
                outcome = "failed"
                failure_reason = "connection_failed"
                raise ProviderUnavailableError(
                    "Ollama server is unavailable."
                ) from error
            except ResponseError as error:
                outcome = "failed"
                failure_reason = _response_error_reason(error)
                raise _translate_response_error(error) from error
            except Exception as error:
                outcome = "failed"
                failure_reason = "request_failed"
                raise ProviderUnavailableError("Ollama request failed.") from error

            try:
                parsed_response = _parse_structured_response(response)
            except InvalidProviderResponseError:
                outcome = "failed"
                failure_reason = _structured_response_failure_reason(response)
                emit_throughput(
                    "ollama_response_validation_failed",
                    reason=failure_reason,
                )
                raise
            emit_throughput("ollama_response_received")
            return parsed_response
        except ProviderError as error:
            outcome = "failed"
            if failure_reason is None:
                failure_reason = _provider_error_reason(error)
            raise
        except Exception:
            outcome = "failed"
            raise
        finally:
            self._active_request_count -= 1
            active_request_count = finish_ollama_request()
            elapsed_ms = _elapsed_milliseconds(request_started_at)
            _LOGGER.debug(
                "assist ollama request completed outcome=%s elapsed_ms=%d "
                "active_requests=%d",
                outcome,
                elapsed_ms,
                active_request_count,
            )
            fields: dict[str, int | str] = {
                "outcome": outcome,
                "elapsed_ms": elapsed_ms,
                "active_requests": active_request_count,
            }
            if failure_reason is not None:
                fields["reason"] = failure_reason
            emit_throughput("ollama_completed", **fields)

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


def _response_error_reason(error: ResponseError) -> str:
    """Classify an SDK HTTP failure without exposing its response details."""

    if error.status_code == 404:
        return "model_unavailable"
    return "http_status_error"


def _provider_error_reason(error: ProviderError) -> str:
    """Classify stable application errors at the client boundary."""

    if isinstance(error, ProviderTimeoutError):
        return "timeout"
    if isinstance(error, InvalidProviderResponseError):
        return "response_validation_failed"
    if isinstance(error, (ProviderAuthenticationError, ProviderRateLimitError)):
        return "http_status_error"
    return "provider_internal_error"


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


def _structured_response_failure_reason(response: object) -> str:
    """Classify a rejected SDK response structurally without retaining content."""

    message = getattr(response, "message", None)
    content = getattr(message, "content", None)
    if not isinstance(content, str) or not content.strip():
        return "empty_response"
    try:
        parsed_content = json.loads(content)
    except json.JSONDecodeError:
        return "malformed_response"
    if not isinstance(parsed_content, dict):
        return "response_validation_failed"
    return "response_validation_failed"


def _elapsed_milliseconds(started_at: float) -> int:
    """Return a privacy-safe monotonic duration for runtime diagnostics."""

    return int((time.monotonic() - started_at) * 1_000)
