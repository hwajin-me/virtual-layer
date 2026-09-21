"""Read-only Dawarich API client and shared UI/runtime validation.

Contracts: Dawarich api/v1 PointsController, Families::Locations,
Visits::FindInTime and Api::VisitSerializer. Never expose HTTP exception text:
query authentication puts credentials in the request URL.
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import asin, cos, isfinite, radians, sin, sqrt

from aiohttp import ClientError
from homeassistant.util import dt as dt_util
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from yarl import URL

from .const import (
    CONF_DAWARICH_API_KEY,
    CONF_DAWARICH_AUTH_MODE,
    CONF_DAWARICH_HISTORY_LIMIT,
    CONF_DAWARICH_MEMBER,
    CONF_DAWARICH_PERSON_ENTITY,
    CONF_DAWARICH_POLL_INTERVAL,
    CONF_DAWARICH_REQUEST_TIMEOUT,
    CONF_DAWARICH_URL,
    CONF_DAWARICH_VERIFY_SSL,
    CONF_DAWARICH_INCLUDE_VISITS,
    CONF_DAWARICH_VISIT_LOOKBACK_DAYS,
    CONF_DAWARICH_MAX_AGE_SECONDS,
    CONF_DAWARICH_MAX_ACCURACY,
)

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
DEFAULT_POLL_INTERVAL = 60
DEFAULT_HISTORY_LIMIT = 10
DEFAULT_REQUEST_TIMEOUT = 15
DEFAULT_VISIT_LOOKBACK_DAYS = 30
DEFAULT_MAX_AGE_SECONDS = 86400
DEFAULT_MAX_ACCURACY = 200


class DawarichError(Exception):
    """A stable error code safe for state attributes, logs and flow errors."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _integer(value, minimum, maximum, field):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise vol.Invalid("invalid Dawarich interval", path=[field])
    try:
        number = float(value)
        if (
            not isfinite(number)
            or not number.is_integer()
            or not minimum <= number <= maximum
        ):
            raise ValueError
        return int(number)
    except (ValueError, TypeError, OverflowError) as err:
        raise vol.Invalid("invalid Dawarich interval", path=[field]) from err


