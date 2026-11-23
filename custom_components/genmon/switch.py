"""Switch platform for Genmon Generator Monitor."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GenmonCoordinator
from .entity import GenmonEntity, should_add_entity
from .exercise import quiet_mode_from_state

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Genmon switches from entity definitions."""
    coordinator: GenmonCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[GenmonSwitch] = []
    known_ids: set[str] = set()

    controller_type = coordinator.entities.get("controller_type")
    capabilities = coordinator.capabilities
    entity_defs = coordinator.entities.get("switches", [])
    for defn in entity_defs:
        if should_add_entity(defn, controller_type, capabilities):
            entities.append(GenmonSwitch(coordinator, defn))
            known_ids.add(defn.get("entity_id", defn.get("name", "")))

    async_add_entities(entities)

    @callback
    def _add_new(new_defs: list[dict]) -> None:
        ct = coordinator.entities.get("controller_type")
        caps = coordinator.capabilities
        async_add_entities([
            GenmonSwitch(coordinator, d)
            for d in new_defs
            if should_add_entity(d, ct, caps)
        ])

    coordinator.register_platform("switches", _add_new, known_ids)


class GenmonSwitch(GenmonEntity, SwitchEntity):
    """A Genmon switch entity (e.g., quiet mode)."""

    def __init__(self, coordinator: GenmonCoordinator, defn: dict[str, Any]) -> None:
        self._defn = defn
        entity_key = defn.get("entity_id", defn.get("name", ""))
        name = defn.get("name", entity_key)
        super().__init__(coordinator, entity_key, name)

        self._command_on = defn.get("command_on", "")
        self._command_off = defn.get("command_off", "")
        self._state_path = defn.get("state_path", "")
        self._payload_on = defn.get("payload_on", "On")
        self._payload_off = defn.get("payload_off", "Off")
        self._attr_icon = defn.get("icon")

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        value = self._get_state_value()
        if value is None and self._defn.get("entity_id") == "quiet_mode":
            # older genmon defines a path it never reports
            value = quiet_mode_from_state(self.coordinator.data)
        if value is None:
            return None
        value = str(value).strip()
        if value == self._payload_on:
            return True
        if value == self._payload_off:
            return False
        return None  # e.g. "Unknown"

    def _get_state_value(self) -> Any | None:
        """The value at state_path, or None if genmon doesn't report it."""
        if not self._state_path:
            return None
        data = self.coordinator.data
        for part in self._state_path.split("/"):
            if not isinstance(data, dict):
                return None
            data = data.get(part)
            if data is None:
                return None
        return self._unwrap_num_json(data)

    async def async_turn_on(self, **kwargs: Any) -> None:
        _LOGGER.debug("Switch ON %s: %s", self.name, self._command_on)
        await self.coordinator.async_send_command(self._command_on)

    async def async_turn_off(self, **kwargs: Any) -> None:
        _LOGGER.debug("Switch OFF %s: %s", self.name, self._command_off)
        await self.coordinator.async_send_command(self._command_off)
