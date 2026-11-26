"""DataUpdateCoordinator for Genmon Generator Monitor."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any, Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    GenmonApiClient,
    GenmonAuthError,
    GenmonCommandError,
    GenmonConnectionError,
)
from .const import (
    CONF_API_KEY,
    CONF_SCAN_INTERVAL,
    CONF_USE_SSL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_USE_SSL,
    DOMAIN,
)
from .exercise import enhanced_exercise_mode

_LOGGER = logging.getLogger(__name__)

# WebSocket reconnect wait in seconds: doubles while genhalink can't be reached
WS_RETRY_MIN = 5
WS_RETRY_MAX = 300


class GenmonCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that polls genhalink and listens on WebSocket."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.api = GenmonApiClient(
            entry.data[CONF_HOST],
            entry.data[CONF_PORT],
            entry.data[CONF_API_KEY],
            use_ssl=entry.data.get(CONF_USE_SSL, DEFAULT_USE_SSL),
        )
        self._ws_task: asyncio.Task | None = None
        self.entities: dict[str, Any] = {}
        self.info: dict[str, Any] = {}
        self._new_entity_callbacks: dict[str, Callable] = {}
        self._known_entity_ids: dict[str, set[str]] = {}
        # (entity domain, unique_id) of registry entities missing from the
        # entity list -> time.monotonic() when first seen missing (__init__.py)
        self.missing_since: dict[tuple[str, str], float] = {}
        # values of controller button inputs set in HA (controller_buttons.ACTION)
        self.controller_inputs: dict[str, int] = {}

        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch latest state from genhalink."""
        try:
            status = await self.api.get_status()
        except GenmonAuthError as err:
            # HA stops polling and asks the user for the new API key
            raise ConfigEntryAuthFailed(str(err)) from err
        except GenmonConnectionError as err:
            raise UpdateFailed(f"Error communicating with genhalink: {err}") from err

        # Entity definitions are needed before the platforms are set up; later
        # updates come from async_refresh_entities.
        try:
            if not self.entities:
                fresh = await self.api.get_entities()
                if fresh:
                    self.entities = fresh
            if not self.info:
                self.info = await self.api.get_info()
        except GenmonAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except GenmonConnectionError:
            _LOGGER.debug("Failed to fetch entities/info, will retry next poll")
        if not self.entities:
            # genhalink answers 503 until genmon has delivered complete data
            raise UpdateFailed("Waiting for entity definitions from genhalink")

        # The API returns {"state": {...}, "values": {...}}
        # Entities need the nested state dict for path traversal
        state = status.get("state", status)
        _LOGGER.debug(
            "Polled genmon: %d top-level keys in state",
            len(state) if isinstance(state, dict) else 0,
        )
        return state

    async def async_send_command(self, command: str) -> None:
        """Send a command to genmon, then refresh.

        Failures raise HomeAssistantError, so the user sees why a button,
        switch or setting didn't work. The messages never contain the
        command: a controller button's command can hold the passcode.
        """
        try:
            await self.api.send_command(command)
        except GenmonCommandError as err:
            if err.result is None or not str(err.result).strip():
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="command_no_answer",
                ) from err
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"result": str(err.result).strip()},
            ) from err
        except GenmonAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except GenmonConnectionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        await self.async_request_refresh()

    @property
    def capabilities(self) -> dict[str, Any]:
        """The controller capabilities from the entity list.

        genhalink copies EnhancedExerciseMode from genmon's start info, where
        genmon doesn't report it (always False); genmon reports it in
        gui_status/ExerciseInfo. None: unknown.
        """
        capabilities = dict(self.entities.get("capabilities", {}))
        if not capabilities:
            return capabilities  # older genhalink: no capability filtering
        capabilities["EnhancedExerciseMode"] = enhanced_exercise_mode(self.data)
        return capabilities

    async def async_refresh_entities(self, _now: Any = None) -> bool:
        """Fetch entity definitions again and add entities that are new.

        Runs on its own timer: WebSocket pushes call async_set_updated_data,
        which reschedules the coordinator poll, so a fetch inside
        _async_update_data never runs while genhalink keeps pushing data.
        Returns True if genhalink answered with an entity list.
        """
        try:
            fresh = await self.api.get_entities()
        except (GenmonConnectionError, GenmonAuthError) as err:
            # Includes 503 while genhalink waits for genmon after a restart;
            # the current list stays until a complete one is available.
            # A rejected API key is reported by the poll (reauth).
            _LOGGER.debug("Entity refresh skipped: %s", err)
            return False
        if not fresh:
            return False
        if self._new_entity_callbacks:
            self._dispatch_new_entities(fresh)
        self.entities = fresh
        return True

    @callback
    def forget_entity(self, platform_key: str, entity_key: str) -> None:
        """An entity was removed: add it again if genhalink lists it again."""
        self._known_entity_ids.get(platform_key, set()).discard(entity_key)

    def register_platform(
        self,
        platform_key: str,
        add_callback: Callable[[list[dict]], None],
        known_ids: set[str],
    ) -> None:
        """Store a platform's callback for dynamic entity discovery."""
        self._new_entity_callbacks[platform_key] = add_callback
        self._known_entity_ids[platform_key] = known_ids

    @callback
    def _dispatch_new_entities(self, fresh: dict) -> None:
        """Detect new entity definitions and dispatch them to platforms."""
        for platform_key, cb in self._new_entity_callbacks.items():
            known = self._known_entity_ids.get(platform_key, set())
            new_defs = []
            for defn in fresh.get(platform_key, []):
                eid = defn.get("entity_id", "")
                if eid and eid not in known:
                    new_defs.append(defn)
                    known.add(eid)
            if new_defs:
                _LOGGER.info(
                    "Discovered %d new %s entities: %s",
                    len(new_defs),
                    platform_key,
                    [d.get("entity_id") for d in new_defs],
                )
                cb(new_defs)

    async def _async_ws_listener(self) -> None:
        """Background WebSocket listener for push updates.

        Reconnects after WS_RETRY_MIN seconds when an established connection
        drops; while genhalink can't be reached the wait doubles up to
        WS_RETRY_MAX. The poll keeps the data current meanwhile.
        """
        failures = 0
        while True:
            logged_in = False
            try:
                logged_in = await self.api.listen_ws(self._handle_ws_message)
            except GenmonAuthError as err:
                # Retrying with the same key can't work: ask for a new one.
                # The entry reloads after reauth, which starts a new listener.
                _LOGGER.warning("genhalink rejected the API key: %s", err)
                self.config_entry.async_start_reauth(self.hass)
                return
            except Exception:  # noqa: BLE001 - keep the listener alive
                _LOGGER.debug("WebSocket listener error", exc_info=True)
            failures = 0 if logged_in else failures + 1
            delay = min(WS_RETRY_MIN * 2 ** max(failures - 1, 0), WS_RETRY_MAX)
            _LOGGER.debug("WebSocket disconnected, reconnecting in %ss", delay)
            await asyncio.sleep(delay)

    async def _handle_ws_message(self, data: dict) -> None:
        """Handle incoming WebSocket push data."""
        msg_type = data.get("type")
        if msg_type in ("state_update", "full_state"):
            state = data.get("state", {})
            if state:
                # Use async_set_updated_data to push new state to entities.
                # This also resets the poll timer so the next poll happens
                # update_interval seconds from now (avoids double-polling).
                self.async_set_updated_data(state)

    async def start_ws(self) -> None:
        """Start the WebSocket listener task."""
        if self._ws_task is None or self._ws_task.done():
            self._ws_task = self.hass.async_create_background_task(
                self._async_ws_listener(),
                f"{DOMAIN}_ws_listener",
            )

    async def stop_ws(self) -> None:
        """Stop the WebSocket listener and close the API session."""
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None
        await self.api.close()
