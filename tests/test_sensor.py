"""Sensor and binary sensor states."""
from __future__ import annotations

import copy

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.genmon.const import DOMAIN

from .conftest import FakeGenmonApi, GenmonConnectionError


def entity_id(hass: HomeAssistant, entry: MockConfigEntry, platform: str, key: str) -> str:
    """The entity_id of the entity genmon defines as `key`."""
    eid = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{entry.entry_id}_{key}")
    assert eid, f"no {platform} for {key}"
    return eid


async def push_state(hass: HomeAssistant, api: FakeGenmonApi, state: dict) -> None:
    """Send a genhalink state_update over the WebSocket."""
    await api.push({"type": "state_update", "state": copy.deepcopy(state)})
    await hass.async_block_till_done()


async def poll(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Run one coordinator poll."""
    await hass.data[DOMAIN][entry.entry_id].async_refresh()
    await hass.async_block_till_done()


async def test_numeric_sensor_values(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """genmon's {"value", "unit"} values become numbers with units."""
    battery = hass.states.get(entity_id(hass, init_integration, "sensor", "battery_voltage"))
    raw = mock_api.state["Status"]["Engine"]["Battery Voltage"]
    assert float(battery.state) == pytest.approx(float(raw["value"]))
    assert battery.attributes["unit_of_measurement"] == "V"


async def test_numeric_sensor_with_text_value_is_unknown(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: FakeGenmonApi,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Text where a number is expected gives "unknown", not a rejected state."""
    eid = entity_id(hass, init_integration, "sensor", "battery_voltage")
    mock_api.state["Status"]["Engine"]["Battery Voltage"] = "Unknown"
    await push_state(hass, mock_api, mock_api.state)

    assert hass.states.get(eid).state == STATE_UNKNOWN
    assert "has the non-numeric value" not in caplog.text


async def test_text_sensor(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """Text sensors show genmon's text and follow WebSocket updates."""
    eid = entity_id(hass, init_integration, "sensor", "engine_state")
    assert hass.states.get(eid).state == mock_api.state["Status"]["Engine"]["Engine State"]

    mock_api.state["Status"]["Engine"]["Engine State"] = "Running"
    await push_state(hass, mock_api, mock_api.state)
    assert hass.states.get(eid).state == "Running"


async def test_binary_sensor_follows_state(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """payload_on lists and payload_on_not_empty."""
    running = entity_id(hass, init_integration, "binary_sensor", "generator_running")
    alarm = entity_id(hass, init_integration, "binary_sensor", "alarm_active")
    engine = mock_api.state["Status"]["Engine"]

    engine["Engine State"] = "Off - Ready"
    engine.pop("System In Alarm", None)
    await push_state(hass, mock_api, mock_api.state)
    assert hass.states.get(running).state == STATE_OFF
    assert hass.states.get(alarm).state == STATE_OFF

    engine["Engine State"] = "Running"
    engine["System In Alarm"] = "Low Battery"
    await push_state(hass, mock_api, mock_api.state)
    assert hass.states.get(running).state == STATE_ON
    assert hass.states.get(alarm).state == STATE_ON


@pytest.mark.parametrize(
    ("platform", "key"),
    [
        ("sensor", "battery_voltage"),
        ("sensor", "engine_state"),
        ("binary_sensor", "generator_running"),
        ("binary_sensor", "alarm_active"),
    ],
)
async def test_unavailable_while_genmon_unreachable(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_api: FakeGenmonApi,
    platform: str,
    key: str,
) -> None:
    """Entities show "unavailable" when polling fails, and recover after."""
    eid = entity_id(hass, init_integration, platform, key)
    before = hass.states.get(eid).state
    assert before != STATE_UNAVAILABLE

    mock_api.error = GenmonConnectionError("down")
    await poll(hass, init_integration)
    assert hass.states.get(eid).state == STATE_UNAVAILABLE

    mock_api.error = None
    await poll(hass, init_integration)
    assert hass.states.get(eid).state == before


@pytest.mark.parametrize("capture", ["evo_lc"])
@pytest.mark.parametrize(
    "key",
    [
        "maintenance_generator_phase",  # "Unknown"
        "maintenance_controller_settings_nominal_line_voltage",  # "Unknown V"
        "maintenance_controller_settings_rated_max_power",  # "Unknown kW"
    ],
)
async def test_genmon_unknown_is_unknown(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: FakeGenmonApi, key: str
) -> None:
    """genmon's "Unknown" (with or without a unit) shows as HA's unknown.

    The captured liquid-cooled Evolution has a parameter group genmon's
    data/param/EvoLCParam.csv doesn't list, so genmon can't look these up.
    """
    assert hass.states.get(entity_id(hass, init_integration, "sensor", key)).state == STATE_UNKNOWN
