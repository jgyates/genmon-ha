"""Entities genhalink stops listing: removed only after STALE_AFTER of
continuous absence, and added again if they come back (issue #8)."""
from __future__ import annotations

import copy

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.genmon import STALE_AFTER, async_refresh_entities
from custom_components.genmon.const import DOMAIN

from .conftest import FakeGenmonApi, GenmonConnectionError

AMBIENT = "ambient_temp"
MINUTE = 60


@pytest.fixture
def capture() -> str:
    """Evolution 2.0: the controller of issue #8 (Ambient Temp went missing)."""
    return "evo2_ac"


def ambient(hass: HomeAssistant, entry: MockConfigEntry) -> str | None:
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{AMBIENT}")


class Genhalink:
    """Switches the fake genhalink between listing Ambient Temp or not."""

    def __init__(self, api: FakeGenmonApi) -> None:
        self.api = api
        self.full = (api.entities, api.status)
        entities = copy.deepcopy(api.entities)
        entities["sensors"] = [d for d in entities["sensors"] if d["entity_id"] != AMBIENT]
        status = copy.deepcopy(api.status)
        status["state"]["Maintenance"].pop("Ambient Temperature Sensor")
        self.without_ambient = (entities, status)

    def list_ambient(self, listed: bool) -> None:
        # genhalink leaves out sensors without a value (e.g. right after genmon starts)
        self.api.entities, self.api.status = self.full if listed else self.without_ambient


async def refresh(hass: HomeAssistant, entry: MockConfigEntry, now: float) -> None:
    """One 60 s timer run (entity list + stale check) and a poll."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await async_refresh_entities(hass, entry, coordinator, now)
    await coordinator.async_refresh()
    await hass.async_block_till_done()


async def test_genmon_restart_soon_after_setup(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """Issue #8: genmon restarts while Home Assistant starts; Ambient Temp is
    left out for a few minutes and must not be lost."""
    genhalink = Genhalink(mock_api)
    eid = ambient(hass, init_integration)
    assert eid

    genhalink.list_ambient(False)
    for minute in range(0, 15):
        await refresh(hass, init_integration, minute * MINUTE)
    assert ambient(hass, init_integration) == eid

    genhalink.list_ambient(True)
    await refresh(hass, init_integration, 15 * MINUTE)
    assert hass.states.get(eid).state != STATE_UNAVAILABLE
    # and the missing time starts over
    genhalink.list_ambient(False)
    await refresh(hass, init_integration, 40 * MINUTE)
    assert ambient(hass, init_integration) == eid


async def test_removed_after_missing_for_stale_after(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """An entity genhalink really stopped listing is removed, and added again
    if it comes back later."""
    genhalink = Genhalink(mock_api)
    genhalink.list_ambient(False)
    await refresh(hass, init_integration, 0)
    await refresh(hass, init_integration, STALE_AFTER - MINUTE)
    assert ambient(hass, init_integration)
    await refresh(hass, init_integration, STALE_AFTER)
    assert ambient(hass, init_integration) is None

    genhalink.list_ambient(True)
    await refresh(hass, init_integration, STALE_AFTER + MINUTE)
    eid = ambient(hass, init_integration)
    assert eid, "added again (before the fix it stayed gone until a reload)"
    assert hass.states.get(eid).state != STATE_UNAVAILABLE


async def test_outage_starts_the_missing_time_over(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """While genhalink can't be reached nothing counts as missing."""
    genhalink = Genhalink(mock_api)
    genhalink.list_ambient(False)
    await refresh(hass, init_integration, 0)

    mock_api.error = GenmonConnectionError("genhalink restarting")
    await refresh(hass, init_integration, 10 * MINUTE)
    mock_api.error = None

    # back after the outage, still without Ambient Temp: the 30 minutes
    # start now, not at minute 0
    await refresh(hass, init_integration, STALE_AFTER + MINUTE)
    assert ambient(hass, init_integration)
    await refresh(hass, init_integration, 2 * STALE_AFTER + MINUTE)
    assert ambient(hass, init_integration) is None
