from django.db import models
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
    """ZKTeco biometric device model"""
    name = models.CharField(max_length=100, help_text="Device name/label")
    ip_address = models.CharField(max_length=50, unique=True, help_text="Device IP address")
    port = models.IntegerField(default=4370, help_text="Device port (default: 4370)")
    password = models.IntegerField(default=0, blank=True, null=True, help_text="Device password (default: 0)")
    location = models.CharField(max_length=100, blank=True, null=True, help_text="Physical location of device")
    is_active = models.BooleanField(default=True)
    last_sync = models.DateTimeField(blank=True, null=True)
    last_sync_status = models.CharField(max_length=20, blank=True, null=True)
    connection_timeout = models.IntegerField(default=5, help_text="Connection timeout in seconds")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'devices'
        verbose_name = 'Device'
        verbose_name_plural = 'Devices'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.ip_address})"

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
        """Fetch attendance data from device"""
        try:
            from zk import ZK, const
            from attendance.models import Attendance
            from django.utils import timezone
            import datetime

            conn = None
            zk = ZK(
                self.ip_address,
                port=self.port,
                timeout=self.connection_timeout,
                password=self.password or 0
            )

            conn = zk.connect()
            conn.disable_device()

            # Get attendance records
            attendances = conn.get_attendance()

            synced_count = 0
            skipped_count = 0
            for att in attendances:
                # Convert device UID to employee
                try:
                    # Convert user_id to integer (handle bytes, str, int)
                    if isinstance(att.user_id, bytes):
                        # Handle byte strings like b'\x05'
                        device_uid = int.from_bytes(att.user_id, byteorder='little', signed=False)
                    elif isinstance(att.user_id, str):
                        # Handle string representations
                        device_uid = int(att.user_id)
                    else:
                        # Handle integers
                        device_uid = int(att.user_id)

                    # Find employee by device_uid and this device
                    employee_device = EmployeeDevice.objects.filter(
                        device=self,
                        device_uid=device_uid
                    ).select_related('employee').first()

                    if not employee_device:
                        skipped_count += 1
                        continue

                    employee = employee_device.employee

                    # Check if attendance already exists
                    existing_attendance = Attendance.objects.filter(
                        employee=employee,
                        device=self,
                        timestamp=att.timestamp
                    ).first()

                    if not existing_attendance:
                        # Create attendance record
                        Attendance.objects.create(
                            employee=employee,
                            device=self,
                            timestamp=att.timestamp,
                            punch_type=att.punch,
                            uid=att.uid
                        )
                        synced_count += 1
                except Exception as e:
                    # Log error but continue processing
                    skipped_count += 1
                    continue

            conn.enable_device()
            conn.disconnect()

            # Update last sync info
            self.last_sync = timezone.now()
            status_msg = f"Success - {synced_count} records"
            if skipped_count > 0:
                status_msg += f" ({skipped_count} skipped)"
            self.last_sync_status = status_msg
            self.save()

            return True, status_msg

        except Exception as e:
            self.last_sync_status = f"Error: {str(e)}"
            self.save()
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
                            new_user = User.objects.create_user(
                                username=username,
                                email=f"{username}@temp.com",
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
