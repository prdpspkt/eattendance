"""Query-count guards for the ADMS hot path.

These are regression tests, not benchmarks. They assert that the number of
database round trips per device request stays flat instead of growing with the
size of the upload, because that is the property the tuning in
deploy/PERFORMANCE.md depends on - and it is the kind of property a single
innocuous-looking ``.filter()`` inside a loop silently destroys.

The exact numbers are upper bounds with a little headroom, not targets. If a
change makes one fail, work out whether the extra query is per-request (fine,
raise the bound) or per-punch (not fine, that is the thing being prevented).
"""
from datetime import date, datetime, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.utils import timezone

from attendance.models import Attendance
from core.models import Employee
from devices.ingest import record_punches
from devices.models import Device, EmployeeDevice

User = get_user_model()

SN = 'ZKPERF000001'


@override_settings(ADMS_PROCESS_ON_PUSH=False)
class AdmsQueryCountTests(TestCase):
    """The per-request cost of the endpoints devices hit constantly."""

    def setUp(self):
        cache.clear()  # device and last_seen caches are process-wide
        self.device = Device.objects.create(
            name='Perf Door', ip_address='192.168.9.9',
            serial_number=SN, is_active=True, push_enabled=True,
        )
        self.employees = []
        for index in range(1, 6):
            user = User.objects.create_user(
                username=f'perfuser{index}', first_name='Perf', last_name=f'User{index}',
            )
            employee = Employee.objects.create(
                user=user, employee_id=f'PERF{index:04d}',
                join_date=date(2024, 1, 1), device_uid=index,
            )
            EmployeeDevice.objects.create(
                employee=employee, device=self.device, device_uid=index,
            )
            self.employees.append(employee)

    def test_heartbeat_is_cheap_and_mostly_query_free(self):
        """A device polling for commands must not write to the database.

        Terminals poll every few seconds. The first request resolves and caches
        the device; subsequent ones should not re-read it, and - crucially -
        should not write last_seen, because SQLite has a single writer and
        heartbeats would otherwise crowd out the punch inserts.
        """
        # Two warm-up requests, not one. The first contact from a device does
        # persist last_seen, and that write fires the post_save receiver that
        # invalidates the cached device - so the second request re-reads and
        # re-caches it. From there the throttle holds and the steady state is
        # query-free. One extra read per last_seen write (every 30s per
        # device) is the intended cost.
        self.client.get(f'/iclock/getrequest?SN={SN}')
        self.client.get(f'/iclock/getrequest?SN={SN}')

        with CaptureQueriesContext(connection) as captured:
            for _ in range(10):
                response = self.client.get(f'/iclock/getrequest?SN={SN}')
                self.assertEqual(response.status_code, 200)

        writes = [
            query['sql'] for query in captured.captured_queries
            if query['sql'].lstrip().upper().startswith(('UPDATE', 'INSERT'))
        ]
        self.assertEqual(
            writes, [],
            f"heartbeat polls wrote to the database: {writes}",
        )
        # One command lookup per poll, and nothing else: no device re-read.
        self.assertLessEqual(
            len(captured.captured_queries), 10,
            f"expected <=1 query per poll, got {len(captured.captured_queries)}",
        )

    def test_punch_upload_query_count_does_not_scale_with_batch_size(self):
        """The whole point of batching: 5 punches and 50 cost the same trips."""
        base = timezone.make_aware(datetime(2026, 4, 6, 9, 0, 0))

        def upload(count, minute_offset):
            lines = []
            for index in range(count):
                employee_index = (index % 5) + 1
                stamp = base + timedelta(minutes=minute_offset + index)
                lines.append(
                    f"{employee_index}\t{stamp.strftime('%Y-%m-%d %H:%M:%S')}\t0\t1"
                )
            body = '\n'.join(lines)
            with CaptureQueriesContext(connection) as captured:
                response = self.client.post(
                    f'/iclock/cdata?SN={SN}&table=ATTLOG&Stamp=1',
                    data=body, content_type='text/plain',
                )
            self.assertEqual(response.status_code, 200)
            return len(captured.captured_queries)

        small = upload(5, 0)
        large = upload(50, 1000)

        self.assertEqual(Attendance.objects.count(), 55)
        self.assertLessEqual(
            large, small + 2,
            f"query count grew with batch size: {small} for 5 punches, "
            f"{large} for 50. Something is querying per punch again.",
        )
        self.assertLessEqual(
            large, 12,
            f"a punch upload should cost a fixed handful of queries, got {large}",
        )


class RecordPunchesBatchTests(TestCase):
    """Batch semantics that the per-punch implementation used to give for free."""

    def setUp(self):
        self.device = Device.objects.create(
            name='Batch Door', ip_address='192.168.9.10',
            serial_number='ZKPERF000002', is_active=True,
        )
        user = User.objects.create_user(username='batchuser', first_name='Batch')
        self.employee = Employee.objects.create(
            user=user, employee_id='BATCH001', join_date=date(2024, 1, 1), device_uid=7,
        )
        EmployeeDevice.objects.create(
            employee=self.employee, device=self.device, device_uid=7,
        )

    def _punch(self, minute, device_uid=7):
        return {
            'device_uid': device_uid,
            'timestamp': timezone.make_aware(datetime(2026, 4, 7, 9, minute, 0)),
            'punch_type': 0,
        }

    def test_duplicates_within_a_single_batch_are_collapsed(self):
        """A device can repeat a record inside one upload."""
        counts, touched = record_punches(
            self.device, [self._punch(5), self._punch(5), self._punch(6)]
        )
        self.assertEqual(counts['created'], 2)
        self.assertEqual(counts['duplicate'], 1)
        self.assertEqual(Attendance.objects.count(), 2)
        self.assertEqual(len(touched), 1)

    def test_batch_mixes_outcomes_without_losing_valid_punches(self):
        """One bad line must not cost the good ones in the same upload."""
        counts, _ = record_punches(self.device, [
            self._punch(10),                      # created
            self._punch(11, device_uid=999),      # unlinked: unknown UID
            {'device_uid': 7, 'timestamp': None},  # invalid
            {'device_uid': 'abc', 'timestamp': timezone.now()},  # invalid
        ])
        self.assertEqual(counts, {
            'created': 1, 'duplicate': 0, 'unlinked': 1, 'invalid': 2,
        })
        self.assertEqual(Attendance.objects.count(), 1)
        # The unknown UID is kept as an unlinked enrolment for an admin to
        # attach later, rather than being dropped.
        self.assertTrue(
            EmployeeDevice.objects.filter(device=self.device, device_uid=999).exists()
        )

    def test_replaying_an_upload_creates_nothing(self):
        """Devices re-send records they are unsure we received."""
        batch = [self._punch(20), self._punch(21)]
        record_punches(self.device, batch)
        counts, touched = record_punches(self.device, batch)

        self.assertEqual(counts['created'], 0)
        self.assertEqual(counts['duplicate'], 2)
        self.assertEqual(touched, set())
        self.assertEqual(Attendance.objects.count(), 2)
