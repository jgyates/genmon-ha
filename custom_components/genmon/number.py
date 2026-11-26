"""Number platform for Genmon Generator Monitor."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import controller_buttons as cb
from .const import CONF_BUTTON_PASSCODE, DOMAIN
from .coordinator import GenmonCoordinator
from .entity import GenmonEntity, should_add_entity
from .exercise import (
    MONTHLY,
    build_exercise_command,
    changed_schedule,
    schedule_from_state,
)

_LOGGER = logging.getLogger(__name__)

# entity_id -> the ExerciseSchedule field the number sets
SCHEDULE_FIELDS = {
    "exercise_day_of_month": "day_of_month",
    "exercise_hour": "hour",
    "exercise_minute": "minute",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Genmon numbers from entity definitions."""
    coordinator: GenmonCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []
    known_ids: set[str] = set()

    controller_type = coordinator.entities.get("controller_type")
    capabilities = coordinator.capabilities
    entity_defs = coordinator.entities.get("numbers", [])
    for defn in entity_defs:
        if should_add_entity(defn, controller_type, capabilities):
            entities.append(GenmonNumber(coordinator, defn))
            known_ids.add(defn.get("entity_id", defn.get("name", "")))

    # Controller buttons that take a number (controller_buttons.py)
    passcode = entry.options.get(CONF_BUTTON_PASSCODE, "")
    for button in coordinator.entities.get("buttons_from_controller", []):
        if cb.classify(button) in (cb.SETTING, cb.ACTION):
            entities.append(GenmonControllerNumber(coordinator, button, passcode))

    async_add_entities(entities)

    @callback
    def _add_new(new_defs: list[dict]) -> None:
        ct = coordinator.entities.get("controller_type")
        caps = coordinator.capabilities
        async_add_entities([
            GenmonNumber(coordinator, d)
            for d in new_defs
            if should_add_entity(d, ct, caps)
        ])

    coordinator.register_platform("numbers", _add_new, known_ids)


class GenmonNumber(GenmonEntity, NumberEntity):
    """A Genmon number entity (exercise hour, minute, day of the month)."""

    def __init__(self, coordinator: GenmonCoordinator, defn: dict[str, Any]) -> None:
        self._defn = defn
        entity_key = defn.get("entity_id", defn.get("name", ""))
        name = defn.get("name", entity_key)
        super().__init__(coordinator, entity_key, name)

        self._attr_native_min_value = float(defn.get("min", 0))
        self._attr_native_max_value = float(defn.get("max", 100))
        self._attr_native_step = float(defn.get("step", 1))
        self._attr_mode = NumberMode.BOX
        self._attr_icon = defn.get("icon")
        self._field = SCHEDULE_FIELDS.get(defn.get("entity_id", ""))
        # shown until genmon reports a schedule
        self._stored_value: float | None = None

    @property
    def native_value(self) -> float | None:
        """Return the value from genmon's exercise schedule."""
        schedule = schedule_from_state(self.coordinator.data)
        if schedule is None or self._field is None:
            return self._stored_value
        value = getattr(schedule, self._field)
        # e.g. the day of the month while the schedule is weekly
        return float(value) if value is not None else None

    async def async_set_native_value(self, value: float) -> None:
        """Send the schedule with this value changed."""
        if self._field is None:
            _LOGGER.warning("%s: no genmon command for this number", self.entity_id)
            return
        schedule = changed_schedule(self.coordinator.data, **{self._field: int(value)})
        if schedule is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="exercise_schedule_unknown",
            )
        if self._field == "day_of_month" and schedule.frequency != MONTHLY:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="exercise_day_of_month_not_monthly",
            )
        command = build_exercise_command(schedule)
        _LOGGER.debug("Sending exercise command: %s", command)
        await self.coordinator.async_send_command(command)
        self._stored_value = value
        self.async_write_ha_state()


class GenmonControllerNumber(GenmonEntity, RestoreNumber):
    """The number input of a controller button.

    SETTING (e.g. Exercise Duration): setting the number writes it to the
    controller. ACTION (e.g. a remote start with a run time): the number is
    kept for the button, which sends it when pressed. genmon doesn't report
    these registers, so the number shows the last value set in Home
    Assistant (kept over restarts).
    """

    _attr_mode = NumberMode.BOX
    _attr_native_step = 1.0

    def __init__(
        self, coordinator: GenmonCoordinator, button: dict[str, Any], passcode: str
    ) -> None:
        self._button = button
        self._input = cb.number_inputs(button)[0]
        self._passcode = passcode
        self._is_setting = cb.classify(button) == cb.SETTING
        title = button.get("title", button.get("onewordcommand", ""))
        key = cb.button_key(button)
        if self._is_setting:
            # replaces the button of the same key (same unique_id suffix)
            super().__init__(coordinator, key, title)
            self._attr_entity_category = EntityCategory.CONFIG
        else:
            super().__init__(coordinator, key + "_input", f"{title} {self._input['input_title']}")
        self._input_key = key
        low, high = cb.input_range(self._input)
        self._attr_native_min_value = float(low)
        self._attr_native_max_value = float(high)
        self._attr_native_unit_of_measurement = cb.unit(self._input)
        self._attr_native_value: float | None = None
        self._attr_icon = "mdi:tune-vertical" if self._is_setting else "mdi:timer-cog-outline"

    async def async_added_to_hass(self) -> None:
        """Restore the last value set in Home Assistant."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._attr_native_value = last.native_value
            if not self._is_setting:
                self.coordinator.controller_inputs[self._input_key] = int(last.native_value)

    async def async_set_native_value(self, value: float) -> None:
        """Write a setting to the controller, or keep an action's input."""
        number = int(value)
        if not cb.value_accepted(self._input, number):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="controller_input_out_of_range",
                translation_placeholders={
                    "value": str(number),
                    "help": self._input.get("tooltip", ""),
                },
            )
        if self._is_setting:
            await self.coordinator.async_send_command(
                cb.build_command(self._button, self._passcode, number)
            )
        else:
            self.coordinator.controller_inputs[self._input_key] = number
        self._attr_native_value = float(number)
        self.async_write_ha_state()
