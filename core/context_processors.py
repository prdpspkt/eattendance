"""Context available to every template.

Keeps role checks and the sidebar's pending-work counters out of the templates,
where they were repeated as ``user.is_superuser or user.role == 'OFFICE_ADMIN'``
on every admin-only block.
"""
from django.conf import settings


def navigation(request):
    """Role flags and pending-approval counts for the sidebar."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {'is_office_admin': False}

    is_office_admin = bool(
        user.is_superuser or getattr(user, 'role', None) == 'OFFICE_ADMIN'
    )

    context = {
        'is_office_admin': is_office_admin,
        'weekend_day_names': _weekend_names(),
        # The BS conversion table, for pages that include the date filter
        # partial. Built once per process, not once per request - it is
        # published calendar data and never changes while the server runs.
        'bs_calendar': _bs_calendar(),
    }

    if is_office_admin:
        # Two cheap COUNT queries, admin pages only. Badge counts are what make
        # a queue visible; without them approvals sit unnoticed.
        from leaves.models import LeaveRequest
        from travel_orders.models import TravelOrder

        context['pending_leave_count'] = LeaveRequest.objects.filter(status='PENDING').count()
        context['pending_travel_count'] = TravelOrder.objects.filter(status='PENDING').count()

    return context


_BS_CALENDAR = None


def _bs_calendar():
    global _BS_CALENDAR
    if _BS_CALENDAR is None:
        from core.nepali_date import calendar_data
        _BS_CALENDAR = calendar_data()
    return _BS_CALENDAR


def _weekend_names():
    try:
        from core.workweek import weekend_day_names
        return weekend_day_names()
    except Exception:  # pragma: no cover - defensive, settings may be partial
        return []
