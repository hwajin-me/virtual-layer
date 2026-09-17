"""Opt-in room-feedback control for one boiler; all temperatures are Celsius."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

ENABLED = "boiler_dynamic_control"
FORMULA = "boiler_dynamic_template"
DEFAULT_FORMULA = (
    "{{ base_water_temperature + "
    "([8, [0, (temperature - room_temperature) * 1.5] | max] | min) "
    "- ([8, [0, room_temperature_rate * 20] | max] | min) "
    "- ([5, heat_accumulation / 120] | min) }}"
)
INTERVAL = 60
MIN_COMMAND_INTERVAL = 120


def finite(value):
    """Accept finite numeric values, never booleans or missing readings."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


@dataclass
class ThermalHistory:
    """Decaying temperature-time proxy, not a measurement of stored energy."""

    accumulation: float = 0
    timestamp: float | None = None
    heating_minutes: float = 0
    was_heating: bool = False
    samples: deque = field(default_factory=deque)

    def observe(self, timestamp, room, water, heating):
        elapsed = max(0, timestamp - self.timestamp) if self.timestamp is not None else 0
        decay = math.exp(-elapsed / 1800)
        self.accumulation *= decay
        continuous = self.timestamp is not None and 0 < elapsed <= 120
        observed_heating = heating and room is not None and water is not None
        if continuous and observed_heating and self.was_heating:
            self.heating_minutes += elapsed / 60
            if room is not None and water is not None:
                gain = finite(max(0, water - room) * (30 * (1 - decay)))
                if gain is not None:
                    self.accumulation = min(3000, self.accumulation + gain)
        else:
            self.heating_minutes = 0
        if not continuous or room is None:
            self.samples.clear()
        if room is not None:
            self.samples.append((timestamp, room))
        while self.samples and timestamp - self.samples[0][0] > 600:
            self.samples.popleft()
        rate = 0
        if len(self.samples) >= 2:
            duration = timestamp - self.samples[0][0]
            if duration >= 60:
                rate = finite((room - self.samples[0][1]) * (60 / duration)) or 0
        self.timestamp = timestamp
        self.was_heating = observed_heating
        return {
            "room_temperature_rate": rate,
            "heating_elapsed_minutes": self.heating_minutes,
            "heat_accumulation": self.accumulation,
        }
