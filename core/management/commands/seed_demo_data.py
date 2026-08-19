"""Fill the database with plausible operational records.

``init_sample_data`` sets up the reference tables (departments, shifts, leave
types). This command builds on top of it and creates the *transactional* data
an office actually accumulates: shift assignments, punches, daily rollups,
overtime, leave balances and requests, absences and travel orders.

It is deterministic (fixed RNG seed) and re-runnable: everything is keyed on
natural uniqueness, so a second run adds only what is genuinely missing.
"""
import random
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import Department, Employee, EmployeeShift, Shift, User
from core.workweek import is_weekend
from attendance.models import (
    PUNCH_BREAK_IN, PUNCH_BREAK_OUT, PUNCH_CHECK_IN, PUNCH_CHECK_OUT,
    Absence, Attendance, DailyAttendance, OvertimeRecord,
)
from devices.models import Device, DeviceCommand
from leaves.models import LeaveBalance, LeaveRequest, LeaveType
from travel_orders.models import TravelExpense, TravelItinerary, TravelOrder

SEED = 20260819

LEAVE_REASONS = [
    'Family function at home village.',
    'Recovering from fever, doctor advised rest.',
    "Attending a relative's wedding.",
    'Personal work at the district office.',
    'Annual home visit.',
    "Child's school programme.",
    'Medical check-up scheduled in Kathmandu.',
]

ABSENCE_REASONS = [
    'Unwell, informed the section chief by phone.',
    'Road blocked by strike, could not reach office.',
    'Family emergency.',
    'Forgot to punch, was present in the field.',
]

DESTINATIONS = [
    ('DOMESTIC', 'Pokhara', 'Field monitoring of ongoing works.'),
    ('DOMESTIC', 'Biratnagar', 'Regional office coordination meeting.'),
    ('DOMESTIC', 'Nepalgunj', 'Verification of beneficiary records.'),
    ('DOMESTIC', 'Butwal', 'Quarterly progress review with the district team.'),
    ('DOMESTIC', 'Dhangadhi', 'Supervision of the far-west programme.'),
    ('DOMESTIC', 'Janakpur', 'Public hearing and follow-up.'),
    ('DOMESTIC', 'Ilam', 'Site inspection before contract award.'),
    ('INTERNATIONAL', 'New Delhi, India', 'Regional workshop on digital governance.'),
    ('INTERNATIONAL', 'Bangkok, Thailand', 'Training on public financial management.'),
    ('INTERNATIONAL', 'Dhaka, Bangladesh', 'Bilateral technical exchange visit.'),
]

ITINERARY_STEPS = [
    ('Departure from Kathmandu', 'Tribhuvan International Airport'),
    ('Arrival and check-in', 'Hotel'),
    ('Coordination meeting', 'Regional office'),
    ('Field visit', 'Project site'),
    ('Debriefing with the district team', 'District office'),
    ('Return journey', 'Kathmandu'),
]

OVERTIME_JOBS = [
    'Cleared the pending file backlog before the quarterly deadline.',
    'Prepared the monthly progress report for the ministry.',
    'Data entry of field verification forms.',
    'Assisted the audit team with document retrieval.',
    'Attended the emergency coordination meeting.',
    'Completed the tender document preparation.',
]


