"""Leave entitlement arithmetic.

Everything that decides *how much* leave somebody has, and *whether* a request
is allowed, lives here rather than in a view. There are two reasons. The rules
change when the government changes them, and a rule that is written once is
changed once. And balances have to be rebuildable: a limit edited today has to
be applied to the years that have already been recorded, which means the
figures must be derivable from the requests on file rather than nudged up and
down as requests come in.

The unit of accumulation is the leave year, which is the calendar year unless
``LEAVE_YEAR_START_MONTH`` says otherwise.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction

from .models import LeaveBalance, LeaveRequest, LeaveType

ZERO = Decimal('0.0')


# ----------------------------------------------------------------------
# the leave year
# ----------------------------------------------------------------------
def year_start_month():
    """First month of the leave year (1 = January)."""
    return int(getattr(settings, 'LEAVE_YEAR_START_MONTH', 1))


def leave_year_of(day):
    """The leave year a date falls in.

    With a start month other than January the year is labelled by the calendar
    year it begins in, so a July-June year running from July 2026 is year 2026
    all the way through to June 2027.
    """
    start = year_start_month()
    if start == 1 or day.month >= start:
        return day.year
    return day.year - 1


def current_leave_year():
    from django.utils import timezone
    return leave_year_of(timezone.localdate())


def year_bounds(year):
    """First and last date of a leave year."""
    start_month = year_start_month()
    start = date(year, start_month, 1)
    if start_month == 1:
        end = date(year, 12, 31)
    else:
        end = date(year + 1, start_month, 1) - timedelta(days=1)
    return start, end


# ----------------------------------------------------------------------
# what has been taken
# ----------------------------------------------------------------------
def days_taken(employee, leave_type, year, exclude_request=None):
    """Approved days of this leave in this leave year.

    A request is counted in the year it starts in. Splitting a request across
    a year boundary would be more precise, but it would also mean an approval
    silently changing two years' balances, and the office counts a leave
    against the year it began.
    """
    start, end = year_bounds(year)
    requests = LeaveRequest.objects.filter(
        employee=employee,
        leave_type=leave_type,
        status='APPROVED',
        start_date__gte=start,
        start_date__lte=end,
    )
    if exclude_request is not None and exclude_request.pk:
        requests = requests.exclude(pk=exclude_request.pk)
    return sum((request.total_days or ZERO for request in requests), ZERO)


def first_year_for(employee):
    """The earliest leave year worth computing for this employee."""
    return leave_year_of(employee.join_date)


# ----------------------------------------------------------------------
# accrual
# ----------------------------------------------------------------------
def carried_forward(leave_type, previous_remaining):
    """What survives the year end out of ``previous_remaining``."""
    if not leave_type.accumulates:
        return ZERO
    return max(ZERO, leave_type.cap(previous_remaining))


@transaction.atomic
def rebuild_balance(employee, leave_type, year, previous_remaining=None):
    """Recompute one employee's balance for one leave type and year.

    ``previous_remaining`` lets a caller walking several years in order pass
    the figure it just computed instead of re-reading it.
    """
    if leave_type.is_occasional:
        return None

    if previous_remaining is None:
        previous = LeaveBalance.objects.filter(
            employee=employee, leave_type=leave_type, year=year - 1
        ).first()
        previous_remaining = previous.remaining_days if previous else ZERO

    opening = carried_forward(leave_type, previous_remaining)
    accrued = Decimal(leave_type.days_per_year or 0)
    lapsed = max(ZERO, previous_remaining - opening)

    total = opening + accrued
    capped_total = leave_type.cap(total)
    lapsed += max(ZERO, total - capped_total)

    used = days_taken(employee, leave_type, year)

    balance, _ = LeaveBalance.objects.update_or_create(
        employee=employee, leave_type=leave_type, year=year,
        defaults={
            'opening_days': opening,
            'accrued_days': accrued,
            'lapsed_days': lapsed,
            'total_days': capped_total,
            'used_days': used,
            'remaining_days': capped_total - used,
        },
    )
    return balance


def rebuild_for_employee(employee, upto_year=None, leave_types=None):
    """Rebuild every year from the employee's first up to ``upto_year``.

    Accumulating leave is a chain - this year's opening is last year's
    closing - so the years have to be walked in order, from the beginning.
    """
    upto_year = upto_year or current_leave_year()
    types = list(leave_types if leave_types is not None
                 else LeaveType.objects.filter(is_active=True, accrual=LeaveType.Accrual.YEARLY))

    rebuilt = 0
    for leave_type in types:
        if leave_type.is_occasional:
            continue
        remaining = ZERO
        for year in range(first_year_for(employee), upto_year + 1):
            balance = rebuild_balance(employee, leave_type, year, previous_remaining=remaining)
            if balance is None:
                break
            remaining = balance.remaining_days
            rebuilt += 1
    return rebuilt


def rebuild_all(upto_year=None, employees=None, leave_types=None):
    """Rebuild balances for every active employee. Returns rows written."""
    from core.models import Employee

    upto_year = upto_year or current_leave_year()
    if employees is None:
        employees = Employee.objects.filter(employment_status='ACTIVE').select_related('user')
    types = list(leave_types if leave_types is not None
                 else LeaveType.objects.filter(is_active=True, accrual=LeaveType.Accrual.YEARLY))

    return sum(
        rebuild_for_employee(employee, upto_year=upto_year, leave_types=types)
        for employee in employees
    )


# ----------------------------------------------------------------------
# what an employee may take
# ----------------------------------------------------------------------
def balance_for(employee, leave_type, year=None):
    """The balance row for a year, computing it if it is not there yet."""
    if leave_type.is_occasional:
        return None
    year = year or current_leave_year()
    balance = LeaveBalance.objects.filter(
        employee=employee, leave_type=leave_type, year=year
    ).first()
    if balance is None:
        rebuild_for_employee(employee, upto_year=year, leave_types=[leave_type])
        balance = LeaveBalance.objects.filter(
            employee=employee, leave_type=leave_type, year=year
        ).first()
    return balance


def entitlement_overview(employee, year=None):
    """Everything an employee is entitled to, ready for a template.

    Yearly and occasional leave answer different questions - "how many days
    are left" against "how many times may I still take this" - so they come
    back as two lists rather than one with half the columns blank.
    """
    year = year or current_leave_year()
    yearly, occasional = [], []

    for leave_type in LeaveType.objects.filter(is_active=True):
        if leave_type.is_occasional:
            used = leave_type.occurrences_used(employee)
            occasional.append({
                'leave_type': leave_type,
                'days_per_occurrence': leave_type.days_per_occurrence,
                'used_occurrences': used,
                'max_occurrences': leave_type.max_occurrences_lifetime,
                'left_occurrences': leave_type.occurrences_left(employee),
                'exhausted': leave_type.occurrences_left(employee) == 0,
            })
        else:
            balance = balance_for(employee, leave_type, year)
            if balance:
                yearly.append(balance)

    return {'year': year, 'yearly': yearly, 'occasional': occasional}


def check_request(employee, leave_type, start_date, end_date, days, exclude_request=None):
    """Reasons this request may not be granted. Empty list means it may.

    Called before a request is saved and again before it is approved: a
    request that was within the balance when it was made can be outside it by
    the time somebody gets round to approving it.
    """
    problems = []
    days = Decimal(days or 0)

    if not leave_type.is_active:
        problems.append(f'{leave_type.name} is no longer in use.')
        return problems

    if days <= 0:
        problems.append('The request does not cover any working day.')
        return problems

    if leave_type.is_occasional:
        allowed = leave_type.days_per_occurrence
        if allowed and days > allowed:
            problems.append(
                f'{leave_type.name} allows {allowed} days each time; this request is {days:g}.'
            )
        limit = leave_type.max_occurrences_lifetime
        if limit:
            used = leave_type.occurrences_used(employee)
            if exclude_request is not None and exclude_request.pk and exclude_request.status == 'APPROVED':
                used -= 1
            if used >= limit:
                problems.append(
                    f'{leave_type.name} may be taken {limit} times in a career, '
                    f'and {used} have already been taken.'
                )
        return problems

    year = leave_year_of(start_date)
    balance = balance_for(employee, leave_type, year)
    if balance is None:
        problems.append(f'No {leave_type.name} balance exists for {year}.')
        return problems

    already_used = days_taken(employee, leave_type, year, exclude_request=exclude_request)
    available = balance.total_days - already_used
    if days > available:
        problems.append(
            f'{leave_type.name}: {days:g} days requested but only {available:g} available '
            f'in {year} (entitlement {balance.total_days:g}, already taken {already_used:g}).'
        )
    return problems
