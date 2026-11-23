"""Quiet mode switch."""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from custom_components.genmon.exercise import quiet_mode_from_state

from .conftest import FakeGenmonApi
from .test_sensor import entity_id, push_state

OLD_PATH = "Maintenance/Exercise/Quiet Mode"  # genmon never reported this path
NEW_PATH = "gui_status/ExerciseInfo/QuietMode"


@pytest.fixture
def capture() -> str:
    """The liquid-cooled Evolution: the controller with quiet mode."""
    return "evo_lc"


def set_quiet_mode(api: FakeGenmonApi, value: str | None) -> None:
    """Report quiet mode the way genmon does (exercise info and text)."""
    exercise_info = api.state["gui_status"]["ExerciseInfo"]
    text = "Weekly Wednesday 12:00 Quiet Mode %s" % (value or "Off")
    if value is None:
        exercise_info.pop("QuietMode", None)
    else:
        exercise_info["QuietMode"] = value
    api.state["Maintenance"]["Exercise"]["Exercise Time"] = text


def set_state_path(api: FakeGenmonApi, path: str) -> None:
    for switch in api.entities["switches"]:
        if switch["entity_id"] == "quiet_mode":
            switch["state_path"] = path


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"gui_status": {"ExerciseInfo": {"QuietMode": "On"}}}, "On"),
        ({"gui_status": {"ExerciseInfo": {"QuietMode": "Off"}}}, "Off"),
        ({"Maintenance": {"Exercise": {"Exercise Time": "Weekly Sunday 16:00 Quiet Mode On"}}}, "On"),
        ({"Maintenance": {"Exercise": {"Exercise Time": "Monthly Day-15 09:00 Quiet Mode Off"}}}, "Off"),
        ({"Maintenance": {"Exercise": {"Exercise Time": "Weekly Sunday 16:00"}}}, None),
        ({"gui_status": {"ExerciseInfo": {"QuietMode": "Unknown"}}}, None),
        ({}, None),
    ],
)
def test_quiet_mode_from_state(state: dict, expected: str | None) -> None:
    assert quiet_mode_from_state(state) == expected


@pytest.mark.parametrize("path", [OLD_PATH, NEW_PATH])
async def test_quiet_mode_state(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: FakeGenmonApi, path: str
) -> None:
    """Quiet mode shows genmon's setting, whichever path genhalink defines."""
    set_state_path(mock_api, path)
    set_quiet_mode(mock_api, "On")
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    eid = entity_id(hass, config_entry, "switch", "quiet_mode")
    assert hass.states.get(eid).state == STATE_ON

    set_quiet_mode(mock_api, "Off")
    await push_state(hass, mock_api, mock_api.state)
    assert hass.states.get(eid).state == STATE_OFF

    set_quiet_mode(mock_api, "Unknown")
    await push_state(hass, mock_api, mock_api.state)
    assert hass.states.get(eid).state == STATE_UNKNOWN


async def test_quiet_mode_from_exercise_time(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """Without the exercise info, the Exercise Time text has it."""
    del mock_api.state["gui_status"]
    mock_api.state["Maintenance"]["Exercise"]["Exercise Time"] = (
        "Weekly Wednesday 12:00 Quiet Mode On"
    )
    await push_state(hass, mock_api, mock_api.state)
    eid = entity_id(hass, init_integration, "switch", "quiet_mode")
    assert hass.states.get(eid).state == STATE_ON


async def test_quiet_mode_commands(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    eid = entity_id(hass, init_integration, "switch", "quiet_mode")
    for service, command in [("turn_on", "setquiet=on"), ("turn_off", "setquiet=off")]:
        await hass.services.async_call("switch", service, {ATTR_ENTITY_ID: eid}, blocking=True)
        assert mock_api.commands[-1] == command