def normalize_config(value):
    """Validate without including credentials or malformed input in errors."""
    allowed = {
        CONF_DAWARICH_URL,
        CONF_DAWARICH_API_KEY,
        CONF_DAWARICH_AUTH_MODE,
        CONF_DAWARICH_POLL_INTERVAL,
        CONF_DAWARICH_HISTORY_LIMIT,
        CONF_DAWARICH_PERSON_ENTITY,
        CONF_DAWARICH_MEMBER,
        CONF_DAWARICH_VERIFY_SSL,
        CONF_DAWARICH_REQUEST_TIMEOUT,
        CONF_DAWARICH_INCLUDE_VISITS,
        CONF_DAWARICH_VISIT_LOOKBACK_DAYS,
        CONF_DAWARICH_MAX_AGE_SECONDS,
        CONF_DAWARICH_MAX_ACCURACY,
    }
    if not isinstance(value, dict) or set(value) - allowed:
        raise vol.Invalid("invalid Dawarich configuration")
    raw_url = value.get(CONF_DAWARICH_URL)
    if not isinstance(raw_url, str) or any(ord(char) < 32 for char in raw_url):
        raise vol.Invalid("invalid Dawarich URL", path=[CONF_DAWARICH_URL])
    try:
        url = URL(raw_url.strip())
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.port == 0
            or url.user is not None
            or url.password is not None
            or url.query_string
            or url.fragment
            or any(char.isspace() for char in url.host)
        ):
            raise ValueError
    except (ValueError, TypeError, UnicodeError) as err:
        raise vol.Invalid("invalid Dawarich URL", path=[CONF_DAWARICH_URL]) from err
    key = value.get(CONF_DAWARICH_API_KEY)
    if (
        not isinstance(key, str)
        or not key.strip()
        or len(key) > 4096
        or any(ord(char) < 32 for char in key)
    ):
        raise vol.Invalid("Dawarich API key is required", path=[CONF_DAWARICH_API_KEY])
    auth = value.get(CONF_DAWARICH_AUTH_MODE, "bearer")
    if not isinstance(auth, str) or auth not in {"bearer", "query"}:
        raise vol.Invalid("invalid Dawarich authentication mode", path=[CONF_DAWARICH_AUTH_MODE])
    person = value.get(CONF_DAWARICH_PERSON_ENTITY, "")
    if not isinstance(person, str):
        raise vol.Invalid("invalid Dawarich person", path=[CONF_DAWARICH_PERSON_ENTITY])
    try:
        if person and (cv.entity_id(person) != person or not person.startswith("person.")):
            raise vol.Invalid("invalid Dawarich person")
    except vol.Invalid:
        raise vol.Invalid("invalid Dawarich person", path=[CONF_DAWARICH_PERSON_ENTITY]) from None
    member = value.get(CONF_DAWARICH_MEMBER, "")
    if (
        not isinstance(member, str)
        or len(member) > 256
        or any(ord(char) < 32 for char in member)
    ):
        raise vol.Invalid("invalid Dawarich member", path=[CONF_DAWARICH_MEMBER])
    for field, default in (
        (CONF_DAWARICH_VERIFY_SSL, False),
        (CONF_DAWARICH_INCLUDE_VISITS, True),
    ):
        if not isinstance(value.get(field, default), bool):
            raise vol.Invalid("invalid Dawarich boolean", path=[field])
    return {
        CONF_DAWARICH_URL: str(url).rstrip("/"),
        CONF_DAWARICH_API_KEY: key.strip(),
        CONF_DAWARICH_AUTH_MODE: auth,
        CONF_DAWARICH_POLL_INTERVAL: _integer(
            value.get(CONF_DAWARICH_POLL_INTERVAL, DEFAULT_POLL_INTERVAL), 15, 3600,
            CONF_DAWARICH_POLL_INTERVAL,
        ),
        CONF_DAWARICH_HISTORY_LIMIT: _integer(
            value.get(CONF_DAWARICH_HISTORY_LIMIT, DEFAULT_HISTORY_LIMIT), 1, 100,
            CONF_DAWARICH_HISTORY_LIMIT,
        ),
        CONF_DAWARICH_PERSON_ENTITY: person,
        CONF_DAWARICH_MEMBER: member.strip(),
        # Keep the historic self-signed-certificate behaviour for existing
        # trackers, while allowing secure public deployments to opt in.
        CONF_DAWARICH_VERIFY_SSL: value.get(CONF_DAWARICH_VERIFY_SSL, False),
        CONF_DAWARICH_REQUEST_TIMEOUT: _integer(
            value.get(CONF_DAWARICH_REQUEST_TIMEOUT, DEFAULT_REQUEST_TIMEOUT), 5, 60,
            CONF_DAWARICH_REQUEST_TIMEOUT,
        ),
        CONF_DAWARICH_INCLUDE_VISITS: value.get(CONF_DAWARICH_INCLUDE_VISITS, True),
        CONF_DAWARICH_VISIT_LOOKBACK_DAYS: _integer(
            value.get(CONF_DAWARICH_VISIT_LOOKBACK_DAYS, DEFAULT_VISIT_LOOKBACK_DAYS),
            1, 365, CONF_DAWARICH_VISIT_LOOKBACK_DAYS,
        ),
        # New UI configurations always persist these filters.  A missing
        # value identifies a legacy tracker, whose historical behaviour must
        # remain unchanged until its owner saves the new controls.
        CONF_DAWARICH_MAX_AGE_SECONDS: (
            _integer(value[CONF_DAWARICH_MAX_AGE_SECONDS], 60, 86400,
                     CONF_DAWARICH_MAX_AGE_SECONDS)
            if value.get(CONF_DAWARICH_MAX_AGE_SECONDS) is not None else None
        ),
        CONF_DAWARICH_MAX_ACCURACY: (
            _integer(value[CONF_DAWARICH_MAX_ACCURACY], 1, 10000,
                     CONF_DAWARICH_MAX_ACCURACY)
            if value.get(CONF_DAWARICH_MAX_ACCURACY) is not None else None
        ),
    }


