"""Tests for the shared asynchronous Ollama client wrapper."""

import asyncio
from dataclasses import dataclass
from typing import Any, cast

import app.infrastructure.providers.ollama.client as ollama_client_module
import httpx
import pytest
from app.application.exceptions import (
    InvalidProviderResponseError,
    ProviderAuthenticationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.core.throughput_diagnostics import configure_throughput_diagnostics
from app.infrastructure.providers.ollama import OllamaClient
from ollama import ResponseError  # type: ignore[import-untyped]


@dataclass
class FakeMessage:
    """Minimal SDK message double."""

    content: object


@dataclass
class FakeResponse:
    """Minimal SDK response double."""

    message: FakeMessage


class FakeAsyncOllamaClient:
    """Controllable asynchronous SDK-client double."""

    def __init__(self, outcome: object | BaseException) -> None:
        """Store one response or failure for chat calls."""

        self._outcome = outcome
        self.calls: list[dict[str, object]] = []
        self.close_calls = 0

    async def chat(self, **kwargs: object) -> object:
        """Record a request and return or raise the configured outcome."""

        self.calls.append(kwargs)
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome

    async def close(self) -> None:
        """Record async client closure."""

        self.close_calls += 1


def make_client(
    outcome: object | BaseException,
) -> tuple[OllamaClient, FakeAsyncOllamaClient]:
    """Create the wrapper with a fake SDK client."""

    fake_client = FakeAsyncOllamaClient(outcome)
    client = OllamaClient(
        base_url="http://127.0.0.1:11434",
        request_timeout_seconds=12.0,
        temperature=0.1,
        context_length=4096,
        keep_alive="5m",
        _client=cast(Any, fake_client),
    )
    return client, fake_client


def generate(client: OllamaClient) -> dict[str, object]:
    """Run one standard structured generation request."""

    return asyncio.run(
        client.generate_structured(
            model="qwen2.5:3b",
            system_prompt="System prompt must not leak.",
            user_prompt="User prompt must not leak.",
            schema={"type": "object", "properties": {"value": {"type": "string"}}},
        )
    )


def test_structured_request_forwards_the_expected_payload() -> None:
    """The wrapper builds one non-streaming structured chat request."""

    client, fake_client = make_client(FakeResponse(FakeMessage('{"value": "ok"}')))

    assert generate(client) == {"value": "ok"}
    assert fake_client.calls == [
        {
            "model": "qwen2.5:3b",
            "messages": [
                {"role": "system", "content": "System prompt must not leak."},
                {"role": "user", "content": "User prompt must not leak."},
            ],
            "format": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
            },
            "stream": False,
            "keep_alive": "5m",
            "options": {"temperature": 0.1, "num_ctx": 4096},
        }
    ]


@pytest.mark.parametrize("content", ["", "   "])
def test_blank_content_is_rejected(content: str) -> None:
    """An empty assistant message cannot be a structured response."""

    client, _ = make_client(FakeResponse(FakeMessage(content)))

    with pytest.raises(InvalidProviderResponseError, match="empty response"):
        generate(client)


def test_malformed_json_is_rejected() -> None:
    """Malformed assistant JSON remains behind the provider boundary."""

    client, _ = make_client(FakeResponse(FakeMessage("not-json")))

    with pytest.raises(InvalidProviderResponseError, match="invalid JSON"):
        generate(client)


@pytest.mark.parametrize("content", ["[]", '"text"', "null"])
def test_non_object_json_is_rejected(content: str) -> None:
    """Structured responses must use a JSON object at their root."""

    client, _ = make_client(FakeResponse(FakeMessage(content)))

    with pytest.raises(InvalidProviderResponseError, match="non-object"):
        generate(client)


def test_timeout_is_mapped_without_leaking_prompts() -> None:
    """Transport timeouts use the stable timeout error without sensitive content."""

    client, _ = make_client(httpx.ReadTimeout("System prompt must not leak."))

    with pytest.raises(ProviderTimeoutError) as error_info:
        generate(client)

    assert "System prompt must not leak." not in str(error_info.value)
    assert "User prompt must not leak." not in str(error_info.value)


