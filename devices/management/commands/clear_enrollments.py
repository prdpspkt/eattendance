from django.core.management.base import BaseCommand
from django.db import transaction
from devices.models import EmployeeDevice
from core.models import Employee


class Command(BaseCommand):
    help = 'Remove all enrolled user data from the application database (protects device database)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--keep-employees',
            action='store_true',
            dest='keep_employees',
            help='Keep employee accounts, only remove EmployeeDevice links',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            dest='force',
            help='Skip confirmation prompt',
        )

    def handle(self, *args, **options):
        # Get statistics
        total_enrollments = EmployeeDevice.objects.count()
        linked_count = EmployeeDevice.objects.exclude(employee__isnull=True).count()
        unlinked_count = EmployeeDevice.objects.filter(employee__isnull=True).count()

        self.stdout.write(self.style.WARNING('='*60))
        self.stdout.write(self.style.WARNING('WARNING: This will remove enrolled user data from the application'))
        self.stdout.write(self.style.WARNING('='*60))
        self.stdout.write(f"\nCurrent statistics:")
        self.stdout.write(f"  Total enrollments: {total_enrollments}")
        self.stdout.write(f"  Linked to employees: {linked_count}")
        self.stdout.write(f"  Unlinked enrollments: {unlinked_count}")

        if not options['keep_employees']:
            employees_to_delete = Employee.objects.filter(
                employee_devices__isnull=False
            ).distinct()
            self.stdout.write(f"  Employees with enrollments: {employees_to_delete.count()}")

        self.stdout.write(self.style.WARNING("\nThis action will:"))
        if options['keep_employees']:
            self.stdout.write("  - Remove all EmployeeDevice links")
            self.stdout.write("  - Keep all employee accounts intact")
        else:
            self.stdout.write("  - Remove all EmployeeDevice links")
            self.stdout.write("  - Delete employee accounts created from device enrollments")
            self.stdout.write("  - Keep device database untouched (biometric device data is safe)")

        self.stdout.write(self.style.ERROR("\nThis action CANNOT be undone!"))
        self.stdout.write(self.style.WARNING('='*60))

        # Confirm
        if not options['force']:
            confirm = input("\nType 'yes' to confirm: ")
            if confirm.lower() != 'yes':
                self.stdout.write(self.style.ERROR("Operation cancelled."))
                return

        # Execute deletion
        try:
            with transaction.atomic():
                if options['keep_employees']:
                    # Only remove EmployeeDevice links
                    deleted_enrollments = EmployeeDevice.objects.all().delete()[0]
                    self.stdout.write(self.style.SUCCESS(f"\n✓ Deleted {deleted_enrollments['devices.EmployeeDevice']} enrollment records"))
                    self.stdout.write(self.style.SUCCESS("✓ Employee accounts preserved"))
                else:
                    # Delete EmployeeDevice links and employee accounts
                    deleted_enrollments = EmployeeDevice.objects.all().delete()[0]

                    # Delete employees that were created from enrollments
                    deleted_employees = Employee.objects.filter(
                        employee_devices__isnull=True
                    ).delete()

                    self.stdout.write(self.style.SUCCESS(f"\n✓ Deleted {deleted_enrollments['devices.EmployeeDevice']} enrollment records"))
                    self.stdout.write(self.style.SUCCESS("✓ Deleted associated employee accounts"))

            self.stdout.write(self.style.SUCCESS("\n✓ Device database protected - biometric device data is safe"))
            self.stdout.write(self.style.SUCCESS("✓ Application database cleared successfully"))
            self.stdout.write(self.style.SUCCESS("\nYou can now re-sync users from devices to start fresh"))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"\n✗ Error: {str(e)}"))
            raise
