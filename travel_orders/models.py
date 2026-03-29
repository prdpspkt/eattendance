from django.db import models
from core.models import Employee

class TravelOrder(models.Model):
    """Travel order request submitted by employee"""
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('CANCELLED', 'Cancelled')
    ]

    TRAVEL_TYPE_CHOICES = [
        ('DOMESTIC', 'Domestic'),
        ('INTERNATIONAL', 'International')
    ]

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='travel_orders')
    travel_type = models.CharField(max_length=20, choices=TRAVEL_TYPE_CHOICES, default='DOMESTIC')
    destination = models.CharField(max_length=200)
    purpose = models.TextField()
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    total_days = models.DecimalField(max_digits=5, decimal_places=1, blank=True, null=True)
    estimated_cost = models.DecimalField(max_digits=12, decimal_places=2, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    attachment = models.FileField(upload_to='travel_attachments/', blank=True, null=True)
    approved_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_travel_orders'
    )
    approved_at = models.DateTimeField(blank=True, null=True)
    rejection_reason = models.TextField(blank=True, null=True)
    admin_notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'travel_orders'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.employee.user.get_full_name()} - {self.destination} ({self.start_date.date()})"

    def calculate_days(self):
        """Calculate total travel days"""
        from datetime import timedelta
        if self.start_date and self.end_date:
            total_seconds = (self.end_date - self.start_date).total_seconds()
            return round(total_seconds / (24 * 3600), 1)
        return 0

    def save(self, *args, **kwargs):
        if not self.total_days:
            self.total_days = self.calculate_days()
        super().save(*args, **kwargs)


class TravelItinerary(models.Model):
    """Travel itinerary details"""
    travel_order = models.ForeignKey(TravelOrder, on_delete=models.CASCADE, related_name='itineraries')
    date_time = models.DateTimeField()
    activity = models.CharField(max_length=200)
    location = models.CharField(max_length=200, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'travel_itineraries'
        ordering = ['date_time']

    def __str__(self):
        return f"{self.travel_order.employee.user.get_full_name()} - {self.activity} ({self.date_time})"


class TravelExpense(models.Model):
    """Travel expense claim"""
    EXPENSE_STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected')
    ]

    travel_order = models.OneToOneField(TravelOrder, on_delete=models.CASCADE, related_name='expense')
    total_expense = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    transportation = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    accommodation = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    meals = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    other_expenses = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    other_description = models.CharField(max_length=200, blank=True, null=True)
    receipt_attachment = models.FileField(upload_to='travel_receipts/', blank=True, null=True)
    status = models.CharField(max_length=20, choices=EXPENSE_STATUS_CHOICES, default='PENDING')
    approved_by = models.ForeignKey(
        'core.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_expenses'
    )
    approved_at = models.DateTimeField(blank=True, null=True)
    rejection_reason = models.TextField(blank=True, null=True)
    payment_date = models.DateTimeField(blank=True, null=True)
    payment_notes = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'travel_expenses'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.travel_order.employee.user.get_full_name()} - {self.total_expense}"

    def calculate_total(self):
        """Calculate total expense"""
        self.total_expense = (
            self.transportation +
            self.accommodation +
            self.meals +
            self.other_expenses
        )
        self.save()

    def save(self, *args, **kwargs):
        if not self.total_expense:
            self.total_expense = (
                self.transportation +
                self.accommodation +
                self.meals +
                self.other_expenses
            )
        super().save(*args, **kwargs)
