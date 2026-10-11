from datetime import UTC, datetime

import pytest

from aorch.config.schedule import next_cron_time, validate_cron


def test_cron_rejects_invalid_fields_and_timezones():
    with pytest.raises(ValueError):
        validate_cron("61 * * * *", "UTC")
    with pytest.raises(ValueError):
        validate_cron("0 9 * * 1", "Mars/Olympus")


def test_cron_skips_nonexistent_spring_time():
    before_gap = datetime(2026, 3, 29, 0, 30, tzinfo=UTC).timestamp()
    following = datetime.fromtimestamp(
        next_cron_time("30 2 * * *", "Europe/Ljubljana", before_gap), UTC
    )
    assert following == datetime(2026, 3, 30, 0, 30, tzinfo=UTC)


def test_cron_runs_once_during_repeated_autumn_time():
    first_0230 = datetime(2026, 10, 25, 0, 30, tzinfo=UTC).timestamp()
    following = datetime.fromtimestamp(
        next_cron_time("30 2 * * *", "Europe/Ljubljana", first_0230), UTC
    )
    assert following == datetime(2026, 10, 26, 1, 30, tzinfo=UTC)


def test_cron_uses_standard_day_of_month_or_weekday_matching():
    after = datetime(2026, 1, 2, 0, 0, tzinfo=UTC).timestamp()
    following = datetime.fromtimestamp(
        next_cron_time("0 9 1 * 1", "UTC", after), UTC
    )
    assert following == datetime(2026, 1, 5, 9, 0, tzinfo=UTC)
