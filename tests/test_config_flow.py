"""Config flow: manual setup, zeroconf, reconfigure, reauth, options."""
from __future__ import annotations

from ipaddress import ip_address

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
try:
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
except ImportError:  # Home Assistant before 2025.1 (the minimum version is tested too)
    from homeassistant.components.zeroconf import ZeroconfServiceInfo

from custom_components.genmon.const import (
    CONF_API_KEY,
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_USE_SSL,
    DOMAIN,
)

from .conftest import API_KEY, HOST, PORT, FakeGenmonApi, GenmonAuthError, GenmonConnectionError

USER_INPUT = {CONF_HOST: HOST, CONF_PORT: PORT, CONF_API_KEY: API_KEY}

pytestmark = pytest.mark.usefixtures("mock_setup_entry")


@pytest.fixture
def mock_setup_entry():
    """Flows only: don't set up the integration after creating the entry."""
    from unittest.mock import patch

    with patch("custom_components.genmon.async_setup_entry", return_value=True) as mock:
        yield mock


async def test_user_flow(hass: HomeAssistant, mock_api: FakeGenmonApi) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == mock_api.info["model"]
    assert result["data"] == {**USER_INPUT, CONF_USE_SSL: False}


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (GenmonConnectionError("down"), "cannot_connect"),
        (GenmonAuthError("bad key"), "invalid_auth"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, mock_api: FakeGenmonApi, error: Exception, reason: str
) -> None:
    """Errors are shown on the form, and the user can try again."""
    mock_api.error = error
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": reason}

    mock_api.error = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_already_configured(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


def zeroconf_info(serial: str = "ABC123", https: str = "0") -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=ip_address(HOST),
        ip_addresses=[ip_address(HOST)],
        hostname="genmon.local.",
        name="Genmon Generator (genmon)._genmon._tcp.local.",
        port=PORT,
        type="_genmon._tcp.local.",
        properties={"api_version": "1", "https": https, "serial": serial, "model": "G0070330"},
    )


async def test_zeroconf_flow(hass: HomeAssistant, mock_api: FakeGenmonApi) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=zeroconf_info()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: API_KEY}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {**USER_INPUT, CONF_USE_SSL: False}
    assert result["result"].unique_id == "ABC123"


async def test_zeroconf_updates_known_host(hass: HomeAssistant, mock_api: FakeGenmonApi) -> None:
    """A known generator found at a new address gets its address updated."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="ABC123",
        data={**USER_INPUT, CONF_HOST: "192.0.2.99", CONF_USE_SSL: False},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=zeroconf_info()
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == HOST


async def test_reconfigure(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    new = {**USER_INPUT, CONF_HOST: "192.0.2.20"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], new)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_HOST] == "192.0.2.20"


async def test_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """A new API key is checked against the entry's host and then saved."""
    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    mock_api.error = GenmonAuthError("bad key")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}

    mock_api.error = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "new-key"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_API_KEY] == "new-key"
    assert config_entry.data[CONF_HOST] == HOST


async def test_options_flow(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 10}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_SCAN_INTERVAL] == 10
