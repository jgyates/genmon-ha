"""Select platform for Genmon Generator Monitor."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GenmonCoordinator
from .entity import GenmonEntity, should_add_entity
from .exercise import (
    WEEKLY,
    build_exercise_command,
    changed_schedule,
    enhanced_exercise_mode,
    schedule_from_state,
)

_LOGGER = logging.getLogger(__name__)

# entity_id -> the ExerciseSchedule field the select sets
SCHEDULE_FIELDS = {
    "exercise_frequency": "frequency",
    "exercise_day_of_week": "day_of_week",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Genmon selects from entity definitions."""
    coordinator: GenmonCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[GenmonSelect] = []
    known_ids: set[str] = set()

    controller_type = coordinator.entities.get("controller_type")
    capabilities = coordinator.capabilities
    entity_defs = coordinator.entities.get("selects", [])
    for defn in entity_defs:
        if should_add_entity(defn, controller_type, capabilities):
            entities.append(GenmonSelect(coordinator, defn))
            known_ids.add(defn.get("entity_id", defn.get("name", "")))

    async_add_entities(entities)

    @callback
    def _add_new(new_defs: list[dict]) -> None:
        ct = coordinator.entities.get("controller_type")
        caps = coordinator.capabilities
        async_add_entities([
            GenmonSelect(coordinator, d)
            for d in new_defs
            if should_add_entity(d, ct, caps)
        ])

    coordinator.register_platform("selects", _add_new, known_ids)


class GenmonSelect(GenmonEntity, SelectEntity):
    """A Genmon select entity (exercise frequency, day of the week)."""

    def __init__(self, coordinator: GenmonCoordinator, defn: dict[str, Any]) -> None:
        self._defn = defn
        entity_key = defn.get("entity_id", defn.get("name", ""))
        name = defn.get("name", entity_key)
        super().__init__(coordinator, entity_key, name)

        self._defn_options: list[str] = defn.get("options", [])
        self._attr_icon = defn.get("icon")
        self._field = SCHEDULE_FIELDS.get(defn.get("entity_id", ""))
        # shown until genmon reports a schedule
        self._current_option: str | None = None

    @property
    def options(self) -> list[str]:
        """The options; frequency is Weekly only without enhanced exercise mode."""
        if (
            self._field == "frequency"
            and enhanced_exercise_mode(self.coordinator.data) is False
        ):
            return [opt for opt in self._defn_options if opt == WEEKLY]
        return self._defn_options

    @property
    def current_option(self) -> str | None:
        """Return the current option from genmon's exercise schedule."""
        schedule = schedule_from_state(self.coordinator.data)
        if schedule is None or self._field is None:
            return self._current_option
        value = getattr(schedule, self._field)
        # e.g. no day of the week while the schedule is monthly
        return self._match_option(value) if value else None

    def _match_option(self, raw: str) -> str | None:
        """Case-insensitive match of a raw value against the defined options."""
        for opt in self._defn_options:
            if opt.lower() == raw.lower():
                return opt
        return raw

    async def async_select_option(self, option: str) -> None:
        """Send the schedule with this option changed."""
        if self._field is None:
            _LOGGER.warning("%s: no genmon command for this select", self.entity_id)
            return
        schedule = changed_schedule(self.coordinator.data, **{self._field: option})
        if schedule is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="exercise_schedule_unknown",
            )
        command = build_exercise_command(schedule)
        _LOGGER.debug("Sending exercise command: %s", command)
        await self.coordinator.async_send_command(command)
        self._current_option = option
        self.async_write_ha_state()
