from django.db import models
from core.models import Employee

class LeaveType(models.Model):
    """Leave type configuration"""
    name = models.CharField(max_length=50, unique=True)
    code = models.CharField(max_length=10, unique=True)
    description = models.TextField(blank=True, null=True)
    days_per_year = models.IntegerField(default=0, help_text="Number of days allowed per year")
    is_paid = models.BooleanField(default=True)
    requires_approval = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'leave_types'
        ordering = ['name']

    def __str__(self):
        return self.name


class LeaveBalance(models.Model):
    """Employee leave balance"""
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='leave_balances')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE, related_name='balances')
    year = models.IntegerField()
    total_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    used_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    remaining_days = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'leave_balances'
        unique_together = ['employee', 'leave_type', 'year']
        ordering = ['-year']

    def __str__(self):
        return f"{self.employee.user.get_full_name()} - {self.leave_type.name} ({self.year}): {self.remaining_days}"

    def update_balance(self, days):
        """Update balance after leave request"""
        self.used_days += days
        self.remaining_days = self.total_days - self.used_days
        self.save()


class LeaveRequest(models.Model):
    """Leave request submitted by employee"""
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('CANCELLED', 'Cancelled')
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='leave_requests')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE, related_name='requests')
    start_date = models.DateField()
    end_date = models.DateField()
    total_days = models.DecimalField(max_digits=5, decimal_places=1)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    attachment = models.FileField(upload_to='leave_attachments/', blank=True, null=True)
    approved_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_leaves'
    )
    approved_at = models.DateTimeField(blank=True, null=True)
    rejection_reason = models.TextField(blank=True, null=True)
    admin_notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'leave_requests'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.employee.user.get_full_name()} - {self.leave_type.name} ({self.start_date} to {self.end_date})"

    def calculate_days(self):
        """Calculate total leave days excluding weekends"""
        from datetime import timedelta
        start = self.start_date
        end = self.end_date
        days = 0
        current = start
        while current <= end:
            # Monday=0, Sunday=6
            if current.weekday() < 5:  # Exclude Saturday (5) and Sunday (6)
                days += 1
            current += timedelta(days=1)
        return days

    def save(self, *args, **kwargs):
        if not self.total_days:
            self.total_days = self.calculate_days()
        super().save(*args, **kwargs)
