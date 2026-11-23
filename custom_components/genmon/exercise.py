"""Exercise schedule, shared by the exercise number and select entities.

genmon reports the schedule in two places of the genhalink state:

    gui_status/ExerciseInfo
        {"Frequency": "Weekly", "Day": "Tuesday", "Hour": "16", "Minute": "48",
         "EnhancedExerciseMode": "False", ...}
        For monthly exercise "Day" is the day of the month ("15").
    Maintenance/Exercise/Exercise Time
        "Weekly Tuesday 16:48 Quiet Mode Off"
        "Biweekly Monday 13:30 Quiet Mode On"
        "Monthly Day-15 09:00 Quiet Mode Off"

and takes a new schedule as "setexercise=<day>,<HH:MM>,<frequency>", where
<day> is a weekday name, or the day of the month for Monthly (genmon
generac_evolution.py SetGeneratorExerciseTime). BiWeekly and Monthly need
EnhancedExerciseMode (reported only in gui_status/ExerciseInfo).
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

WEEKLY = "Weekly"
MONTHLY = "Monthly"
# genmon's spellings (lower case) -> the select options' spellings
FREQUENCIES = {"weekly": WEEKLY, "biweekly": "BiWeekly", "monthly": MONTHLY}
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# used for parts of the schedule genmon has not reported
DEFAULT_DAY_OF_WEEK = "Monday"
DEFAULT_DAY_OF_MONTH = 1
DEFAULT_HOUR = 12
DEFAULT_MINUTE = 0

_TIME = re.compile(r"(\d{1,2}):(\d{2})")
_QUIET_MODE = re.compile(r"Quiet Mode (On|Off)\b", re.IGNORECASE)
_DAY_OF_MONTH = re.compile(r"Day-?(\d{1,2})", re.IGNORECASE)


@dataclass(frozen=True)
class ExerciseSchedule:
    """An exercise schedule; None for parts that are unknown or don't apply."""

    frequency: str | None = None
    day_of_week: str | None = None
    day_of_month: int | None = None
    hour: int | None = None
    minute: int | None = None


def _frequency(raw: Any) -> str | None:
    return FREQUENCIES.get(str(raw).strip().lower()) if raw is not None else None


def _weekday(raw: Any) -> str | None:
    text = str(raw).strip().lower()
    return next((day for day in WEEKDAYS if day.lower() == text), None)


def _int(raw: Any) -> int | None:
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


def parse_exercise_time(raw: Any) -> ExerciseSchedule:
    """Parse genmon's "Exercise Time" text."""
    parts = raw.split() if isinstance(raw, str) else []
    if len(parts) < 3 or (frequency := _frequency(parts[0])) is None:
        return ExerciseSchedule()

    day_of_week = day_of_month = None
    rest = parts[1:]
    if frequency == MONTHLY:
        if match := _DAY_OF_MONTH.fullmatch(rest[0]):
            day_of_month, rest = int(match.group(1)), rest[1:]
        elif rest[0].lower() == "day":  # "Monthly Day 15 09:00"
            day_of_month, rest = _int(rest[1]), rest[2:]
    else:
        day_of_week, rest = _weekday(rest[0]), rest[1:]

    hour = minute = None
    if rest and (match := _TIME.fullmatch(rest[0])):
        hour, minute = int(match.group(1)), int(match.group(2))
    return ExerciseSchedule(frequency, day_of_week, day_of_month, hour, minute)


def _exercise_info(state: Any) -> dict[str, Any] | None:
    """gui_status/ExerciseInfo from the genhalink state, if present."""
    if not isinstance(state, dict):
        return None
    gui = state.get("gui_status")
    info = gui.get("ExerciseInfo") if isinstance(gui, dict) else None
    return info if isinstance(info, dict) else None


def schedule_from_state(state: Any) -> ExerciseSchedule | None:
    """The current schedule, or None if genmon doesn't report one."""
    info = _exercise_info(state)
    if info and info.get("Enabled", True) and (frequency := _frequency(info.get("Frequency"))):
        monthly = frequency == MONTHLY
        day = info.get("Day")
        return ExerciseSchedule(
            frequency,
            day_of_week=None if monthly else _weekday(day),
            day_of_month=_int(day) if monthly else None,
            hour=_int(info.get("Hour")),
            minute=_int(info.get("Minute")),
        )

    maintenance = state.get("Maintenance") if isinstance(state, dict) else None
    exercise = maintenance.get("Exercise") if isinstance(maintenance, dict) else None
    if isinstance(exercise, dict):
        schedule = parse_exercise_time(exercise.get("Exercise Time"))
        if schedule.frequency is not None:
            return schedule
    return None


def quiet_mode_from_state(state: Any) -> str | None:
    """genmon's quiet mode setting, "On" or "Off"; None if not reported.

    genmon reports it in gui_status/ExerciseInfo/QuietMode and at the end of
    the "Exercise Time" text, not as a value of its own (older genmon
    entity definitions read it from a Maintenance path that never exists).
    """
    info = _exercise_info(state)
    if info is not None and "QuietMode" in info:
        value = str(info["QuietMode"]).strip().capitalize()
        return value if value in ("On", "Off") else None

    maintenance = state.get("Maintenance") if isinstance(state, dict) else None
    exercise = maintenance.get("Exercise") if isinstance(maintenance, dict) else None
    text = exercise.get("Exercise Time") if isinstance(exercise, dict) else None
    if isinstance(text, str) and (match := _QUIET_MODE.search(text)):
        return match.group(1).capitalize()
    return None


def enhanced_exercise_mode(state: Any) -> bool | None:
    """True if the controller supports BiWeekly and Monthly exercise.

    None when genmon doesn't report it (gui_status missing from the state).
    """
    info = _exercise_info(state)
    if info is None or "EnhancedExerciseMode" not in info:
        return None
    return str(info["EnhancedExerciseMode"]).strip().lower() in ("true", "on", "enabled", "yes")


def build_exercise_command(schedule: ExerciseSchedule) -> str:
    """The genmon command that sets this schedule."""
    frequency = schedule.frequency or WEEKLY
    hour = DEFAULT_HOUR if schedule.hour is None else schedule.hour
    minute = DEFAULT_MINUTE if schedule.minute is None else schedule.minute
    if frequency == MONTHLY:
        day = str(schedule.day_of_month or DEFAULT_DAY_OF_MONTH)
    else:
        day = schedule.day_of_week or DEFAULT_DAY_OF_WEEK
    return f"setexercise={day},{hour:02d}:{minute:02d},{frequency}"