class Command(BaseCommand):
    help = 'Fill the database with plausible demo records (punches, leave, travel, overtime).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days', type=int, default=120,
            help='How many calendar days of punches to generate, ending yesterday (default: 120).',
        )
        parser.add_argument(
            '--no-punches', action='store_true',
            help='Skip punch generation; only seed the surrounding records.',
        )
        parser.add_argument(
            '--skip-reference', action='store_true',
            help='Do not run init_sample_data first.',
        )

    def handle(self, *args, **options):
        self.tz = timezone.get_current_timezone()
        self.today = timezone.localdate()

        if not options['skip_reference']:
            self.stdout.write('Ensuring reference data (departments, shifts, leave types)...')
            call_command('init_sample_data', verbosity=0)

        self.admin = (
            User.objects.filter(role=User.UserRole.SUPERUSER).first()
            or User.objects.filter(is_superuser=True).first()
        )
        self.employees = list(Employee.objects.select_related('user').order_by('id'))
        if not self.employees:
            self.stderr.write('No employees found - nothing to seed against.')
            return

        with transaction.atomic():
            self.assign_departments()
            self.set_overtime_rates()
            self.assign_shifts()
        if not options['no_punches']:
            self.generate_punches(options['days'])
        with transaction.atomic():
            self.seed_leave_requests()
            self.seed_absences()
            self.seed_travel_orders()
            self.seed_device_commands()
        self.process_daily_attendance()
        with transaction.atomic():
            self.annotate_overtime()
            self.seed_leave_balances()

        self.stdout.write(self.style.SUCCESS('\nDone. Current row counts:'))
        for model in (Department, Shift, EmployeeShift, Attendance, DailyAttendance,
                      OvertimeRecord, Absence, LeaveBalance, LeaveRequest,
                      TravelOrder, TravelItinerary, TravelExpense, DeviceCommand):
            self.stdout.write(f'  {model._meta.label:<32} {model.objects.count()}')

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def rng_for(self, phase):
        """A generator of its own per phase.

        Each phase draws from an independent stream, so skipping one (or
        re-running when its records already exist) cannot shift the numbers
        the other phases produce - which is what keeps a second run from
        inventing a second, differently-dated set of leave and travel records.
        """
        return random.Random(f'{SEED}:{phase}')

    def aware(self, day, moment):
        return timezone.make_aware(datetime.combine(day, moment), self.tz)

    def report(self, label, count):
        style = self.style.SUCCESS if count else self.style.WARNING
        self.stdout.write(style(f'{label}: {count}'))

    # ------------------------------------------------------------------
    # core records
    # ------------------------------------------------------------------
    def assign_departments(self):
        departments = list(Department.objects.order_by('name'))
        if not departments:
            return
        assigned = 0
        for index, employee in enumerate(self.employees):
            if employee.department_id is None:
                employee.department = departments[index % len(departments)]
                employee.save(update_fields=['department', 'updated_at'])
                assigned += 1
        self.report('Employees given a department', assigned)

    def set_overtime_rates(self):
        rng = self.rng_for('overtime-rates')
        updated = 0
        for employee in self.employees:
            if employee.overtime_hourly_rate is None:
                employee.overtime_hourly_rate = Decimal(rng.choice([250, 300, 350, 400, 450, 500]))
                employee.save(update_fields=['overtime_hourly_rate', 'updated_at'])
                updated += 1
        self.report('Overtime rates set', updated)

    def assign_shifts(self):
        """Everyone works the day shift; a couple move to nights later on.

        Attendance processing needs a shift to judge lateness and overtime
        against, so the effective date is pushed back to the earliest punch we
        already hold for that employee.
        """
        day_shift = (
            Shift.objects.filter(name='Day Shift').first()
            or Shift.objects.filter(is_active=True).order_by('start_time').first()
        )
        if day_shift is None:
            return
        night_shift = Shift.objects.filter(name='Night Shift').first()

        created = 0
        for position, employee in enumerate(self.employees):
            first_punch = (
                Attendance.objects.filter(employee=employee)
                .order_by('timestamp').values_list('timestamp', flat=True).first()
            )
            effective = employee.join_date
            if first_punch:
                effective = min(effective, timezone.localtime(first_punch).date())

            _, made = EmployeeShift.objects.get_or_create(
                employee=employee, effective_date=effective,
                defaults={'shift': day_shift, 'is_active': True},
            )
            created += int(made)

            # Two people moved onto nights a month ago - the rollup has to cope
            # with a shift crossing midnight, so give it something to cope with.
            if night_shift and position % 10 == 4:
                switch = self.today - timedelta(days=30)
                _, made = EmployeeShift.objects.get_or_create(
                    employee=employee, effective_date=switch,
                    defaults={'shift': night_shift, 'is_active': True},
                )
                created += int(made)
        self.report('Shift assignments created', created)

    # ------------------------------------------------------------------
    # punches
    # ------------------------------------------------------------------
    def generate_punches(self, days):
        self.rng = self.rng_for('punches')
        device = Device.objects.filter(is_active=True).first() or Device.objects.first()
        start = self.today - timedelta(days=days)
        end = self.today - timedelta(days=1)

        # Days that already hold punches are left exactly as they are.
        existing = {
            (employee_id, timezone.localtime(stamp).date())
            for employee_id, stamp in Attendance.objects.filter(
                timestamp__gte=self.aware(start, time.min)
            ).values_list('employee_id', 'timestamp')
        }

        # A day with no punches is how this generator represents an absence,
        # so "no punches" cannot be the test for "not seeded yet" - it would
        # fill in those absences on every re-run. Recent activity is the test
        # instead: if the employee already has punches near the end of the
        # window, the window has been populated already.
        recently_seeded = self.aware(end - timedelta(days=5), time.min)

        rows = []
        for employee in self.employees:
            if Attendance.objects.filter(
                employee=employee, timestamp__gte=recently_seeded
            ).exists():
                continue
            day = start
            while day <= end:
                if is_weekend(day) or day < employee.join_date or (employee.id, day) in existing:
                    day += timedelta(days=1)
                    continue
                shift = Attendance.get_shift_for(employee, day)
                if shift is None:
                    day += timedelta(days=1)
                    continue
                rows.extend(self.punches_for_day(employee, day, shift, device))
                day += timedelta(days=1)

        Attendance.objects.bulk_create(rows, batch_size=500)
        self.report('Punches generated', len(rows))

    def punches_for_day(self, employee, day, shift, device):
        rng = self.rng
        if rng.random() < 0.05:
            return []  # absent - no punches at all

        def punch(moment, punch_type):
            return Attendance(
                employee=employee, device=device, timestamp=moment,
                punch_type=punch_type, uid=employee.device_uid,
                source=Attendance.SOURCE_DEVICE, is_processed=False,
            )

        shift_start = self.aware(day, shift.start_time)
        offset = timedelta(days=1) if shift.is_overnight else timedelta(0)
        shift_end = self.aware(day, shift.end_time) + offset

        if rng.random() < 0.18:
            check_in = shift_start + timedelta(minutes=rng.randint(16, 70))
        else:
            check_in = shift_start - timedelta(minutes=rng.randint(-8, 35))
        check_in += timedelta(seconds=rng.randint(0, 59))

        punches = [punch(check_in, PUNCH_CHECK_IN)]

        if rng.random() < 0.45:
            lunch_out = shift_start + timedelta(minutes=rng.randint(170, 210))
            lunch_in = lunch_out + timedelta(minutes=rng.randint(35, 65))
            if lunch_in < shift_end:
                punches.append(punch(lunch_out, PUNCH_BREAK_OUT))
                punches.append(punch(lunch_in, PUNCH_BREAK_IN))

        if rng.random() < 0.03:
            return punches  # forgot to punch out

        if rng.random() < 0.14:
            check_out = shift_end + timedelta(minutes=rng.randint(65, 200))
        else:
            check_out = shift_end + timedelta(minutes=rng.randint(-25, 20))
        check_out += timedelta(seconds=rng.randint(0, 59))
        punches.append(punch(check_out, PUNCH_CHECK_OUT))
        return punches

    def process_daily_attendance(self):
        """Rebuild the daily rollups.

        Every day with punches, plus the approved leave and absence days -
        those carry no punches by definition, but a day someone was on leave
        is still a day the register should show, and the rollup labels it
        ON_LEAVE or ABSENT once asked about it.
        """
        processed = 0
        for employee in self.employees:
            days = {
                timezone.localtime(stamp).date()
                for stamp in Attendance.objects.filter(employee=employee)
                .values_list('timestamp', flat=True)
            }
            for start, end in LeaveRequest.objects.filter(
                employee=employee, status='APPROVED'
            ).values_list('start_date', 'end_date'):
                days.update(
                    start + timedelta(days=offset)
                    for offset in range((end - start).days + 1)
                )
            days.update(
                Absence.objects.filter(employee=employee).values_list('date', flat=True)
            )
            days = sorted(day for day in days if day <= self.today)

            for day in days:
                Attendance.process_daily_attendance(employee, day, create_if_no_punches=True)
                processed += 1
            if days:
                self.stdout.write(f'  {employee.employee_id}: {len(days)} days rolled up')
        self.report('Daily attendance records processed', processed)

    def annotate_overtime(self):
        """Write the job description a human would write, and sign some off."""
        rng = self.rng_for('overtime-annotation')
        touched = 0
        for record in OvertimeRecord.objects.filter(status=OvertimeRecord.STATUS_PENDING):
            if record.job_performed:
                continue
            record.job_performed = rng.choice(OVERTIME_JOBS)
            if record.date < self.today - timedelta(days=14) and rng.random() < 0.7:
                record.status = OvertimeRecord.STATUS_APPROVED
                record.approved_by = self.admin
                record.approved_at = self.aware(record.date + timedelta(days=3), time(11, 0))
                record.hourly_rate = record.employee.overtime_hourly_rate
            record.save()
            touched += 1
        self.report('Overtime records annotated', touched)

    # ------------------------------------------------------------------
    # leave
    # ------------------------------------------------------------------
    def seed_leave_requests(self):
        rng = self.rng_for('leave-requests')
        leave_types = list(LeaveType.objects.filter(is_active=True))
        if not leave_types:
            return
        created = 0
        for employee in self.employees:
            for _ in range(rng.randint(2, 5)):
                # Every draw happens before anything can skip the record:
                # skipping mid-way would leave the generator at a different
                # point for the next iteration, and a re-run would then invent
                # a fresh set of requests instead of recognising its own.
                start = self.today - timedelta(days=rng.randint(5, 330))
                end = start + timedelta(days=rng.randint(0, 4))
                status = rng.choices(
                    ['APPROVED', 'PENDING', 'REJECTED', 'CANCELLED'],
                    weights=[62, 18, 12, 8],
                )[0]
                leave_type = rng.choice(leave_types)
                reason = rng.choice(LEAVE_REASONS)

                if start < employee.join_date or LeaveRequest.objects.filter(
                    employee=employee, start_date=start, end_date=end
                ).exists():
                    continue

                request = LeaveRequest(
                    employee=employee,
                    leave_type=leave_type,
                    start_date=start,
                    end_date=end,
                    reason=reason,
                    status=status,
                )
                if status in ('APPROVED', 'REJECTED'):
                    request.approved_by = self.admin
                    request.approved_at = self.aware(start - timedelta(days=1), time(15, 30))
                if status == 'REJECTED':
                    request.rejection_reason = 'Office work load during the requested period.'
                request.save()
                created += 1
        self.report('Leave requests created', created)

    def seed_leave_balances(self):
        """One balance row per employee/type/year, with the used days taken
        from the approved requests that actually exist."""
        leave_types = list(LeaveType.objects.filter(is_active=True))
        years = sorted({self.today.year, self.today.year - 1})
        created = updated = 0
        for employee in self.employees:
            for year in years:
                for leave_type in leave_types:
                    used = sum(
                        (request.total_days or Decimal('0'))
                        for request in LeaveRequest.objects.filter(
                            employee=employee, leave_type=leave_type,
                            status='APPROVED', start_date__year=year,
                        )
                    )
                    total = Decimal(leave_type.days_per_year)
                    used = min(Decimal(used), total)
                    balance, made = LeaveBalance.objects.get_or_create(
                        employee=employee, leave_type=leave_type, year=year,
                        defaults={
                            'total_days': total,
                            'used_days': used,
                            'remaining_days': total - used,
                        },
                    )
                    if made:
                        created += 1
                    else:
                        balance.total_days = total
                        balance.used_days = used
                        balance.remaining_days = total - used
                        balance.save()
                        updated += 1
        self.report('Leave balances created', created)
        self.report('Leave balances refreshed', updated)

    def seed_absences(self):
        """An absence for days where the employee was expected but never punched."""
        rng = self.rng_for('absences')
        created = 0
        window_start = self.today - timedelta(days=120)
        for employee in self.employees:
            punched = {
                timezone.localtime(stamp).date()
                for stamp in Attendance.objects.filter(
                    employee=employee, timestamp__gte=self.aware(window_start, time.min)
                ).values_list('timestamp', flat=True)
            }
            day = max(window_start, employee.join_date)
            missed = [
                candidate for candidate in (
                    day + timedelta(days=offset)
                    for offset in range((self.today - day).days)
                )
                if not is_weekend(candidate) and candidate not in punched
            ]
            for absent_day in rng.sample(missed, min(len(missed), rng.randint(1, 4))):
                if Absence.objects.filter(employee=employee, date=absent_day).exists():
                    continue
                status = rng.choices(['APPROVED', 'PENDING', 'REJECTED'], weights=[65, 25, 10])[0]
                absence = Absence(
                    employee=employee, date=absent_day,
                    reason=rng.choice(ABSENCE_REASONS), status=status,
                )
                if status != 'PENDING':
                    absence.approved_by = self.admin
                    absence.approved_at = self.aware(absent_day + timedelta(days=1), time(10, 30))
                if status == 'REJECTED':
                    absence.rejection_reason = 'No prior information given.'
                absence.save()
                created += 1
        self.report('Absence records created', created)

    # ------------------------------------------------------------------
    # travel
    # ------------------------------------------------------------------
    def seed_travel_orders(self):
        rng = self.rng_for('travel')
        created = itineraries = expenses = 0
        for employee in self.employees:
            for _ in range(rng.randint(0, 2)):
                # As with leave: draw everything first, skip afterwards, so a
                # re-run walks the same sequence and recognises its own records.
                travel_type, destination, purpose = rng.choice(DESTINATIONS)
                start_day = self.today - timedelta(days=rng.randint(3, 300))
                nights = rng.randint(1, 6)
                start = self.aware(start_day, time(rng.choice([6, 7, 8, 9]), 0))
                end = self.aware(start_day + timedelta(days=nights), time(rng.choice([16, 17, 18]), 0))
                status = rng.choices(
                    ['APPROVED', 'PENDING', 'REJECTED', 'CANCELLED'],
                    weights=[65, 15, 12, 8],
                )[0]
                steps = rng.randint(3, len(ITINERARY_STEPS))
                transportation = Decimal(rng.randrange(3000, 45000, 500))
                accommodation = Decimal(rng.randrange(2000, 9000, 500) * (nights + 1))
                meals = Decimal(rng.randrange(800, 2500, 100) * (nights + 1))
                other = Decimal(rng.randrange(0, 4000, 250))
                expense_status = rng.choices(
                    ['APPROVED', 'PENDING', 'REJECTED'], weights=[70, 22, 8]
                )[0]

                if start_day < employee.join_date or TravelOrder.objects.filter(
                    employee=employee, destination=destination, start_date=start
                ).exists():
                    continue

                per_day = 6000 if travel_type == 'INTERNATIONAL' else 2500
                order = TravelOrder(
                    employee=employee, travel_type=travel_type, destination=destination,
                    purpose=purpose, start_date=start, end_date=end, status=status,
                    estimated_cost=Decimal(per_day * (nights + 1)),
                )
                if status in ('APPROVED', 'REJECTED'):
                    order.approved_by = self.admin
                    order.approved_at = start - timedelta(days=2)
                if status == 'REJECTED':
                    order.rejection_reason = 'Budget not available for this quarter.'
                order.save()
                created += 1

                for index, (activity, location) in enumerate(ITINERARY_STEPS[:steps]):
                    TravelItinerary.objects.create(
                        travel_order=order,
                        date_time=start + timedelta(days=min(index, nights), hours=index % 3),
                        activity=activity,
                        location=f'{location}, {destination}' if location != 'Kathmandu' else location,
                    )
                    itineraries += 1

                # Only a finished, approved trip has a bill to settle.
                if status == 'APPROVED' and end.date() < self.today:
                    expense = TravelExpense(
                        travel_order=order,
                        transportation=transportation, accommodation=accommodation,
                        meals=meals, other_expenses=other,
                        other_description='Local conveyance and communication.' if other else None,
                        status=expense_status,
                    )
                    if expense_status != 'PENDING':
                        expense.approved_by = self.admin
                        expense.approved_at = end + timedelta(days=4)
                    if expense_status == 'APPROVED':
                        expense.payment_date = end + timedelta(days=10)
                        expense.payment_notes = 'Paid through the office account.'
                    if expense_status == 'REJECTED':
                        expense.rejection_reason = 'Receipts not attached.'
                    expense.save()
                    expenses += 1

        self.report('Travel orders created', created)
        self.report('Itinerary entries created', itineraries)
        self.report('Expense claims created', expenses)

    # ------------------------------------------------------------------
    # devices
    # ------------------------------------------------------------------
    def seed_device_commands(self):
        rng = self.rng_for('device-commands')
        device = Device.objects.first()
        if device is None or DeviceCommand.objects.exists():
            return
        samples = [
            ('CHECK', DeviceCommand.STATUS_DONE, 0, 'OK'),
            ('INFO', DeviceCommand.STATUS_DONE, 0, 'OK'),
            ('DATA QUERY ATTLOG', DeviceCommand.STATUS_DONE, 0, 'OK'),
            ('CLEAR LOG', DeviceCommand.STATUS_FAILED, -1, 'Device busy'),
            ('REBOOT', DeviceCommand.STATUS_SENT, None, None),
            ('DATA UPDATE USER PIN=1\tName=Pradeep Sapkota', DeviceCommand.STATUS_PENDING, None, None),
        ]
        for offset, (command, status, code, response) in enumerate(samples):
            created_at = timezone.now() - timedelta(hours=offset * 5 + rng.randint(0, 4))
            record = DeviceCommand.objects.create(
                device=device, command=command, status=status,
                return_code=code, response=response, created_by=self.admin,
            )
            DeviceCommand.objects.filter(pk=record.pk).update(created_at=created_at)
            if status in (DeviceCommand.STATUS_DONE, DeviceCommand.STATUS_FAILED):
                DeviceCommand.objects.filter(pk=record.pk).update(
                    sent_at=created_at + timedelta(seconds=20),
                    completed_at=created_at + timedelta(seconds=45),
                )
            elif status == DeviceCommand.STATUS_SENT:
                DeviceCommand.objects.filter(pk=record.pk).update(
                    sent_at=created_at + timedelta(seconds=20)
                )
        self.report('Device commands created', len(samples))
