"""Coordinator: polling, WebSocket updates and authentication failures."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant

from custom_components.genmon.const import DOMAIN

from .conftest import FakeGenmonApi, GenmonAuthError


def reauth_flows(hass: HomeAssistant) -> list:
    return [
        f for f in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if f["context"]["source"] == SOURCE_REAUTH
    ]


async def test_websocket_update(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """A state_update replaces the coordinator data."""
    coordinator = hass.data[DOMAIN][init_integration.entry_id]
    await mock_api.ws_connected.wait()
    await mock_api.push({"type": "state_update", "state": {"Status": {"x": 1}}})
    assert coordinator.data == {"Status": {"x": 1}}
    # other message types and empty states are ignored
    await mock_api.push({"type": "command_response", "result": "OK"})
    await mock_api.push({"type": "state_update", "state": {}})
    assert coordinator.data == {"Status": {"x": 1}}


async def test_poll_auth_error_starts_reauth(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """The API key changing while running asks the user for the new one."""
    mock_api.error = GenmonAuthError("Invalid API key")
    await hass.data[DOMAIN][init_integration.entry_id].async_refresh()
    await hass.async_block_till_done()
    assert len(reauth_flows(hass)) == 1


async def test_websocket_auth_error_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """A rejected WebSocket login stops reconnecting and asks for a new key."""
    mock_api.ws_error = GenmonAuthError("WebSocket auth failed")
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    assert coordinator._ws_task.done()
    assert len(reauth_flows(hass)) == 1


async def test_entity_refresh_survives_auth_error(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """The entity list timer doesn't raise on a rejected key (polling reports it)."""
    coordinator = hass.data[DOMAIN][init_integration.entry_id]
    before = coordinator.entities
    mock_api.error = GenmonAuthError("Invalid API key")
    await coordinator.async_refresh_entities()
    assert coordinator.entities is before


async def test_websocket_reconnect_backoff(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """The reconnect wait doubles while genhalink can't be reached (up to
    5 minutes) and starts over after a connection that logged in."""
    coordinator = hass.data[DOMAIN][init_integration.entry_id]
    results = iter([False] * 8 + [True, False, OSError("boom")])
    waits: list[float] = []

    async def listen_ws(_callback) -> bool:
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    async def sleep(delay: float) -> None:
        waits.append(delay)
        if len(waits) == 11:
            raise asyncio.CancelledError

    with (
        patch.object(coordinator.api, "listen_ws", listen_ws),
        patch("custom_components.genmon.coordinator.asyncio.sleep", sleep),
        pytest.raises(asyncio.CancelledError),
    ):
        await coordinator._async_ws_listener()
    assert waits == [5, 10, 20, 40, 80, 160, 300, 300, 5, 5, 10]
