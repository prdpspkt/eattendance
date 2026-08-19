"""Bikram Sambat dates.

The office works in BS and the database stores AD, so every date the user
types or reads has to cross that boundary. This module is the only place the
conversion happens, and it is deliberately thin: ``nepali_datetime`` carries
the calendar data (the length of each BS month is not a formula - it is a
published table that changes year to year), and this wraps it in the two
operations the rest of the system actually needs.

The conversion table is also handed to the browser once per page, so a filter
can convert as you type without asking the server. It is the same table, read
from the same library, rather than a second copy that can drift.
"""
from datetime import date, datetime

import nepali_datetime

# The library covers BS 1975-2100. Outside that there is no calendar data to
# convert with, so a date outside it is not a conversion failure to paper over -
# it is a date this system cannot express in BS.
MIN_BS_YEAR = nepali_datetime.date.min.year
MAX_BS_YEAR = nepali_datetime.date.max.year

MONTH_NAMES = [
    'Baishakh', 'Jestha', 'Ashadh', 'Shrawan', 'Bhadra', 'Ashwin',
    'Kartik', 'Mangsir', 'Poush', 'Magh', 'Falgun', 'Chaitra',
]


def to_bs(value):
    """AD date -> BS date, or None if it cannot be represented.

    Accepts a date, a datetime, or an ISO string, because this is reached from
    templates as a filter and a template variable that is missing arrives as
    an empty string rather than as None. A filter that raises on an empty
    value takes the whole page down with it.
    """
    if not value:
        return None
    if isinstance(value, str):
        try:
            year, month, day = (int(part) for part in value.split('-'))
            value = date(year, month, day)
        except (ValueError, TypeError):
            return None
    if isinstance(value, datetime):
        # datetime is a subclass of date, but the library type-checks exactly.
        value = value.date()
    try:
        return nepali_datetime.date.from_datetime_date(value)
    except (ValueError, TypeError, OverflowError, IndexError):
        return None


def format_bs(value, separator='-'):
    """AD date -> 'YYYY-MM-DD' in BS, or '' when out of range."""
    converted = to_bs(value)
    if converted is None:
        return ''
    return separator.join((
        f'{converted.year:04d}', f'{converted.month:02d}', f'{converted.day:02d}'
    ))


def format_bs_long(value):
    """AD date -> '03 Bhadra 2083', for reading rather than for a form."""
    converted = to_bs(value)
    if converted is None:
        return ''
    return f'{converted.day:02d} {MONTH_NAMES[converted.month - 1]} {converted.year}'


def parse_bs(value):
    """'2083-05-03' (or 2083/05/03) in BS -> AD date, or None if unusable.

    Anything unparsable returns None rather than raising: this reads what
    somebody typed into a filter box, and a half-typed date is an ordinary
    state for that box to be in, not an error worth a stack trace.
    """
    if not value:
        return None
    parts = str(value).replace('/', '-').replace('.', '-').split('-')
    if len(parts) != 3:
        return None
    try:
        year, month, day = (int(part) for part in parts)
        return nepali_datetime.date(year, month, day).to_datetime_date()
    except (ValueError, TypeError, IndexError):
        return None


def parse_either(ad_value, bs_value, fallback=None):
    """Read a date filter that may have been typed in either calendar.

    The AD field wins when both carry a value: it is the one the browser's own
    date picker fills in, so if the two disagree it is because the BS box holds
    a stale value the user has already moved past.
    """
    if ad_value:
        try:
            year, month, day = (int(part) for part in str(ad_value).split('-'))
            return date(year, month, day)
        except (ValueError, TypeError):
            pass
    converted = parse_bs(bs_value)
    if converted is not None:
        return converted
    return fallback


def calendar_data():
    """The conversion table, for the browser.

    The library stores each BS year as cumulative day counts; the browser
    wants month lengths. Deriving them here keeps the library's table the one
    source of calendar data - the alternative, a second table maintained by
    hand in JavaScript, is a table that will eventually disagree with this one
    for some year nobody is testing.

    One AD anchor date is enough for the client to convert both ways.
    """
    calendar = nepali_datetime._CALENDAR
    years = sorted(calendar)
    month_days = [
        [calendar[year][month] - calendar[year][month - 1] if month > 1
         else calendar[year][1]
         for month in range(1, 13)]
        for year in years
    ]
    anchor = nepali_datetime.date(years[0], 1, 1).to_datetime_date()
    return {
        'minYear': years[0],
        'maxYear': years[-1],
        'monthNames': MONTH_NAMES,
        # [[days in month 1 ... days in month 12], ...] from minYear upwards.
        'monthDays': month_days,
        # AD date of BS minYear-01-01, as [year, month, day].
        'anchorAd': [anchor.year, anchor.month, anchor.day],
    }
