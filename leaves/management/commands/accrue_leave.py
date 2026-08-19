"""Credit the year's leave and carry forward what survives.

Run this once at the start of each leave year - or any time a limit changes,
since it recomputes rather than increments and so is safe to run twice.
"""
from django.core.management.base import BaseCommand

from core.models import Employee
from leaves import policy
from leaves.models import LeaveType


class Command(BaseCommand):
    help = "Rebuild leave balances: carry forward, credit the year's entitlement, deduct approved leave."

    def add_arguments(self, parser):
        parser.add_argument(
            '--year', type=int, default=None,
            help='Leave year to rebuild up to (default: the current leave year).',
        )
        parser.add_argument(
            '--employee', default=None,
            help='Limit to one employee, by employee ID (e.g. EMP0007).',
        )
        parser.add_argument(
            '--include-inactive', action='store_true',
            help='Include employees who are not ACTIVE.',
        )

    def handle(self, *args, **options):
        year = options['year'] or policy.current_leave_year()

        employees = Employee.objects.select_related('user')
        if not options['include_inactive']:
            employees = employees.filter(employment_status='ACTIVE')
        if options['employee']:
            employees = employees.filter(employee_id=options['employee'])
            if not employees.exists():
                self.stderr.write(f"No employee with ID {options['employee']}.")
                return

        types = list(LeaveType.objects.filter(
            is_active=True, accrual=LeaveType.Accrual.YEARLY
        ))
        if not types:
            self.stdout.write(self.style.WARNING(
                'No yearly leave types are configured - nothing to accrue.'
            ))
            return

        self.stdout.write(f'Rebuilding leave balances up to {year} for {employees.count()} employees...')
        for leave_type in types:
            self.stdout.write(f'  {leave_type.name}: {leave_type.policy_summary}')

        written = policy.rebuild_all(upto_year=year, employees=employees, leave_types=types)
        self.stdout.write(self.style.SUCCESS(f'{written} entitlement rows written.'))
