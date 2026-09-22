"""Bounded UTC buckets, accepted anchors, and isolated reacquisition."""

from collections import deque
from math import asin, cos, isfinite, radians, sin, sqrt

from .models import GPS, Settings


def distance(a, b):
    lat = radians(b[0] - a[0])
    lon = radians(b[1] - a[1])
    h = sin(lat / 2) ** 2 + cos(radians(a[0])) * cos(radians(b[0])) * sin(lon / 2) ** 2
    return 12742000 * asin(sqrt(min(1, max(0, h))))


def meters(a, b):
    return distance((a.latitude, a.longitude), (b.latitude, b.longitude))


class Path:
    def __init__(self, settings: Settings):
        self.s = settings
        self.latest = None
        self.available = True
        self.rejection = None
        self.reacquire = None
        self.bucket = None
        self.closed_bucket = float("-inf")
        self.anchor = None
        self.previous_bucket = None
        self.segments = deque(maxlen=4096)
        self.samples = deque(maxlen=4097)
        self.generation = 0

    def fresh(self, now):
        p = self.latest
        return bool(
            self.available
            and p
            and now - p.observed < self.s.gps_stale_after_s
            and p.accuracy <= self.s.gps_max_accuracy_m
        )

    def _speed_ok(self, a, b):
        return (
            b.observed > a.observed
            and max(0, meters(a, b) - a.accuracy - b.accuracy)
            / (b.observed - a.observed)
            <= self.s.max_speed_m_s
        )

    def add(self, p: GPS, now):
        self.rejection = None
        values = (p.latitude, p.longitude, p.accuracy, p.observed, p.received)
        if (
            any(
                isinstance(v, bool)
                or not isinstance(v, (int, float))
                or not isfinite(v)
                for v in values
            )
            or not (-90 <= p.latitude <= 90 and -180 <= p.longitude <= 180)
            or p.accuracy <= 0
        ):
            self.rejection = "invalid"
        elif p.observed > now + self.s.gps_future_tolerance_s:
            self.rejection = "future"
        elif p.accuracy > self.s.gps_max_accuracy_m:
            # A poor fix must neither become a path/speed anchor nor authorize
            # a subsequent jump through its inflated uncertainty allowance.
            self.rejection = "accuracy"
            self.available = False
        elif self.latest and p.observed <= self.latest.observed:
            self.rejection = (
                "out_of_order"
                if p.observed < self.latest.observed
                else "duplicate"
                if (p.latitude, p.longitude, p.accuracy)
                == (self.latest.latitude, self.latest.longitude, self.latest.accuracy)
                else "timestamp_conflict"
            )
        if self.rejection:
            if self.rejection == "duplicate":
                # A registry rename/temporary outage may hide the source.
                # Its identical valid measured fix restores availability only;
                # the original observation time and history remain unchanged.
                self.available = True
            return False
        if self.reacquire and p.observed <= self.reacquire[1].observed:
            previous = self.reacquire[1]
            self.rejection = (
                "out_of_order"
                if p.observed < previous.observed
                else "duplicate"
                if (p.latitude, p.longitude, p.accuracy)
                == (previous.latitude, previous.longitude, previous.accuracy)
                else "timestamp_conflict"
            )
            return False
        if (
            self.latest
            and p.observed - self.latest.observed > self.s.max_observation_gap_s
        ):
            q = self.reacquire
            if (
                q is None
                or p.observed - q[1].observed > self.s.max_observation_gap_s
                or not self._speed_ok(q[1], p)
            ):
                self.reacquire = (p, p)
            elif p.observed - q[0].observed >= 30:
                self.reacquire = None
                self.anchor = self.previous_bucket = self.bucket = None
                self.samples.clear()
                self.generation += 1
            else:
                self.reacquire = (q[0], p)
            if self.reacquire:
                self.rejection = "reacquiring"
                return False
        elif self.latest and not self._speed_ok(self.latest, p):
            self.rejection = "jump"
            return False
        self.latest = p
        self.available = True
        self.reacquire = None
        self.advance(now)
        index = int(p.observed // self.s.movement_bucket_s)
        if (
            index > self.closed_bucket
            and (index + 1) * self.s.movement_bucket_s > now
            and (
                self.bucket is None
                or index != self.bucket[0]
                or (p.accuracy, -p.observed)
                < (
                    self.bucket[1].accuracy,
                    -self.bucket[1].observed,
                )
            )
        ):
            self.bucket = (index, p)
        return True

    def advance(self, now):
        if self.bucket and (self.bucket[0] + 1) * self.s.movement_bucket_s <= now:
            index, p = self.bucket
            self.closed_bucket = index
            self.bucket = None
            self.samples.append(p)
            if self.anchor is None or self.previous_bucket is None:
                self.anchor = p
            else:
                d = meters(self.anchor, p)
                if d > max(10, self.anchor.accuracy + p.accuracy):
                    self.segments.append((self.anchor.observed, p.observed, d))
                    self.anchor = p
                elif p.observed - self.anchor.observed > self.s.movement_window_s:
                    self.anchor = p
            self.previous_bucket = p
        cutoff = now - self.s.movement_window_s
        while self.segments and self.segments[0][1] <= cutoff:
            self.segments.popleft()
        sample_cutoff = now - max(self.s.movement_window_s, self.s.direction_window_s)
        while len(self.samples) > 1 and self.samples[1].observed < sample_cutoff:
            self.samples.popleft()

    def movement(self, now, since=float("-inf")):
        cutoff = max(now - self.s.movement_window_s, since)
        parts = [
            d * (end - max(start, cutoff)) / (end - start)
            for start, end, d in self.segments
            if end > cutoff and end > start >= since
        ]
        return sum(parts), len(parts)

    def score(self, now, since=float("-inf")):
        if not self.fresh(now):
            return 0
        age = now - self.latest.observed
        f = min(
            1,
            max(
                0,
                (self.s.gps_stale_after_s - age)
                / (self.s.gps_stale_after_s - self.s.gps_fresh_full_s),
            ),
        )
        return (
            self.movement(now, since)[0]
            * min(1, max(0.1, 25 / max(25, self.latest.accuracy)))
            * f
        )

    def displacement(self, now):
        """Observed endpoint displacement, distinct from accumulated path."""
        points = [
            p for p in self.samples if p.observed >= now - self.s.movement_window_s
        ]
        return meters(points[0], points[-1]) if len(points) >= 2 else 0.0

    def direction(self, now, home, since):
        ps = [
            p
            for p in self.samples
            if p.observed >= max(since, now - self.s.direction_window_s)
        ]
        if (
            len(ps) < 3
            or ps[-1].observed - ps[0].observed < self.s.direction_min_span_s
        ):
            return None
        # Raw accepted-observation gaps reset samples during reacquisition.
        # Bucket spacing itself is not a source observation gap.
        ds = [distance((p.latitude, p.longitude), home[:2]) for p in ps]
        ts = [p.observed - ps[0].observed for p in ps]
        mt, md = sum(ts) / len(ts), sum(ds) / len(ds)
        slope = sum((t - mt) * (d - md) for t, d in zip(ts, ds)) / sum(
            (t - mt) ** 2 for t in ts
        )
        threshold = max(self.s.direction_min_change_m, ps[0].accuracy + ps[-1].accuracy)
        if abs(ds[-1] - ds[0]) <= threshold:
            return "stationary"
        if slope < -self.s.direction_slope_m_s:
            return "towards"
        if slope > self.s.direction_slope_m_s:
            return "away_from"
        return None
