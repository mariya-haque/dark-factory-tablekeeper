"""Local wall-clock times, real instants and DST rules (spec §4, §8, §9).

Instants are stored as integer UTC epoch seconds. A local time is resolved with
fold=0 (the first occurrence in a repeated hour); a local time that does not survive
a round trip through UTC lies in a spring-forward gap and does not exist.
"""
from __future__ import annotations

import datetime as dt
import re
from functools import lru_cache
from zoneinfo import ZoneInfo

from .errors import ApiError, invalid

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

LOCAL_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}$")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
HHMM_RE = re.compile(r"^[0-9]{2}:[0-9]{2}$")
UTC = dt.timezone.utc


@lru_cache(maxsize=256)
def zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def valid_zone(name: object) -> bool:
    if not isinstance(name, str) or not name or len(name) > 64:
        return False
    try:
        zone(name)
    except Exception:
        return False
    return True


def parse_local(value: str) -> dt.datetime:
    """A bare local `YYYY-MM-DDTHH:MM`; anything else is 422 validation_failed."""
    if not LOCAL_RE.match(value):
        raise invalid("starts_at_local must be a local YYYY-MM-DDTHH:MM")
    try:
        naive = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise invalid("starts_at_local is not a valid date and time") from None
    if not MIN_YEAR <= naive.year <= MAX_YEAR:
        raise invalid(f"starts_at_local year must be from {MIN_YEAR} to {MAX_YEAR}")
    return naive


def parse_date(value: str) -> dt.date:
    if not DATE_RE.match(value):
        raise invalid("date must be YYYY-MM-DD")
    try:
        day = dt.date.fromisoformat(value)
    except ValueError:
        raise invalid("date is not a valid calendar date") from None
    if not MIN_YEAR <= day.year <= MAX_YEAR:
        raise invalid(f"date year must be from {MIN_YEAR} to {MAX_YEAR}")
    return day


def parse_hhmm(value: object) -> int | None:
    """Minutes after midnight for `HH:MM`, or None if invalid."""
    if not isinstance(value, str) or not HHMM_RE.match(value):
        return None
    h, m = int(value[:2]), int(value[3:])
    if h > 23 or m > 59:
        return None
    return h * 60 + m


def format_local(naive: dt.datetime) -> str:
    return naive.strftime("%Y-%m-%dT%H:%M")


def to_instant(naive: dt.datetime, tz_name: str) -> int:
    """Epoch seconds for a local time, first occurrence, without a gap check."""
    return int(naive.replace(tzinfo=zone(tz_name), fold=0).timestamp())


def resolve(naive: dt.datetime, tz_name: str) -> int | None:
    """Epoch seconds for an existing local time; None inside a spring-forward gap."""
    tz = zone(tz_name)
    aware = naive.replace(tzinfo=tz, fold=0)
    back = aware.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
    if back != naive:
        return None
    return int(aware.timestamp())


EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
MIN_YEAR, MAX_YEAR = 1000, 9998  # keeps every instant and its local rendering in range


def rfc3339(ts: int, tz_name: str) -> str:
    # Arithmetic from the epoch, not fromtimestamp(): works for pre-1970 instants everywhere.
    return (EPOCH + dt.timedelta(seconds=ts)).astimezone(zone(tz_name)).isoformat()


def now_ts() -> float:
    return dt.datetime.now(UTC).timestamp()


def now_rfc3339() -> str:
    return dt.datetime.now(UTC).replace(microsecond=0).isoformat()


def windows(opening_hours: list, day: dt.date) -> list[tuple[int, int]]:
    """(opens, closes) minute pairs for the weekday of `day`, sorted by opening."""
    wd = WEEKDAYS[day.weekday()]
    out = []
    for h in opening_hours:
        if h["weekday"] == wd:
            out.append((parse_hhmm(h["opens"]), parse_hhmm(h["closes"])))
    return sorted(out)


def at_minutes(day: dt.date, minutes: int) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, minutes // 60, minutes % 60)


def check_start(restaurant: dict, naive: dt.datetime) -> tuple[int, int]:
    """Validate a requested start against DST, opening hours and the slot grid.

    Returns (start_ts, end_ts) in epoch seconds.
    """
    tz_name = restaurant["timezone"]
    start = resolve(naive, tz_name)
    if start is None:
        raise ApiError(422, "invalid_local_time", "that local time does not exist (daylight-saving gap)")
    minutes = naive.hour * 60 + naive.minute
    day = naive.date()
    window = next(((o, c) for o, c in windows(restaurant["opening_hours"], day) if o <= minutes < c), None)
    if window is None:
        raise ApiError(422, "outside_opening_hours", "the restaurant is not open at that time")
    opens, closes = window
    if (minutes - opens) % restaurant["slot_minutes"] != 0:
        raise ApiError(422, "not_on_slot_grid", "start time is not on the restaurant's slot grid")
    end = start + restaurant["reservation_duration_minutes"] * 60
    if end > to_instant(at_minutes(day, closes), tz_name):
        raise ApiError(422, "outside_opening_hours", "the reservation would end after closing time")
    return start, end


def day_slots(restaurant: dict, day: dt.date) -> list[tuple[dt.datetime, int, int]]:
    """Every bookable (local start, start_ts, end_ts) on `day`, in local time order."""
    tz_name = restaurant["timezone"]
    step = restaurant["slot_minutes"]
    dur = restaurant["reservation_duration_minutes"] * 60
    seen: dict[int, tuple[dt.datetime, int, int]] = {}
    for opens, closes in windows(restaurant["opening_hours"], day):
        close_ts = to_instant(at_minutes(day, closes), tz_name)
        m = opens
        while m < closes:
            if m not in seen:
                naive = at_minutes(day, m)
                ts = resolve(naive, tz_name)
                if ts is not None and ts + dur <= close_ts:
                    seen[m] = (naive, ts, ts + dur)
            m += step
    return [seen[m] for m in sorted(seen)]
