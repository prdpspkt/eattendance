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

    A thin wrapper over :func:`record_punches` so there is exactly one
    implementation of de-duplication and employee resolution to keep correct.
    """
    counts, _touched = record_punches(device, [{
        'device_uid': device_uid,
        'timestamp': timestamp,
        'punch_type': punch_type,
        'uid': uid,
        'user_name': user_name,
    }])
    for outcome, count in counts.items():
        if count:
            return outcome
    return 'invalid'


def _resolve_enrollments(device, device_uids, names):
    """Map every device UID in a batch to its enrolment, in two queries.

    Resolving them one at a time is what the per-punch path used to do, which
    on a 60-punch upload meant 60 round trips before a single row was written.
    """
    from .models import EmployeeDevice

    enrollments = {
        enrollment.device_uid: enrollment
        for enrollment in EmployeeDevice.objects.filter(
            device=device, device_uid__in=device_uids
        ).only('id', 'device_uid', 'employee_id', 'user_name')
    }

    missing = [uid for uid in device_uids if uid not in enrollments]
    if not missing:
        return enrollments

    # Unknown UIDs become unlinked enrolments rather than being dropped, so
    # punches are not silently lost and an admin can attach the person later.
    # ignore_conflicts covers the race where two workers handle uploads from
    # the same newly enrolled user at once; the follow-up query then reads
    # back whichever row won, so both requests end up with a usable enrolment.
    EmployeeDevice.objects.bulk_create(
        [
            EmployeeDevice(
                device=device,
                device_uid=uid,
                user_name=names.get(uid) or f'User {uid}',
            )
            for uid in missing
        ],
        ignore_conflicts=True,
    )
    logger.info(
        "Created %s unlinked enrollment(s) on device %s: %s",
        len(missing), device, missing,
    )
    for enrollment in EmployeeDevice.objects.filter(
        device=device, device_uid__in=missing
    ).only('id', 'device_uid', 'employee_id', 'user_name'):
        enrollments[enrollment.device_uid] = enrollment

    return enrollments


def record_punches(device, punches):
    """Store many punches.

    ``punches`` is an iterable of dicts with keys: device_uid, timestamp,
    punch_type, uid (optional), user_name (optional).

    Returns ``(counts, touched)`` where counts tallies the outcomes and touched
    is the set of ``(employee_id, date)`` pairs that changed, ready to be
    re-processed into daily summaries.

    The whole batch costs a fixed handful of queries rather than three per
    punch. That matters more here than the query count alone suggests: this
    database has a single writer, so time spent inside the insert loop is time
    every other request that needs to write is blocked.
    """
    from attendance.models import Attendance

    counts = {'created': 0, 'duplicate': 0, 'unlinked': 0, 'invalid': 0}
    touched = set()

    # --- normalise, dropping anything unusable --------------------------
    cleaned = []
    names = {}
    for punch in punches:
        timestamp = punch.get('timestamp')
        device_uid = coerce_device_uid(punch.get('device_uid'))
        if timestamp is None or device_uid is None:
            counts['invalid'] += 1
            continue
        try:
            punch_type = int(punch.get('punch_type', 0) or 0)
        except (TypeError, ValueError):
            punch_type = 0
        if punch.get('user_name'):
            names.setdefault(device_uid, punch['user_name'])
        cleaned.append((device_uid, timestamp, punch_type, punch.get('uid')))

    if not cleaned:
        return counts, touched

    enrollments = _resolve_enrollments(
        device, list({uid for uid, _, _, _ in cleaned}), names
    )

    # Backfill a name the device has now told us about, for enrolments that
    # were created before it did. One query per newly named enrolment, but only
    # ever on the first upload that carries the name.
    for device_uid, name in names.items():
        enrollment = enrollments.get(device_uid)
        if enrollment is not None and not enrollment.user_name:
            enrollment.user_name = name
            enrollment.save(update_fields=['user_name'])

    # --- de-duplicate ----------------------------------------------------
    # Against the batch itself first (a device can repeat a record within one
    # upload), then against what is already stored. De-duplication is on
    # (employee, device, timestamp): the record id a device assigns differs
    # between the pull and push transports, so it must not take part or the
    # same punch would land twice.
    candidates = []
    seen = set()
    for device_uid, timestamp, punch_type, uid in cleaned:
        enrollment = enrollments.get(device_uid)
        if enrollment is None or enrollment.employee_id is None:
            # Enrolled on the device but not linked to an employee yet.
            counts['unlinked'] += 1
            continue
        key = (enrollment.employee_id, timestamp)
        if key in seen:
            counts['duplicate'] += 1
            continue
        seen.add(key)
        candidates.append((enrollment.employee_id, timestamp, punch_type, uid, device_uid))

    if not candidates:
        return counts, touched

    existing = set(
        Attendance.objects.filter(
            device=device,
            employee_id__in={employee_id for employee_id, _, _, _, _ in candidates},
            timestamp__in={timestamp for _, timestamp, _, _, _ in candidates},
        ).values_list('employee_id', 'timestamp')
    )

    to_create = []
    for employee_id, timestamp, punch_type, uid, device_uid in candidates:
        if (employee_id, timestamp) in existing:
            counts['duplicate'] += 1
            continue
        to_create.append(Attendance(
            employee_id=employee_id,
            device=device,
            timestamp=timestamp,
            punch_type=punch_type,
            uid=uid if uid is not None else device_uid,
        ))
        local_date = (
            timezone.localtime(timestamp).date() if settings.USE_TZ else timestamp.date()
        )
        touched.add((employee_id, local_date))

    if to_create:
        # ignore_conflicts guards the unique constraint against a concurrent
        # upload of the same punches; without it one racing duplicate would
        # abort the entire batch.
        Attendance.objects.bulk_create(to_create, ignore_conflicts=True)
        counts['created'] += len(to_create)

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
