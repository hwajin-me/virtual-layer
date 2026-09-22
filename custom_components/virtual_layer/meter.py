"""Validated utility-meter schedules and progressive billing calculations."""

from calendar import monthrange
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from cronsim import CronSim, CronSimError
from homeassistant.util import dt as dt_util
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

PREFIX = "utility_meter_"
CYCLES = ("none", "quarter-hourly", "hourly", "daily", "weekly", "monthly", "bimonthly", "quarterly", "yearly", "days", "cron")
DEFAULTS = {
    "enabled": False, "cycle": "monthly", "start": "", "days": 30,
    "offset": 0, "cron": "0 0 1 * *", "delta_values": False,
    "net_consumption": False, "periodically_resetting": True,
    "always_available": True, "tariff_entity": "", "tariff": "",
    "rate": 0, "base_charge": 0, "currency": "KRW", "tiers": [],
    "compare_previous_month": False,
}


class InvalidNumber(vol.Invalid, ValueError):
    """Numeric error usable by both HA services and native entity methods."""


def number(value):
    """Reject boolean, non-finite and excessively large monetary/energy values."""
    try:
        result = Decimal(str(value))
        if isinstance(value, bool) or not result.is_finite() or abs(result) > Decimal("1e18"):
            raise ValueError
        return result
    except (InvalidOperation, ValueError, TypeError) as err:
        raise InvalidNumber("Expected a finite number with magnitude at most 1e18") from err


def options(config):
    """Validate the UI/storage contract without discarding unknown fields."""
    result = {key: config.get(PREFIX + key, default) for key, default in DEFAULTS.items()}
    def invalid(key, message):
        return vol.Invalid(message, path=[PREFIX + key])

    def field_number(key, value):
        try:
            return number(value)
        except vol.Invalid as err:
            raise invalid(key, str(err)) from err

    for key in ("enabled", "delta_values", "net_consumption", "periodically_resetting", "always_available", "compare_previous_month"):
        if not isinstance(result[key], bool):
            raise invalid(key, key + " must be a boolean")
    if result["cycle"] not in CYCLES:
        raise invalid("cycle", "Invalid meter cycle")
    for key, minimum, maximum in (("days", 1, 36600), ("offset", 0, 40319)):
        value = field_number(key, result[key])
        if value != int(value) or not minimum <= value <= maximum:
            raise invalid(key, "Invalid " + key)
        result[key] = int(value)
    start = result["start"]
    if not isinstance(start, str) or (start and dt_util.parse_datetime(start) is None):
        raise invalid("start", "Start must be an ISO date/time")
    if result["cycle"] == "days" and not start:
        raise invalid("start", "An N-day cycle needs a start date/time")
    if result["cycle"] == "cron":
        try:
            next(CronSim(result["cron"], dt_util.now()))
        except (CronSimError, ValueError, TypeError, AttributeError, StopIteration) as err:
            raise invalid("cron", "Invalid or impossible cron schedule") from err
    for key in ("rate", "base_charge"):
        if field_number(key, result[key]) < 0:
            raise invalid(key, key + " cannot be negative")
    currency = result["currency"]
    if not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha() or not currency.isascii():
        raise invalid("currency", "Currency must be a three-letter code")
    result["currency"] = currency.upper()
    for key in ("tariff_entity", "tariff"):
        if not isinstance(result[key], str):
            raise invalid(key, "Expected text")
    if result["tariff_entity"]:
        try:
            selector = cv.entity_id(result["tariff_entity"])
            if selector.split(".", 1)[0] not in ("select", "input_select"):
                raise ValueError("Expected a select or input_select entity")
        except (vol.Invalid, ValueError) as err:
            raise invalid("tariff_entity", str(err)) from err
        if not result["tariff"].strip():
            raise invalid("tariff", "Tariff name is required")
    tiers = result["tiers"]
    if not isinstance(tiers, list) or len(tiers) > 100:
        raise invalid("tiers", "Expected at most 100 billing tiers")
    previous = Decimal(0)
    for tier in tiers:
        if not isinstance(tier, dict) or set(tier) != {"up_to", "rate"}:
            raise invalid("tiers", "Each tier needs up_to and rate")
        limit = field_number("tiers", tier["up_to"])
        if limit <= previous or field_number("tiers", tier["rate"]) < 0:
            raise invalid("tiers", "Tier bounds must increase and rates must be non-negative")
        previous = limit
    return result


