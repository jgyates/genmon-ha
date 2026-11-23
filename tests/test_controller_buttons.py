"""Controller buttons with inputs: number entities for settings, a button
plus an input number for actions, buttons for passcode inputs."""
from __future__ import annotations

import copy
import json

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from homeassistant.components.number import ATTR_MAX, ATTR_MIN, ATTR_VALUE, SERVICE_SET_VALUE
from homeassistant.const import ATTR_ENTITY_ID, ATTR_UNIT_OF_MEASUREMENT
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.genmon import STALE_AFTER, _async_cleanup_stale_entities
from custom_components.genmon import controller_buttons as cb
from custom_components.genmon.const import DOMAIN

from .conftest import FakeGenmonApi, load_capture
from .test_sensor import entity_id



def controller_buttons(capture: str) -> dict[str, dict]:
    buttons = load_capture(capture)["entities"]["buttons_from_controller"]
    return {b["onewordcommand"]: b for b in buttons}


# Power Zone 200: a run time, then a start and transfer
PZ_START = controller_buttons("pz200")["pzremotestarttransfer"]


# ---------------------------------------------------------------- helpers
@pytest.mark.parametrize(
    ("capture", "command", "kind", "expected_range", "unit"),
    [
        ("evo_lc", "resetmaintwarn", cb.BUTTON, None, None),
        ("evo_lc", "exerciseduration", cb.SETTING, (12, 60), "min"),
        ("evo_lc", "startupdelay", cb.SETTING, (5, 1500), "s"),
        ("evo_lc", "warmupdelay", cb.SETTING, (2, 1500), "s"),
        ("evo_lc", "thresholdvolts", cb.SETTING, (0, 342), "V"),
        ("evo_lc", "pickupvolts", cb.SETTING, (175, 342), "V"),
        ("mebay", "start", cb.BUTTON, None, None),  # passcode input only
        ("pz200", "pzremotestop", cb.BUTTON, None, None),
        ("pz200", "pzremotestarttransfer", cb.ACTION, (5, 3600), "s"),
        ("pz200", "exerciseduration", cb.SETTING, (5, 60), "min"),
        ("pz200", "returntoutilitydelay", cb.SETTING, (15, 1500), "s"),
        ("pz200", "cooldowntime", cb.SETTING, (60, 300), "s"),
    ],
)
def test_classify_captured_buttons(capture, command, kind, expected_range, unit) -> None:
    """Ranges match the tooltips genmon shows for these inputs."""
    button = controller_buttons(capture)[command]
    assert cb.classify(button) == kind
    if expected_range:
        step = cb.number_inputs(button)[0]
        assert cb.input_range(step) == expected_range
        assert cb.unit(step) == unit


def test_classify_action_and_unsupported() -> None:
    assert cb.classify(PZ_START) == cb.ACTION
    assert cb.input_range(PZ_START["command_sequence"][0]) == (5, 3600)
    two_inputs = copy.deepcopy(PZ_START)
    two_inputs["command_sequence"][1] = dict(two_inputs["command_sequence"][0], reg="0005")
    assert cb.classify(two_inputs) == cb.UNSUPPORTED


def test_build_command() -> None:
    mebay = controller_buttons("mebay")["start"]
    payload = json.loads(cb.build_command(mebay, "4321").split("=", 1)[1])
    assert payload[0]["onewordcommand"] == "start"
    assert payload[0]["command_sequence"][0]["value"] == "4321"

    payload = json.loads(cb.build_command(PZ_START, "", 600).split("=", 1)[1])
    assert [s.get("value") for s in payload[0]["command_sequence"]] == ["600", "0019"]
    # the definition itself is not changed
    assert "value" not in PZ_START["command_sequence"][0]


# ---------------------------------------------------------------- entities
@pytest.fixture
def capture() -> str:
    return "evo_lc"


async def set_number(hass: HomeAssistant, eid: str, value: float) -> None:
    await hass.services.async_call(
        "number", SERVICE_SET_VALUE, {ATTR_ENTITY_ID: eid, ATTR_VALUE: value}, blocking=True
    )


def sent_value(api: FakeGenmonApi) -> tuple[str, list]:
    """The button and input values of the last set_button_command."""
    payload = json.loads(api.commands[-1].split("=", 1)[1])[0]
    return payload["onewordcommand"], [s.get("value") for s in payload["command_sequence"]]


