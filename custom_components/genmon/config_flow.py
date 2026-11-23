"""Config flow for Genmon Generator Monitor."""
from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .api import GenmonApiClient, GenmonAuthError, GenmonConnectionError
from .const import (
    CONF_API_KEY,
    CONF_BLACKLIST,
    CONF_BUTTON_PASSCODE,
    CONF_INCLUDE_MONITOR_STATS,
    CONF_INCLUDE_WEATHER,
    CONF_SCAN_INTERVAL,
    CONF_USE_SSL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Required(CONF_API_KEY): str,
    }
)

API_KEY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): str,
    }
)


class GenmonConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Genmon."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._discovery_info: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle manual setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            info, use_ssl = await self._try_validate(
                errors,
                user_input[CONF_HOST],
                user_input[CONF_PORT],
                user_input[CONF_API_KEY],
            )
            if not errors:
                unique_id = self._get_unique_id(info, user_input[CONF_HOST])
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                title = info.get("model", info.get("Model", "Genmon Generator"))
                data = {**user_input, CONF_USE_SSL: use_ssl}
                return self.async_create_entry(title=title, data=data)

        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )

    async def async_step_zeroconf(
        self, discovery_info
    ) -> FlowResult:
        """Handle Zeroconf discovery."""
        host = str(discovery_info.host)
        port = discovery_info.port or DEFAULT_PORT
        properties = discovery_info.properties or {}

        self._host = host
        self._port = port
        self._discovery_info = properties
        self._discovery_use_ssl = properties.get("https", "0") == "1"

        # Use serial as unique ID if available
        serial = properties.get("serial", "")
        if serial and serial != "unknown":
            await self.async_set_unique_id(serial)
            self._abort_if_unique_id_configured(
                updates={CONF_HOST: host, CONF_PORT: port}
            )
        else:
            await self.async_set_unique_id(f"genmon_{host}_{port}")
            self._abort_if_unique_id_configured()

        model = properties.get("model", "Genmon Generator")
        self.context["title_placeholders"] = {
            "name": model,
            "host": host,
            "port": str(port),
        }

        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Confirm Zeroconf discovery — user just needs to enter API key."""
        errors: dict[str, str] = {}

        if user_input is not None:
            info, use_ssl = await self._try_validate(
                errors, self._host, self._port, user_input[CONF_API_KEY]
            )
            if not errors:
                title = info.get("model", info.get("Model", "Genmon Generator"))
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_HOST: self._host,
                        CONF_PORT: self._port,
                        CONF_API_KEY: user_input[CONF_API_KEY],
                        CONF_USE_SSL: use_ssl,
                    },
                )

        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=API_KEY_SCHEMA,
            errors=errors,
            description_placeholders={
                "host": self._host,
                "port": str(self._port),
                "model": self._discovery_info.get("model", "Generator"),
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reconfiguration."""
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])

        if user_input is not None:
            _info, use_ssl = await self._try_validate(
                errors,
                user_input[CONF_HOST],
                user_input[CONF_PORT],
                user_input[CONF_API_KEY],
            )
            if not errors:
                data = {**user_input, CONF_USE_SSL: use_ssl}
                return self.async_update_reload_and_abort(
                    entry, data=data
                )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=entry.data.get(CONF_HOST, "")): str,
                    vol.Required(CONF_PORT, default=entry.data.get(CONF_PORT, DEFAULT_PORT)): int,
                    vol.Required(CONF_API_KEY, default=entry.data.get(CONF_API_KEY, "")): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> FlowResult:
        """genhalink rejected the API key: ask for the new one."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Enter and check the new API key."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            _info, use_ssl = await self._try_validate(
                errors,
                entry.data[CONF_HOST],
                entry.data[CONF_PORT],
                user_input[CONF_API_KEY],
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_API_KEY: user_input[CONF_API_KEY],
                        CONF_USE_SSL: use_ssl,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=API_KEY_SCHEMA,
            errors=errors,
            description_placeholders={
                "host": entry.data[CONF_HOST],
                "port": str(entry.data[CONF_PORT]),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return GenmonOptionsFlow(config_entry)

    async def _validate(self, host: str, port: int, api_key: str) -> tuple[dict, bool]:
        """Validate the connection and return (info, use_ssl).

        Tries HTTPS first; falls back to HTTP automatically.
        """
        client = GenmonApiClient(host, port, api_key, use_ssl=True)
        try:
            info = await client.validate_connection()
            # After validate_connection, client._use_ssl reflects what worked
            return info, client._use_ssl
        finally:
            await client.close()

    async def _try_validate(
        self, errors: dict[str, str], host: str, port: int, api_key: str
    ) -> tuple[dict, bool]:
        """_validate, recording a failure in errors["base"] for the form."""
        try:
            return await self._validate(host, port, api_key)
        except GenmonConnectionError:
            errors["base"] = "cannot_connect"
        except GenmonAuthError:
            errors["base"] = "invalid_auth"
        except Exception:
            _LOGGER.exception("Unexpected error")
            errors["base"] = "unknown"
        return {}, False

    @staticmethod
    def _get_unique_id(info: dict, host: str) -> str:
        serial = info.get("serial", info.get("SerialNumber", ""))
        if serial:
            return str(serial)
        return f"genmon_{host}"


class GenmonOptionsFlow(OptionsFlow):
    """Options flow for Genmon."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL,
                        default=self._config_entry.options.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=2, max=60)),
                    vol.Optional(
                        CONF_INCLUDE_MONITOR_STATS,
                        default=self._config_entry.options.get(
                            CONF_INCLUDE_MONITOR_STATS, True
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_INCLUDE_WEATHER,
                        default=self._config_entry.options.get(
                            CONF_INCLUDE_WEATHER, True
                        ),
                    ): bool,
                    vol.Optional(
                        CONF_BLACKLIST,
                        default=self._config_entry.options.get(CONF_BLACKLIST, ""),
                    ): str,
                    vol.Optional(
                        CONF_BUTTON_PASSCODE,
                        default=self._config_entry.options.get(CONF_BUTTON_PASSCODE, ""),
                    ): str,
                }
            ),
        )
