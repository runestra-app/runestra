"""Cron matching with UTC iteration and IANA timezone display semantics."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _field(expression, minimum, maximum, sunday=False):
    values = set()
    for component in expression.split(","):
        base, separator, step_text = component.partition("/")
        step = int(step_text) if separator else 1
        if step < 1:
            raise ValueError("Cron steps must be positive")
        if base == "*":
            start, stop = minimum, maximum
        elif "-" in base:
            parts = base.split("-")
            if len(parts) != 2:
                raise ValueError("Invalid cron range")
            start, stop = map(int, parts)
        else:
            start = stop = int(base)
        if sunday and stop == 7:
            stop = 7
        if start < minimum or stop > maximum or start > stop:
            raise ValueError("Cron field is out of range")
        values.update(range(start, stop + 1, step))
    return values


def _parse(expression):
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("Cron expression must have five fields")
    minute, hour, day, month, weekday = fields
    return (
        _field(minute, 0, 59),
        _field(hour, 0, 23),
        _field(day, 1, 31),
        _field(month, 1, 12),
        _field(weekday, 0, 7, sunday=True),
        day == "*",
        weekday == "*",
    )


def validate_cron(expression, timezone_name="UTC"):
    _parse(expression)
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, TypeError, ValueError) as error:
        raise ValueError("Cron timezone must be a valid IANA timezone") from error


def next_cron_time(expression, timezone_name, after):
    minute, hour, day, month, weekday, day_any, weekday_any = _parse(expression)
    zone = ZoneInfo(timezone_name)
    previous = datetime.fromtimestamp(after, UTC).astimezone(zone).replace(tzinfo=None)
    candidate = datetime.fromtimestamp(after, UTC).replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = candidate + timedelta(days=366 * 8)
    while candidate < limit:
        local = candidate.astimezone(zone)
        cron_weekday = (local.weekday() + 1) % 7
        day_match = local.day in day
        weekday_match = cron_weekday in weekday or (cron_weekday == 0 and 7 in weekday)
        calendar_match = (
            day_match or weekday_match
            if not day_any and not weekday_any
            else (weekday_match if day_any else day_match)
        )
        if (
            local.minute in minute
            and local.hour in hour
            and local.month in month
            and calendar_match
            and local.replace(tzinfo=None) != previous
        ):
            return candidate.timestamp()
        candidate += timedelta(minutes=1)
    raise ValueError("Cron expression has no occurrence within eight years")
