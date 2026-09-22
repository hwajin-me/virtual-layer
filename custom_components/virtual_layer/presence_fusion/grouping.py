"""Pair observation evidence, not timer ticks; deterministic complete link."""

from dataclasses import dataclass
from itertools import combinations

from .movement import meters


@dataclass
class Pair:
    first: float
    last: float
    count: int
    stamp: tuple
    separated: bool = False
    interrupted: bool = False
    confirmed_at: float | None = None
    comparable: bool = True


class Relations:
    def __init__(self, settings):
        self.s = settings
        self.pairs = {}

    def update(self, paths, now, mono):
        for a, b in combinations(sorted(paths), 2):
            pa, pb = paths[a], paths[b]
            x, y = pa.latest, pb.latest
            key = (a, b)
            old = self.pairs.get(key)
            if (
                not (pa.fresh(now) and pb.fresh(now))
                or max(now - x.observed, now - y.observed) > self.s.pair_max_age_s
                or abs(x.observed - y.observed) > self.s.pair_max_skew_s
                or x.assumed
                or y.assumed
            ):
                # Recent confirmed companionship may authorize bounded failover;
                # an interrupted close run must not authorize reunion.
                if old:
                    old.comparable = False
                    # Different arrival order temporarily skews the pair. A
                    # next packet within the evidence horizon may complete it.
                    # Actual outages/poor fixes or expired runs break continuity.
                    if (
                        not (pa.fresh(now) and pb.fresh(now))
                        or x.assumed
                        or y.assumed
                        or mono - old.last > self.s.pair_max_age_s
                    ):
                        old.interrupted = True
                continue
            stamp = (x.observed, y.observed)
            d = meters(x, y)
            if d - x.accuracy - y.accuracy >= self.s.separation_radius_m:
                self.pairs[key] = Pair(mono, mono, 0, stamp, True)
            elif d + x.accuracy + y.accuracy <= self.s.together_radius_m:
                if (
                    old is None
                    or old.separated
                    or old.interrupted
                    or mono - old.last > self.s.pair_max_age_s
                ):
                    self.pairs[key] = Pair(
                        mono,
                        mono,
                        1,
                        stamp,
                        confirmed_at=old.confirmed_at
                        if old and not old.separated
                        else None,
                    )
                elif min(stamp) > min(old.stamp):
                    old.last, old.stamp = mono, stamp
                    old.count = min(3, old.count + 1)
                current = self.pairs[key]
                current.comparable = True
                if (
                    current.count >= 3
                    and current.last - current.first >= self.s.group_confirm_s
                ):
                    current.confirmed_at = current.last
            elif old:
                old.interrupted = True

    def pair(self, a, b):
        return self.pairs.get(tuple(sorted((a, b))))

    def close(self, a, b, mono, hold=None):
        p = self.pair(a, b)
        return bool(
            p
            and not p.separated
            and not p.interrupted
            and p.comparable
            and p.count >= 3
            and mono - p.last <= self.s.pair_max_age_s
            and p.last - p.first >= (self.s.group_confirm_s if hold is None else hold)
        )

    def groups(self, ids, mono):
        groups = []
        for device in sorted(ids):
            for group in groups:
                if all(self.close(device, member, mono) for member in group):
                    group.add(device)
                    break
            else:
                groups.append({device})
        return groups