def records(payload):
    """Accept documented arrays and older wrapper objects."""
    for _ in range(4):
        if isinstance(payload, list):
            return [record for record in payload if isinstance(record, dict)]
        if not isinstance(payload, dict):
            return []
        if any(key in payload for key in ("latitude", "lat", "longitude", "lon")):
            return [payload]
        for key in ("points", "data", "locations", "results", "visits", "members"):
            if isinstance(payload.get(key), (dict, list)):
                payload = payload[key]
                break
        else:
            return []
    return []


def point_time(point):
    """Parse actual measurement time, including numeric strings, as UTC."""
    if not isinstance(point, dict):
        return None
    raw = next(
        (
            point[key]
            for key in (
                "timestamp",
                "recorded_at",
                "datetime",
                "created_at",
                "updated_at",
            )
            if point.get(key) is not None
        ),
        None,
    )
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError, OverflowError):
        if not isinstance(raw, str):
            return None
        try:
            parsed = dt_util.parse_datetime(raw)
            return (
                (
                    parsed.replace(tzinfo=timezone.utc)
                    if parsed.tzinfo is None
                    else dt_util.as_utc(parsed)
                )
                if parsed
                else None
            )
        except (ValueError, OverflowError):
            return None
    try:
        if not isfinite(number):
            return None
        # Compatibility with clients returning Unix milliseconds.
        if number >= 1_000_000_000_000:
            number /= 1000
        return datetime.fromtimestamp(number, tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def family_point(payload, identifier, *, explicit=False):
    """Require a unique match; never fall back to another family member."""
    wanted = str(identifier or "").strip().casefold()
    if not wanted:
        return None
    matches = []
    for member in records(payload):
        nested = member.get("user", member.get("member", {}))
        nested = nested if isinstance(nested, dict) else {}
        identities = [member.get("email"), nested.get("email")]
        if explicit:
            identities.extend([member.get("user_id"), nested.get("id")])
        else:
            identities.extend(
                [member.get("name"), member.get("user_name"), nested.get("name")]
            )
        if not any(
            isinstance(value, (str, int))
            and not isinstance(value, bool)
            and str(value).strip().casefold() == wanted
            for value in identities
        ):
            continue
        location = member.get("location")
        if isinstance(location, dict):
            location = {**member, **location}
        else:
            location = member
        matches.append(location)
    if len(matches) > 1:
        raise DawarichError("ambiguous_member")
    return matches[0] if matches else None


def summary(record):
    """Only emit bounded JSON-safe location metadata, not arbitrary API data."""
    fields = (
        "id",
        "timestamp",
        "recorded_at",
        "latitude",
        "longitude",
        "altitude",
        "speed",
        "velocity",
        "bearing",
        "course",
        "accuracy",
        "horizontal_accuracy",
        "activity",
        "address",
        "city",
        "country",
        "country_name",
        "place_name",
        "arrival_at",
        "departure_at",
        "started_at",
        "ended_at",
        "duration",
        "name",
        "status",
        "battery",
        "battery_status",
    )
    result = {}
    for key in fields:
        value = record.get(key)
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, str):
            result[key] = value[:1024]
        elif isinstance(value, (int, float)):
            try:
                if isfinite(value) and abs(value) <= 2**63 - 1:
                    result[key] = value
            except OverflowError:
                pass
    return result


