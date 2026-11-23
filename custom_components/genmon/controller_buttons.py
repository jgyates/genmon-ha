"""Controller buttons: genmon's "buttons" (StartInfo), sent with set_button_command.

A button is a command_sequence of register writes. A step with an
"input_title" takes a whole number from the user (genmon supports only
"type": "int"), checked by genmon against the step's "bounds_regex"
(genmonlib controller.py ExecuteRemoteCommand). In Home Assistant:

    BUTTON   no input, or only a passcode input (filled with the
             button_passcode option): a button
    SETTING  one number input and nothing else, e.g. Exercise Duration:
             a number; setting it writes it to the controller
    ACTION   one number input plus fixed steps, e.g. a remote start with a
             run time: a button, and a number for its input that is only
             kept in Home Assistant until the button is pressed

Other buttons (more than one number input) aren't supported.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

BUTTON = "button"
SETTING = "setting"
ACTION = "action"
UNSUPPORTED = "unsupported"

PASSCODE_TITLES = ("passcode", "password")

# input_title -> unit of measurement
UNITS = {"minutes": "min", "seconds": "s", "volts": "V", "hours": "h"}

# the largest value of an input of each "length" (in registers; genmon
# writes "%04x" for 2, "%08x" for 4); ranges are searched up to this
MAX_BY_LENGTH = {2: 0xFFFF, 4: 99999}


def button_key(button: dict[str, Any]) -> str:
    """The entity key (unique_id suffix) of a controller button."""
    return "ctrl_" + str(button.get("onewordcommand", "")).lower().replace(" ", "_")


def is_input(step: dict[str, Any]) -> bool:
    return "input_title" in step


def is_passcode(step: dict[str, Any]) -> bool:
    return is_input(step) and str(step["input_title"]).strip().lower() in PASSCODE_TITLES


def number_inputs(button: dict[str, Any]) -> list[dict[str, Any]]:
    """The inputs that take a value from the user (not the passcode)."""
    return [
        step for step in button.get("command_sequence", [])
        if is_input(step) and not is_passcode(step)
    ]


def classify(button: dict[str, Any]) -> str:
    """BUTTON, SETTING, ACTION or UNSUPPORTED (see the module docstring)."""
    inputs = number_inputs(button)
    if not inputs:
        return BUTTON
    if len(inputs) > 1 or inputs[0].get("type", "int") != "int":
        return UNSUPPORTED
    if all(is_input(step) for step in button.get("command_sequence", [])):
        return SETTING
    return ACTION


def input_range(step: dict[str, Any]) -> tuple[int, int]:
    """The smallest and largest whole numbers genmon accepts for an input."""
    largest = MAX_BY_LENGTH.get(step.get("length", 2), 0xFFFF)
    regex = step.get("bounds_regex")
    if not regex:
        return 0, largest
    try:
        pattern = re.compile(regex)
    except re.error:
        return 0, largest
    accepted = [n for n in range(largest + 1) if pattern.fullmatch(str(n))]
    if not accepted:
        return 0, largest
    return accepted[0], accepted[-1]


def value_accepted(step: dict[str, Any], value: int) -> bool:
    """True if genmon's bounds check accepts value (some ranges have gaps)."""
    regex = step.get("bounds_regex")
    if not regex:
        return True
    try:
        return re.fullmatch(regex, str(value)) is not None
    except re.error:
        return True


def unit(step: dict[str, Any]) -> str | None:
    return UNITS.get(str(step.get("input_title", "")).strip().lower())


def build_command(button: dict[str, Any], passcode: str, value: int | None = None) -> str:
    """The set_button_command for this button.

    Passcode inputs get the passcode, the number input gets value.
    """
    sequence = copy.deepcopy(button.get("command_sequence", []))
    for step in sequence:
        if not is_input(step):
            continue
        if is_passcode(step):
            step["value"] = passcode
        elif value is not None:
            step["value"] = str(int(value))
    payload = [{"onewordcommand": button.get("onewordcommand", ""), "command_sequence": sequence}]
    return "set_button_command=" + json.dumps(payload)
