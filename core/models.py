from django.contrib.auth.models import AbstractUser
from django.db import models
from django.core.validators import RegexValidator

class User(AbstractUser):
    """
    Custom User model with 3 role levels:
    - SUPERUSER: Full system access
    - OFFICE_ADMIN: Can manage employees, approve leaves/travel orders, manage devices
    - EMPLOYEE: Can view their own data, request leaves/travel orders
    """
    class UserRole(models.TextChoices):
        SUPERUSER = 'SUPERUSER', 'Superuser'
        OFFICE_ADMIN = 'OFFICE_ADMIN', 'Office Admin'
        EMPLOYEE = 'EMPLOYEE', 'Employee'

    role = models.CharField(
        max_length=20,
        choices=UserRole.choices,
        default=UserRole.EMPLOYEE
    )
    phone = models.CharField(max_length=20, blank=True, null=True)
    profile_picture = models.ImageField(upload_to='profiles/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        permissions = [
            ('can_manage_devices', 'Can manage biometric devices'),
            ('can_manage_employees', 'Can manage employees'),
            ('can_approve_leaves', 'Can approve leave requests'),
            ('can_approve_travel_orders', 'Can approve travel orders'),
        ]

    def __str__(self):
        return f"{self.get_full_name()} ({self.get_role_display()})"

    def has_perm(self, perm, obj=None):
        """
        Override Django's has_perm to check custom permissions based on role.
        Superusers always return True (handled by parent class).
        """
        # First check if the parent class (Django's default) grants permission
        if super().has_perm(perm, obj):
            return True

        # For custom permissions, check based on role
        custom_permissions = {
            'core.can_manage_devices': [self.UserRole.SUPERUSER, self.UserRole.OFFICE_ADMIN],
            'core.can_manage_employees': [self.UserRole.SUPERUSER, self.UserRole.OFFICE_ADMIN],
            'core.can_approve_leaves': [self.UserRole.SUPERUSER, self.UserRole.OFFICE_ADMIN],
            'core.can_approve_travel_orders': [self.UserRole.SUPERUSER, self.UserRole.OFFICE_ADMIN],
        }

        return self.role in custom_permissions.get(perm, [])


class Department(models.Model):
    """Department model for organizing employees"""
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'departments'
        ordering = ['name']

    def __str__(self):
        return self.name


class Employee(models.Model):
    """Employee model linked to User"""
    GENDER_CHOICES = [
        ('M', 'Male'),
        ('F', 'Female'),
        ('O', 'Other')
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='employee')
    employee_id = models.CharField(max_length=20, unique=True)
    department = models.ForeignKey(Department, on_delete=models.SET_NULL, null=True, blank=True, related_name='employees')
    gender = models.CharField(max_length=1, choices=GENDER_CHOICES, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    postal_code = models.CharField(max_length=20, blank=True, null=True)
    emergency_contact_name = models.CharField(max_length=100, blank=True, null=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True, null=True)
    blood_group = models.CharField(max_length=5, blank=True, null=True)
    join_date = models.DateField()
    employment_status = models.CharField(
        max_length=20,
        choices=[
            ('ACTIVE', 'Active'),
            ('ON_LEAVE', 'On Leave'),
            ('SUSPENDED', 'Suspended'),
            ('TERMINATED', 'Terminated'),
            ('RESIGNED', 'Resigned')
        ],
        default='ACTIVE'
    )
    # Device UID for biometric device
    device_uid = models.IntegerField(unique=True, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'employees'
        verbose_name = 'Employee'
        verbose_name_plural = 'Employees'
        ordering = ['-join_date']

    def __str__(self):
        return f"{self.user.get_full_name()} ({self.employee_id})"

    def get_full_name(self):
        return self.user.get_full_name()


class Shift(models.Model):
    """Shift model for managing work schedules"""
    name = models.CharField(max_length=50, unique=True)
    start_time = models.TimeField()
    end_time = models.TimeField()
    late_grace_minutes = models.IntegerField(default=15, help_text="Grace period in minutes")
    early_exit_minutes = models.IntegerField(default=15, help_text="Early exit tolerance in minutes")
    break_duration_minutes = models.IntegerField(default=60, help_text="Break duration in minutes")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shifts'
        ordering = ['start_time']

    def __str__(self):
        return f"{self.name} ({self.start_time} - {self.end_time})"

    def get_working_hours(self):
        """Calculate total working hours excluding break"""
        from datetime import datetime, timedelta
        start = datetime.combine(datetime.today(), self.start_time)
        end = datetime.combine(datetime.today(), self.end_time)
        total_minutes = (end - start).total_seconds() / 60
        return (total_minutes - self.break_duration_minutes) / 60


class EmployeeShift(models.Model):
    """Assign shift to employee"""
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='employee_shifts')
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE, related_name='shift_employees')
    effective_date = models.DateField()
    end_date = models.DateField(blank=True, null=True, help_text="Leave blank if ongoing")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'employee_shifts'
        unique_together = ['employee', 'effective_date']
        ordering = ['-effective_date']

    def __str__(self):
        return f"{self.employee.user.get_full_name()} - {self.shift.name} ({self.effective_date})"
