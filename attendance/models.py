from django.db import models
from core.models import Employee, Shift
from devices.models import Device

class Attendance(models.Model):
    """Attendance record from biometric device"""
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='attendances')
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name='attendances')
    timestamp = models.DateTimeField(db_index=True)
    punch_type = models.IntegerField(default=0, help_text="0=Check-in, 1=Check-out, 2=Overtime in, 3=Overtime out")
    uid = models.IntegerField(help_text="Device UID")
    is_processed = models.BooleanField(default=False, help_text="Whether this record has been processed for calculations")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'attendances'
        verbose_name = 'Attendance'
        verbose_name_plural = 'Attendances'
        ordering = ['-timestamp']
        unique_together = ['employee', 'device', 'timestamp', 'uid']

    def __str__(self):
        return f"{self.employee.user.get_full_name()} - {self.timestamp}"

    @classmethod
    def process_daily_attendance(cls, employee, date):
        """Process daily attendance and calculate hours"""
        from datetime import datetime, timedelta
        attendances = cls.objects.filter(
            employee=employee,
            timestamp__date=date
        ).order_by('timestamp')

        if not attendances.exists():
            return None

        # Get shift for the date
        from core.models import EmployeeShift
        try:
            employee_shift = EmployeeShift.objects.filter(
                employee=employee,
                effective_date__lte=date
                ).filter(
                    models.Q(end_date__isnull=True) | models.Q(end_date__gte=date)
                ).latest('effective_date')
            shift = employee_shift.shift
        except EmployeeShift.DoesNotExist:
            shift = None

        # Create or update daily attendance record
        daily_attendance, created = DailyAttendance.objects.get_or_create(
            employee=employee,
            date=date,
            defaults={'shift': shift}
        )

        if shift:
            daily_attendance.shift = shift

        # Find check-in and check-out
        check_ins = attendances.filter(punch_type__in=[0, 2])
        check_outs = attendances.filter(punch_type__in=[1, 3])

        if check_ins.exists():
            daily_attendance.check_in = check_ins.first().timestamp

        if check_outs.exists():
            daily_attendance.check_out = check_outs.last().timestamp

        # Calculate late arrival
        if daily_attendance.check_in and shift:
            shift_start = datetime.combine(date, shift.start_time)
            grace_time = shift_start + timedelta(minutes=shift.late_grace_minutes)
            if daily_attendance.check_in > grace_time:
                daily_attendance.late_minutes = int((daily_attendance.check_in - shift_start).total_seconds() / 60)

        # Calculate early exit
        if daily_attendance.check_out and shift:
            shift_end = datetime.combine(date, shift.end_time)
            early_tolerance = shift_end - timedelta(minutes=shift.early_exit_minutes)
            if daily_attendance.check_out < early_tolerance:
                daily_attendance.early_exit_minutes = int((shift_end - daily_attendance.check_out).total_seconds() / 60)

        # Calculate working hours
        if daily_attendance.check_in and daily_attendance.check_out:
            # Normal case: both check-in and check-out
            total_seconds = (daily_attendance.check_out - daily_attendance.check_in).total_seconds()
            daily_attendance.working_hours = round(total_seconds / 3600, 2)

            # Calculate overtime
            if shift:
                shift_hours = shift.get_working_hours()
                if daily_attendance.working_hours > shift_hours:
                    daily_attendance.overtime_hours = round(daily_attendance.working_hours - shift_hours, 2)

        elif daily_attendance.check_in and shift:
            # Employee checked in but forgot to check out
            # Estimate working hours from check-in to shift end time
            shift_end = datetime.combine(date, shift.end_time)
            # Only calculate if check-in is before shift end
            if daily_attendance.check_in < shift_end:
                total_seconds = (shift_end - daily_attendance.check_in).total_seconds()
                daily_attendance.working_hours = round(total_seconds / 3600, 2)

                # Calculate overtime if applicable
                shift_hours = shift.get_working_hours()
                if daily_attendance.working_hours > shift_hours:
                    daily_attendance.overtime_hours = round(daily_attendance.working_hours - shift_hours, 2)

                # Add note about estimated time
                if not daily_attendance.notes:
                    daily_attendance.notes = "Working hours estimated (no check-out recorded)"
        elif daily_attendance.check_in:
            # Employee checked in but no shift assigned
            # Cannot estimate working hours without shift info
            if not daily_attendance.notes:
                daily_attendance.notes = "Checked in but no check-out recorded (no shift assigned)"

        # Determine status
        if daily_attendance.check_in:
            # If employee checked in, they are PRESENT (even if no check-out)
            # Check if they were late
            if daily_attendance.late_minutes and daily_attendance.late_minutes > 0:
                daily_attendance.status = 'LATE'
            else:
                daily_attendance.status = 'PRESENT'
        else:
            # No check-in recorded
            daily_attendance.status = 'ABSENT'

        daily_attendance.save()
        return daily_attendance


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
