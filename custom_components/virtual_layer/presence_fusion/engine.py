"""Deterministic arbitration and presence; no HA objects or system clock."""

from collections import deque
from math import isfinite

from .grouping import Relations
from .models import Snapshot
from .movement import Path, distance


class Engine:
    def __init__(self, devices, settings, home):
        self.devices = {d.id: d for d in devices}
        self.s = settings
        self.home = home
        self.home_boundary = None
        self.home_shape_valid = True
        self.paths = {d.id: Path(settings) for d in devices if d.candidate}
        self.relations = Relations(settings)
        self.locals = {d.id: {} for d in devices}
        self.rooms = {}
        self.returned = set()
        self.deferred_priority = set()
        self.away_observed = set()
        self.active = set()
        self.separated = set()
        self.primary = None
        self.mode = "priority"
        self.reason = "initial_ambiguous"
        self.health = "no_data"
        self.presence = None
        self.presence_at = None
        self.missing_since = None
        self.missing_mono = None
        self.candidate = None
        self.primary_since = float("-inf")
        self.session = float("-inf")
        self.direction_since = float("-inf")
        self.override_until = None
        self.override_mono = None
        self.pending_presence = None
        self.pending_local_reunion = None
        self.nearby = False
        self.room = self.room_source = None
        self.room_pending = None
        self.room_missing = None
        self.history = deque(maxlen=32)
        self.conflicts = {}

    def transition(self, reason):
        if reason != self.reason:
            self.history.append(reason)
            self.reason = reason

    def observe(self, device, gps, now, mono):
        if device not in self.paths:
            return False
        return self.paths[device].add(gps, now)

    def local(self, device, source, value, expires=None, room=None):
        if device not in self.locals:
            return
        previous = self.locals[device].get(source)
        self.locals[device][source] = (value, expires)
        # A different BLE/room source appearing is not a Wi-Fi return edge.
        # Initial snapshots and repeated positive reports are not transitions.
        if (
            value == "present"
            and previous
            and previous[0] == "absent"
            and device in self.active
        ):
            self.returned.add(device)
        if not any(v == "present" for v, _ in self.locals[device].values()):
            self.returned.discard(device)
        if room is not None:
            self.rooms[device] = (room if value == "present" else None, expires)

    def local_home(self, device, now):
        return any(
            v == "present" and (expires is None or now < expires)
            for v, expires in self.locals[device].values()
        )

    def _priority(self, ids):
        return min(ids, key=lambda i: (self.devices[i].priority, i)) if ids else None

    def _select(self, device, group, mode, reason, now, mono):
        if device != self.primary:
            self.primary_since = mono
            self.direction_since = now
            self.pending_presence = None
        self.primary = device
        self.active = set(group)
        self.mode = mode
        self.candidate = None
        if reason in {"movement_departure", "manual_override"}:
            self.returned.clear()
            self.away_observed.clear()
            self.deferred_priority.clear()
            self.pending_local_reunion = None
        self.transition(reason)

    def set_primary(self, device, duration, now, mono):
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not isfinite(duration)
            or not 1 <= duration <= 86400
        ):
            raise ValueError("duration")
        if device not in self.paths or not self.paths[device].fresh(now):
            raise ValueError("unavailable_candidate")
        group = next(g for g in self.relations.groups(self.paths, mono) if device in g)
        self.separated |= self.active - group
        self.separated -= group
        self._select(device, group, "manual", "manual_override", now, mono)
        self.override_until, self.override_mono = now + duration, mono + duration

    def clear_override(self):
        self.override_until = self.override_mono = None
        self.candidate = None
        self.pending_local_reunion = None
        self.mode = "dynamic"

    def _far(self, device, now):
        p = self.paths.get(device)
        return bool(
            p
            and p.fresh(now)
            and self.home_margin(p.latest) < -p.latest.accuracy
            and distance((p.latest.latitude, p.latest.longitude), self.home[:2])
            - p.latest.accuracy
            >= max(500, self.home[2] + self.s.home_exit_margin_m)
        )

    def home_margin(self, gps):
        """Signed boundary clearance; injected polygon geometry stays HA-free."""
        if self.home_boundary is not None:
            return self.home_boundary(gps.latitude, gps.longitude)
        return self.home[2] - distance((gps.latitude, gps.longitude), self.home[:2])

    def _arbitrate(self, now, mono):
        valid = {i for i, p in self.paths.items() if p.fresh(now)}
        groups = self.relations.groups(valid, mono)
        self.health = "ok" if valid else "no_data"
        if self.override_until is not None:
            if now >= self.override_until or (
                self.override_mono is not None and mono >= self.override_mono
            ):
                self.clear_override()
            else:
                self.health = "ok" if self.primary in valid else "degraded"
                return
        local_group = {
            i for i in self.devices if self.local_home(i, now) and not self._far(i, now)
        }
        for i in self.active & valid:
            p = self.paths[i].latest
            if self.home_margin(p) <= -p.accuracy - self.s.home_exit_margin_m:
                self.away_observed.add(i)
        if self.primary is None and local_group and valid <= local_group:
            candidate = self._priority(local_group & valid) or self._priority(
                local_group & self.paths.keys()
            )
            if candidate is not None:
                self._select(
                    candidate, local_group, "priority", "initial_priority", now, mono
                )
        if self.primary is None and valid:
            if (
                len(self.paths) == 1
                or (len(valid) > 1 and any(valid <= g for g in groups))
                or valid <= local_group
            ):
                group = next(g for g in groups if self._priority(valid) in g) | (
                    local_group if valid <= local_group else set()
                )
                self._select(
                    self._priority(valid),
                    group,
                    "priority",
                    "initial_priority",
                    now,
                    mono,
                )
            else:
                self.health = "ambiguous"
                self.transition("initial_ambiguous")
        # Remove departed members from eligible local evidence, preserving
        # their eligibility as an observed successor until a challenger wins.
        if self.primary is not None:
            # New companions need actual complete-link evidence. Previously
            # separated devices enter only through the reunion branch below.
            for i in sorted(valid - self.active - self.separated):
                if self.active and all(
                    self.relations.close(i, j, mono) for j in self.active
                ):
                    self.active.add(i)
        previous_active = set(self.active)
        separated_now = {
            i
            for i in self.active
            if i != self.primary
            and (p := self.relations.pair(i, self.primary))
            and p.separated
        }
        # Reunion only involves this session's separated members.
        if self.primary is not None and self.separated:
            reunion = {self.primary}
            for i in sorted(self.separated | self.active):
                if i != self.primary and all(
                    self.relations.close(i, j, mono, self.s.reunion_hold_s)
                    for j in reunion
                ):
                    reunion.add(i)
            local_return = self.active & self.returned & local_group
            gps_return = {
                i
                for i in self.active & valid & self.away_observed
                if not self.paths[i].latest.assumed
                and self.home_margin(self.paths[i].latest)
                >= self.paths[i].latest.accuracy
            }
            local_return |= gps_return
            local_group |= gps_return
            local_reunited = bool(local_return and self.separated & local_group)
            if local_reunited:
                signature = tuple(sorted(local_group))
                if (
                    self.pending_local_reunion is None
                    or self.pending_local_reunion[0] != signature
                ):
                    self.pending_local_reunion = (signature, mono)
                if mono - self.pending_local_reunion[1] >= self.s.reunion_hold_s:
                    reunion |= local_group
            else:
                self.pending_local_reunion = None
            if reunion & self.separated:
                self.separated -= reunion
                self.session = now
                self.returned.clear()
                self.away_observed.clear()
                self.deferred_priority = (reunion & self.paths.keys()) - valid
                self._select(
                    self._priority(reunion & valid) or self.primary,
                    reunion,
                    "priority",
                    "reunion_priority",
                    now,
                    mono,
                )
                return
        # A local reunion can complete while the preferred GPS is unavailable.
        # Reconsider only those reunion members, after a new valid fix proves
        # they are still here. Do not apply global priority at a stationary stop.
        for i in sorted(self.deferred_priority & valid):
            p = self.paths[i].latest
            together = self.relations.close(i, self.primary, mono)
            home_together = i in local_group and self.primary in local_group
            if p.observed > self.session and (together or home_together):
                self.deferred_priority.discard(i)
                if self._priority({i, self.primary}) == i:
                    self._select(
                        i, self.active, "priority", "reunion_priority", now, mono
                    )
        if self.primary and self.primary not in valid:
            safe = set()
            for i in self.active & valid:
                p = self.relations.pair(i, self.primary)
                if (
                    p
                    and not p.separated
                    and p.confirmed_at is not None
                    and mono - p.confirmed_at <= self.s.safe_companion_age_s
                ):
                    safe.add(i)
            if safe:
                self._select(
                    self._priority(safe),
                    self.active,
                    self.mode,
                    "safe_companion_failover",
                    now,
                    mono,
                )
            else:
                self.candidate = None
                self.health = "degraded"
                self.transition("no_safe_fallback")
                return
        # Every eligible candidate needs measured movement, not merely a
        # timestamp or a high fixed priority. Group size never adds score.
        scores = {i: self.paths[i].score(now, self.session) for i in valid}
        moving = {
            i
            for i in valid
            if self.paths[i].movement(now, self.session)[0] >= self.s.minimum_movement_m
            and self.paths[i].movement(now, self.session)[1]
            >= self.s.minimum_movement_segments
        }
        allowed = valid if self.primary is None else valid & previous_active
        remote_moving = moving - allowed
        if remote_moving:
            self.health = "ambiguous"
            self.transition("conflicting_clusters")
        candidates = moving & allowed - {self.primary}
        # The current priority device can itself lead a departure. It must
        # leave behind the old local group even though its ID does not change.
        leading_departure = self.primary in moving and bool(separated_now)
        if leading_departure:
            candidates.add(self.primary)
        winner = (
            max(candidates, key=lambda i: (scores[i], -self.devices[i].priority, i))
            if candidates
            else None
        )
        baseline = scores.get(self.primary, 0)
        if winner == self.primary and leading_departure:
            baseline = max((scores.get(i, 0) for i in separated_now), default=0)
        if self.primary is None and winner:
            winning_group = next(g for g in groups if winner in g)
            baseline = max(
                (v for i, v in scores.items() if i not in winning_group), default=0
            )
        if winner and scores[winner] > max(
            baseline * self.s.challenger_ratio, baseline + self.s.challenger_margin
        ):
            if self.candidate is None or self.candidate[0] != winner:
                self.candidate = (winner, mono)
            departure = (
                self.primary is None
                or winner in separated_now
                or (winner == self.primary and leading_departure)
            )
            if mono - self.candidate[1] >= self.s.challenger_hold_s and (
                departure or mono - self.primary_since >= self.s.minimum_primary_hold_s
            ):
                group = next(g for g in groups if winner in g)
                self.separated |= self.active - group
                self._select(
                    winner,
                    group,
                    "dynamic",
                    "movement_departure" if departure else "movement_challenger",
                    now,
                    mono,
                )
        else:
            self.candidate = None
            if (
                self.mode == "dynamic"
                and self.primary in valid
                and not moving
                and self.reason in {"movement_departure", "movement_challenger"}
            ):
                self.transition("retained_stationary")
        # Left-behind local evidence must cease immediately on actual
        # separation, but eligibility for an observed departure must survive.
        # The active set is changed on selection; fusion filters separated pairs.

    def _missing(self, now, mono, conflict=False):
        if self.missing_since is None:
            self.missing_since, self.missing_mono = now, mono
        expired = (
            now - self.missing_since >= self.s.evidence_hold_s
            or mono - self.missing_mono >= self.s.evidence_hold_s
        )
        if expired:
            self.presence = None
        if conflict:
            self.health = "ambiguous"
            self.transition("conflicting_local_and_gps")
        elif self.reason != "no_safe_fallback":
            self.transition("evidence_expired" if expired else "held_missing_evidence")

    def _fuse(self, now, mono):
        p = self.paths.get(self.primary)
        gps = p.latest if p and p.fresh(now) else None
        dist = distance((gps.latitude, gps.longitude), self.home[:2]) if gps else None
        direction = p.direction(now, self.home, self.direction_since) if gps else None
        eligible = {
            i
            for i in self.active
            if i == self.primary
            or not ((pair := self.relations.pair(i, self.primary)) and pair.separated)
        }
        local = {i for i in eligible if self.local_home(i, now)}
        conflict = any(self._far(i, now) for i in local)
        geometry_ok = self.home_shape_valid and not self.s.errors(self.home[2])
        far_ids = {i for i in local if self._far(i, now)}
        self.conflicts = {
            i: evidence for i, evidence in self.conflicts.items() if i in far_ids
        }
        for i in far_ids:
            stamp = self.paths[i].latest.observed
            previous, count = self.conflicts.get(i, (None, 0))
            self.conflicts[i] = (stamp, min(2, count + (stamp != previous)))
        repeated_conflict = any(count >= 2 for _, count in self.conflicts.values())
        target, hold, reason = None, 0, None
        if conflict or not geometry_ok:
            self.pending_presence = None
            self._missing(now, mono, repeated_conflict)
            if not geometry_ok:
                dist = direction = None
                self.health = "ambiguous"
                self.transition("invalid_home_geometry")
            elif not repeated_conflict:
                self.health = "degraded"
        else:
            if local:
                target, hold, reason = (
                    "home",
                    self.s.home_enter_hold_s,
                    "local_home_evidence",
                )
            elif gps:
                if not gps.assumed and self.home_margin(gps) >= gps.accuracy:
                    target, hold, reason = (
                        "home",
                        self.s.gps_home_enter_hold_s,
                        "gps_home_evidence",
                    )
                elif self.home_margin(gps) <= -gps.accuracy - self.s.home_exit_margin_m:
                    if self.nearby:
                        self.nearby = dist <= self.s.nearby_exit_m
                    else:
                        self.nearby = dist <= self.s.nearby_enter_m
                    target = (
                        "arriving"
                        if self.nearby and direction == "towards"
                        else "nearby"
                        if self.nearby
                        else "away"
                    )
                    hold = self.s.home_exit_hold_s if self.presence == "home" else 0
                    if any(
                        self.locals[i]
                        and not any(
                            v == "absent" and (ttl is None or now < ttl)
                            for v, ttl in self.locals[i].values()
                        )
                        for i in eligible
                    ):
                        reason = "gps_outside_local_unknown"
                        self.health = "degraded"
            if target is None:
                self.pending_presence = None
                self._missing(now, mono)
            else:
                pending_key = "home" if target == "home" else "outside"
                if (
                    self.pending_presence is None
                    or self.pending_presence[0] != pending_key
                ):
                    self.pending_presence = (pending_key, mono)
                if mono - self.pending_presence[1] >= hold:
                    changed = target != self.presence
                    self.presence = target
                    self.presence_at = now
                    self.missing_since = self.missing_mono = None
                    if changed and reason:
                        self.transition(reason)
        room_conflict = self._room(eligible, now, mono)
        return Snapshot(
            self.primary,
            self.mode,
            self.health,
            self.presence,
            (gps.latitude, gps.longitude, gps.accuracy) if gps else None,
            self.room,
            self.room_source,
            round(dist, 1) if dist is not None else None,
            direction,
            self.reason,
            self.missing_since is not None and self.presence is not None,
            self.missing_since,
            room_conflict,
        )

    def _room(self, eligible, now, mono):
        if self.room_source is not None and self.room_source not in eligible:
            # Missing-source grace cannot retain a left-behind device's room.
            self.room = self.room_source = self.room_pending = self.room_missing = None
        if self.presence != "home":
            self.room = self.room_source = self.room_pending = self.room_missing = None
            return False
        rooms = {
            i: room
            for i, (room, ttl) in self.rooms.items()
            if i in eligible and room and (ttl is None or now < ttl)
        }
        source = self.primary if self.primary in rooms else self._priority(set(rooms))
        if self.room_source and self.room_source not in rooms:
            if self.room_missing is None:
                self.room_missing = mono
            if mono - self.room_missing < self.s.room_missing_hold_s:
                return len(set(rooms.values())) > 1
            self.room = self.room_source = None
        else:
            self.room_missing = None
        value = rooms.get(source)
        if value is None:
            self.room_pending = None
        elif (value, source) != (self.room, self.room_source):
            if self.room_pending is None or self.room_pending[:2] != (value, source):
                self.room_pending = (value, source, mono)
            if mono - self.room_pending[2] >= self.s.room_change_hold_s:
                self.room, self.room_source = value, source
                self.room_pending = None
        else:
            self.room_pending = None
        return len(set(rooms.values())) > 1

    def evaluate(self, now, mono):
        for path in self.paths.values():
            path.advance(now)
        self.relations.update(self.paths, now, mono)
        self._arbitrate(now, mono)
        return self._fuse(now, mono)

    def metadata(self):
        return {
            "version": 1,
            "primary": self.primary,
            "active": sorted(self.active),
            "separated": sorted(self.separated),
            "mode": self.mode,
            "session": self.session if isfinite(self.session) else None,
            "presence": self.presence,
            "presence_at": self.presence_at,
            "missing_since": self.missing_since,
            "override_until": self.override_until,
            "deferred_priority": sorted(self.deferred_priority),
        }

    def restore(self, data, now, mono):
        if not isinstance(data, dict) or data.get("version") != 1:
            return
        try:
            primary = data["primary"]
            active, separated = set(data["active"]), set(data["separated"])
            deferred = set(data.get("deferred_priority", []))
            mode = data["mode"]
            if (
                primary not in self.paths
                or primary not in active
                or active & separated
                or not deferred <= active & self.paths.keys()
                or not active <= self.devices.keys()
                or not separated <= self.devices.keys()
                or mode not in {"priority", "dynamic", "manual"}
                or data.get("presence")
                not in {None, "home", "nearby", "arriving", "away"}
            ):
                return
            for key in ("session", "presence_at", "missing_since", "override_until"):
                v = data.get(key)
                if v is not None and (
                    isinstance(v, bool)
                    or not isinstance(v, (int, float))
                    or not isfinite(v)
                ):
                    return
                if key != "override_until" and v is not None and v > now:
                    return
            self.primary, self.active, self.separated, self.mode = (
                primary,
                active,
                separated,
                mode,
            )
            self.deferred_priority = deferred
            self.session = (
                data.get("session") if data.get("session") is not None else now
            )
            self.presence_at = data.get("presence_at")
            self.missing_since = (
                data.get("missing_since")
                if data.get("missing_since") is not None
                else self.presence_at
            )
            if self.missing_since is not None:
                self.missing_mono = mono - max(0, now - self.missing_since)
            if (
                data.get("presence") in {"home", "nearby", "arriving", "away"}
                and self.missing_since is not None
                and 0 <= now - self.missing_since < self.s.evidence_hold_s
            ):
                self.presence = data["presence"]
            until = data.get("override_until")
            if mode == "manual" and until is not None and now < until <= now + 86400:
                self.override_until, self.override_mono = until, mono + until - now
            elif mode == "manual":
                self.mode = "dynamic"
        except (KeyError, TypeError, ValueError):
            return
