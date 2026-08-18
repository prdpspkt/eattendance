from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from attendance.models import Attendance, DailyAttendance
from core.models import Employee, EmployeeShift, Shift
from core.workweek import is_weekend, working_days_between
from devices.models import Device, EmployeeDevice

User = get_user_model()

# 2026-03-02 is a Monday; 2026-03-07 a Saturday; 2026-03-08 a Sunday.
MONDAY = date(2026, 3, 2)
SATURDAY = date(2026, 3, 7)
SUNDAY = date(2026, 3, 8)


def at(day, hour, minute=0):
    """Aware datetime in the active timezone."""
    return timezone.make_aware(
        datetime.combine(day, time(hour, minute)), timezone.get_current_timezone()
    )


class WorkweekTests(TestCase):
    def test_saturday_and_sunday_are_weekend_by_default(self):
        self.assertTrue(is_weekend(SATURDAY))
        self.assertTrue(is_weekend(SUNDAY))
        self.assertFalse(is_weekend(MONDAY))

    def test_working_days_skips_weekend(self):
        # Monday 2 Mar to Sunday 8 Mar = 5 working days.
        self.assertEqual(working_days_between(MONDAY, SUNDAY), 5)

    def test_single_weekend_day_range_is_zero(self):
        self.assertEqual(working_days_between(SATURDAY, SUNDAY), 0)

    @override_settings(WEEKEND_DAYS=[5])
    def test_weekend_days_is_configurable(self):
        self.assertTrue(is_weekend(SATURDAY))
        self.assertFalse(is_weekend(SUNDAY))
        self.assertEqual(working_days_between(MONDAY, SUNDAY), 6)


