"""Tests for the Tauri-managed backend sidecar launcher."""

import asyncio
import io
import json
import socket
import tomllib
from collections.abc import Sequence
from pathlib import Path

import app.sidecar as sidecar
import pytest
import uvicorn
from app.core.config import Settings


class FakeServer:
    """Short-lived Uvicorn-compatible server fake for launcher tests."""

    def __init__(self, config: uvicorn.Config) -> None:
        """Retain the application configuration and received sockets."""

        self.config = config
        self.should_exit = False
        self.sockets: Sequence[socket.socket] | None = None

    async def serve(self, sockets: list[socket.socket]) -> None:
        """Run the real app lifespan once without opening a network server."""

        self.sockets = sockets
        app = self.config.app
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0)


class FakeListeningSocket:
    """Socket fake that records binding without opening a real listener."""

    def __init__(self, family: int = socket.AF_INET) -> None:
        """Initialize an unbound fake socket for one address family."""

        self.family = family
        self.bound_address: tuple[str, int] | None = None
        self.is_closed = False
        self.listen_backlog: int | None = None
        self.is_blocking: bool | None = None

    def setsockopt(self, level: int, option: int, value: int) -> None:
        """Accept the launcher's socket options without storing unrelated detail."""

        del level, option, value

    def bind(self, address: tuple[str, int]) -> None:
        """Record binding and select a deterministic ephemeral port for zero."""

        host, port = address
        self.bound_address = (host, 51842 if port == 0 else port)

    def listen(self, backlog: int) -> None:
        """Record the requested listen backlog."""

        self.listen_backlog = backlog

    def setblocking(self, flag: bool) -> None:
        """Record non-blocking configuration."""

        self.is_blocking = flag

    def getsockname(self) -> tuple[str, int]:
        """Return the deterministic bound address."""

        assert self.bound_address is not None
        return self.bound_address

    def close(self) -> None:
        """Record idempotent socket closure."""

        self.is_closed = True

    def fileno(self) -> int:
        """Mirror the closed descriptor convention used by real sockets."""

        return -1 if self.is_closed else 42


class FailingServer:
    """Server fake that fails before FastAPI readiness."""

    def __init__(self, config: uvicorn.Config) -> None:
        """Accept the expected Uvicorn configuration."""

        self.should_exit = False

    async def serve(self, sockets: list[socket.socket]) -> None:
        """Fail immediately without using the supplied listener."""

        del sockets
        raise RuntimeError("server failed")


def _settings(**overrides: object) -> Settings:
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        sidecar_auth_token="launch-token",
        **overrides,
    )


def test_port_zero_binds_an_ephemeral_loopback_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A port-zero request retains the selected socket without a rebind race."""

    fake_socket = FakeListeningSocket()
    monkeypatch.setattr(sidecar.socket, "socket", lambda *_: fake_socket)

    listening_socket = sidecar.create_listening_socket(host="127.0.0.1", port=0)
    try:
        host, port = listening_socket.getsockname()[:2]

        assert host == "127.0.0.1"
        assert port == 51842
        assert fake_socket.listen_backlog == 128
        assert fake_socket.is_blocking is False
    finally:
        listening_socket.close()


def test_readiness_is_compact_safe_and_emitted_once() -> None:
    """Readiness stdout contains only the protocol's non-sensitive fields."""

    output = io.StringIO()
    sidecar.write_readiness(host="127.0.0.1", port=51842, output=output)

    line = output.getvalue()
    assert line.count("\n") == 1
    assert " " not in line
    assert "launch-token" not in line
    assert json.loads(line) == {
        "type": "ready",
        "version": 1,
        "host": "127.0.0.1",
        "port": 51842,
        "protocol_version": 1,
    }


def test_launcher_requires_an_injected_sidecar_token() -> None:
    """The sidecar cannot start without per-launch authentication material."""

    with pytest.raises(RuntimeError, match="authentication token"):
        asyncio.run(sidecar.run_sidecar(Settings(sidecar_auth_token=None)))


def test_launcher_passes_the_prebound_socket_and_runs_lifespan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The launcher gives Uvicorn its retained socket after FastAPI starts."""

    created_servers: list[FakeServer] = []
    output = io.StringIO()
    fake_socket = FakeListeningSocket()
    fake_socket.bind(("127.0.0.1", 0))
    monkeypatch.setattr(sidecar, "create_listening_socket", lambda **_: fake_socket)

    def server_factory(config: uvicorn.Config) -> FakeServer:
        server = FakeServer(config)
        created_servers.append(server)
        return server

    asyncio.run(
        sidecar.run_sidecar(_settings(), stdout=output, server_factory=server_factory)
    )

    (server,) = created_servers
    assert server.sockets is not None
    assert len(server.sockets) == 1
    assert fake_socket.fileno() == -1
    assert json.loads(output.getvalue())["type"] == "ready"


def test_launcher_configures_opt_in_throughput_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sidecar enables only the explicitly configured safe metric stream."""

    configured: list[bool] = []
    output = io.StringIO()
    fake_socket = FakeListeningSocket()
    fake_socket.bind(("127.0.0.1", 0))
    monkeypatch.setattr(sidecar, "create_listening_socket", lambda **_: fake_socket)
    monkeypatch.setattr(
        sidecar,
        "configure_throughput_diagnostics",
        lambda *, enabled: configured.append(enabled),
    )

    asyncio.run(
        sidecar.run_sidecar(
            _settings(throughput_diagnostics_enabled=True),
            stdout=output,
            server_factory=FakeServer,
        )
    )

    assert configured == [True]


def test_launcher_closes_socket_when_server_startup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed server startup cannot leak a bound loopback socket."""

    listening_socket = FakeListeningSocket()
    monkeypatch.setattr(
        sidecar, "create_listening_socket", lambda **_: listening_socket
    )

    with pytest.raises(RuntimeError, match="server failed"):
        asyncio.run(sidecar.run_sidecar(_settings(), server_factory=FailingServer))

    assert listening_socket.fileno() == -1


def test_project_registers_the_sidecar_cli_entry_point() -> None:
    """The development command resolves to the launcher main function."""

    pyproject_path = Path(__file__).parents[3] / "pyproject.toml"
    with pyproject_path.open("rb") as pyproject_file:
        project = tomllib.load(pyproject_file)

    assert project["project"]["scripts"]["ai-meeting-copilot-sidecar"] == (
        "app.sidecar:main"
    )
