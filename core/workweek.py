"""Work-week helpers.

Single source of truth for which days are weekly off days. Both leave-day
counting and daily attendance processing use these helpers so a change to
``settings.WEEKEND_DAYS`` applies everywhere.

Day numbers follow Python's ``date.weekday()``: Monday=0 ... Sunday=6.
The default (Saturday, Sunday) lives in settings.
"""
from datetime import timedelta

from django.conf import settings

MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY, SATURDAY, SUNDAY = range(7)

DAY_NAMES = {
    MONDAY: 'Monday',
    TUESDAY: 'Tuesday',
    WEDNESDAY: 'Wednesday',
    THURSDAY: 'Thursday',
    FRIDAY: 'Friday',
    SATURDAY: 'Saturday',
    SUNDAY: 'Sunday',
}

DEFAULT_WEEKEND_DAYS = (SATURDAY, SUNDAY)


def weekend_days():
    """Return the configured weekly off days as a set of weekday numbers."""
    days = getattr(settings, 'WEEKEND_DAYS', DEFAULT_WEEKEND_DAYS)
    return {int(day) for day in days}


def is_weekend(day):
    """True if ``day`` (a date) falls on a configured weekly off day."""
    return day.weekday() in weekend_days()


def is_working_day(day):
    """True if ``day`` is a normal working day."""
    return not is_weekend(day)


def working_days_between(start_date, end_date):
    """Count working days in the inclusive range ``start_date``..``end_date``."""
    if start_date is None or end_date is None or end_date < start_date:
        return 0

    off_days = weekend_days()
    days = 0
    current = start_date
    while current <= end_date:
        if current.weekday() not in off_days:
            days += 1
        current += timedelta(days=1)
    return days


def weekend_day_names():
    """Human-readable list of the configured off days, e.g. ['Saturday', 'Sunday']."""
    return [DAY_NAMES[day] for day in sorted(weekend_days())]
