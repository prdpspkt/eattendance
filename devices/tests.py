from datetime import date, datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from attendance.models import Attendance, DailyAttendance
from core.models import Employee, EmployeeShift, Shift
from devices.adms import parse_attlog, parse_operlog_users
from devices.ingest import parse_device_datetime, record_punch
from devices.models import Device, DeviceCommand, EmployeeDevice

User = get_user_model()

SN = 'ZK8000123456'
MONDAY = date(2026, 3, 2)


class AdmsTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.device = Device.objects.create(
            name='Main Door', ip_address='192.168.1.201', serial_number=SN, is_active=True,
        )
        self.user = User.objects.create_user(
            username='rmaharjan', first_name='Rita', last_name='Maharjan',
            password='test-pass-123',
        )
        self.employee = Employee.objects.create(
            user=self.user, employee_id='EMP0001', join_date=date(2024, 1, 1), device_uid=1,
        )
        self.enrollment = EmployeeDevice.objects.create(
            employee=self.employee, device=self.device, device_uid=1, user_name='Rita',
        )

    def post_attlog(self, body, stamp='9999'):
        return self.client.post(
            f'/iclock/cdata?SN={SN}&table=ATTLOG&Stamp={stamp}',
            data=body, content_type='text/plain',
        )


class ParserTests(TestCase):
    def test_parse_attlog_tab_separated(self):
        body = (
            "1\t2026-03-02 09:07:12\t0\t1\t0\t0\t0\n"
            "2\t2026-03-02 18:01:44\t1\t1\t0\t0\t0\n"
        )
        punches = parse_attlog(body)

        self.assertEqual(len(punches), 2)
        self.assertEqual(punches[0]['device_uid'], '1')
        self.assertEqual(punches[0]['punch_type'], '0')
        self.assertEqual(timezone.localtime(punches[0]['timestamp']).hour, 9)
        self.assertEqual(punches[1]['punch_type'], '1')

    def test_parse_attlog_space_separated_variant(self):
        punches = parse_attlog("1 2026-03-02 09:07:12 0 1 0")

        self.assertEqual(len(punches), 1)
        self.assertEqual(punches[0]['device_uid'], '1')

    def test_parse_attlog_skips_malformed_lines(self):
        body = (
            "1\t2026-03-02 09:07:12\t0\t1\n"
            "garbage\n"
            "\n"
            "2\tnot-a-date\t0\t1\n"
        )
        punches = parse_attlog(body)

        self.assertEqual(len(punches), 1)

    def test_parse_operlog_users(self):
        body = (
            "USER PIN=7\tName=Bikash Khadka\tPri=0\tPasswd=\tCard=0\tGrp=1\n"
            "FP PIN=7\tFID=0\tValid=1\n"
            "OPLOG 4\t1\t2026-03-02 09:00:00\t0\t0\n"
        )
        users = parse_operlog_users(body)

        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]['device_uid'], '7')
        self.assertEqual(users[0]['user_name'], 'Bikash Khadka')

    def test_parse_device_datetime_formats(self):
        for raw in ('2026-03-02 09:07:12', '2026-03-02T09:07:12', '20260302090712'):
            parsed = parse_device_datetime(raw)
            self.assertIsNotNone(parsed, raw)
            self.assertEqual(timezone.localtime(parsed).hour, 9)

        self.assertIsNone(parse_device_datetime('not a date'))
        self.assertIsNone(parse_device_datetime(''))


