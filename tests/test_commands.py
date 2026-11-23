"""Commands from buttons, switches, numbers and selects: genmon's errors are
shown to the user instead of being ignored."""
from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.genmon.const import CONF_BUTTON_PASSCODE, DOMAIN

from .conftest import FakeGenmonApi, GenmonAuthError, GenmonConnectionError
from .test_sensor import entity_id

# (platform, entity key, service, service data, command genmon gets) for the
# liquid-cooled Evolution capture (it has every kind of control)
ACTIONS = [
    ("button", "start", "press", {}, "setremote=START"),
    ("button", "ctrl_resetmaintwarn", "press", {}, "set_button_command="),
    ("switch", "quiet_mode", "turn_on", {}, "setquiet=on"),
    ("number", "exercise_hour", "set_value", {"value": 10}, "setexercise=Wednesday,10:00,Weekly"),
    ("select", "exercise_day_of_week", "select_option", {"option": "Friday"},
     "setexercise=Friday,12:00,Weekly"),
]


@pytest.fixture
def capture() -> str:
    return "evo_lc"


async def call(
    hass: HomeAssistant, entry: MockConfigEntry, platform: str, key: str,
    service: str, data: dict[str, Any],
) -> None:
    await hass.services.async_call(
        platform, service,
        {ATTR_ENTITY_ID: entity_id(hass, entry, platform, key), **data},
        blocking=True,
    )


@pytest.mark.parametrize(("platform", "key", "service", "data", "command"), ACTIONS)
async def test_command_sent(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: FakeGenmonApi,
    platform: str, key: str, service: str, data: dict, command: str,
) -> None:
    await call(hass, init_integration, platform, key, service, data)
    assert mock_api.commands[-1].startswith(command)


@pytest.mark.parametrize(("platform", "key", "service", "data", "command"), ACTIONS)
@pytest.mark.parametrize(
    ("answer", "error", "translation_key"),
    [
        ("Not supported on this controller.", None, "command_failed"),
        ("Error in ExecuteRemoteCommand: Failed bounds check", None, "command_failed"),
        (None, None, "command_no_answer"),
        ("OK", GenmonConnectionError("Timeout connecting to genhalink"), "cannot_connect"),
    ],
)
async def test_command_errors_are_shown(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: FakeGenmonApi,
    platform: str, key: str, service: str, data: dict, command: str,
    answer: str | None, error: Exception | None, translation_key: str,
) -> None:
    """A rejected or unanswered command raises a translated HomeAssistantError."""
    mock_api.command_result = answer
    mock_api.error = error
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, init_integration, platform, key, service, data)
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == translation_key
    if answer and not error:
        assert answer in str(err.value)


@pytest.mark.parametrize("capture", ["mebay"])
async def test_passcode_not_in_error(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: FakeGenmonApi,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A controller button's command carries the passcode; errors don't show it."""
    hass.config_entries.async_update_entry(config_entry, options={CONF_BUTTON_PASSCODE: "4321"})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()

    mock_api.command_result = "Error in ExecuteRemoteCommand: Failed bounds check"
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, config_entry, "button", "ctrl_start", "press", {})
    assert '"value": "4321"' in mock_api.commands[-1]
    assert "4321" not in str(err.value)
    assert "4321" not in caplog.text


async def test_command_with_rejected_key_starts_reauth(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    mock_api.error = GenmonAuthError("Invalid API key")
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, init_integration, "button", "start", "press", {})
    assert err.value.translation_key == "invalid_auth"
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]
