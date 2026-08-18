"""Queue a data-pull command for a device running in push (ADMS/WDMS) mode.

In push mode the server cannot connect to the device, so pulling data means
queueing a request the device collects on its next poll (every ~10 seconds).
This is the scriptable counterpart to the Devices admin actions, and the only
place a date-ranged attendance query is exposed.

Examples:

    # What can I talk to?
    python manage.py adms_pull --list

    # Pull the user/employee table (unknown PINs become unlinked enrolments)
    python manage.py adms_pull --serial ZK8000123456 --users

    # Backfill a weekend the device spent offline
    python manage.py adms_pull --serial ZK8000123456 \\
        --attlog-from 2026-08-15 --attlog-to 2026-08-17

    # Re-request the entire attendance history
    python manage.py adms_pull --serial ZK8000123456 --all-attendance

    # Every push device at once
    python manage.py adms_pull --all-devices --users

Nothing here is destructive: the device is only ever asked to *send* data, and
re-sent punches are de-duplicated on arrival.
"""
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from devices.models import Device


class Command(BaseCommand):
    help = 'Queue a data-pull command for ADMS/WDMS push-mode devices.'

    def add_arguments(self, parser):
        target = parser.add_argument_group('which device')
        target.add_argument('--serial', help='Device serial number (SN).')
        target.add_argument('--name', help='Device name, if you do not know the serial.')
        target.add_argument(
            '--all-devices', action='store_true',
            help='Every active device using the push protocol.',
        )
        target.add_argument(
            '--list', action='store_true',
            help='List devices and their mode, then exit.',
        )

        what = parser.add_argument_group('what to pull')
        what.add_argument(
            '--users', action='store_true',
            help="Upload the device's user table (DATA QUERY USERINFO).",
        )
        what.add_argument('--pin', help='With --users, request just this PIN.')
        what.add_argument('--attlog-from', metavar='YYYY-MM-DD', help='Start of an attendance range.')
        what.add_argument('--attlog-to', metavar='YYYY-MM-DD', help='End of an attendance range (inclusive).')
        what.add_argument(
            '--all-attendance', action='store_true',
            help='Clear the resume point so the device re-sends its whole log.',
        )

    def handle(self, *args, **options):
        if options['list']:
            return self._list_devices()

        devices = self._resolve_devices(options)
        actions = self._resolve_actions(options)

        for device in devices:
            for describe, run in actions:
                command = run(device)
                self.stdout.write(self.style.SUCCESS(
                    f'{device.name} ({device.serial_number}): queued {describe} '
                    f'[command #{command.id}]'
                ))

        self.stdout.write(
            '\nDevices collect queued commands on their next poll, usually within '
            'seconds. Watch the outcome in the admin under Device Commands: '
            'return code 0 (or 1 on some firmware) means the device accepted it.'
        )
        if options['all_attendance']:
            self.stdout.write(self.style.WARNING(
                'Note: the resume point is only read when a device handshakes, which '
                'may not happen immediately. Queue a REBOOT from the admin to force one.'
            ))

    # -- targets ----------------------------------------------------------
    def _list_devices(self):
        devices = Device.objects.order_by('name')
        if not devices:
            self.stdout.write('No devices registered.')
            return
        self.stdout.write(f'{"NAME":<28} {"SERIAL":<22} {"MODE":<14} ACTIVE')
        for device in devices:
            if device.push_enabled:
                mode = 'push/online' if device.is_online else 'push/offline'
            else:
                mode = 'pull (SDK)'
            self.stdout.write(
                f'{device.name[:27]:<28} {(device.serial_number or "-"):<22} '
                f'{mode:<14} {"yes" if device.is_active else "no"}'
            )

    def _resolve_devices(self, options):
        if options['all_devices']:
            devices = list(Device.objects.filter(is_active=True, push_enabled=True))
            if not devices:
                raise CommandError('No active push-mode devices found.')
            return devices

        if options['serial']:
            lookup = {'serial_number': options['serial']}
        elif options['name']:
            lookup = {'name': options['name']}
        else:
            raise CommandError(
                'Choose a device with --serial, --name or --all-devices '
                '(or use --list to see what is registered).'
            )

        device = Device.objects.filter(**lookup).first()
        if device is None:
            raise CommandError(f'No device matching {lookup}. Try --list.')
        if not device.push_enabled:
            raise CommandError(
                f'{device.name} is not using the push protocol, so it has no command '
                'queue. Devices reachable over TCP can be pulled directly with the '
                '"Sync Attendance" / "Sync Users" admin buttons instead.'
            )
        return [device]

    # -- operations -------------------------------------------------------
    def _resolve_actions(self, options):
        actions = []

        if options['users']:
            pin = options['pin']
            actions.append((
                f'user query{f" for PIN {pin}" if pin else " (all users)"}',
                lambda device, pin=pin: device.queue_user_query(pin=pin),
            ))

        start, end = options['attlog_from'], options['attlog_to']
        if start or end:
            if not (start and end):
                raise CommandError('--attlog-from and --attlog-to must be given together.')
            start_date, end_date = _parse_date(start), _parse_date(end)
            if end_date < start_date:
                raise CommandError('--attlog-to is earlier than --attlog-from.')
            actions.append((
                f'attendance query {start_date} to {end_date}',
                lambda device: device.queue_attlog_query(start_date, end_date),
            ))

        if options['all_attendance']:
            actions.append((
                'full attendance re-send (resume point cleared)',
                lambda device: device.request_all_attendance(),
            ))

        if not actions:
            raise CommandError(
                'Nothing to pull. Choose --users, --attlog-from/--attlog-to, '
                'or --all-attendance.'
            )
        return actions


def _parse_date(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        raise CommandError(f'Not a date in YYYY-MM-DD form: {value!r}')
