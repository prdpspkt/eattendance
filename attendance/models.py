from datetime import datetime, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Employee, Shift
from core.workweek import is_weekend
from devices.models import Device

# Punch/status codes as reported by the device. Shared by both transports:
# the ADMS/WDMS push protocol and the ZK pull SDK use the same numbering.
PUNCH_CHECK_IN = 0
PUNCH_CHECK_OUT = 1
PUNCH_BREAK_OUT = 2
PUNCH_BREAK_IN = 3
PUNCH_OVERTIME_IN = 4
PUNCH_OVERTIME_OUT = 5

PUNCH_TYPE_CHOICES = [
    (PUNCH_CHECK_IN, 'Check In'),
    (PUNCH_CHECK_OUT, 'Check Out'),
    (PUNCH_BREAK_OUT, 'Break Out'),
    (PUNCH_BREAK_IN, 'Break In'),
    (PUNCH_OVERTIME_IN, 'Overtime In'),
    (PUNCH_OVERTIME_OUT, 'Overtime Out'),
]

CHECK_IN_CODES = {PUNCH_CHECK_IN, PUNCH_OVERTIME_IN}
CHECK_OUT_CODES = {PUNCH_CHECK_OUT, PUNCH_OVERTIME_OUT}

# How far outside an overnight shift's window punches are still attributed to
# that shift's day.
OVERNIGHT_PUNCH_TOLERANCE_HOURS = 4


def _as_aware(day, time_of_day, day_offset=0):
    """Combine a date and a time into a datetime in the active timezone."""
    naive = datetime.combine(day + timedelta(days=day_offset), time_of_day)
    if settings.USE_TZ:
        return timezone.make_aware(naive, timezone.get_current_timezone())
    return naive


def _day_bounds(day):
    """Start (inclusive) and end (exclusive) of a calendar day, timezone-aware."""
    start = _as_aware(day, datetime.min.time())
    return start, start + timedelta(days=1)


