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
        leave_types = [
            {
                'name': 'Annual Leave',
                'code': 'AL',
                'description': 'Paid annual vacation leave',
                'days_per_year': 20,
                'is_paid': True,
                'requires_approval': True
            },
            {
                'name': 'Sick Leave',
                'code': 'SL',
                'description': 'Paid sick leave with medical certificate',
                'days_per_year': 14,
                'is_paid': True,
                'requires_approval': True
            },
            {
                'name': 'Casual Leave',
                'code': 'CL',
                'description': 'Paid casual leave for personal reasons',
                'days_per_year': 10,
                'is_paid': True,
                'requires_approval': True
            },
            {
                'name': 'Maternity Leave',
                'code': 'ML',
                'description': 'Paid maternity leave',
                'days_per_year': 90,
                'is_paid': True,
                'requires_approval': True
            },
            {
                'name': 'Paternity Leave',
                'code': 'PL',
                'description': 'Paid paternity leave',
                'days_per_year': 7,
                'is_paid': True,
                'requires_approval': True
            },
            {
                'name': 'Unpaid Leave',
                'code': 'UL',
                'description': 'Unpaid leave for various reasons',
                'days_per_year': 0,
                'is_paid': False,
                'requires_approval': True
            },
            {
                'name': 'Study Leave',
                'code': 'ST',
                'description': 'Leave for educational purposes',
                'days_per_year': 5,
                'is_paid': False,
                'requires_approval': True
            },
        ]

        for leave_data in leave_types:
            leave_type, created = LeaveType.objects.get_or_create(
                code=leave_data['code'],
                defaults={
                    'name': leave_data['name'],
                    'description': leave_data['description'],
                    'days_per_year': leave_data['days_per_year'],
                    'is_paid': leave_data['is_paid'],
                    'requires_approval': leave_data['requires_approval']
                }
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'Created leave type: {leave_type.name}'))

        self.stdout.write(self.style.SUCCESS('\nSample data initialized successfully!'))
        self.stdout.write('\nNext steps:')
        self.stdout.write('1. Create a superuser: python manage.py createsuperuser')
        self.stdout.write('2. Add ZKTeco devices via admin panel')
        self.stdout.write('3. Create users and employees')
        self.stdout.write('4. Assign shifts to employees')
