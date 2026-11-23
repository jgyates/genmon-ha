"""Shared fixtures for the Genmon integration tests.

The integration talks to genhalink (addon/genhalink.py in the genmon
repository). The tests replace the API client with FakeGenmonApi, which
answers from real genhalink responses in tests/fixtures/<capture>/:

    info.json      GET /api/info
    status.json    GET /api/status
    entities.json  GET /api/entities

They were captured from genhalink running against genmon and its generator
simulator (genmon tests/sim, the anonymized register captures), one folder
per controller. tests/test_api.py tests the real client against a local
server.
"""
from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import Awaitable, Callable, Generator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant

from custom_components.genmon.api import (
    GenmonAuthError,
    GenmonCommandError,
    GenmonConnectionError,
    command_failed,
)
from custom_components.genmon.const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_PORT,
    CONF_USE_SSL,
    DOMAIN,
)

FIXTURES = Path(__file__).parent / "fixtures"
CAPTURES = sorted(p.name for p in FIXTURES.iterdir() if p.is_dir())

HOST = "192.0.2.10"  # documentation address (RFC 5737)
PORT = 9083
API_KEY = "test-api-key"
ENTRY_DATA = {CONF_HOST: HOST, CONF_PORT: PORT, CONF_API_KEY: API_KEY, CONF_USE_SSL: False}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/genmon in every test."""


def load_capture(name: str) -> dict[str, Any]:
    """Return a capture's API answers as {"info", "status", "entities"}."""
    folder = FIXTURES / name
    return {
        key: json.loads((folder / f"{key}.json").read_text())
        for key in ("info", "status", "entities")
    }


class FakeGenmonApi:
    """Stands in for GenmonApiClient, answering from a capture.

    Tests change self.status / self.entities / self.info to change what the
    "server" answers, or set self.error to make every request fail.
    """

    def __init__(self, capture: str = "evo_ac") -> None:
        answers = load_capture(capture)
        self.info: dict[str, Any] = answers["info"]
        self.status: dict[str, Any] = answers["status"]
        self.entities: dict[str, Any] = answers["entities"]
        self.error: Exception | None = None
        self.ws_error: Exception | None = None
        self.commands: list[str] = []
        # genmon's answer to commands (genhalink's {"result": ...}); None:
        # genhalink got no answer from genmon
        self.command_result: str | None = "OK"
        self.ws_callback: Callable[[dict], Awaitable[None]] | None = None
        self.ws_connected = asyncio.Event()
        self._use_ssl = False
        self.closed = False

    @property
    def state(self) -> dict[str, Any]:
        """The genmon state dict entities read from (status["state"])."""
        return self.status["state"]

    def _answer(self, value: dict) -> dict:
        if self.error is not None:
            raise self.error
        return copy.deepcopy(value)

    async def get_health(self) -> dict:
        if isinstance(self.error, GenmonConnectionError):
            raise self.error
        return {"status": "ok", "version": "1.0.0"}

    async def get_info(self) -> dict:
        return self._answer(self.info)

    async def get_status(self) -> dict:
        return self._answer(self.status)

    async def get_entities(self) -> dict:
        return self._answer(self.entities)

    async def validate_connection(self) -> dict:
        await self.get_health()
        return await self.get_info()

    async def send_command(self, command: str) -> str:
        """Like GenmonApiClient.send_command (same check of genmon's answer)."""
        if self.error is not None:
            raise self.error
        self.commands.append(command)
        if command_failed(self.command_result):
            raise GenmonCommandError(command, self.command_result)
        return self.command_result

    async def listen_ws(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        """Hold the connection open until the test pushes messages or ends."""
        if self.ws_error is not None:
            raise self.ws_error
        self.ws_callback = callback
        self.ws_connected.set()
        await asyncio.Event().wait()

    async def push(self, message: dict) -> None:
        """Send a WebSocket message to the integration."""
        assert self.ws_callback is not None, "WebSocket not connected"
        await self.ws_callback(message)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def capture() -> str:
    """The capture the fake API answers from; override with parametrize."""
    return "evo_ac"


@pytest.fixture
def mock_api(capture: str) -> Generator[FakeGenmonApi]:
    """Patch the API client everywhere the integration creates one."""
    api = FakeGenmonApi(capture)
    with (
        patch("custom_components.genmon.coordinator.GenmonApiClient", return_value=api),
        patch("custom_components.genmon.config_flow.GenmonApiClient", return_value=api),
    ):
        yield api


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A Genmon config entry added to hass (not set up)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test Generator",
        unique_id=f"genmon_{HOST}",
        data=dict(ENTRY_DATA),
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def init_integration(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: FakeGenmonApi
) -> MockConfigEntry:
    """Set up the integration with the fake API and return the entry."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


__all__ = [
    "API_KEY",
    "CAPTURES",
    "ENTRY_DATA",
    "FakeGenmonApi",
    "GenmonAuthError",
    "GenmonCommandError",
    "GenmonConnectionError",
    "HOST",
    "PORT",
    "load_capture",
]
