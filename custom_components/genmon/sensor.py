"""Sensor platform for Genmon Generator Monitor."""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import GenmonCoordinator
from .entity import GenmonEntity, should_add_entity

_LOGGER = logging.getLogger(__name__)
_UNSET = object()  # nothing written yet

UNIT_FIXUP = {"C": "°C", "F": "°F", "K": "K"}

# genmon's text for a value it can't determine, alone or with the unit it
# would have (e.g. "Unknown", "Unknown V", "Unknown kW")
GENMON_UNKNOWN = re.compile(r"unknown(\s+\S+)?", re.IGNORECASE)

DEVICE_CLASS_MAP = {
    "voltage": SensorDeviceClass.VOLTAGE,
    "current": SensorDeviceClass.CURRENT,
    "power": SensorDeviceClass.POWER,
    "energy": SensorDeviceClass.ENERGY,
    "frequency": SensorDeviceClass.FREQUENCY,
    "temperature": SensorDeviceClass.TEMPERATURE,
    "humidity": SensorDeviceClass.HUMIDITY,
    "pressure": SensorDeviceClass.PRESSURE,
    "duration": SensorDeviceClass.DURATION,
}

STATE_CLASS_MAP = {
    "measurement": SensorStateClass.MEASUREMENT,
    "total": SensorStateClass.TOTAL,
    "total_increasing": SensorStateClass.TOTAL_INCREASING,
}

CATEGORY_MAP = {
    "diagnostic": EntityCategory.DIAGNOSTIC,
    "config": EntityCategory.CONFIG,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Genmon sensors from entity definitions."""
    coordinator: GenmonCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[GenmonSensor] = []
    known_ids: set[str] = set()

    controller_type = coordinator.entities.get("controller_type")
    capabilities = coordinator.capabilities
    entity_defs = coordinator.entities.get("sensors", [])
    for defn in entity_defs:
        if should_add_entity(defn, controller_type, capabilities):
            entities.append(GenmonSensor(coordinator, defn))
            known_ids.add(defn.get("entity_id", defn.get("path", "")))

    async_add_entities(entities)

    @callback
    def _add_new(new_defs: list[dict]) -> None:
        ct = coordinator.entities.get("controller_type")
        caps = coordinator.capabilities
        async_add_entities([
            GenmonSensor(coordinator, d)
            for d in new_defs
            if should_add_entity(d, ct, caps)
        ])

    coordinator.register_platform("sensors", _add_new, known_ids)


class GenmonSensor(GenmonEntity, SensorEntity):
    """A Genmon sensor entity."""

    def __init__(self, coordinator: GenmonCoordinator, defn: dict[str, Any]) -> None:
        self._defn = defn
        entity_key = defn.get("path", defn.get("entity_id", ""))
        name = defn.get("name", entity_key.rsplit("/", 1)[-1])
        super().__init__(coordinator, defn.get("entity_id", entity_key), name)

        self._path = defn.get("path", "")
        self._attr_icon = defn.get("icon")
        # genhalink may send a bare "C"/"F"; HA's temperature device class needs "°C"/"°F"
        _defn_unit = defn.get("unit")
        self._expected_unit = UNIT_FIXUP.get(_defn_unit, _defn_unit)
        self._is_numeric = (
            self._expected_unit is not None
            or defn.get("state_class") in STATE_CLASS_MAP
            or defn.get("device_class") in (
                "voltage", "current", "power", "energy", "frequency",
                "temperature", "humidity", "pressure", "duration",
            )
        )

        if defn.get("device_class") in DEVICE_CLASS_MAP:
            self._attr_device_class = DEVICE_CLASS_MAP[defn["device_class"]]

        if defn.get("state_class") in STATE_CLASS_MAP:
            self._attr_state_class = STATE_CLASS_MAP[defn["state_class"]]

        if defn.get("category") in CATEGORY_MAP:
            self._attr_entity_category = CATEGORY_MAP[defn["category"]]

        if self._expected_unit:
            self._attr_native_unit_of_measurement = self._expected_unit

        self._attr_entity_registry_enabled_default = defn.get("enabled_default", True)
        self._last_written = _UNSET

    @callback
    def _handle_coordinator_update(self) -> None:
        """Write HA state only when the value or availability changes.

        A failed poll keeps the old data, so availability must be compared
        too or the entity would never show "unavailable".
        """
        new_val = (self.available, self.native_value)
        if self._last_written is not _UNSET and new_val == self._last_written:
            return
        self._last_written = new_val
        self.async_write_ha_state()

    @property
    def native_value(self) -> Any | None:
        raw = self._get_raw_value()
        if raw is None:
            return None

        if self._is_numeric:
            numeric, unit = self.extract_numeric(raw)
            if numeric is not None:
                # Set discovered unit if we didn't have one predefined
                if self._expected_unit is None and unit:
                    self._attr_native_unit_of_measurement = UNIT_FIXUP.get(unit, unit)
                return numeric
            # HA refuses text for a sensor with a unit or state class
            return None

        if raw == "" and self._path in self._EMPTY_DEFAULTS:
            return self._EMPTY_DEFAULTS[self._path]
        text = str(raw).strip()
        if not text or GENMON_UNKNOWN.fullmatch(text):
            return None
        return text

    # Paths where empty string from genmon means a known "clear" state
    _EMPTY_DEFAULTS = {
        "Status/Engine/System In Alarm": "No Active Alarms",
    }

    def _get_raw_value(self) -> Any | None:
        if self.coordinator.data is None:
            return None
        parts = self._path.split("/") if self._path else []
        data = self.coordinator.data
        for part in parts:
            if isinstance(data, dict):
                data = data.get(part)
            else:
                return None
            if data is None:
                return None
        return self._unwrap_num_json(data)
