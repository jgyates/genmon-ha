"""Exercise schedule: parsing genmon's schedule and the setexercise command."""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.number import ATTR_VALUE, SERVICE_SET_VALUE
from homeassistant.components.select import ATTR_OPTION, ATTR_OPTIONS, SERVICE_SELECT_OPTION
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.genmon.const import DOMAIN
from custom_components.genmon.exercise import (
    ExerciseSchedule,
    build_exercise_command,
    enhanced_exercise_mode,
    parse_exercise_time,
    schedule_from_state,
)

from .conftest import FakeGenmonApi
from .test_sensor import entity_id


# ---------------------------------------------------------------- parsing
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Weekly Tuesday 16:48 Quiet Mode Off", ExerciseSchedule("Weekly", "Tuesday", None, 16, 48)),
        ("Weekly  Monday 12:00", ExerciseSchedule("Weekly", "Monday", None, 12, 0)),
        ("Biweekly Monday 13:30 Quiet Mode On", ExerciseSchedule("BiWeekly", "Monday", None, 13, 30)),
        ("Monthly Day-15 09:00 Quiet Mode Off", ExerciseSchedule("Monthly", None, 15, 9, 0)),
        ("Monthly Day 15 09:05", ExerciseSchedule("Monthly", None, 15, 9, 5)),
        ("", ExerciseSchedule()),
        ("garbage", ExerciseSchedule()),
    ],
)
def test_parse_exercise_time(raw: str, expected: ExerciseSchedule) -> None:
    assert parse_exercise_time(raw) == expected


def test_schedule_prefers_gui_status() -> None:
    """gui_status/ExerciseInfo (structured) wins over the Exercise Time text."""
    state = {
        "gui_status": {"ExerciseInfo": {
            "Enabled": True, "Frequency": "Monthly", "Day": "07", "Hour": "08",
            "Minute": "30", "EnhancedExerciseMode": "True",
        }},
        "Maintenance": {"Exercise": {"Exercise Time": "Weekly Monday 12:00"}},
    }
    assert schedule_from_state(state) == ExerciseSchedule("Monthly", None, 7, 8, 30)
    assert enhanced_exercise_mode(state) is True


def test_schedule_falls_back_to_exercise_time() -> None:
    state = {"Maintenance": {"Exercise": {"Exercise Time": "Weekly Friday 06:15 Quiet Mode Off"}}}
    assert schedule_from_state(state) == ExerciseSchedule("Weekly", "Friday", None, 6, 15)
    # unknown without gui_status: genmon only reports it there
    assert enhanced_exercise_mode(state) is None
    assert schedule_from_state({}) is None


@pytest.mark.parametrize(("value", "expected"), [("True", True), ("False", False), (False, False)])
def test_enhanced_exercise_mode_values(value, expected) -> None:
    state = {"gui_status": {"ExerciseInfo": {"EnhancedExerciseMode": value}}}
    assert enhanced_exercise_mode(state) is expected


@pytest.mark.parametrize(
    ("schedule", "command"),
    [
        (ExerciseSchedule("Weekly", "Tuesday", None, 16, 48), "setexercise=Tuesday,16:48,Weekly"),
        (ExerciseSchedule("BiWeekly", "Monday", None, 7, 5), "setexercise=Monday,07:05,BiWeekly"),
        # genmon wants the day of the month first for monthly exercise
        (ExerciseSchedule("Monthly", None, 15, 9, 0), "setexercise=15,09:00,Monthly"),
        (ExerciseSchedule("Monthly", "Monday", None, 9, 0), "setexercise=1,09:00,Monthly"),
        (ExerciseSchedule("Weekly", None, 15, 9, 0), "setexercise=Monday,09:00,Weekly"),
    ],
)
def test_build_exercise_command(schedule: ExerciseSchedule, command: str) -> None:
    assert build_exercise_command(schedule) == command