def schedule_profile(settings):
    """Persist only fields that affect the active schedule, including timezone."""
    cycle = settings["cycle"]
    keys = ["cycle"]
    if cycle == "cron":
        keys.append("cron")
    elif cycle != "none":
        keys.extend(("start", "offset"))
        if cycle == "days":
            keys.append("days")
    return {**{key: settings[key] for key in keys}, "timezone": str(dt_util.get_default_time_zone())}


def collection_started(settings, now):
    """Use the same start gate during reload and live source updates."""
    start = dt_util.parse_datetime(settings["start"]) if settings["start"] else None
    if start is None:
        return True
    if start.tzinfo is None:
        start = start.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_utc(start) <= dt_util.as_utc(now)


def previous_month_same_time(now):
    """Return the local calendar instant one month ago, clamping month ends.

    This is deliberately calendar based: 31 March maps to 28/29 February,
    rather than subtracting an arbitrary number of days.  A nonexistent local
    DST wall time is normalized by Home Assistant in the same way as resets;
    an ambiguous historical wall time uses its first occurrence.
    """
    local = dt_util.as_local(now)
    year, month = divmod(local.year * 12 + local.month - 2, 12)
    month += 1
    candidate = local.replace(
        year=year,
        month=month,
        day=min(local.day, monthrange(year, month)[1]),
        fold=0,
    )
    return dt_util.as_local(dt_util.as_utc(candidate))


def next_reset(settings, after):
    """Next local-calendar boundary, anchored without month-end drift.

    N days means local calendar days (not N*24 hours over DST). Calendar
    anchors on day 31 clamp to the last day of shorter months. Nonexistent
    local times normalize through UTC to the first corresponding real time.
    """
    cycle = settings["cycle"]
    if cycle == "none":
        return None
    local = dt_util.as_local(after)
    if cycle == "cron":
        return next(CronSim(settings["cron"], local))
    anchor = dt_util.parse_datetime(settings["start"]) if settings["start"] else None
    if anchor is None:
        anchor = datetime(1970, 1, 1, tzinfo=local.tzinfo)
        if cycle == "weekly":
            anchor += timedelta(days=4)  # Monday
    elif anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=local.tzinfo)
    else:
        anchor = dt_util.as_local(anchor)
    anchor += timedelta(minutes=settings["offset"])
    if cycle in ("quarter-hourly", "hourly"):
        # Fixed sub-day intervals must include the repeated hour at DST fall
        # back. Wall-clock subtraction would silently skip that reset.
        step = timedelta(minutes=15 if cycle == "quarter-hourly" else 60)
        origin = dt_util.as_utc(anchor)
        count = max(0, (dt_util.as_utc(after) - origin) // step + 1)
        return dt_util.as_local(origin + count * step)
    if cycle in ("daily", "weekly", "days"):
        step = timedelta(days={
            "daily": 1, "weekly": 7, "days": settings["days"],
        }[cycle])
        count = max(0, (local - anchor) // step)
        candidate = anchor + count * step
        candidate = dt_util.as_local(dt_util.as_utc(candidate))
        while dt_util.as_utc(candidate) <= dt_util.as_utc(after):
            count += 1
            candidate = dt_util.as_local(dt_util.as_utc(anchor + count * step))
        return candidate
    months = {"monthly": 1, "bimonthly": 2, "quarterly": 3, "yearly": 12}[cycle]
    count = max(0, ((local.year - anchor.year) * 12 + local.month - anchor.month) // months)
    while True:
        year, month = divmod(anchor.year * 12 + anchor.month - 1 + count * months, 12)
        month += 1
        candidate = anchor.replace(year=year, month=month, day=min(anchor.day, monthrange(year, month)[1]))
        candidate = dt_util.as_local(dt_util.as_utc(candidate))
        if dt_util.as_utc(candidate) > dt_util.as_utc(after):
            return candidate
        count += 1


def cost(settings, usage):
    """Progressive tiers; the ordinary rate applies above the final tier."""
    remaining = max(Decimal(0), number(usage))
    total = number(settings["base_charge"])
    previous = Decimal(0)
    for tier in settings["tiers"]:
        upper = number(tier["up_to"])
        amount = min(remaining, upper - previous)
        total += amount * number(tier["rate"])
        remaining -= amount
        previous = upper
    return total + remaining * number(settings["rate"])