def valid_point(raw):
    """Normalize an individual point; one bad record must not poison a page."""
    timestamp = point_time(raw)
    if timestamp is None or timestamp.timestamp() < 0 or timestamp > dt_util.utcnow():
        return None
    latitude = raw.get("latitude", raw.get("lat"))
    longitude = raw.get("longitude", raw.get("lon", raw.get("lng")))
    accuracy = raw.get(
        "accuracy", raw.get("horizontal_accuracy", raw.get("gps_accuracy", 0))
    )
    try:
        if any(isinstance(value, bool) for value in (latitude, longitude, accuracy)):
            return None
        latitude, longitude, accuracy = (
            float(latitude),
            float(longitude),
            float(accuracy or 0),
        )
        if (
            not all(isfinite(value) for value in (latitude, longitude, accuracy))
            or not -90 <= latitude <= 90
            or not -180 <= longitude <= 180
            or accuracy < 0
        ):
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    return {
        **summary(raw),
        "latitude": latitude,
        "longitude": longitude,
        "accuracy": accuracy,
        "timestamp": timestamp.timestamp(),
    }


@dataclass(frozen=True)
class DawarichSnapshot:
    point: dict
    history: list[dict]
    visit: dict | None
    visit_error: str | None = None
    analysis: dict | None = None


def _distance_meters(first, second) -> float:
    """Return the great-circle distance between two validated GPS points."""
    latitude_1, longitude_1 = map(radians, first)
    latitude_2, longitude_2 = map(radians, second)
    value = sin((latitude_2 - latitude_1) / 2) ** 2 + cos(latitude_1) * cos(latitude_2) * sin((longitude_2 - longitude_1) / 2) ** 2
    return 6_371_000 * 2 * asin(sqrt(max(0.0, min(1.0, value))))


def movement_analysis(points):
    """Derive a bounded, explainable movement sample from two GPS fixes.

    Raw Dawarich activity is retained where supplied.  Otherwise this is an
    estimate only: accuracy circles are removed before classifying movement.
    """
    if not points:
        return {}
    raw_activity = points[0].get("activity")
    result = {"raw_activity": raw_activity} if isinstance(raw_activity, str) else {}
    if len(points) < 2:
        result["state"] = "unknown"
        return result
    latest, previous = points[0], points[1]
    elapsed = latest["timestamp"] - previous["timestamp"]
    if elapsed <= 0:
        result["state"] = "unknown"
        return result
    distance = _distance_meters(
        (previous["latitude"], previous["longitude"]),
        (latest["latitude"], latest["longitude"]),
    )
    adjusted = max(0.0, distance - previous["accuracy"] - latest["accuracy"])
    speed = adjusted / elapsed
    result.update({
        "distance_m": round(distance, 1),
        "duration_s": round(elapsed, 1),
        "speed_m_s": round(speed, 3),
        "state": (
            "stationary" if speed < 0.5 else "walking" if speed < 2.5
            else "running" if speed < 5.5 else "cycling" if speed < 12
            else "driving"
        ),
    })
    return result