async def test_setting_is_a_number(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """Exercise Duration is a number (12-60 min) that writes the controller."""
    registry = er.async_get(hass)
    uid = f"{init_integration.entry_id}_ctrl_exerciseduration"
    assert not registry.async_get_entity_id("button", DOMAIN, uid)
    eid = registry.async_get_entity_id("number", DOMAIN, uid)
    attributes = hass.states.get(eid).attributes
    assert (attributes[ATTR_MIN], attributes[ATTR_MAX]) == (12, 60)
    assert attributes[ATTR_UNIT_OF_MEASUREMENT] == "min"
    assert registry.async_get(eid).entity_category == "config"

    await set_number(hass, eid, 30)
    assert sent_value(mock_api) == ("exerciseduration", ["30"])
    assert float(hass.states.get(eid).state) == 30

    # outside 12-60: refused by Home Assistant, nothing sent
    sent = len(mock_api.commands)
    with pytest.raises(ServiceValidationError):
        await set_number(hass, eid, 61)
    assert len(mock_api.commands) == sent


async def test_rejected_setting_keeps_value(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """If genmon rejects the value, the number doesn't show it."""
    eid = entity_id(hass, init_integration, "number", "ctrl_pickupvolts")
    mock_api.command_result = "Error in ExecuteRemoteCommand: Failed bounds check"
    with pytest.raises(HomeAssistantError):
        await set_number(hass, eid, 200)
    assert hass.states.get(eid).state == "unknown"


async def test_value_genmon_refuses_within_range(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """A range with gaps: values genmon's bounds check refuses aren't sent."""
    gappy = {"onewordcommand": "gappy", "title": "Gappy", "command_sequence": [
        {"reg": "0100", "input_title": "Volts", "type": "int", "length": 2,
         "bounds_regex": "^(10|20|30)$", "tooltip": "10, 20 or 30."}]}
    mock_api.entities["buttons_from_controller"].append(gappy)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    eid = entity_id(hass, config_entry, "number", "ctrl_gappy")
    with pytest.raises(ServiceValidationError) as err:
        await set_number(hass, eid, 15)
    assert err.value.translation_key == "controller_input_out_of_range"
    await set_number(hass, eid, 20)
    assert sent_value(mock_api) == ("gappy", ["20"])


@pytest.mark.parametrize("capture", ["pz200"])
async def test_action_with_input(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """A start with a run time: the number is kept, the button sends it."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    button = entity_id(hass, config_entry, "button", "ctrl_pzremotestarttransfer")
    number = entity_id(hass, config_entry, "number", "ctrl_pzremotestarttransfer_input")

    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: button}, blocking=True)
    assert err.value.translation_key == "controller_input_not_set"

    sent = len(mock_api.commands)
    await set_number(hass, number, 600)
    assert len(mock_api.commands) == sent  # setting the run time doesn't start it

    await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: button}, blocking=True)
    assert sent_value(mock_api) == ("pzremotestarttransfer", ["600", "0019"])


@pytest.mark.parametrize("capture", ["pz200"])
async def test_action_input_restored(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """The run time set before a restart is still used after it."""
    registry = er.async_get(hass)
    number = registry.async_get_or_create(
        "number", DOMAIN, f"{config_entry.entry_id}_ctrl_pzremotestarttransfer_input",
        config_entry=config_entry,
    ).entity_id
    mock_restore_cache_with_extra_data(hass, [(
        State(number, "120"),
        {"native_max_value": 3600, "native_min_value": 5, "native_step": 1,
         "native_unit_of_measurement": "s", "native_value": 120},
    )])
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    assert float(hass.states.get(number).state) == 120
    button = entity_id(hass, config_entry, "button", "ctrl_pzremotestarttransfer")
    await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: button}, blocking=True)
    assert sent_value(mock_api) == ("pzremotestarttransfer", ["120", "0019"])


async def test_old_input_buttons_removed(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """The buttons the settings used to be are removed; the numbers stay."""
    registry = er.async_get(hass)
    uid = f"{config_entry.entry_id}_ctrl_exerciseduration"
    old = registry.async_get_or_create("button", DOMAIN, uid, config_entry=config_entry)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][config_entry.entry_id]
    _async_cleanup_stale_entities(hass, config_entry, coordinator, 0)
    assert registry.async_get(old.entity_id)  # removed only after STALE_AFTER
    _async_cleanup_stale_entities(hass, config_entry, coordinator, STALE_AFTER)
    assert registry.async_get(old.entity_id) is None
    assert registry.async_get_entity_id("number", DOMAIN, uid)
    assert registry.async_get_entity_id(
        "button", DOMAIN, f"{config_entry.entry_id}_ctrl_resetmaintwarn"
    )