@pytest.mark.parametrize(
    "schedule",
    [
        ExerciseSchedule(),
        ExerciseSchedule(None, "Tuesday", None, 16, 48),
        ExerciseSchedule("Weekly", "Tuesday", None, None, 48),
        ExerciseSchedule("Weekly", "Tuesday", None, 16, None),
    ],
)
def test_build_exercise_command_incomplete(schedule: ExerciseSchedule) -> None:
    """No defaults for the frequency or time: they would overwrite genmon's."""
    with pytest.raises(ValueError):
        build_exercise_command(schedule)


@pytest.mark.parametrize(
    ("schedule", "complete"),
    [
        (ExerciseSchedule("Weekly", "Tuesday", None, 16, 48), True),
        (ExerciseSchedule("Monthly", None, 15, 9, 0), True),
        (ExerciseSchedule("Weekly", None, None, 16, 48), False),
        (ExerciseSchedule("Monthly", "Monday", None, 9, 0), False),
        (ExerciseSchedule("Weekly", "Tuesday", None, None, None), False),
        (ExerciseSchedule(), False),
    ],
)
def test_schedule_complete(schedule: ExerciseSchedule, complete: bool) -> None:
    assert schedule.complete is complete


# ---------------------------------------------------------------- entities
def set_schedule(api: FakeGenmonApi, text: str, info: dict) -> None:
    """Make the fake genhalink report an exercise schedule."""
    api.state["Maintenance"]["Exercise"]["Exercise Time"] = text
    api.state["gui_status"]["ExerciseInfo"].update(info)


def make_enhanced(api: FakeGenmonApi) -> None:
    """List every exercise control, as genhalink before the EnhancedExerciseMode
    fix did (definitions from genmon's data/homeassistant/generac_evo_nexus.json)."""
    entities = api.entities
    entities["capabilities"]["EnhancedExerciseMode"] = True
    for select_def in entities["selects"]:
        if select_def["entity_id"] == "exercise_frequency":
            select_def["options"] = ["Weekly", "BiWeekly", "Monthly"]
    if not any(n["entity_id"] == "exercise_day_of_month" for n in entities["numbers"]):
        entities["numbers"].append({
            "command_template": "exercise", "entity_id": "exercise_day_of_month",
            "icon": "mdi:calendar-month", "max": 28, "min": 1,
            "name": "Exercise Day of Month", "step": 1,
        })


async def set_number(hass: HomeAssistant, eid: str, value: float) -> None:
    await hass.services.async_call(
        "number", SERVICE_SET_VALUE, {ATTR_ENTITY_ID: eid, ATTR_VALUE: value}, blocking=True
    )