class HandshakeTests(AdmsTestCase):
    def test_handshake_returns_configuration(self):
        response = self.client.get(f'/iclock/cdata?SN={SN}&options=all&pushver=2.2.14')
        body = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn(f'GET OPTION FROM: {SN}', body)
        self.assertIn('ATTLOGStamp=', body)
        self.assertIn('OPERLOGStamp=', body)
        self.assertIn('Realtime=1', body)
        self.assertIn('ServerVer=', body)

    def test_handshake_records_last_seen_and_push_mode(self):
        self.client.get(f'/iclock/cdata?SN={SN}&options=all')

        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.last_seen)
        self.assertTrue(self.device.push_enabled)
        self.assertTrue(self.device.is_online)

    def test_request_without_serial_is_rejected(self):
        response = self.client.get('/iclock/cdata?options=all')

        self.assertEqual(response.status_code, 403)

    def test_unknown_serial_is_registered_inactive_and_refused(self):
        response = self.client.get('/iclock/cdata?SN=BRANDNEW999&options=all')

        # 401 keeps the records on the device until an admin approves it.
        self.assertEqual(response.status_code, 401)
        device = Device.objects.get(serial_number='BRANDNEW999')
        self.assertFalse(device.is_active)
        self.assertTrue(device.push_enabled)

    @override_settings(ADMS_AUTO_REGISTER_DEVICES=False)
    def test_unknown_serial_rejected_when_auto_register_disabled(self):
        response = self.client.get('/iclock/cdata?SN=NOPE123&options=all')

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Device.objects.filter(serial_number='NOPE123').exists())

    def test_pull_configured_device_is_adopted_by_ip(self):
        legacy = Device.objects.create(name='Back Door', ip_address='10.0.0.5')

        self.client.get('/iclock/cdata?SN=NEWSERIAL1&options=all', REMOTE_ADDR='10.0.0.5')

        legacy.refresh_from_db()
        self.assertEqual(legacy.serial_number, 'NEWSERIAL1')
        self.assertEqual(Device.objects.filter(name='Back Door').count(), 1)


