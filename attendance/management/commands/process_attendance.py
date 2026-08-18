"""Build daily attendance summaries from raw device punches.

Raw punches are useless to the rest of the app until they are rolled up into
DailyAttendance rows. Celery Beat does this nightly, but this command lets you
run it on demand and backfill history without Redis or a worker.

Examples:
    python manage.py process_attendance                     # yesterday
    python manage.py process_attendance --date 2026-03-29
    python manage.py process_attendance --from 2024-09-01 --to 2026-03-31
    python manage.py process_attendance --all               # every day with punches
    python manage.py process_attendance --days 30 --employee EMP0001
"""
from datetime import date, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Min, Max
from django.utils import timezone

from attendance.models import Attendance
from attendance.tasks import process_day
from core.models import Employee


class Command(BaseCommand):
    help = 'Process raw attendance punches into daily attendance summaries'

    def add_arguments(self, parser):
        parser.add_argument('--date', help='Single date to process (YYYY-MM-DD)')
        parser.add_argument('--from', dest='date_from', help='Start of range (YYYY-MM-DD)')
        parser.add_argument('--to', dest='date_to', help='End of range (YYYY-MM-DD)')
        parser.add_argument('--days', type=int, help='Process the last N days (including today)')
        parser.add_argument(
            '--all', action='store_true',
            help='Process every day from the first punch in the database until today',
        )
        parser.add_argument(
            '--employee',
            help='Limit to a single employee (employee ID, e.g. EMP0001)',
        )

    def handle(self, *args, **options):
        start, end = self._resolve_range(options)

        employee = None
        if options['employee']:
            try:
                employee = Employee.objects.select_related('user').get(
                    employee_id=options['employee']
                )
            except Employee.DoesNotExist:
                raise CommandError(f"No employee with ID {options['employee']}")

        scope = employee.user.get_full_name() if employee else 'all active employees'
        total_days = (end - start).days + 1
        self.stdout.write(
            f"Processing {total_days} day(s) from {start} to {end} for {scope}..."
        )

        totals = {'processed': 0, 'skipped': 0, 'errors': 0}
        current = start
        while current <= end:
            result = process_day(current, employee=employee)
            for key in totals:
                totals[key] += result[key]
            if result['processed'] or result['errors']:
                line = f"  {current}: {result['processed']} processed"
                if result['errors']:
                    line += f", {result['errors']} errors"
                self.stdout.write(line)
            current += timedelta(days=1)

        style = self.style.WARNING if totals['errors'] else self.style.SUCCESS
        self.stdout.write(style(
            f"\nDone. {totals['processed']} summaries written, "
            f"{totals['skipped']} skipped, {totals['errors']} errors."
        ))

    def _resolve_range(self, options):
        today = timezone.localdate()

        if options['all']:
            bounds = Attendance.objects.aggregate(first=Min('timestamp'), last=Max('timestamp'))
            if not bounds['first']:
                raise CommandError('No attendance punches in the database yet.')
            start = timezone.localtime(bounds['first']).date()
            end = min(timezone.localtime(bounds['last']).date(), today)
            return start, end

        if options['days']:
            if options['days'] < 1:
                raise CommandError('--days must be 1 or more')
            return today - timedelta(days=options['days'] - 1), today

        if options['date']:
            single = self._parse(options['date'], '--date')
            return single, single

        if options['date_from'] or options['date_to']:
            start = self._parse(options['date_from'], '--from') if options['date_from'] else today
            end = self._parse(options['date_to'], '--to') if options['date_to'] else today
            if end < start:
                raise CommandError('--to must not be earlier than --from')
            return start, end

        # Default: yesterday, matching the nightly Celery Beat job.
        yesterday = today - timedelta(days=1)
        return yesterday, yesterday

    @staticmethod
    def _parse(value, flag):
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise CommandError(f'{flag} must be a date in YYYY-MM-DD format (got "{value}")')