class DawarichClient:
    """One bounded, authenticated read; no upload or mutation endpoints."""

    def __init__(self, session, config):
        self.session = session
        self.config = normalize_config(config)

    async def _get(self, endpoint, params=None):
        params = dict(params or {})
        headers = {"Accept": "application/json"}
        if self.config[CONF_DAWARICH_AUTH_MODE] == "query":
            params["api_key"] = self.config[CONF_DAWARICH_API_KEY]
        else:
            headers["Authorization"] = "Bearer " + self.config[CONF_DAWARICH_API_KEY]
        try:
            async with asyncio.timeout(self.config[CONF_DAWARICH_REQUEST_TIMEOUT]):
                async with self.session.get(
                    self.config[CONF_DAWARICH_URL] + endpoint,
                    headers=headers,
                    params=params,
                    allow_redirects=False,
                    ssl=self.config[CONF_DAWARICH_VERIFY_SSL],
                ) as response:
                    if response.status in {401, 403}:
                        raise DawarichError("invalid_auth")
                    if response.status != 200:
                        raise DawarichError("http_error")
                    body = bytearray()
                    stream = response.content
                    while chunk := await stream.read(
                        min(65536, MAX_RESPONSE_BYTES + 1 - len(body))
                    ):
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise DawarichError("invalid_response")
                    try:
                        payload = json.loads(body)
                    except (ValueError, UnicodeError, RecursionError):
                        raise DawarichError("invalid_response") from None
                    return payload, response.headers
        except (ClientError, asyncio.TimeoutError, OSError):
            raise DawarichError("cannot_connect") from None

    async def async_fetch(self, person_name="", *, include_visit=True):
        family = bool(
            self.config[CONF_DAWARICH_MEMBER]
            or self.config[CONF_DAWARICH_PERSON_ENTITY]
        )
        payload, _ = await self._get(
            "/api/v1/families/locations" if family else "/api/v1/points",
            None
            if family
            else {
                "per_page": self.config[CONF_DAWARICH_HISTORY_LIMIT],
                "page": 1,
                "order": "desc",
            },
        )
        if family:
            identifier = self.config[CONF_DAWARICH_MEMBER] or person_name
            point = family_point(
                payload, identifier, explicit=bool(self.config[CONF_DAWARICH_MEMBER])
            )
            if point is None:
                raise DawarichError("member_not_found")
            candidates = [point]
        else:
            candidates = records(payload)
        points = [
            point for raw in candidates if (point := valid_point(raw)) is not None
        ]
        points.sort(key=lambda point: point["timestamp"], reverse=True)
        points = points[: self.config[CONF_DAWARICH_HISTORY_LIMIT]]
        max_age = self.config[CONF_DAWARICH_MAX_AGE_SECONDS]
        max_accuracy = self.config[CONF_DAWARICH_MAX_ACCURACY]
        if max_age is not None or max_accuracy is not None:
            newest_allowed = dt_util.utcnow().timestamp() - max_age if max_age is not None else None
            points = [
                point for point in points
                if (newest_allowed is None or point["timestamp"] >= newest_allowed)
                and (max_accuracy is None or point["accuracy"] <= max_accuracy)
            ]
        if not points:
            raise DawarichError("no_points")
        visit = None
        visit_error = None
        if include_visit and self.config[CONF_DAWARICH_INCLUDE_VISITS] and not family:
            try:
                visit = await self._latest_visit(points[0])
            except DawarichError as err:
                visit_error = err.code
        return DawarichSnapshot(points[0], points, visit, visit_error, movement_analysis(points))

    async def _latest_visit(self, point):
        end = datetime.fromtimestamp(point["timestamp"], tz=timezone.utc)
        params = {
            "start_at": (end - timedelta(days=self.config[CONF_DAWARICH_VISIT_LOOKBACK_DAYS])).isoformat(),
            "end_at": end.isoformat(),
            "page": 1,
            "per_page": 100,
        }
        payload, headers = await self._get("/api/v1/visits", params)
        try:
            pages = int(headers.get("X-Total-Pages", "1"))
        except (TypeError, ValueError, OverflowError):
            raise DawarichError("invalid_response") from None
        if not 1 <= pages <= 1_000_000:
            if pages == 0:
                return None
            raise DawarichError("invalid_response")
        # Current Dawarich orders visits ascending, unlike points.
        if pages > 1:
            payload, _ = await self._get("/api/v1/visits", {**params, "page": pages})
        visits = records(payload)
        dated = [
            (timestamp, visit)
            for visit in visits
            if (
                timestamp := point_time(
                    {"timestamp": visit.get("started_at", visit.get("arrival_at"))}
                )
            )
            is not None
            and timestamp <= end
        ]
        if not dated:
            return None
        return summary(max(dated, key=lambda pair: pair[0])[1])
