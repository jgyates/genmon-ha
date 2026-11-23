"""Setup, unload and entity creation, for every captured controller."""
from __future__ import annotations

import logging

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.genmon import controller_buttons as cb
from custom_components.genmon.const import DOMAIN
from custom_components.genmon.entity import should_add_entity
from custom_components.genmon.exercise import enhanced_exercise_mode

from .conftest import CAPTURES, FakeGenmonApi, GenmonAuthError, GenmonConnectionError

PLATFORM_KEYS = {
    "sensors": "sensor",
    "binary_sensors": "binary_sensor",
    "buttons": "button",
    "switches": "switch",
    "selects": "select",
    "numbers": "number",
}


def expected_unique_ids(entry: MockConfigEntry, api: FakeGenmonApi) -> set[tuple[str, str]]:
    """(domain, unique_id) the integration should create from the capture's definitions."""
    entities = api.entities
    controller = entities.get("controller_type")
    capabilities = {
        **entities.get("capabilities", {}),
        # genmon reports it in gui_status, not where genhalink looks
        "EnhancedExerciseMode": enhanced_exercise_mode(api.state),
    }
    ids = set()
    for key in PLATFORM_KEYS:
        for defn in entities.get(key, []):
            if should_add_entity(defn, controller, capabilities):
                ids.add((PLATFORM_KEYS[key], f"{entry.entry_id}_{defn['entity_id']}"))
    for button in entities.get("buttons_from_controller", []):
        uid = f"{entry.entry_id}_ctrl_{button['onewordcommand'].lower()}"
        kind = cb.classify(button)
        if kind in (cb.BUTTON, cb.ACTION):
            ids.add(("button", uid))
        if kind == cb.SETTING:
            ids.add(("number", uid))
        if kind == cb.ACTION:
            ids.add(("number", uid + "_input"))
    return ids


@pytest.mark.parametrize("capture", CAPTURES)
async def test_setup_creates_entities(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: FakeGenmonApi,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every capture sets up cleanly and every entity gets a valid state."""
    caplog.set_level(logging.WARNING)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, config_entry.entry_id)
    created = {(e.domain, e.unique_id) for e in entries}
    assert created == expected_unique_ids(config_entry, mock_api)

    # A state HA refuses (e.g. text for a numeric sensor) is logged as an
    # error and the entity keeps no state.
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, [r.getMessage() for r in errors]
    for reg_entry in entries:
        if reg_entry.disabled_by:
            continue
        state = hass.states.get(reg_entry.entity_id)
        assert state is not None, reg_entry.entity_id
        assert state.state != STATE_UNAVAILABLE, reg_entry.entity_id


async def test_unload(hass: HomeAssistant, init_integration: MockConfigEntry, mock_api) -> None:
    """Unloading stops the WebSocket listener and closes the client."""
    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED
    assert mock_api.closed
    assert init_integration.entry_id not in hass.data.get(DOMAIN, {})


async def test_setup_retries_when_unreachable(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """A connection error at startup makes HA retry setup later."""
    mock_api.error = GenmonConnectionError("down")
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    # each retry makes a new client: the failed one must close its session
    assert mock_api.closed


async def test_setup_auth_error_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """A rejected API key at startup asks the user for a new one."""
    mock_api.error = GenmonAuthError("Invalid API key")
    await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert mock_api.closed
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]