class Attendance(models.Model):
    """A single punch: from a terminal, or entered by an administrator.

    Manual corrections are stored here as punches rather than as edits to the
    daily summary, and the reason is deliberate: ``process_daily_attendance``
    *rebuilds* ``DailyAttendance`` from punches every time a device pushes
    data. A hand-edited summary would therefore be silently overwritten the
    next time anyone in the office scanned a finger. A punch, on the other
    hand, flows through the same pipeline as any other - de-duplicated,
    rolled up, and visible in the log with an audit trail attached.
    """
    SOURCE_DEVICE = 'DEVICE'
    SOURCE_MANUAL = 'MANUAL'
    SOURCE_CHOICES = [
        (SOURCE_DEVICE, 'Biometric device'),
        (SOURCE_MANUAL, 'Entered by administrator'),
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='attendances')
    # Null for a manual entry: there is no terminal behind it, and inventing
    # one would put a fabricated device in the audit trail.
    device = models.ForeignKey(
        Device, on_delete=models.CASCADE, related_name='attendances',
        blank=True, null=True,
        help_text="The terminal that reported this punch. Empty for manual entries.",
    )
    timestamp = models.DateTimeField(db_index=True)
    punch_type = models.IntegerField(
        default=PUNCH_CHECK_IN,
        choices=PUNCH_TYPE_CHOICES,
        help_text="0=Check in, 1=Check out, 2=Break out, 3=Break in, 4=Overtime in, 5=Overtime out",
    )
    uid = models.IntegerField(help_text="Device UID", blank=True, null=True)
    is_processed = models.BooleanField(default=False, help_text="Whether this record has been processed for calculations")

    source = models.CharField(
        max_length=10, choices=SOURCE_CHOICES, default=SOURCE_DEVICE, db_index=True,
        help_text="Where this punch came from.",
    )
    reason = models.TextField(
        blank=True, null=True,
        help_text=(
            "Why this punch was entered by hand - forgotten scan, device outage, "
            "off-site work. Required for manual entries."
        ),
    )
    recorded_by = models.ForeignKey(
        'core.User', on_delete=models.SET_NULL, blank=True, null=True,
        related_name='recorded_attendances',
        help_text="The administrator who entered this manually.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'attendances'
        verbose_name = 'Attendance'
        verbose_name_plural = 'Attendances'
        ordering = ['-timestamp']
        # Device punches de-duplicate on this. It does NOT constrain manual
        # entries: SQL treats NULLs as distinct, so a null device means the
        # index never matches. That is the right trade - a device replaying its
        # log must not create duplicates, whereas two manual punches at the same
        # instant are a human decision an admin can see and delete.
        unique_together = ['employee', 'device', 'timestamp', 'uid']
        constraints = [
            # A manual punch with no explanation is an unexplained edit to
            # someone's pay record. Enforced in the database so it holds for
            # the admin, the shell and any future importer alike.
            models.CheckConstraint(
                condition=(
                    ~models.Q(source='MANUAL')
                    | (models.Q(reason__isnull=False) & ~models.Q(reason=''))
                ),
                name='manual_attendance_requires_reason',
            ),
        ]

    @property
    def is_manual(self):
        return self.source == self.SOURCE_MANUAL

    @property
    def source_label(self):
        """What to show in a log: the device name, or who keyed it in."""
        if self.is_manual:
            who = self.recorded_by.get_full_name() or self.recorded_by.username if self.recorded_by else 'an administrator'
            return f'Manual entry by {who}'
        return self.device.name if self.device else 'Unknown device'

    def __str__(self):
        return f"{self.employee.user.get_full_name()} - {self.timestamp}"

    # ------------------------------------------------------------------
    # Daily processing
    # ------------------------------------------------------------------
    @classmethod
    def get_shift_for(cls, employee, day):
        """Return the shift assigned to ``employee`` that is effective on ``day``."""
        from core.models import EmployeeShift
        try:
            employee_shift = EmployeeShift.objects.filter(
                employee=employee,
                effective_date__lte=day,
            ).filter(
                models.Q(end_date__isnull=True) | models.Q(end_date__gte=day)
            ).latest('effective_date')
        except EmployeeShift.DoesNotExist:
            return None
        return employee_shift.shift

    @classmethod
    def punch_window(cls, day, shift):
        """Time range whose punches belong to ``day``.

        For a normal shift this is the calendar day. For an overnight shift
        (e.g. 20:00-05:00) the window follows the shift across midnight, so the
        morning check-out is attributed to the day the shift started.
        """
        if shift is None or not shift.is_overnight:
            return _day_bounds(day)

        tolerance = timedelta(hours=OVERNIGHT_PUNCH_TOLERANCE_HOURS)
        start = _as_aware(day, shift.start_time) - tolerance
        end = _as_aware(day, shift.end_time, day_offset=1) + tolerance
        return start, end

    @classmethod
    def process_daily_attendance(cls, employee, date, create_if_no_punches=True):
        """Build (or rebuild) the ``DailyAttendance`` summary for one employee/day.

        Returns the DailyAttendance instance, or None when there is nothing to
        record and ``create_if_no_punches`` is False.
        """
        shift = cls.get_shift_for(employee, date)
        window_start, window_end = cls.punch_window(date, shift)

        punch_qs = cls.objects.filter(
            employee=employee,
            timestamp__gte=window_start,
            timestamp__lt=window_end,
        ).order_by('timestamp')
        punches = list(punch_qs)

        if not punches and not create_if_no_punches:
            return None

        daily, _created = DailyAttendance.objects.get_or_create(
            employee=employee, date=date, defaults={'shift': shift}
        )
        daily.shift = shift
        notes = []

        check_in, check_out = cls._derive_in_out(punches, notes)
        daily.check_in = check_in
        daily.check_out = check_out

        # --- late arrival / early exit -------------------------------------
        daily.late_minutes = None
        daily.early_exit_minutes = None
        shift_start = shift_end = None
        if shift:
            shift_start = _as_aware(date, shift.start_time)
            shift_end = _as_aware(date, shift.end_time, day_offset=1 if shift.is_overnight else 0)

            if check_in:
                grace_deadline = shift_start + timedelta(minutes=shift.late_grace_minutes)
                if check_in > grace_deadline:
                    daily.late_minutes = int((check_in - shift_start).total_seconds() // 60)

            if check_out:
                early_deadline = shift_end - timedelta(minutes=shift.early_exit_minutes)
                if check_out < early_deadline:
                    daily.early_exit_minutes = int((shift_end - check_out).total_seconds() // 60)

        # --- worked time ---------------------------------------------------
        net_minutes = None
        if check_in and check_out:
            gross_minutes = (check_out - check_in).total_seconds() / 60
            break_minutes = cls._break_minutes(punches, shift, gross_minutes)
            net_minutes = max(0.0, gross_minutes - break_minutes)
            daily.working_hours = round(net_minutes / 60, 2)
            if break_minutes:
                notes.append(f"{int(break_minutes)} min break deducted")
        else:
            # Never invent hours for an incomplete day: a missing check-out is
            # an exception for an admin to resolve, not an estimate to publish.
            daily.working_hours = None
            if check_in:
                notes.append("No check-out recorded")

        # --- overtime ------------------------------------------------------
        daily.overtime_hours = cls._overtime_hours(
            date, shift, shift_end, check_in, check_out, net_minutes, notes
        )

        # --- status --------------------------------------------------------
        daily.status = cls._status(employee, date, punches, daily, net_minutes)

        daily.notes = "; ".join(notes) if notes else None
        daily.save()

        cls._sync_overtime_record(employee, date, shift_end, check_in, check_out, daily)

        if punches:
            punch_qs.filter(is_processed=False).update(is_processed=True)

        return daily

    @staticmethod
    def _sync_overtime_record(employee, date, shift_end, check_in, check_out, daily):
        """Keep the day's OvertimeRecord in step with the computed overtime.

        Creates the record so somebody can write down what was worked on, and
        refreshes the derived numbers while it is still pending. It never
        writes ``job_performed`` - only a human knows that - and it never
        touches an approved record, because approving is signing off on an
        amount and a signed-off amount that silently moves is worthless.
        """
        overtime = daily.overtime_hours or 0
        existing = OvertimeRecord.objects.filter(employee=employee, date=date).first()

        if existing and existing.is_frozen:
            return existing

        if not overtime:
            # No overtime any more. Drop the derived record unless somebody has
            # already written on it - that text is theirs to remove, not ours.
            if existing and existing.needs_job_description:
                existing.delete()
                return None
            if existing:
                existing.hours = 0
                existing.save(update_fields=['hours', 'updated_at'])
            return existing

        # Where the overtime sat in the day. With the default policy
        # (OVERTIME_AFTER_SHIFT_END_ONLY) it is the stretch past the shift's
        # end; otherwise the whole worked period is the best window available.
        start_at = shift_end if (shift_end and check_out and check_out > shift_end) else check_in
        end_at = check_out
        if not (start_at and end_at):
            return existing

        if existing:
            existing.start_at = start_at
            existing.end_at = end_at
            existing.hours = overtime
            existing.save(update_fields=['start_at', 'end_at', 'hours', 'updated_at'])
            return existing

        return OvertimeRecord.objects.create(
            employee=employee, date=date,
            start_at=start_at, end_at=end_at, hours=overtime,
        )

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _derive_in_out(punches, notes):
        """First check-in and last check-out for the day.

        Prefers the device's punch codes. Many deployments leave every
        punch on the default code, so when the codes cannot separate in from
        out this falls back to first punch / last punch.

        Scans a few seconds apart are the same person re-presenting a finger,
        not a departure, so a "check-out" within MINIMUM_PUNCH_GAP_MINUTES of
        the check-in is discarded rather than booked as a zero-hour day.
        """
        if not punches:
            return None, None

        ins = [p.timestamp for p in punches if p.punch_type in CHECK_IN_CODES]
        outs = [p.timestamp for p in punches if p.punch_type in CHECK_OUT_CODES]

        first_punch = punches[0].timestamp
        last_punch = punches[-1].timestamp

        check_in = min(ins) if ins else first_punch
        check_out = max(outs) if outs else None

        if check_out is None or check_out <= check_in:
            # Either no out-coded punch, or the codes disagree with the clock.
            # Trust the clock and span the day's punches.
            if last_punch > check_in:
                if check_out is not None:
                    notes.append("Punch codes inconsistent; used first/last punch")
                else:
                    notes.append("Check-out inferred from last punch")
                check_out = last_punch
            else:
                check_out = None

        if check_out is not None:
            minimum_gap = timedelta(
                minutes=getattr(settings, 'MINIMUM_PUNCH_GAP_MINUTES', 5)
            )
            if check_out - check_in < minimum_gap:
                check_out = None
                notes.append("Repeated scan ignored; no genuine check-out")

        return check_in, check_out

    @staticmethod
    def _break_minutes(punches, shift, gross_minutes):
        """Break time to deduct: recorded break punches if present, else the
        shift's scheduled break on days long enough to have taken one."""
        recorded = 0.0
        break_start = None
        for punch in punches:
            if punch.punch_type == PUNCH_BREAK_OUT and break_start is None:
                break_start = punch.timestamp
            elif punch.punch_type == PUNCH_BREAK_IN and break_start is not None:
                recorded += (punch.timestamp - break_start).total_seconds() / 60
                break_start = None
        if recorded > 0:
            return recorded

        if shift and shift.break_duration_minutes:
            half_day_minutes = getattr(settings, 'HALF_DAY_MAX_HOURS', 4) * 60
            if gross_minutes > half_day_minutes + shift.break_duration_minutes:
                return float(shift.break_duration_minutes)
        return 0.0

    @staticmethod
    def _overtime_hours(day, shift, shift_end, check_in, check_out, net_minutes, notes):
        """Overtime for the day, or None when it cannot be determined.

        Rules:
          * an incomplete day (no check-out) earns no overtime;
          * work on a weekly off day is entirely overtime;
          * otherwise overtime is time worked past the shift end (default) or
            net time beyond the shift's scheduled hours;
          * short overruns below the configured minimum are ignored, and the
            remainder is rounded down to the configured increment.
        """
        if not (check_in and check_out) or net_minutes is None:
            return None

        if is_weekend(day):
            overtime = net_minutes
            notes.append("Worked on a weekly off day; counted as overtime")
        elif shift is None:
            # No shift assigned means no baseline to measure overtime against.
            return None
        elif getattr(settings, 'OVERTIME_AFTER_SHIFT_END_ONLY', True):
            overtime = (check_out - shift_end).total_seconds() / 60
        else:
            overtime = net_minutes - (shift.get_working_hours() * 60)

        if overtime < getattr(settings, 'OVERTIME_MINIMUM_MINUTES', 30):
            return 0

        increment = getattr(settings, 'OVERTIME_ROUNDING_MINUTES', 15) or 1
        overtime = (int(overtime) // increment) * increment
        return round(overtime / 60, 2)

    @staticmethod
    def _status(employee, day, punches, daily, net_minutes):
        """Resolve the day's status."""
        if not punches:
            from leaves.models import LeaveRequest
            on_leave = LeaveRequest.objects.filter(
                employee=employee,
                status='APPROVED',
                start_date__lte=day,
                end_date__gte=day,
            ).exists()
            if on_leave:
                return 'ON_LEAVE'
            if is_weekend(day):
                return 'WEEKEND'
            return 'ABSENT'

        if is_weekend(day):
            return 'PRESENT'

        half_day_minutes = getattr(settings, 'HALF_DAY_MAX_HOURS', 4) * 60
        if net_minutes is not None and net_minutes <= half_day_minutes:
            return 'HALF_DAY'
        if daily.late_minutes:
            return 'LATE'
        return 'PRESENT'


class DailyAttendance(models.Model):
    """Processed daily attendance summary"""
    STATUS_CHOICES = [
        ('PRESENT', 'Present'),
        ('ABSENT', 'Absent'),
        ('LATE', 'Late'),
        ('HALF_DAY', 'Half Day'),
        ('ON_LEAVE', 'On Leave'),
        ('HOLIDAY', 'Holiday'),
        ('WEEKEND', 'Weekend')
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='daily_attendances')
    date = models.DateField(db_index=True)
    shift = models.ForeignKey(Shift, on_delete=models.SET_NULL, null=True, blank=True, related_name='daily_attendances')
    check_in = models.DateTimeField(blank=True, null=True)
    check_out = models.DateTimeField(blank=True, null=True)
    working_hours = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    overtime_hours = models.DecimalField(max_digits=5, decimal_places=2, blank=True, null=True)
    late_minutes = models.IntegerField(blank=True, null=True)
    early_exit_minutes = models.IntegerField(blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PRESENT')
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'daily_attendances'
        verbose_name = 'Daily Attendance'
        verbose_name_plural = 'Daily Attendances'
        unique_together = ['employee', 'date']
        ordering = ['-date']

    def __str__(self):
        return f"{self.employee.user.get_full_name()} - {self.date}"

    @property
    def is_incomplete(self):
        """Checked in but never checked out."""
        return self.check_in is not None and self.check_out is None


class Absence(models.Model):
    """Absence record submitted by employee or admin"""
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected')
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='absences')
    date = models.DateField()
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    approved_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_absences'
    )
    approved_at = models.DateTimeField(blank=True, null=True)
    rejection_reason = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'absences'
        unique_together = ['employee', 'date']
        ordering = ['-date']

    def __str__(self):
        return f"{self.employee.user.get_full_name()} - {self.date} ({self.status})"


class OvertimeRecord(models.Model):
    """Overtime worked on one day, with the job performed and what it is worth.

    The daily rollup already computes *how much* overtime someone did. What it
    cannot know is *what they were doing*, and an organisation that pays for
    overtime has to put that on the payment voucher alongside the hours. So
    this model is created automatically from the computed overtime and then
    annotated by a human.

    Two rules keep it honest as a payment document:

    * ``job_performed`` is never touched by the rollup. Only a person writes it.
    * Once a record is APPROVED it is frozen - later recomputation will not
      move its hours or its rate. Approving is signing off on a number, and a
      number that keeps changing afterwards is not a sign-off. If the
      underlying punches genuinely change, an admin reopens the record.
    """
    STATUS_PENDING = 'PENDING'
    STATUS_APPROVED = 'APPROVED'
    STATUS_REJECTED = 'REJECTED'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    employee = models.ForeignKey(
        Employee, on_delete=models.CASCADE, related_name='overtime_records',
    )
    date = models.DateField(db_index=True, help_text="The working day this overtime belongs to.")

    start_at = models.DateTimeField(help_text="When the overtime period began.")
    end_at = models.DateTimeField(help_text="When it ended.")
    hours = models.DecimalField(
        max_digits=5, decimal_places=2,
        help_text="Payable overtime, after the minimum and rounding rules in settings.",
    )

    job_performed = models.TextField(
        blank=True, null=True,
        help_text=(
            "What was worked on during this period. Appears on the overtime "
            "report; fill it in before approving."
        ),
    )

    # The rate is copied onto the record rather than read from the employee at
    # print time. Rates change, and a report of last quarter's overtime must
    # not silently re-price itself when someone gets a raise.
    hourly_rate = models.DecimalField(
        max_digits=10, decimal_places=2, blank=True, null=True,
        help_text="Overtime rate applied to these hours. Copied from the employee when approved.",
    )

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    approved_by = models.ForeignKey(
        'core.User', on_delete=models.SET_NULL, blank=True, null=True,
        related_name='approved_overtime_records',
    )
    approved_at = models.DateTimeField(blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'overtime_records'
        verbose_name = 'Overtime Record'
        verbose_name_plural = 'Overtime Records'
        ordering = ['-date', 'employee']
        # One derived record per employee per day. A day with two separate
        # overtime blocks is a single payable period as far as the rollup is
        # concerned; splitting it is an admin's decision, made by editing.
        unique_together = ['employee', 'date']
        indexes = [models.Index(fields=['status', 'date'])]

    def __str__(self):
        return f"{self.employee.user.get_full_name()} - {self.date} ({self.hours}h)"

    @property
    def is_frozen(self):
        """Approved records are payment documents and stop tracking the rollup."""
        return self.status == self.STATUS_APPROVED

    @property
    def amount(self):
        """What these hours are worth, or None when no rate has been set."""
        if self.hourly_rate is None or self.hours is None:
            return None
        return (self.hours * self.hourly_rate).quantize(Decimal('0.01'))

    @property
    def needs_job_description(self):
        """Flagged in the UI: hours with nothing to justify them."""
        return not (self.job_performed or '').strip()

    def approve(self, user, rate=None):
        """Sign the record off, snapshotting the rate that applies to it."""
        self.status = self.STATUS_APPROVED
        self.approved_by = user
        self.approved_at = timezone.now()
        if rate is not None:
            self.hourly_rate = rate
        elif self.hourly_rate is None:
            self.hourly_rate = self.employee.overtime_hourly_rate
        self.save(update_fields=[
            'status', 'approved_by', 'approved_at', 'hourly_rate', 'updated_at',
        ])
        return self
