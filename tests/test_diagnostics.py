"""Diagnostics download."""
from __future__ import annotations

import json

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.genmon.const import CONF_BUTTON_PASSCODE

from .conftest import API_KEY, FakeGenmonApi

try:
    from pytest_homeassistant_custom_component.components.diagnostics import (
        get_diagnostics_for_config_entry,
    )
except ImportError:  # the test package for Home Assistant 2024.11 doesn't have it
    from custom_components.genmon.diagnostics import async_get_config_entry_diagnostics

    async def get_diagnostics_for_config_entry(hass, _hass_client, entry):
        """The integration's diagnostics, without the download view."""
        return await async_get_config_entry_diagnostics(hass, entry)

PASSCODE = "4321"


async def test_diagnostics_redacts_secrets(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    config_entry: MockConfigEntry,
    mock_api: FakeGenmonApi,
) -> None:
    """The API key and the button passcode never appear in diagnostics."""
    hass.config_entries.async_update_entry(
        config_entry, options={CONF_BUTTON_PASSCODE: PASSCODE, "scan_interval": 5}
    )
    assert await async_setup_component(hass, "diagnostics", {})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    result = await get_diagnostics_for_config_entry(hass, hass_client, config_entry)

    assert result["config_entry"]["api_key"] == "**REDACTED**"
    assert result["options"][CONF_BUTTON_PASSCODE] == "**REDACTED**"
    assert result["options"]["scan_interval"] == 5
    text = json.dumps(result)
    assert API_KEY not in text
    assert PASSCODE not in text
    assert result["entity_definitions"]["sensors"] == len(mock_api.entities["sensors"])