def test_connection_failure_is_mapped_to_provider_unavailable() -> None:
    """An unreachable local daemon becomes a stable unavailable error."""

    client, _ = make_client(httpx.ConnectError("daemon offline"))

    with pytest.raises(ProviderUnavailableError, match="server is unavailable"):
        generate(client)


def test_sdk_connection_failure_is_mapped_to_provider_unavailable() -> None:
    """The official SDK's built-in connection error remains privacy-safe."""

    client, _ = make_client(ConnectionError("daemon address must not leak"))

    with pytest.raises(
        ProviderUnavailableError, match="server is unavailable"
    ) as error_info:
        generate(client)

    assert "daemon address must not leak" not in str(error_info.value)


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [
        (401, ProviderAuthenticationError),
        (403, ProviderAuthenticationError),
        (429, ProviderRateLimitError),
        (500, ProviderUnavailableError),
        (404, ProviderUnavailableError),
    ],
)
def test_http_response_errors_are_mapped(
    status_code: int, expected_error: type[BaseException]
) -> None:
    """SDK HTTP response statuses map to stable application errors."""

    client, _ = make_client(ResponseError("response body must not leak", status_code))

    with pytest.raises(expected_error) as error_info:
        generate(client)

    assert "response body must not leak" not in str(error_info.value)


def test_existing_provider_errors_are_preserved() -> None:
    """Application-level provider errors are never remapped by the wrapper."""

    original_error = ProviderUnavailableError("already translated")
    client, _ = make_client(original_error)

    with pytest.raises(ProviderUnavailableError) as error_info:
        generate(client)

    assert error_info.value is original_error


def test_cancellation_propagates_unchanged() -> None:
    """Cancellation is never translated into an Ollama provider error."""

    client, _ = make_client(asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        generate(client)


def test_close_is_idempotent_and_blocks_new_calls() -> None:
    """Closing delegates once and prevents future daemon calls."""

    client, fake_client = make_client(FakeResponse(FakeMessage('{"value": "ok"}')))

    asyncio.run(client.close())
    asyncio.run(client.close())

    assert fake_client.close_calls == 1
    with pytest.raises(ProviderUnavailableError, match="client is closed"):
        generate(client)


def test_opt_in_throughput_metrics_exclude_prompt_and_response_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ollama diagnostics expose timing and in-flight count only."""

    client, _ = make_client(FakeResponse(FakeMessage('{"value": "ok"}')))
    events: list[tuple[str, dict[str, int | str]]] = []

    def capture(stage: str, /, **fields: int | str) -> None:
        events.append((stage, fields))

    monkeypatch.setattr(ollama_client_module, "emit_throughput", capture)

    configure_throughput_diagnostics(enabled=True)
    try:
        assert generate(client) == {"value": "ok"}
    finally:
        configure_throughput_diagnostics(enabled=False)

    assert events[0] == ("ollama_started", {"active_requests": 1})
    assert events[1] == ("ollama_response_received", {})
    assert events[2][0] == "ollama_completed"
    assert events[2][1]["outcome"] == "completed"
    assert events[2][1]["active_requests"] == 0
    assert isinstance(events[2][1]["elapsed_ms"], int)
    assert all(
        "prompt" not in fields and "response" not in fields for _, fields in events
    )


def test_malformed_response_metric_uses_only_a_closed_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Structured-response diagnostics never forward model output."""

    client, _ = make_client(FakeResponse(FakeMessage("not-json")))
    events: list[tuple[str, dict[str, int | str]]] = []

    def capture(stage: str, /, **fields: int | str) -> None:
        events.append((stage, fields))

    monkeypatch.setattr(ollama_client_module, "emit_throughput", capture)

    configure_throughput_diagnostics(enabled=True)
    try:
        with pytest.raises(InvalidProviderResponseError):
            generate(client)
    finally:
        configure_throughput_diagnostics(enabled=False)

    assert (
        "ollama_response_validation_failed",
        {"reason": "malformed_response"},
    ) in events
    assert events[-1][0] == "ollama_completed"
    assert events[-1][1]["outcome"] == "failed"
    assert events[-1][1]["reason"] == "malformed_response"
    assert events[-1][1]["active_requests"] == 0
    assert isinstance(events[-1][1]["elapsed_ms"], int)
    assert all("not-json" not in str(fields) for _, fields in events)
