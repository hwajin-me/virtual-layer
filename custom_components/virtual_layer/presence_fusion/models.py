"""HA-independent values. UTC epoch seconds and monotonic time are explicit."""

from dataclasses import dataclass, fields
from math import isfinite


@dataclass(frozen=True)
class Settings:
    movement_window_s: float = 600
    movement_bucket_s: float = 30
    minimum_movement_m: float = 100
    minimum_movement_segments: int = 2
    gps_stale_after_s: float = 300
    gps_fresh_full_s: float = 60
    gps_max_accuracy_m: float = 100
    gps_missing_accuracy_m: float = 100
    gps_future_tolerance_s: float = 30
    max_observation_gap_s: float = 180
    max_speed_m_s: float = 100
    pair_max_skew_s: float = 60
    pair_max_age_s: float = 120
    together_radius_m: float = 100
    separation_radius_m: float = 150
    group_confirm_s: float = 60
    challenger_ratio: float = 1.3
    challenger_margin: float = 50
    challenger_hold_s: float = 45
    minimum_primary_hold_s: float = 120
    reunion_hold_s: float = 120
    safe_companion_age_s: float = 120
    home_enter_hold_s: float = 5
    gps_home_enter_hold_s: float = 60
    home_exit_hold_s: float = 60
    home_exit_margin_m: float = 50
    nearby_enter_m: float = 1000
    nearby_exit_m: float = 1200
    direction_window_s: float = 180
    direction_min_span_s: float = 60
    direction_min_change_m: float = 50
    direction_slope_m_s: float = 0.3
    room_change_hold_s: float = 8
    room_missing_hold_s: float = 30
    evidence_hold_s: float = 300
    manual_override_default_s: float = 3600

    def errors(self, radius=100):
        errors = {
            f.name: "positive_number"
            for f in fields(self)
            if isinstance(getattr(self, f.name), bool)
            or not isinstance(getattr(self, f.name), (float, int))
            or not isfinite(getattr(self, f.name))
            or not 0 < getattr(self, f.name) <= 1000000
        }
        if errors:
            return errors
        for lower, upper in [
            ("gps_fresh_full_s", "gps_stale_after_s"),
            ("nearby_enter_m", "nearby_exit_m"),
            ("together_radius_m", "separation_radius_m"),
            ("movement_bucket_s", "movement_window_s"),
        ]:
            if getattr(self, lower) >= getattr(self, upper):
                errors[upper] = "invalid_order"
        if self.nearby_enter_m <= radius:
            errors["nearby_enter_m"] = "home_radius"
        if self.minimum_movement_segments != int(self.minimum_movement_segments):
            errors["minimum_movement_segments"] = "integer_required"
        if not 1 <= self.manual_override_default_s <= 86400:
            errors["manual_override_default_s"] = "duration"
        # Bound memory/CPU independently of the number of incoming HA events.
        if (
            max(self.movement_window_s, self.direction_window_s)
            / self.movement_bucket_s
            > 4096
        ):
            errors["movement_bucket_s"] = "too_many_buckets"
        return errors


@dataclass(frozen=True)
class Device:
    id: str
    name: str
    priority: int
    candidate: bool = True


@dataclass(frozen=True)
class GPS:
    latitude: float
    longitude: float
    accuracy: float
    observed: float
    received: float
    assumed: bool = False
    basis: str = "measurement"
    source: str = ""
    tracked_device_id: str = ""
    validity: str = "valid"
    rejection_reason: str | None = None


@dataclass(frozen=True)
class Snapshot:
    primary: str | None
    mode: str
    health: str
    presence: str | None
    gps: tuple[float, float, float] | None
    room: str | None
    room_source: str | None
    distance: float | None
    direction: str | None
    reason: str
    held: bool
    missing_since: float | None
    room_conflict: bool = False
    zone: str | None = None
    zone_id: str | None = None
    zone_error: bool = False
    map_revision: int = 0
