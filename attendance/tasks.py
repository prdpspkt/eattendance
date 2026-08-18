from celery import shared_task
from django.utils import timezone
from datetime import date, datetime, timedelta
import logging

logger = logging.getLogger(__name__)


def _start_of_day(day):
    """Timezone-aware start of a calendar day."""
    from django.conf import settings

    naive = datetime.combine(day, datetime.min.time())
    if settings.USE_TZ:
        return timezone.make_aware(naive, timezone.get_current_timezone())
    return naive


def _coerce_date(value):
    """Accept a date, a datetime or an ISO string (Celery serialises to JSON)."""
    if value is None:
        return timezone.localdate()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def process_day(target_date=None, employee=None):
    """Rebuild daily attendance for one day, for one employee or everyone.

    Returns a summary dict. This is plain Python so it can be called from a
    management command, the admin, or a Celery task.
    """
    from .models import Attendance, DailyAttendance
    from core.models import Employee

    target_date = _coerce_date(target_date)
    today = timezone.localdate()

    if target_date > today:
        return {
            'date': target_date.isoformat(), 'processed': 0, 'skipped': 0, 'errors': 0,
            'timestamp': timezone.now().isoformat(),
        }

    if employee is not None:
        employees = [employee]
    else:
        employees = Employee.objects.filter(employment_status='ACTIVE').select_related('user')

    # Employees who punched anywhere near this day. A punch is proof the person
    # was working, which outranks a join_date that may just be the day their
    # profile was auto-created during a device sync.
    punched_ids = set(
        Attendance.objects.filter(
            timestamp__gte=_start_of_day(target_date - timedelta(days=1)),
            timestamp__lt=_start_of_day(target_date + timedelta(days=2)),
        ).values_list('employee_id', flat=True).distinct()
    )

    processed = 0
    skipped = 0
    errors = 0

    for emp in employees:
        # Don't fabricate absences for days before someone joined.
        if emp.join_date and target_date < emp.join_date and emp.id not in punched_ids:
            skipped += 1
            continue

        try:
            daily = Attendance.process_daily_attendance(emp, target_date)
            if daily:
                processed += 1
        except Exception as exc:
            errors += 1
            logger.exception(
                "Error processing attendance for %s on %s: %s",
                emp.user.get_full_name(), target_date, exc
            )

    return {
        'date': target_date.isoformat(),
        'processed': processed,
        'skipped': skipped,
        'errors': errors,
        'timestamp': timezone.now().isoformat(),
    }


@shared_task(name='attendance.tasks.process_all_daily_attendance')
def process_all_daily_attendance(target_date=None):
    """
    Process daily attendance for all active employees.
    Runs daily at 1:00 AM via Celery Beat (processes the previous day).
    """
    if target_date is None:
        # The nightly run happens after midnight, so close out yesterday.
        target_date = timezone.localdate() - timedelta(days=1)
    result = process_day(target_date)
    logger.info("Daily attendance processed: %s", result)
    return result


@shared_task(name='attendance.tasks.process_employee_attendance')
def process_employee_attendance(employee_id, target_date=None):
    """Process attendance for a specific employee"""
    from core.models import Employee

    try:
        employee = Employee.objects.get(id=employee_id)
    except Employee.DoesNotExist:
        return {
            'employee_id': employee_id,
            'success': False,
            'error': 'Employee not found',
            'timestamp': timezone.now().isoformat(),
        }

    result = process_day(target_date, employee=employee)
    result['employee_id'] = employee_id
    result['employee_name'] = employee.user.get_full_name()
    result['success'] = result['errors'] == 0
    return result
