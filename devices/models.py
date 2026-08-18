from django.db import models
from django.utils import timezone

from core.models import Employee

class EmployeeDevice(models.Model):
    """Intermediate model for Employee-Device many-to-many relationship"""
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='employee_devices', blank=True, null=True)
    device = models.ForeignKey('Device', on_delete=models.CASCADE, related_name='device_employees')
    device_uid = models.IntegerField(help_text="User ID on this specific device")
    user_name = models.CharField(max_length=100, blank=True, null=True, help_text="User name as stored on the device")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'employee_devices'
        unique_together = ['device', 'device_uid']
        verbose_name = 'Employee Device'
        verbose_name_plural = 'Employee Devices'

    def __str__(self):
        if self.employee:
            return f"{self.employee.user.get_full_name()} - {self.device.name} (UID: {self.device_uid})"
        name = self.user_name or f"UID {self.device_uid}"
        return f"Unlinked - {name} on {self.device.name}"


class Device(models.Model):
    """A biometric terminal, reached by either transport.

    How a device is identified depends on how it talks to us:

    * **Push (ADMS/WDMS)** - the device connects to us, and its *serial
      number* is the identity. Its IP address is whatever NAT and DHCP happen
      to give it that day, which is why ``ip_address`` is optional: for a
      terminal at a remote branch it is neither knowable in advance nor useful.
    * **Pull (ZK SDK)** - we connect to the device, so an ``ip_address`` is
      required and is the identity.

    A device must therefore carry at least one of the two, which the database
    constraint at the bottom of this class enforces.
    """
    name = models.CharField(max_length=100, help_text="Device name/label")
    ip_address = models.CharField(
        max_length=50, unique=True, blank=True, null=True,
        help_text=(
            "Address to connect to for pull-mode syncing. Leave empty for "
            "push-mode devices, which reach us and are identified by serial number."
        ),
    )
    last_ip = models.CharField(
        max_length=50, blank=True, null=True,
        help_text="Source address of the device's most recent push. Informational.",
    )
    port = models.IntegerField(default=4370, help_text="Device port (default: 4370)")
    password = models.IntegerField(default=0, blank=True, null=True, help_text="Device password (default: 0)")
    location = models.CharField(max_length=100, blank=True, null=True, help_text="Physical location of device")
    is_active = models.BooleanField(default=True)
    last_sync = models.DateTimeField(blank=True, null=True)
    last_sync_status = models.CharField(max_length=200, blank=True, null=True)
    connection_timeout = models.IntegerField(default=5, help_text="Connection timeout in seconds")

    # --- ADMS / WDMS push protocol -------------------------------------
    # Three identifiers, and they are not interchangeable:
    #
    #   serial_number  The only one present at request time - every ADMS call
    #                  carries ?SN=..., so this is what a device is matched on.
    #   device_id      The terminal's own "Device ID" / machine number (often
    #                  1). Set by whoever configured the unit; NOT unique across
    #                  devices, so it identifies a terminal only within a site.
    #   mac_address    Burned into the hardware. Unique in practice and useful
    #                  for matching a database row to a physical box, but the
    #                  device only tells us on an OPTIONS/INFO upload, which
    #                  happens after the handshake - so it can confirm an
    #                  identity, never establish one.
    serial_number = models.CharField(
        max_length=64, unique=True, blank=True, null=True,
        help_text="Device serial number (SN). Identifies the device when it pushes data to us.",
    )
    device_id = models.CharField(
        max_length=32, blank=True, null=True,
        help_text=(
            "The terminal's own Device ID / machine number. Reported by the device; "
            "not unique between devices, so it is a label rather than an identity."
        ),
    )
    mac_address = models.CharField(
        max_length=32, blank=True, null=True,
        help_text=(
            "Hardware MAC address, reported by the device after it connects. "
            "Useful for matching this record to a physical unit."
        ),
    )
    push_enabled = models.BooleanField(
        default=False,
        help_text="Set automatically the first time this device contacts the ADMS endpoint.",
    )
    last_seen = models.DateTimeField(
        blank=True, null=True, help_text="Last contact from the device (any ADMS request)",
    )
    firmware_version = models.CharField(max_length=100, blank=True, null=True)
    device_info = models.TextField(
        blank=True, null=True, help_text="Raw INFO string last reported by the device",
    )
    attlog_stamp = models.CharField(
        max_length=32, default='0',
        help_text="Resume point for attendance log uploads (ADMS)",
    )
    operlog_stamp = models.CharField(
        max_length=32, default='0',
        help_text="Resume point for operation log uploads (ADMS)",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'devices'
        verbose_name = 'Device'
        verbose_name_plural = 'Devices'
        ordering = ['name']
        constraints = [
            # A device we can neither connect to nor recognise when it connects
            # to us is not a device, it is a row. One of the two identities has
            # to be present.
            models.CheckConstraint(
                condition=models.Q(ip_address__isnull=False) | models.Q(serial_number__isnull=False),
                name='device_has_ip_or_serial',
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.address_label})"

    @property
    def address_label(self):
        """How to refer to this device: its serial in push mode, its IP in pull."""
        if self.push_enabled or not self.ip_address:
            return self.serial_number or 'unregistered'
        return self.ip_address

    @property
    def is_online(self):
        """True when the device has checked in recently (push mode only)."""
        from django.conf import settings as django_settings
        from django.utils import timezone as django_timezone
        if not self.last_seen:
            return False
        timeout = getattr(django_settings, 'ADMS_OFFLINE_AFTER_SECONDS', 180)
        return (django_timezone.now() - self.last_seen).total_seconds() < timeout

    def queue_command(self, command, created_by=None):
        """Queue a command for the device to pick up on its next poll."""
        return DeviceCommand.objects.create(
            device=self, command=command, created_by=created_by
        )

    # ------------------------------------------------------------------
    # Pulling data from a device that is in push mode
    # ------------------------------------------------------------------
    # In push mode the server never connects to the device, so "pull" means
    # "ask, then wait for the upload". Devices poll every ~10 seconds
    # (Delay=10 in the handshake), so these take effect within seconds.
    def queue_user_query(self, pin=None, created_by=None):
        """Ask the device to upload its user table (one user, or all of them).

        Records arrive as an OPERLOG upload. Unknown PINs become *unlinked*
        enrolments, listed under Unlinked Enrollments for an admin to attach to
        an employee - nothing is dropped.
        """
        from . import adms_commands

        return self.queue_command(
            adms_commands.query_userinfo(pin), created_by=created_by
        )

    def queue_attlog_query(self, start, end, created_by=None):
        """Ask the device to re-upload attendance between two dates."""
        from . import adms_commands

        return self.queue_command(
            adms_commands.query_attlog(start, end), created_by=created_by
        )

    def request_all_attendance(self, created_by=None):
        """Ask the device to re-send its entire attendance log.

        The protocol resumes from a stamp: the handshake tells the device
        ``ATTLOGStamp=<what we have>`` and it sends only what is newer.
        Clearing the stamp therefore says "we have nothing", and the device
        replays everything it still holds. Re-sent punches are harmless -
        ``devices.ingest`` de-duplicates on (employee, device, timestamp), so a
        full replay simply counts as duplicates.

        Two caveats worth knowing before you rely on this:

        1. **The stamp is only read at a handshake**, not at every poll. A
           device that is already connected may not re-handshake for a long
           time, so a CHECK is queued alongside to prompt an upload of anything
           outstanding. To force the matter, queue a REBOOT as well: the device
           handshakes on start-up.
        2. **The device record is cached** for ADMS_DEVICE_CACHE_SECONDS. The
           worker handling this request drops its copy immediately (see
           devices/signals.py), but with the default local-memory cache other
           workers can serve the old stamp for up to that long. Set REDIS_URL
           to make the invalidation immediate everywhere.
        """
        from . import adms_commands

        self.attlog_stamp = '0'
        self.save(update_fields=['attlog_stamp'])
        return self.queue_command(adms_commands.CHECK, created_by=created_by)

    def test_connection(self):
        """Test connection to the device using pyzk"""
        try:
            from zk import ZK
            conn = None
            zk = ZK(
                self.ip_address,
                port=self.port,
                timeout=self.connection_timeout,
                password=self.password or 0,
                force_udp=False,
                ommit_ping=False
            )
            conn = zk.connect()
            if conn:
                conn.disconnect()
                return True, "Connection successful"
            return False, "Connection failed"
        except Exception as e:
            return False, str(e)

    def sync_attendance(self):
        """Fetch attendance data from the device over the pull SDK (pyzk).

        Shares its ingestion path with the ADMS push protocol, so
        de-duplication, unlinked enrolments and the roll-up into daily
        summaries behave identically however a punch arrives.
        """
        from django.conf import settings as django_settings

        from .ingest import process_touched_days, record_punches

        conn = None
        try:
            from zk import ZK

            zk = ZK(
                self.ip_address,
                port=self.port,
                timeout=self.connection_timeout,
                password=self.password or 0
            )

            conn = zk.connect()
            conn.disable_device()
            device_attendances = conn.get_attendance()
            conn.enable_device()
            conn.disconnect()
            conn = None

            punches = []
            for att in device_attendances:
                timestamp = att.timestamp
                if django_settings.USE_TZ and timezone.is_naive(timestamp):
                    # Devices report their own local wall-clock time.
                    timestamp = timezone.make_aware(
                        timestamp, timezone.get_current_timezone()
                    )
                punches.append({
                    'device_uid': att.user_id,
                    'timestamp': timestamp,
                    'punch_type': att.punch,
                    'uid': att.uid,
                })

            counts, touched = record_punches(self, punches)

            if counts['created'] and getattr(django_settings, 'ADMS_PROCESS_ON_PUSH', True):
                process_touched_days(touched)

            status_msg = f"Success - {counts['created']} records"
            extras = []
            if counts['duplicate']:
                extras.append(f"{counts['duplicate']} already stored")
            if counts['unlinked']:
                extras.append(f"{counts['unlinked']} unlinked")
            if counts['invalid']:
                extras.append(f"{counts['invalid']} invalid")
            if extras:
                status_msg += f" ({', '.join(extras)})"

            self.last_sync = timezone.now()
            self.last_sync_status = status_msg[:200]
            self.save(update_fields=['last_sync', 'last_sync_status'])

            return True, status_msg

        except Exception as e:
            if conn is not None:
                try:
                    conn.enable_device()
                    conn.disconnect()
                except Exception:
                    pass
            self.last_sync_status = f"Error: {str(e)}"[:200]
            self.save(update_fields=['last_sync_status'])
            return False, str(e)

    def sync_users(self):
        """Sync users from device to create EmployeeDevice records"""
        try:
            from zk import ZK
            from django.utils import timezone
            from core.models import Employee

            conn = None
            zk = ZK(
                self.ip_address,
                port=self.port,
                timeout=self.connection_timeout,
                password=self.password or 0
            )

            print(f"\n{'='*60}")
            print(f"Connecting to device: {self.ip_address}:{self.port}")
            print(f"{'='*60}")

            conn = zk.connect()
            conn.disable_device()

            # Get users from device
            device_users = conn.get_users()

            print(f"\nTotal users found on device: {len(device_users)}")
            print(f"\n{'-'*60}")
            print(f"{'UID':<10} {'Name':<30} {'Type':<10}")
            print(f"{'-'*60}")

            created_count = 0
            updated_count = 0
            skipped_count = 0

            for user in device_users:
                try:
                    # Print user information
                    uid_value = user.uid if hasattr(user, 'uid') else 'N/A'
                    name_value = getattr(user, 'name', 'N/A') or 'N/A'
                    user_type = getattr(user, 'privilege', 'N/A')

                    print(f"{str(uid_value):<10} {str(name_value):<30} {str(user_type):<10}")

                    # Convert uid to integer
                    if isinstance(user.uid, bytes):
                        device_uid = int.from_bytes(user.uid, byteorder='little', signed=False)
                    else:
                        device_uid = int(user.uid)

                    # Get user name from device
                    user_name = getattr(user, 'name', '') or f"User {device_uid}"

                    # Check if EmployeeDevice already exists
                    employee_device = EmployeeDevice.objects.filter(
                        device=self,
                        device_uid=device_uid
                    ).first()

                    if employee_device:
                        # EmployeeDevice already exists
                        # Update user_name if it's empty
                        if not employee_device.user_name and user_name:
                            employee_device.user_name = user_name
                            employee_device.save()
                            print(f"  → Updated existing record with user name")
                        skipped_count += 1
                    else:
                        # Try to find employee by user_id (if it matches device_uid)
                        # This allows auto-linking if employee was created with matching ID
                        employee = Employee.objects.filter(device_uid=device_uid).first()

                        if employee:
                            # Found matching employee, create EmployeeDevice link
                            EmployeeDevice.objects.create(
                                employee=employee,
                                device=self,
                                device_uid=device_uid,
                                user_name=user_name
                            )
                            print(f"  → Linked to existing employee: {employee.user.get_full_name()}")
                            updated_count += 1
                        else:
                            # No employee found, automatically create employee profile
                            from django.contrib.auth import get_user_model
                            User = get_user_model()

                            # Parse name to create username
                            name_parts = user_name.strip().split()
                            first_name = name_parts[0] if name_parts else ''
                            last_name = ' '.join(name_parts[1:]) if len(name_parts) > 1 else ''

                            # Create username from name (lowercase, no spaces)
                            base_username = user_name.strip().lower().replace(' ', '')
                            username = base_username
                            counter = 1
                            while User.objects.filter(username=username).exists():
                                username = f"{base_username}{counter}"
                                counter += 1

                            # Generate a random password
                            import secrets
                            import string
                            alphabet = string.ascii_letters + string.digits
                            password = ''.join(secrets.choice(alphabet) for i in range(10))

                            # Create user account
                            from django.conf import settings as django_settings
                            placeholder_domain = getattr(
                                django_settings, 'PLACEHOLDER_EMAIL_DOMAIN', 'invalid'
                            )

                            new_user = User.objects.create_user(
                                username=username,
                                email=f"{username}@{placeholder_domain}",
                                first_name=first_name,
                                last_name=last_name,
                                password=password,
                                role='EMPLOYEE'
                            )

                            # Create employee profile
                            new_employee = Employee.objects.create(
                                user=new_user,
                                employee_id=f"EMP{device_uid:04d}",
                                device_uid=device_uid,
                                join_date=timezone.now().date()
                            )

                            # Create EmployeeDevice link
                            EmployeeDevice.objects.create(
                                employee=new_employee,
                                device=self,
                                device_uid=device_uid,
                                user_name=user_name
                            )
                            print(f"  → Created new employee profile: {new_user.get_full_name()} (username: {username}, password: {password})")
                            created_count += 1

                except Exception as e:
                    # Log the error for debugging
                    print(f"  ✗ Error: {str(e)}")
                    skipped_count += 1
                    continue

            print(f"{'-'*60}")
            print(f"\nSummary:")
            print(f"  New employee profiles created: {created_count}")
            print(f"  Linked to existing employees: {updated_count}")
            print(f"  Skipped (already exist): {skipped_count}")
            print(f"{'='*60}\n")

            conn.enable_device()
            conn.disconnect()

            # Update last sync info
            self.last_sync = timezone.now()
            status_msg = f"Users synced - {created_count} new employees created, {updated_count} linked"
            if skipped_count > 0:
                status_msg += f" ({skipped_count} skipped)"
            self.last_sync_status = status_msg
            self.save()

            return True, status_msg

        except Exception as e:
            error_msg = f"Error: {str(e)}"
            print(f"\n✗ Sync users error: {error_msg}")
            print(f"{'='*60}\n")
            self.last_sync_status = error_msg
            self.save()
            return False, error_msg

    def push_user(self, employee_device, created_by=None):
        """Queue a DATA UPDATE USER command that writes a user onto the device."""
        name = employee_device.user_name or ''
        if employee_device.employee:
            name = employee_device.employee.user.get_full_name() or name
        command = (
            f"DATA UPDATE USER PIN={employee_device.device_uid}\t"
            f"Name={name}\tPri=0\tPasswd=\tCard=\tGrp=1\tTZ=0000000000000000"
        )
        return self.queue_command(command, created_by=created_by)

    def get_device_info(self):
        """Get device information"""
        try:
            from zk import ZK
            conn = None
            zk = ZK(self.ip_address, port=self.port, timeout=self.connection_timeout)
            conn = zk.connect()

            info = {
                'firmware': conn.get_firmware_version(),
                'serial_number': conn.get_serialnumber(),
                'platform': conn.get_platform(),
                'device_name': conn.get_device_name(),
                'mac': conn.get_mac(),
                'users': conn.users,
                'fingers': conn.fingers,
                'records': conn.records
            }

            conn.disconnect()
            return True, info
        except Exception as e:
            return False, str(e)


class DeviceCommand(models.Model):
    """A command queued for a device to collect over the ADMS push protocol.

    In push mode the server cannot reach the device: the device polls
    ``/iclock/getrequest`` and we answer with whatever is pending. The device
    then reports the outcome to ``/iclock/devicecmd``.
    """
    STATUS_PENDING = 'PENDING'
    STATUS_SENT = 'SENT'
    STATUS_DONE = 'DONE'
    STATUS_FAILED = 'FAILED'

    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_SENT, 'Sent to device'),
        (STATUS_DONE, 'Completed'),
        (STATUS_FAILED, 'Failed'),
    ]

    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name='commands')
    command = models.TextField(help_text="Command body, e.g. 'CHECK' or 'DATA UPDATE USER PIN=1...'")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    return_code = models.IntegerField(
        blank=True, null=True, help_text="Return value reported by the device (0 = success)",
    )
    response = models.CharField(max_length=200, blank=True, null=True)
    created_by = models.ForeignKey(
        'core.User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='queued_device_commands',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        db_table = 'device_commands'
        verbose_name = 'Device Command'
        verbose_name_plural = 'Device Commands'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['device', 'status'])]

    def __str__(self):
        return f"{self.device.name}: {self.command[:40]} ({self.status})"

    def as_wire_format(self):
        """Render as the device expects it: ``C:<id>:<command>``."""
        return f"C:{self.id}:{self.command}"

    def mark_sent(self):
        self.status = self.STATUS_SENT
        self.sent_at = timezone.now()
        self.save(update_fields=['status', 'sent_at'])

    def mark_result(self, return_code, response=None):
        try:
            return_code = int(return_code)
        except (TypeError, ValueError):
            return_code = None
        self.return_code = return_code
        self.response = (response or '')[:200] or None
        # Devices report 0 (and some firmware 1) for success.
        self.status = self.STATUS_DONE if return_code in (0, 1) else self.STATUS_FAILED
        self.completed_at = timezone.now()
        self.save(update_fields=['return_code', 'response', 'status', 'completed_at'])