async def select(hass: HomeAssistant, eid: str, option: str) -> None:
    await hass.services.async_call(
        "select", SERVICE_SELECT_OPTION, {ATTR_ENTITY_ID: eid, ATTR_OPTION: option}, blocking=True
    )


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_weekly_only_controller(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """Without enhanced exercise mode only weekly exercise is offered.

    The captured Evolution reports EnhancedExerciseMode "False"; genmon
    rejects BiWeekly and Monthly for it.
    """
    await setup(hass, config_entry)
    freq = entity_id(hass, config_entry, "select", "exercise_frequency")
    assert hass.states.get(freq).attributes[ATTR_OPTIONS] == ["Weekly"]
    assert hass.states.get(freq).state == "Weekly"
    assert not er.async_get(hass).async_get_entity_id(
        "number", DOMAIN, f"{config_entry.entry_id}_exercise_day_of_month"
    )

    # captured schedule: Weekly Tuesday 16:48
    await set_number(hass, entity_id(hass, config_entry, "number", "exercise_hour"), 10)
    assert mock_api.commands[-1] == "setexercise=Tuesday,10:48,Weekly"
    await select(hass, entity_id(hass, config_entry, "select", "exercise_day_of_week"), "Friday")
    assert mock_api.commands[-1] == "setexercise=Friday,16:48,Weekly"


async def test_weekly_only_with_older_genhalink(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """Older genhalink lists every exercise control (it always reported
    EnhancedExerciseMode False and didn't filter on it); gui_status decides."""
    make_enhanced(mock_api)
    mock_api.entities["capabilities"]["EnhancedExerciseMode"] = False
    await setup(hass, config_entry)
    freq = entity_id(hass, config_entry, "select", "exercise_frequency")
    assert hass.states.get(freq).attributes[ATTR_OPTIONS] == ["Weekly"]
    assert not er.async_get(hass).async_get_entity_id(
        "number", DOMAIN, f"{config_entry.entry_id}_exercise_day_of_month"
    )


@pytest.mark.parametrize("capture", ["evo2_ac"])
async def test_monthly_schedule(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """A monthly schedule is read and every change keeps it monthly.

    The captured Evolution 2.0 supports enhanced exercise; its schedule is
    changed to monthly here.
    """
    set_schedule(mock_api, "Monthly Day-15 09:00 Quiet Mode Off", {
        "Frequency": "Monthly", "Day": "15", "Hour": "09", "Minute": "00",
    })
    await setup(hass, config_entry)
    freq = entity_id(hass, config_entry, "select", "exercise_frequency")
    dom = entity_id(hass, config_entry, "number", "exercise_day_of_month")
    dow = entity_id(hass, config_entry, "select", "exercise_day_of_week")

    assert hass.states.get(freq).attributes[ATTR_OPTIONS] == ["Weekly", "BiWeekly", "Monthly"]
    assert hass.states.get(freq).state == "Monthly"
    assert float(hass.states.get(dom).state) == 15
    assert hass.states.get(dow).state == STATE_UNKNOWN

    await set_number(hass, entity_id(hass, config_entry, "number", "exercise_hour"), 10)
    assert mock_api.commands[-1] == "setexercise=15,10:00,Monthly"
    await set_number(hass, dom, 20)
    assert mock_api.commands[-1] == "setexercise=20,09:00,Monthly"
    await select(hass, freq, "Weekly")
    assert mock_api.commands[-1] == "setexercise=Monday,09:00,Weekly"


@pytest.mark.parametrize("capture", ["evo2_ac"])
async def test_switch_weekly_to_monthly(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_api: FakeGenmonApi
) -> None:
    """Choosing Monthly sends a day of the month; the day of the month is
    only settable while the schedule is monthly (captured: Weekly Saturday 11:00)."""
    await setup(hass, config_entry)
    freq = entity_id(hass, config_entry, "select", "exercise_frequency")
    assert hass.states.get(freq).attributes[ATTR_OPTIONS] == ["Weekly", "BiWeekly", "Monthly"]
    assert hass.states.get(freq).state == "Weekly"

    with pytest.raises(ServiceValidationError):
        await set_number(hass, entity_id(hass, config_entry, "number", "exercise_day_of_month"), 5)
    assert mock_api.commands == []

    await select(hass, freq, "Monthly")
    assert mock_api.commands[-1] == "setexercise=1,11:00,Monthly"
    await select(hass, freq, "BiWeekly")
    assert mock_api.commands[-1] == "setexercise=Saturday,11:00,BiWeekly"


@pytest.mark.parametrize(
    ("exercise_time", "info"),
    [
        # no schedule reported at all
        ("", {"Frequency": "", "Day": "", "Hour": "", "Minute": ""}),
        # a schedule without the time
        ("Weekly Tuesday", {"Frequency": "Weekly", "Day": "Tuesday", "Hour": "", "Minute": ""}),
    ],
)
async def test_unknown_schedule_not_changed(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_api: FakeGenmonApi,
    exercise_time: str,
    info: dict,
) -> None:
    """Without the complete schedule no setexercise is sent: it would
    overwrite the generator's schedule with made-up parts."""
    set_schedule(mock_api, exercise_time, info)
    await setup(hass, config_entry)

    with pytest.raises(HomeAssistantError, match="complete exercise schedule"):
        await set_number(hass, entity_id(hass, config_entry, "number", "exercise_hour"), 10)
    with pytest.raises(HomeAssistantError, match="complete exercise schedule"):
        await select(hass, entity_id(hass, config_entry, "select", "exercise_day_of_week"), "Friday")
    with pytest.raises(HomeAssistantError, match="complete exercise schedule"):
        await select(hass, entity_id(hass, config_entry, "select", "exercise_frequency"), "Weekly")
    assert mock_api.commands == []
