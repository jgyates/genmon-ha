"""GenmonApiClient against a local server that answers like genhalink.

The server mirrors addon/genhalink.py in the genmon repository: Bearer auth
on /api/*, 503 from /api/entities until genmon's data is complete, and a
WebSocket that expects {"type": "auth", "token": ...} as its first message.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
import aiohttp
from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer

from custom_components.genmon.api import (
    GenmonApiClient,
    GenmonAuthError,
    GenmonCommandError,
    GenmonConnectionError,
)

from .conftest import API_KEY, load_capture

pytestmark = pytest.mark.usefixtures("socket_enabled")


class FakeGenhalink:
    """The parts of genhalink's REST and WebSocket API the client uses."""

    def __init__(self) -> None:
        self.answers = load_capture("evo_ac")
        self.entities_ready = True
        self.commands: list[str] = []
        self.command_result: str | None = "OK"
        self.app = web.Application()
        self.app.router.add_get("/api/health", self.health)
        self.app.router.add_get("/api/info", self.info)
        self.app.router.add_get("/api/status", self.status)
        self.app.router.add_get("/api/entities", self.entities)
        self.app.router.add_post("/api/command", self.command)
        self.app.router.add_get("/ws", self.websocket)

    @staticmethod
    def authorized(request: web.Request) -> bool:
        return request.headers.get("Authorization") == f"Bearer {API_KEY}"

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "version": "1.0.0"})

    async def info(self, request: web.Request) -> web.Response:
        if not self.authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response(self.answers["info"])

    async def status(self, request: web.Request) -> web.Response:
        if not self.authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        return web.json_response(self.answers["status"])

    async def entities(self, request: web.Request) -> web.Response:
        if not self.authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        if not self.entities_ready:
            return web.json_response({"error": "not_ready"}, status=503)
        return web.json_response(self.answers["entities"])

    async def command(self, request: web.Request) -> web.Response:
        if not self.authorized(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        body = await request.json()
        self.commands.append(body["command"])
        return web.json_response({"result": self.command_result})

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        msg = await ws.receive()
        auth = msg.json() if msg.type == WSMsgType.TEXT else {}
        if auth.get("type") != "auth" or auth.get("token") != API_KEY:
            await ws.send_json({"type": "auth_error", "error": "unauthorized"})
            await ws.close(code=4001, message=b"unauthorized")
            return ws
        await ws.send_json({"type": "auth_ok"})
        await ws.send_json({"type": "full_state", "state": self.answers["status"]["state"]})
        await ws.send_json({"type": "state_update", "state": {"Status": {}}})
        await ws.close()
        return ws


@pytest.fixture
async def server() -> AsyncGenerator[tuple[FakeGenhalink, TestServer]]:
    fake = FakeGenhalink()
    test_server = TestServer(fake.app, host="127.0.0.1")
    await test_server.start_server()
    yield fake, test_server
    await test_server.close()


def session() -> aiohttp.ClientSession:
    """A session with aiohttp's threaded DNS resolver: closing the default
    (aiodns) resolver leaves a thread behind, which Home Assistant 2024.11's
    test cleanup check reports."""
    return aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver()))


def client(test_server: TestServer, api_key: str = API_KEY, use_ssl: bool = False) -> GenmonApiClient:
    return GenmonApiClient(
        "127.0.0.1", test_server.port, api_key, session=session(), use_ssl=use_ssl
    )


async def test_requests(server) -> None:
    fake, test_server = server
    api = client(test_server)
    try:
        assert await api.get_info() == fake.answers["info"]
        assert await api.get_status() == fake.answers["status"]
        assert await api.get_entities() == fake.answers["entities"]
        assert await api.send_command("setquiet=on") == "OK"
        assert fake.commands == ["setquiet=on"]
    finally:
        await api.close()


# genmon's answers to the commands Home Assistant sends (genmonlib)
COMMAND_SUCCESS = [
    "OK",
    "Remote command sent successfully",
    "Remote command sent successfully (auto)",
    "Set Exercise Time Command sent",
    "Set Exercise Time Command sent (using legacy write)",
    "Set Quiet Mode Command sent",
    "Time Set: Command Sent\n",
    "The command was sent to the controller.",
]
COMMAND_ERRORS = [
    "Error",
    "Error setting time",
    "Error: Invalid input for Set Button Command entry.",
    "Error in ExecuteRemoteCommand: Failed bounds check",
    "Invalid command syntax for command setexercise",
    "Invalid Return",
    "Not Supported",
    "Not supported on this controller.",
    "Remote start transfer not supported",
    "Command not found.",
    "Malformed button in JSON file.",
    "No commands defined.",
    "",
    None,  # genhalink got no answer from genmon
]


@pytest.mark.parametrize("answer", COMMAND_SUCCESS)
async def test_command_success(server, answer) -> None:
    fake, test_server = server
    fake.command_result = answer
    api = client(test_server)
    try:
        assert await api.send_command("setremote=start") == answer.strip()
    finally:
        await api.close()


@pytest.mark.parametrize("answer", COMMAND_ERRORS)
async def test_command_error(server, answer) -> None:
    """genmon's error answers (HTTP 200 from genhalink) raise GenmonCommandError."""
    fake, test_server = server
    fake.command_result = answer
    api = client(test_server)
    try:
        with pytest.raises(GenmonCommandError) as err:
            await api.send_command("set_button_command=[secret]")
    finally:
        await api.close()
    assert err.value.result == answer
    # the command can hold a controller passcode: not in the message
    assert "secret" not in str(err.value)


async def test_wrong_api_key(server) -> None:
    _fake, test_server = server
    api = client(test_server, api_key="wrong")
    try:
        with pytest.raises(GenmonAuthError):
            await api.get_status()
    finally:
        await api.close()


async def test_entities_not_ready(server) -> None:
    """genhalink answers 503 until genmon has delivered complete data."""
    fake, test_server = server
    fake.entities_ready = False
    api = client(test_server)
    try:
        with pytest.raises(GenmonConnectionError):
            await api.get_entities()
    finally:
        await api.close()


async def test_unreachable() -> None:
    api = GenmonApiClient("127.0.0.1", 1, API_KEY, session=session(), use_ssl=False)
    try:
        with pytest.raises(GenmonConnectionError):
            await api.get_status()
    finally:
        await api.close()


async def test_validate_falls_back_to_http(server) -> None:
    """A plain-HTTP genhalink is found when HTTPS is tried first."""
    fake, test_server = server
    api = client(test_server, use_ssl=True)
    try:
        assert await api.validate_connection() == fake.answers["info"]
        assert api._use_ssl is False
    finally:
        await api.close()


async def test_websocket(server) -> None:
    """Messages after a successful login reach the callback."""
    _fake, test_server = server
    api = client(test_server)
    received: list[dict] = []

    async def callback(message: dict) -> None:
        received.append(message)

    try:
        await asyncio.wait_for(api.listen_ws(callback), 5)
    finally:
        await api.close()
    assert [m["type"] for m in received] == ["full_state", "state_update"]


async def test_websocket_wrong_api_key(server) -> None:
    """A rejected WebSocket login raises GenmonAuthError."""
    _fake, test_server = server
    api = client(test_server, api_key="wrong")

    async def callback(message: dict) -> None:
        raise AssertionError("no messages expected")

    try:
        with pytest.raises(GenmonAuthError):
            await asyncio.wait_for(api.listen_ws(callback), 5)
    finally:
        await api.close()