class AttendanceUploadTests(AdmsTestCase):
    def test_attlog_upload_creates_punches(self):
        response = self.post_attlog(
            "1\t2026-03-02 09:07:12\t0\t1\t0\t0\t0\n"
            "1\t2026-03-02 18:01:44\t1\t1\t0\t0\t0\n"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('OK', response.content.decode())
        self.assertEqual(Attendance.objects.filter(employee=self.employee).count(), 2)

    def test_duplicate_push_is_ignored(self):
        line = "1\t2026-03-02 09:07:12\t0\t1\t0\t0\t0\n"
        self.post_attlog(line)
        self.post_attlog(line)

        self.assertEqual(Attendance.objects.count(), 1)

    def test_stamp_is_remembered(self):
        self.post_attlog("1\t2026-03-02 09:07:12\t0\t1\t0\t0\t0\n", stamp='123456')

        self.device.refresh_from_db()
        self.assertEqual(self.device.attlog_stamp, '123456')

    def test_unknown_uid_becomes_unlinked_enrollment(self):
        self.post_attlog("99\t2026-03-02 09:07:12\t0\t1\t0\t0\t0\n")

        enrollment = EmployeeDevice.objects.get(device=self.device, device_uid=99)
        self.assertIsNone(enrollment.employee)
        # No employee to attribute the punch to yet, so nothing is stored.
        self.assertEqual(Attendance.objects.count(), 0)

    def test_push_rolls_up_into_daily_attendance(self):
        """The whole point: pushed punches become usable summaries immediately."""
        shift = Shift.objects.create(
            name='Day Shift', start_time=time(9, 0), end_time=time(18, 0),
            late_grace_minutes=15, early_exit_minutes=15, break_duration_minutes=60,
        )
        EmployeeShift.objects.create(
            employee=self.employee, shift=shift, effective_date=date(2024, 1, 1),
        )

        self.post_attlog(
            "1\t2026-03-02 09:00:00\t0\t1\t0\t0\t0\n"
            "1\t2026-03-02 18:00:00\t1\t1\t0\t0\t0\n"
        )

        daily = DailyAttendance.objects.get(employee=self.employee, date=MONDAY)
        self.assertEqual(float(daily.working_hours), 8.0)
        self.assertEqual(daily.status, 'PRESENT')

    @override_settings(ADMS_PROCESS_ON_PUSH=False)
    def test_inline_processing_can_be_disabled(self):
        self.post_attlog("1\t2026-03-02 09:00:00\t0\t1\t0\t0\t0\n")

        self.assertEqual(Attendance.objects.count(), 1)
        self.assertEqual(DailyAttendance.objects.count(), 0)

    def test_inactive_device_upload_is_refused(self):
        self.device.is_active = False
        self.device.save()

        response = self.post_attlog("1\t2026-03-02 09:07:12\t0\t1\t0\t0\t0\n")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_operlog_upload_registers_enrollment(self):
        response = self.client.post(
            f'/iclock/cdata?SN={SN}&table=OPERLOG&Stamp=42',
            data="USER PIN=12\tName=Sita Karki\tPri=0\tGrp=1\n",
            content_type='text/plain',
        )

        self.assertEqual(response.status_code, 200)
        enrollment = EmployeeDevice.objects.get(device=self.device, device_uid=12)
        self.assertEqual(enrollment.user_name, 'Sita Karki')

    def test_unhandled_table_is_acknowledged(self):
        response = self.client.post(
            f'/iclock/cdata?SN={SN}&table=ATTPHOTO', data='binary-ish',
            content_type='text/plain',
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('OK', response.content.decode())


class CommandQueueTests(AdmsTestCase):
    def test_idle_poll_returns_ok(self):
        response = self.client.get(f'/iclock/getrequest?SN={SN}')

        self.assertEqual(response.content.decode().strip(), 'OK')

    def test_pending_commands_are_dispatched_once(self):
        command = self.device.queue_command('CHECK')

        response = self.client.get(f'/iclock/getrequest?SN={SN}')

        self.assertEqual(response.content.decode().strip(), f'C:{command.id}:CHECK')
        command.refresh_from_db()
        self.assertEqual(command.status, DeviceCommand.STATUS_SENT)
        self.assertIsNotNone(command.sent_at)

        # A second poll must not repeat an already dispatched command.
        response = self.client.get(f'/iclock/getrequest?SN={SN}')
        self.assertEqual(response.content.decode().strip(), 'OK')

    def test_command_result_is_recorded(self):
        command = self.device.queue_command('CHECK')
        self.client.get(f'/iclock/getrequest?SN={SN}')

        response = self.client.post(
            f'/iclock/devicecmd?SN={SN}',
            data=f'ID={command.id}&Return=0&CMD=CHECK',
            content_type='text/plain',
        )

        self.assertEqual(response.status_code, 200)
        command.refresh_from_db()
        self.assertEqual(command.status, DeviceCommand.STATUS_DONE)
        self.assertEqual(command.return_code, 0)

    def test_failed_command_result(self):
        command = self.device.queue_command('REBOOT')

        self.client.post(
            f'/iclock/devicecmd?SN={SN}', data=f'ID={command.id}&Return=-1&CMD=REBOOT',
            content_type='text/plain',
        )

        command.refresh_from_db()
        self.assertEqual(command.status, DeviceCommand.STATUS_FAILED)

    def test_poll_limit_is_respected(self):
        for index in range(4):
            self.device.queue_command(f'INFO {index}')

        with override_settings(ADMS_MAX_COMMANDS_PER_POLL=2):
            response = self.client.get(f'/iclock/getrequest?SN={SN}')

        self.assertEqual(len(response.content.decode().strip().splitlines()), 2)

    def test_push_user_command_format(self):
        command = self.device.push_user(self.enrollment)

        self.assertIn('DATA UPDATE USER PIN=1', command.command)
        self.assertIn('Name=Rita Maharjan', command.command)

    def test_ping_endpoint(self):
        response = self.client.get(f'/iclock/ping?SN={SN}')

        self.assertEqual(response.content.decode().strip(), 'OK')


class IngestTests(AdmsTestCase):
    def test_record_punch_outcomes(self):
        stamp = timezone.make_aware(datetime.combine(MONDAY, time(9, 0)))

        self.assertEqual(record_punch(self.device, 1, stamp), 'created')
        self.assertEqual(record_punch(self.device, 1, stamp), 'duplicate')
        self.assertEqual(record_punch(self.device, 55, stamp), 'unlinked')
        self.assertEqual(record_punch(self.device, 'abc', stamp), 'invalid')
        self.assertEqual(record_punch(self.device, 1, None), 'invalid')

    def test_bytes_device_uid_is_handled(self):
        stamp = timezone.make_aware(datetime.combine(MONDAY, time(9, 30)))

        self.assertEqual(record_punch(self.device, b'1', stamp), 'created')

    def test_same_punch_from_both_transports_is_stored_once(self):
        """Pull and push assign different record ids; dedupe must ignore them."""
        stamp = timezone.make_aware(datetime.combine(MONDAY, time(9, 0)))

        record_punch(self.device, 1, stamp, uid=4321)   # pull SDK record id
        record_punch(self.device, 1, stamp, uid=1)      # push (no record id)

        self.assertEqual(Attendance.objects.count(), 1)
