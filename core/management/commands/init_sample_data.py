from django.core.management.base import BaseCommand
from core.models import Department, Shift
from leaves.models import LeaveType


class Command(BaseCommand):
    help = 'Initialize sample data for the E-Attendance system'

    def handle(self, *args, **options):
        self.stdout.write('Initializing sample data...')

        # Create Departments
        departments = [
            {'name': 'Information Technology', 'description': 'IT and Software Development'},
            {'name': 'Human Resources', 'description': 'HR and Administration'},
            {'name': 'Finance', 'description': 'Finance and Accounting'},
            {'name': 'Marketing', 'description': 'Marketing and Sales'},
            {'name': 'Operations', 'description': 'Operations and Logistics'},
        ]

        for dept_data in departments:
            department, created = Department.objects.get_or_create(
                name=dept_data['name'],
                defaults={'description': dept_data['description']}
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created department: {department.name}'))

        # Create Shifts
        shifts = [
            {
                'name': 'Morning Shift',
                'start_time': '09:00',
                'end_time': '18:00',
                'late_grace_minutes': 15,
                'early_exit_minutes': 15,
                'break_duration_minutes': 60
            },
            {
                'name': 'General Shift',
                'start_time': '10:00',
                'end_time': '19:00',
                'late_grace_minutes': 15,
                'early_exit_minutes': 15,
                'break_duration_minutes': 60
            },
            {
                'name': 'Night Shift',
                'start_time': '20:00',
                'end_time': '05:00',
                'late_grace_minutes': 15,
                'early_exit_minutes': 15,
                'break_duration_minutes': 60
            },
            {
                'name': 'Flexi Shift',
                'start_time': '08:00',
                'end_time': '17:00',
                'late_grace_minutes': 30,
                'early_exit_minutes': 30,
                'break_duration_minutes': 60
            },
        ]

        for shift_data in shifts:
            shift, created = Shift.objects.get_or_create(
                name=shift_data['name'],
                defaults={
                    'start_time': shift_data['start_time'],
                    'end_time': shift_data['end_time'],
                    'late_grace_minutes': shift_data['late_grace_minutes'],
                    'early_exit_minutes': shift_data['early_exit_minutes'],
                    'break_duration_minutes': shift_data['break_duration_minutes']
                }
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created shift: {shift.name}'))

        # Create Leave Types
        #
        # Each carries the rule that governs it, not just a day count: whether
        # it is credited yearly or granted when an event happens, and what
        # becomes of an unused balance at the year end. The Leave Types page
        # is where these get changed when the regulations change.
        leave_types = [
            {
                'name': 'Home Leave', 'code': 'HL',
                'description': 'Credited yearly and accumulates up to a ceiling.',
                'accrual': 'YEARLY', 'days_per_year': 30,
                'carry_forward': 'CAPPED', 'max_accumulation_days': 180,
            },
            {
                'name': 'Sick Leave', 'code': 'SL',
                'description': 'Credited yearly and accumulates without limit.',
                'accrual': 'YEARLY', 'days_per_year': 12,
                'carry_forward': 'UNLIMITED',
            },
            {
                'name': 'Casual Leave', 'code': 'CL',
                'description': 'Credited yearly; anything unused lapses at the year end.',
                'accrual': 'YEARLY', 'days_per_year': 12,
                'carry_forward': 'NONE',
            },
            {
                'name': 'Festival Leave', 'code': 'FL',
                'description': 'Credited yearly for festivals; lapses at the year end.',
                'accrual': 'YEARLY', 'days_per_year': 6,
                'carry_forward': 'NONE',
            },
            {
                'name': 'Maternity Leave', 'code': 'ML',
                'description': 'Granted when the event happens, twice in a career.',
                'accrual': 'OCCASIONAL', 'days_per_occurrence': 98,
                'max_occurrences_lifetime': 2,
            },
            {
                'name': 'Parenting Leave', 'code': 'PL',
                'description': 'Granted when the event happens, twice in a career.',
                'accrual': 'OCCASIONAL', 'days_per_occurrence': 15,
                'max_occurrences_lifetime': 2,
            },
            {
                'name': 'Wedding Leave', 'code': 'WL',
                'description': "Granted for the employee's own wedding.",
                'accrual': 'OCCASIONAL', 'days_per_occurrence': 5,
            },
            {
                'name': 'Death Rituals Leave', 'code': 'DR',
                'description': 'Granted for the funeral rites of a close family member.',
                'accrual': 'OCCASIONAL', 'days_per_occurrence': 15,
                'max_occurrences_lifetime': 4,
            },
            {
                'name': 'Unpaid Leave', 'code': 'UL',
                'description': 'Unpaid leave for various reasons.',
                'accrual': 'OCCASIONAL', 'days_per_occurrence': 30,
                'is_paid': False,
            },
            {
                'name': 'Study Leave', 'code': 'ST',
                'description': 'Leave for educational purposes.',
                'accrual': 'OCCASIONAL', 'days_per_occurrence': 30,
                'is_paid': False,
            },
        ]

        for leave_data in leave_types:
            defaults = {
                'name': leave_data['name'],
                'description': leave_data['description'],
                'accrual': leave_data.get('accrual', 'YEARLY'),
                'days_per_year': leave_data.get('days_per_year', 0),
                'carry_forward': leave_data.get('carry_forward', 'NONE'),
                'max_accumulation_days': leave_data.get('max_accumulation_days'),
                'days_per_occurrence': leave_data.get('days_per_occurrence'),
                'max_occurrences_lifetime': leave_data.get('max_occurrences_lifetime'),
                'is_paid': leave_data.get('is_paid', True),
                'requires_approval': leave_data.get('requires_approval', True),
            }
            leave_type, created = LeaveType.objects.get_or_create(
                code=leave_data['code'], defaults=defaults
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created leave type: {leave_type.name}'))


        self.stdout.write(self.style.SUCCESS('\nSample data initialized successfully!'))
        self.stdout.write('\nNext steps:')
        self.stdout.write('1. Create a superuser: python manage.py createsuperuser')
        self.stdout.write('2. Add biometric devices via admin panel')
        self.stdout.write('3. Create users and employees')
        self.stdout.write('4. Assign shifts to employees')
