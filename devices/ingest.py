"""Shared ingestion for attendance punches and device enrolments.

Both transports use this module:

  * the pull SDK (``Device.sync_attendance`` / ``Device.sync_users`` via pyzk), and
  * the ADMS/WDMS push protocol (``devices.adms``), where the device posts to us.

Keeping one implementation means de-duplication, employee resolution and
unlinked-enrolment handling behave identically no matter how a punch arrived.
"""
import logging
from datetime import datetime

from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

# Formats seen in ADMS payloads across firmware versions.
DEVICE_DATETIME_FORMATS = (
    '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%dT%H:%M:%S',
    '%Y-%m-%d %H:%M',
    '%Y%m%d%H%M%S',
)


def parse_device_datetime(value):
    """Parse a device timestamp into an aware datetime in the server timezone.

    Devices report their own local wall-clock time with no offset, so the value
    is interpreted in ``settings.TIME_ZONE``. If that does not match where the
    devices physically are, every stored punch will be skewed.
    """
    if not value:
        return None

    raw = str(value).strip()
    for fmt in DEVICE_DATETIME_FORMATS:
        try:
            naive = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if settings.USE_TZ:
            return timezone.make_aware(naive, timezone.get_current_timezone())
        return naive
    return None


def coerce_device_uid(value):
    """Normalise a device user id (PIN) to an int; pyzk may hand back bytes."""
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return int(value.decode().strip())
        except (UnicodeDecodeError, ValueError):
            return int.from_bytes(value, byteorder='little', signed=False)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def resolve_enrollment(device, device_uid, user_name=None, create_unlinked=True):
    """Find (or create) the EmployeeDevice enrolment for a device UID.

    An unknown UID becomes an *unlinked* enrolment rather than being dropped,
    so punches are not silently lost and an admin can attach the person later
    from the Unlinked Enrollments screen.
    """
    from .models import EmployeeDevice

    if device_uid is None:
        return None

    enrollment = EmployeeDevice.objects.filter(
        device=device, device_uid=device_uid
    ).select_related('employee', 'employee__user').first()

    if enrollment:
        if user_name and not enrollment.user_name:
            enrollment.user_name = user_name
            enrollment.save(update_fields=['user_name'])
        return enrollment

    if not create_unlinked:
        return None

    enrollment, _created = EmployeeDevice.objects.get_or_create(
        device=device,
        device_uid=device_uid,
        defaults={'user_name': user_name or f'User {device_uid}'},
    )
    logger.info(
        "Created unlinked enrollment for UID %s on device %s", device_uid, device
    )
    return enrollment


def record_punch(device, device_uid, timestamp, punch_type=0, uid=None, user_name=None):
    """Store a single punch.

    Returns one of: 'created', 'duplicate', 'unlinked', 'invalid'.
    """
    from attendance.models import Attendance

    if timestamp is None:
        return 'invalid'

    device_uid = coerce_device_uid(device_uid)
    if device_uid is None:
        return 'invalid'

    enrollment = resolve_enrollment(device, device_uid, user_name=user_name)
    if enrollment is None or enrollment.employee_id is None:
        # Enrolment exists on the device but is not linked to an employee yet.
        return 'unlinked'

    # De-duplicate on (employee, device, timestamp). The record id a device
    # assigns differs between the pull and push transports, so it must not take
    # part in the duplicate check or the same punch would land twice.
    exists = Attendance.objects.filter(
        employee_id=enrollment.employee_id,
        device=device,
        timestamp=timestamp,
    ).exists()
    if exists:
        return 'duplicate'

    try:
        punch_type = int(punch_type)
    except (TypeError, ValueError):
        punch_type = 0

    Attendance.objects.create(
        employee_id=enrollment.employee_id,
        device=device,
        timestamp=timestamp,
        punch_type=punch_type,
        uid=uid if uid is not None else device_uid,
    )
    return 'created'


def record_punches(device, punches):
    """Store many punches.

    ``punches`` is an iterable of dicts with keys: device_uid, timestamp,
    punch_type, uid (optional), user_name (optional).

    Returns ``(counts, touched)`` where counts tallies the outcomes and
    touched is the set of ``(employee_id, date)`` pairs that changed, ready to
    be re-processed into daily summaries.
    """
    counts = {'created': 0, 'duplicate': 0, 'unlinked': 0, 'invalid': 0}
    touched = set()

    for punch in punches:
        outcome = record_punch(
            device,
            punch.get('device_uid'),
            punch.get('timestamp'),
            punch_type=punch.get('punch_type', 0),
            uid=punch.get('uid'),
            user_name=punch.get('user_name'),
        )
        counts[outcome] += 1

        if outcome == 'created':
            enrollment = resolve_enrollment(
                device, coerce_device_uid(punch.get('device_uid')), create_unlinked=False
            )
            if enrollment and enrollment.employee_id:
                local_date = timezone.localtime(punch['timestamp']).date() \
                    if settings.USE_TZ else punch['timestamp'].date()
                touched.add((enrollment.employee_id, local_date))

    return counts, touched


def process_touched_days(touched):
    """Rebuild daily summaries for the employee/day pairs a sync touched.

    Punches are only useful once they are rolled up, and this project cannot
    assume a Celery worker is running, so the rollup happens inline.
    """
    from attendance.models import Attendance
    from core.models import Employee

    if not touched:
        return 0

    processed = 0
    employees = {
        employee.id: employee
        for employee in Employee.objects.filter(
            id__in={employee_id for employee_id, _ in touched}
        ).select_related('user')
    }

    for employee_id, day in sorted(touched, key=lambda item: item[1]):
        employee = employees.get(employee_id)
        if employee is None:
            continue
        try:
            Attendance.process_daily_attendance(employee, day)
            processed += 1
        except Exception:
            logger.exception(
                "Failed to process daily attendance for employee %s on %s",
                employee_id, day
            )
    return processed
