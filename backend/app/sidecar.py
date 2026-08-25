"""Local Tauri-managed FastAPI sidecar launcher."""

import asyncio
import json
import socket
import sys
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Protocol, TextIO

import uvicorn

from app.core.config import Settings, get_settings
from app.core.logging import get_logger, setup_logging
from app.core.throughput_diagnostics import configure_throughput_diagnostics
from app.main import create_app

_READINESS_VERSION = 1
_PROTOCOL_VERSION = 1
_LISTEN_BACKLOG = 128
_LOGGER = get_logger(__name__)


class _Server(Protocol):
    """Minimal Uvicorn server surface used by the launcher."""

    should_exit: bool

    async def serve(self, sockets: list[socket.socket]) -> None:
        """Serve the application through pre-bound listening sockets."""


type ServerFactory = Callable[[uvicorn.Config], _Server]


async def run_sidecar(
    settings: Settings | None = None,
    *,
    stdout: TextIO | None = None,
    server_factory: ServerFactory | None = None,
) -> None:
    """Run the existing FastAPI app through a local, authenticated sidecar."""

    resolved_settings = settings or get_settings()
    if resolved_settings.sidecar_auth_token is None:
        raise RuntimeError("Sidecar authentication token must be configured.")

    setup_logging(resolved_settings, stream=sys.stderr)
    configure_throughput_diagnostics(
        enabled=resolved_settings.throughput_diagnostics_enabled
    )
    _LOGGER.debug(
        "sidecar runtime source=repository_worktree env_file=%s "
        "translation_provider=%s translation_model=%s "
        "simplification_provider=%s reply_coaching_provider=%s",
        _settings_env_file_source(),
        resolved_settings.translation_provider,
        resolved_settings.ollama_translation_model,
        resolved_settings.german_simplification_provider,
        resolved_settings.reply_coaching_provider,
    )
    listening_socket = create_listening_socket(
        host=resolved_settings.sidecar_host,
        port=resolved_settings.sidecar_port,
    )
    server: _Server | None = None
    server_task: asyncio.Task[None] | None = None

    try:
        readiness_event = asyncio.Event()
        app = create_app(resolved_settings, readiness_event=readiness_event)
        config = uvicorn.Config(
            app,
            host=resolved_settings.sidecar_host,
            port=resolved_settings.sidecar_port,
            access_log=False,
            log_config=None,
        )
        server = (server_factory or uvicorn.Server)(config)
        server_task = asyncio.create_task(server.serve(sockets=[listening_socket]))

        await _wait_for_readiness(
            readiness_event,
            server_task,
            timeout_seconds=resolved_settings.sidecar_startup_timeout_seconds,
        )
        host, port = _get_bound_address(listening_socket)
        write_readiness(host=host, port=port, output=stdout)
        await server_task
    finally:
        if server is not None:
            server.should_exit = True
        if server_task is not None and not server_task.done():
            with suppress(Exception):
                await server_task
        listening_socket.close()


def create_listening_socket(*, host: str, port: int) -> socket.socket:
    """Bind and retain a loopback listener so port zero has no rebind race."""

    family = socket.AF_INET6 if host == "::1" else socket.AF_INET
    listening_socket = socket.socket(family, socket.SOCK_STREAM)
    try:
        listening_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            listening_socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        listening_socket.bind((host, port))
        listening_socket.listen(_LISTEN_BACKLOG)
        listening_socket.setblocking(False)
    except Exception:
        listening_socket.close()
        raise
    return listening_socket


def _settings_env_file_source() -> str:
    """Classify the configured dotenv source without exposing a filesystem path."""

    configured = Settings.model_config.get("env_file")
    if not isinstance(configured, Path) or not configured.exists():
        return "no_env_file"
    repository_root_env = Path(__file__).resolve().parents[2] / ".env"
    if configured.resolve() == repository_root_env.resolve():
        return "repository_root_env"
    return "other_config_source"


def write_readiness(*, host: str, port: int, output: TextIO | None = None) -> None:
    """Write the one permitted compact sidecar stdout message exactly once."""

    readiness = {
        "type": "ready",
        "version": _READINESS_VERSION,
        "host": host,
        "port": port,
        "protocol_version": _PROTOCOL_VERSION,
    }
    target = output or sys.stdout
    target.write(json.dumps(readiness, separators=(",", ":")) + "\n")
    target.flush()


async def _wait_for_readiness(
    readiness_event: asyncio.Event,
    server_task: asyncio.Task[None],
    *,
    timeout_seconds: float,
) -> None:
    """Wait for explicit FastAPI lifespan startup without sleeps or polling."""

    readiness_task = asyncio.create_task(readiness_event.wait())
    try:
        done, _ = await asyncio.wait(
            {readiness_task, server_task},
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if readiness_task in done:
            return
        if server_task in done:
            await server_task
            raise RuntimeError("Sidecar server exited before becoming ready.")
        raise TimeoutError("Sidecar startup timed out.")
    finally:
        readiness_task.cancel()
        with suppress(asyncio.CancelledError):
            await readiness_task


def _get_bound_address(listening_socket: socket.socket) -> tuple[str, int]:
    """Return the actual address selected for an already bound listener."""

    address = listening_socket.getsockname()
    return str(address[0]), int(address[1])


def main() -> None:
    """Run the launcher for the project's installed sidecar CLI command."""

    asyncio.run(run_sidecar())
