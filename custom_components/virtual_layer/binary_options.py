"""Shared validation for binary-sensor detection settings."""

import math

import voluptuous as vol


def detection_minutes(value):
    """Accept whole minutes without truncation or boolean coercion."""
    if isinstance(value, bool):
        raise vol.Invalid("hold time must be whole minutes")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as err:
        raise vol.Invalid("hold time must be whole minutes") from err
    if not math.isfinite(number) or not number.is_integer() or not 0 <= number <= 1440:
        raise vol.Invalid("hold time must be whole minutes between 0 and 1440")
    return int(number)