class AttendanceProcessingTestCase(TestCase):
    """Shared fixture: one employee on a 09:00-18:00 shift with a 60 min break."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='rmaharjan', first_name='Rita', last_name='Maharjan',
            password='test-pass-123',
        )
        self.employee = Employee.objects.create(
            user=self.user, employee_id='EMP0001', join_date=date(2024, 1, 1), device_uid=1,
        )
        self.device = Device.objects.create(name='Main Door', ip_address='192.168.1.201')
        EmployeeDevice.objects.create(
            employee=self.employee, device=self.device, device_uid=1, user_name='Rita',
        )
        self.shift = Shift.objects.create(
            name='Day Shift', start_time=time(9, 0), end_time=time(18, 0),
            late_grace_minutes=15, early_exit_minutes=15, break_duration_minutes=60,
        )
        EmployeeShift.objects.create(
            employee=self.employee, shift=self.shift, effective_date=date(2024, 1, 1),
        )

    def punch(self, when, punch_type=0):
        return Attendance.objects.create(
            employee=self.employee, device=self.device, timestamp=when,
            punch_type=punch_type, uid=int(when.timestamp()) % 100000,
        )

    def process(self, day):
        return Attendance.process_daily_attendance(self.employee, day)


class WorkedHoursTests(AttendanceProcessingTestCase):
    def test_break_is_deducted_once(self):
        """09:00-18:00 is a 9h span; with a 60 min break that is 8h worked.

        The old code compared a 9h span against 8h of scheduled hours and
        reported 1h of overtime for a perfectly normal day.
        """
        self.punch(at(MONDAY, 9, 0), punch_type=0)
        self.punch(at(MONDAY, 18, 0), punch_type=1)

        daily = self.process(MONDAY)

        self.assertEqual(float(daily.working_hours), 8.0)
        self.assertEqual(float(daily.overtime_hours), 0)
        self.assertEqual(daily.status, 'PRESENT')

    def test_no_overtime_for_a_standard_day(self):
        self.punch(at(MONDAY, 8, 55), punch_type=0)
        self.punch(at(MONDAY, 18, 5), punch_type=1)

        daily = self.process(MONDAY)

        self.assertEqual(float(daily.overtime_hours), 0)

    def test_overtime_past_shift_end_is_rounded_down(self):
        # Out at 20:20 = 2h20m past shift end; rounds down to 2h15m = 2.25h.
        self.punch(at(MONDAY, 9, 0), punch_type=0)
        self.punch(at(MONDAY, 20, 20), punch_type=1)

        daily = self.process(MONDAY)

        self.assertEqual(float(daily.overtime_hours), 2.25)

    def test_short_overrun_below_minimum_is_ignored(self):
        # 20 minutes late leaving is under the 30 min minimum.
        self.punch(at(MONDAY, 9, 0), punch_type=0)
        self.punch(at(MONDAY, 18, 20), punch_type=1)

        daily = self.process(MONDAY)

        self.assertEqual(float(daily.overtime_hours), 0)

    def test_missing_checkout_does_not_invent_hours(self):
        self.punch(at(MONDAY, 9, 0), punch_type=0)

        daily = self.process(MONDAY)

        self.assertIsNone(daily.working_hours)
        self.assertIsNone(daily.overtime_hours)
        self.assertTrue(daily.is_incomplete)
        self.assertIn('No check-out recorded', daily.notes)

    def test_break_punches_take_priority_over_scheduled_break(self):
        self.punch(at(MONDAY, 9, 0), punch_type=0)
        self.punch(at(MONDAY, 13, 0), punch_type=2)   # break out
        self.punch(at(MONDAY, 13, 30), punch_type=3)  # break in
        self.punch(at(MONDAY, 18, 0), punch_type=1)

        daily = self.process(MONDAY)

        # 9h span minus the 30 min actually taken.
        self.assertEqual(float(daily.working_hours), 8.5)

    def test_half_day_when_short(self):
        self.punch(at(MONDAY, 9, 0), punch_type=0)
        self.punch(at(MONDAY, 12, 0), punch_type=1)

        daily = self.process(MONDAY)

        self.assertEqual(float(daily.working_hours), 3.0)
        self.assertEqual(daily.status, 'HALF_DAY')

    def test_punches_are_marked_processed(self):
        self.punch(at(MONDAY, 9, 0), punch_type=0)
        self.punch(at(MONDAY, 18, 0), punch_type=1)

        self.process(MONDAY)

        self.assertEqual(Attendance.objects.filter(is_processed=False).count(), 0)


class LateAndEarlyTests(AttendanceProcessingTestCase):
    def test_within_grace_is_not_late(self):
        self.punch(at(MONDAY, 9, 10), punch_type=0)
        self.punch(at(MONDAY, 18, 0), punch_type=1)

        daily = self.process(MONDAY)

        self.assertIsNone(daily.late_minutes)
        self.assertEqual(daily.status, 'PRESENT')

    def test_late_beyond_grace_is_measured_from_shift_start(self):
        self.punch(at(MONDAY, 9, 45), punch_type=0)
        self.punch(at(MONDAY, 18, 0), punch_type=1)

        daily = self.process(MONDAY)

        self.assertEqual(daily.late_minutes, 45)
        self.assertEqual(daily.status, 'LATE')

    def test_early_exit_recorded(self):
        self.punch(at(MONDAY, 9, 0), punch_type=0)
        self.punch(at(MONDAY, 16, 0), punch_type=1)

        daily = self.process(MONDAY)

        self.assertEqual(daily.early_exit_minutes, 120)

    def test_timezone_aware_comparison_does_not_crash(self):
        """The old code compared an aware check_in against a naive shift start."""
        self.punch(at(MONDAY, 9, 30), punch_type=0)
        self.punch(at(MONDAY, 18, 0), punch_type=1)

        daily = self.process(MONDAY)  # would raise TypeError before the fix

        self.assertIsNotNone(daily.late_minutes)


class WeekendTests(AttendanceProcessingTestCase):
    def test_weekend_without_punches_is_weekend_not_absent(self):
        daily = self.process(SATURDAY)

        self.assertEqual(daily.status, 'WEEKEND')

        daily = self.process(SUNDAY)

        self.assertEqual(daily.status, 'WEEKEND')

    def test_weekday_without_punches_is_absent(self):
        daily = self.process(MONDAY)

        self.assertEqual(daily.status, 'ABSENT')

    def test_all_weekend_work_counts_as_overtime(self):
        self.punch(at(SATURDAY, 10, 0), punch_type=0)
        self.punch(at(SATURDAY, 14, 0), punch_type=1)

        daily = self.process(SATURDAY)

        self.assertEqual(daily.status, 'PRESENT')
        self.assertEqual(float(daily.overtime_hours), 4.0)

    def test_approved_leave_marks_on_leave(self):
        from leaves.models import LeaveRequest, LeaveType

        leave_type = LeaveType.objects.create(name='Annual Leave', code='AL', days_per_year=20)
        LeaveRequest.objects.create(
            employee=self.employee, leave_type=leave_type,
            start_date=MONDAY, end_date=MONDAY, reason='Family event', status='APPROVED',
        )

        daily = self.process(MONDAY)

        self.assertEqual(daily.status, 'ON_LEAVE')


class OvernightShiftTests(AttendanceProcessingTestCase):
    def setUp(self):
        super().setUp()
        EmployeeShift.objects.all().delete()
        self.night = Shift.objects.create(
            name='Night Shift', start_time=time(20, 0), end_time=time(5, 0),
            late_grace_minutes=15, early_exit_minutes=15, break_duration_minutes=60,
        )
        EmployeeShift.objects.create(
            employee=self.employee, shift=self.night, effective_date=date(2024, 1, 1),
        )

    def test_scheduled_hours_are_positive(self):
        """20:00-05:00 is a 9h span, 8h after the break (was -16h before)."""
        self.assertEqual(self.night.get_working_hours(), 8.0)

    def test_checkout_after_midnight_belongs_to_the_start_day(self):
        self.punch(at(MONDAY, 20, 0), punch_type=0)
        self.punch(at(MONDAY + timedelta(days=1), 5, 0), punch_type=1)

        daily = self.process(MONDAY)

        self.assertEqual(float(daily.working_hours), 8.0)
        self.assertEqual(float(daily.overtime_hours), 0)
        self.assertEqual(daily.status, 'PRESENT')

    def test_overnight_overtime(self):
        self.punch(at(MONDAY, 20, 0), punch_type=0)
        self.punch(at(MONDAY + timedelta(days=1), 7, 0), punch_type=1)

        daily = self.process(MONDAY)

        self.assertEqual(float(daily.overtime_hours), 2.0)


class PunchCodeFallbackTests(AttendanceProcessingTestCase):
    def test_first_and_last_punch_used_when_all_codes_are_check_in(self):
        """Many devices report every punch as code 0."""
        self.punch(at(MONDAY, 9, 0), punch_type=0)
        self.punch(at(MONDAY, 18, 0), punch_type=0)

        daily = self.process(MONDAY)

        self.assertEqual(float(daily.working_hours), 8.0)
        self.assertIn('inferred', daily.notes)

    def test_overtime_codes_are_not_dropped(self):
        """Codes 4/5 (overtime in/out) were ignored by the old filters."""
        self.punch(at(MONDAY, 9, 0), punch_type=4)
        self.punch(at(MONDAY, 19, 30), punch_type=5)

        daily = self.process(MONDAY)

        self.assertIsNotNone(daily.check_in)
        self.assertIsNotNone(daily.check_out)
        self.assertEqual(float(daily.overtime_hours), 1.5)

    def test_single_punch_day_has_no_checkout(self):
        self.punch(at(MONDAY, 9, 0), punch_type=0)

        daily = self.process(MONDAY)

        self.assertIsNotNone(daily.check_in)
        self.assertIsNone(daily.check_out)

    def test_repeated_scan_is_not_a_checkout(self):
        """Real data has pairs two seconds apart: a re-scan, not a departure."""
        self.punch(timezone.make_aware(datetime(2026, 3, 2, 9, 36, 58)), punch_type=0)
        self.punch(timezone.make_aware(datetime(2026, 3, 2, 9, 37, 0)), punch_type=0)

        daily = self.process(MONDAY)

        self.assertIsNone(daily.check_out)
        self.assertIsNone(daily.working_hours)
        self.assertNotEqual(daily.status, 'HALF_DAY')
        self.assertIn('Repeated scan ignored', daily.notes)

    def test_genuine_short_day_is_still_recorded(self):
        """The re-scan guard must not swallow a real (if short) working day."""
        self.punch(at(MONDAY, 9, 0), punch_type=0)
        self.punch(at(MONDAY, 11, 30), punch_type=1)

        daily = self.process(MONDAY)

        self.assertEqual(float(daily.working_hours), 2.5)
        self.assertEqual(daily.status, 'HALF_DAY')


class LeaveDayCountTests(TestCase):
    def test_leave_request_excludes_weekend_days(self):
        from leaves.models import LeaveRequest, LeaveType

        user = User.objects.create_user(username='bkhadka', password='test-pass-123')
        employee = Employee.objects.create(
            user=user, employee_id='EMP0002', join_date=date(2024, 1, 1),
        )
        leave_type = LeaveType.objects.create(name='Annual Leave', code='AL', days_per_year=20)

        request = LeaveRequest.objects.create(
            employee=employee, leave_type=leave_type,
            start_date=MONDAY, end_date=SUNDAY, reason='Trip',
        )

        self.assertEqual(float(request.total_days), 5)

    def test_editing_dates_recomputes_total_days(self):
        from leaves.models import LeaveRequest, LeaveType

        user = User.objects.create_user(username='skarki', password='test-pass-123')
        employee = Employee.objects.create(
            user=user, employee_id='EMP0003', join_date=date(2024, 1, 1),
        )
        leave_type = LeaveType.objects.create(name='Sick Leave', code='SL', days_per_year=14)

        request = LeaveRequest.objects.create(
            employee=employee, leave_type=leave_type,
            start_date=MONDAY, end_date=MONDAY, reason='Flu',
        )
        self.assertEqual(float(request.total_days), 1)

        request.end_date = MONDAY + timedelta(days=2)
        request.save()

        self.assertEqual(float(request.total_days), 3)
