"""The Genmon Generator Monitor integration."""
from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_time_interval

from . import controller_buttons as cb
from .const import DOMAIN, PLATFORMS
from .coordinator import GenmonCoordinator
from .entity import should_add_entity

_LOGGER = logging.getLogger(__name__)

type GenmonConfigEntry = ConfigEntry

ENTITY_REFRESH_INTERVAL = timedelta(seconds=60)
# An entity genhalink no longer lists is removed only after it has been
# missing from every entity list for this long. Right after genmon starts,
# genhalink leaves out sensors that have no value yet; removing those would
# lose the user's changes to them (and they never came back, issue #8).
STALE_AFTER = 30 * 60  # seconds

# entity domain -> the entity list key (and coordinator.register_platform)
PLATFORM_KEYS = {
    "sensor": "sensors",
    "binary_sensor": "binary_sensors",
    "button": "buttons",
    "switch": "switches",
    "select": "selects",
    "number": "numbers",
}


async def async_setup_entry(hass: HomeAssistant, entry: GenmonConfigEntry) -> bool:
    """Set up Genmon from a config entry."""
    coordinator = GenmonCoordinator(hass, entry)

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        # HA retries setup with a new coordinator (and client): close
        # this one's aiohttp session or every retry leaks one
        await coordinator.api.close()
        raise
    await coordinator.start_ws()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _refresh_entities(_now: Any) -> None:
        await async_refresh_entities(hass, entry, coordinator, time.monotonic())

    entry.async_on_unload(
        async_track_time_interval(hass, _refresh_entities, ENTITY_REFRESH_INTERVAL)
    )

    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    return True


def _build_valid_unique_ids(
    entry_id: str, entities: dict[str, Any], capabilities: dict[str, Any]
) -> set[tuple[str, str]]:
    """Build the set of (entity domain, unique_id) the API currently defines.

    The domain is part of it: a controller button with a number input is a
    number entity with the unique_id the button had, so the old button must
    still count as stale. The entity_key logic here must mirror each
    platform's __init__.
    """
    valid: set[tuple[str, str]] = set()
    controller_type = entities.get("controller_type")

    # Map platform keys → entity_key extraction (mirrors each platform class)
    # sensor.py: super().__init__(..., defn.get("entity_id", entity_key), ...)
    #   so entity_id wins over path for the unique_id in ALL platforms.
    key_builders: dict[str, tuple[str, Any]] = {
        "sensors": ("sensor", lambda d: d.get("entity_id", d.get("path", ""))),
        "binary_sensors": ("binary_sensor", lambda d: d.get("entity_id", d.get("path", ""))),
        "buttons": ("button", lambda d: d.get("entity_id", d.get("name", ""))),
        "switches": ("switch", lambda d: d.get("entity_id", d.get("name", ""))),
        "selects": ("select", lambda d: d.get("entity_id", d.get("name", ""))),
        "numbers": ("number", lambda d: d.get("entity_id", d.get("name", ""))),
    }

    for platform_key, (domain, get_key) in key_builders.items():
        for defn in entities.get(platform_key, []):
            if should_add_entity(defn, controller_type, capabilities):
                entity_key = get_key(defn)
                if entity_key:
                    valid.add((domain, f"{entry_id}_{entity_key}"))

    # Controller buttons (controller_buttons.py): buttons, numbers or both
    for btn in entities.get("buttons_from_controller", []):
        if not btn.get("onewordcommand"):
            continue
        key = f"{entry_id}_{cb.button_key(btn)}"
        kind = cb.classify(btn)
        if kind in (cb.BUTTON, cb.ACTION):
            valid.add(("button", key))
        if kind == cb.SETTING:
            valid.add(("number", key))
        if kind == cb.ACTION:
            valid.add(("number", key + "_input"))

    return valid


async def async_refresh_entities(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: GenmonCoordinator, now: float
) -> None:
    """Refresh the entity list (adds new entities), then remove stale ones.

    now: time.monotonic(). A refresh without an answer (genhalink restarting
    or unreachable) starts the missing time over: an outage isn't "missing".
    """
    if await coordinator.async_refresh_entities():
        _async_cleanup_stale_entities(hass, entry, coordinator, now)
    else:
        coordinator.missing_since.clear()


@callback
def _async_cleanup_stale_entities(
    hass: HomeAssistant, entry: ConfigEntry, coordinator: GenmonCoordinator, now: float
) -> None:
    """Remove registry entities the entity list has left out for STALE_AFTER."""
    if not coordinator.entities:
        return

    valid_ids = _build_valid_unique_ids(
        entry.entry_id, coordinator.entities, coordinator.capabilities
    )
    if not valid_ids:
        return  # Safety: don't nuke everything if definitions failed to load

    registry = er.async_get(hass)
    missing = coordinator.missing_since
    still_missing: set[tuple[str, str]] = set()
    prefix = f"{entry.entry_id}_"
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        key = (reg_entry.domain, reg_entry.unique_id)
        if key in valid_ids:
            continue
        since = missing.setdefault(key, now)
        if now - since < STALE_AFTER:
            still_missing.add(key)
            continue
        _LOGGER.info(
            "Removing stale entity %s (%s): not listed by genhalink for %d minutes",
            reg_entry.entity_id,
            reg_entry.unique_id,
            (now - since) // 60,
        )
        registry.async_remove(reg_entry.entity_id)
        platform_key = PLATFORM_KEYS.get(reg_entry.domain)
        if platform_key and reg_entry.unique_id.startswith(prefix):
            coordinator.forget_entity(platform_key, reg_entry.unique_id[len(prefix):])
    # listed again, removed, or deleted by the user: no longer missing
    for key in set(missing) - still_missing:
        del missing[key]


async def async_unload_entry(hass: HomeAssistant, entry: GenmonConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: GenmonCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.stop_ws()
    return unload_ok


async def _async_update_options(hass: HomeAssistant, entry: GenmonConfigEntry) -> None:
    """Handle options update — reload the integration."""
    await hass.config_entries.async_reload(entry.entry_id)
