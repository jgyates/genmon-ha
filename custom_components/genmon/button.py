"""Button platform for Genmon Generator Monitor."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import controller_buttons as cb
from .const import CONF_BUTTON_PASSCODE, DOMAIN
from .coordinator import GenmonCoordinator
from .entity import GenmonEntity, should_add_entity

_LOGGER = logging.getLogger(__name__)

# Icon mapping for common controller button onewordcommand values
_ICON_MAP = {
    "start": "mdi:play",
    "startcmd": "mdi:play",
    "stop": "mdi:stop",
    "stopcmd": "mdi:stop",
    "auto": "mdi:autorenew",
    "manual": "mdi:hand-back-right",
    "mute": "mdi:volume-off",
    "reset": "mdi:restart",
    "faultreset": "mdi:restart-alert",
    "test": "mdi:test-tube",
    "startexercise": "mdi:test-tube",
    "transfer": "mdi:transfer",
    "off": "mdi:power-off",
    "on": "mdi:power",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Genmon buttons from entity definitions."""
    coordinator: GenmonCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = []

    # Static button definitions from JSON files
    controller_type = coordinator.entities.get("controller_type")
    capabilities = coordinator.capabilities
    known_ids: set[str] = set()
    entity_defs = coordinator.entities.get("buttons", [])
    for defn in entity_defs:
        if should_add_entity(defn, controller_type, capabilities):
            entities.append(GenmonButton(coordinator, defn))
            known_ids.add(defn.get("entity_id", defn.get("name", "")))

    # Dynamic buttons from controller StartInfo
    controller_buttons = coordinator.entities.get("buttons_from_controller", [])
    passcode = entry.options.get(CONF_BUTTON_PASSCODE, "")
    for button in controller_buttons:
        kind = cb.classify(button)
        if kind in (cb.BUTTON, cb.ACTION):
            entities.append(GenmonControllerButton(coordinator, button, passcode))
        elif kind == cb.UNSUPPORTED:
            _LOGGER.warning(
                "Controller button %s has more than one input; not supported",
                button.get("onewordcommand"),
            )
        # SETTING buttons are number entities (number.py)

    async_add_entities(entities)

    @callback
    def _add_new(new_defs: list[dict]) -> None:
        ct = coordinator.entities.get("controller_type")
        caps = coordinator.capabilities
        async_add_entities([
            GenmonButton(coordinator, d)
            for d in new_defs
            if should_add_entity(d, ct, caps)
        ])

    coordinator.register_platform("buttons", _add_new, known_ids)


class GenmonButton(GenmonEntity, ButtonEntity):
    """A Genmon button entity for sending commands."""

    def __init__(self, coordinator: GenmonCoordinator, defn: dict[str, Any]) -> None:
        self._defn = defn
        entity_key = defn.get("entity_id", defn.get("name", ""))
        name = defn.get("name", entity_key)
        super().__init__(coordinator, entity_key, name)

        self._command = defn.get("command", "")
        self._attr_icon = defn.get("icon")

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.debug("Pressing button %s: %s", self.name, self._command)
        await self.coordinator.async_send_command(self._command)


class GenmonControllerButton(GenmonEntity, ButtonEntity):
    """A button from the controller's StartInfo buttons (set_button_command).

    Passcode inputs get the button_passcode option. A button with a number
    input (controller_buttons.ACTION) uses the value of its input number.
    """

    def __init__(
        self,
        coordinator: GenmonCoordinator,
        button: dict[str, Any],
        passcode: str,
    ) -> None:
        onewordcommand = button.get("onewordcommand", "")
        title = button.get("title", onewordcommand)
        super().__init__(coordinator, cb.button_key(button), title)

        self._button = button
        self._onewordcommand = onewordcommand
        self._passcode = passcode
        self._has_input = cb.classify(button) == cb.ACTION
        self._attr_icon = _ICON_MAP.get(onewordcommand.lower(), "mdi:button-pointer")

    async def async_press(self) -> None:
        """Handle the button press via set_button_command."""
        value = None
        if self._has_input:
            value = self.coordinator.controller_inputs.get(cb.button_key(self._button))
            if value is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="controller_input_not_set",
                    translation_placeholders={"button": self.name or self._onewordcommand},
                )
        _LOGGER.debug("Pressing controller button %s", self._onewordcommand)
        await self.coordinator.async_send_command(
            cb.build_command(self._button, self._passcode, value)
        )
