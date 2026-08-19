from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

from core.models import Employee


class LeaveType(models.Model):
    """A leave entitlement and the rules that govern it.

    The rules are data, not code, because they are set by government
    regulation and change with it. Three questions describe every type this
    office uses:

    * Is it credited every year, or granted when a particular event happens?
    * When the year ends, does an unused balance lapse, accumulate up to a
      ceiling, or accumulate without limit?
    * Is there a limit on how many times in a career it can be taken?

    Home leave, for instance, is credited 30 days a year and accumulates to a
    ceiling of 180. Sick leave is credited 12 days a year and accumulates with
    no ceiling. Casual leave is credited 12 days a year and lapses at the year
    end. Maternity leave is not credited at all - it is granted when the event
    happens, for a fixed number of days, at most twice in a career.
    """

    class Accrual(models.TextChoices):
        YEARLY = 'YEARLY', 'Credited every year'
        OCCASIONAL = 'OCCASIONAL', 'Granted when the event happens'

    class CarryForward(models.TextChoices):
        NONE = 'NONE', 'Expires at the end of the year'
        CAPPED = 'CAPPED', 'Accumulates up to a ceiling'
        UNLIMITED = 'UNLIMITED', 'Accumulates without limit'

    name = models.CharField(max_length=50, unique=True)
    code = models.CharField(max_length=10, unique=True)
    description = models.TextField(blank=True, null=True)

    accrual = models.CharField(
        max_length=12, choices=Accrual.choices, default=Accrual.YEARLY,
        help_text="Whether the entitlement arrives every year or only when the event occurs.",
    )
    days_per_year = models.IntegerField(
        default=0,
        help_text="Days credited each year. Only used by yearly leave.",
    )
    carry_forward = models.CharField(
        max_length=12, choices=CarryForward.choices, default=CarryForward.NONE,
        help_text="What happens to an unused balance when the year ends.",
    )
    max_accumulation_days = models.IntegerField(
        blank=True, null=True,
        help_text="Ceiling on the accumulated balance, e.g. 180 for home leave. "
                  "Leave empty when the accumulation is unlimited.",
    )
    days_per_occurrence = models.IntegerField(
        blank=True, null=True,
        help_text="Days granted each time the event happens, e.g. 15 for death rituals. "
                  "Only used by occasional leave.",
    )
    max_occurrences_lifetime = models.IntegerField(
        blank=True, null=True,
        help_text="How many times this leave may be taken in a whole career, "
                  "e.g. 2 for maternity. Leave empty for no limit.",
    )

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

    # ------------------------------------------------------------------
    # policy questions the rest of the system asks
    # ------------------------------------------------------------------
    @property
    def is_occasional(self):
        return self.accrual == self.Accrual.OCCASIONAL

    @property
    def is_yearly(self):
        return self.accrual == self.Accrual.YEARLY

    @property
    def accumulates(self):
        """True when an unused balance survives the year end."""
        return self.is_yearly and self.carry_forward != self.CarryForward.NONE

    @property
    def ceiling(self):
        """The most that may stand to an employee's credit, or None."""
        if self.carry_forward == self.CarryForward.CAPPED:
            return Decimal(self.max_accumulation_days or 0)
        return None

    def cap(self, days):
        """Trim ``days`` to the accumulation ceiling, if there is one."""
        ceiling = self.ceiling
        if ceiling is None:
            return days
        return min(days, ceiling)

    def occurrences_used(self, employee):
        """How many times this employee has already taken this leave."""
        return self.requests.filter(employee=employee, status='APPROVED').count()

    def occurrences_left(self, employee):
        """Remaining lifetime occurrences, or None when there is no limit."""
        if not self.max_occurrences_lifetime:
            return None
        return max(0, self.max_occurrences_lifetime - self.occurrences_used(employee))

    @property
    def policy_summary(self):
        """One line describing the rule, for a list the office has to read."""
        if self.is_occasional:
            parts = ['Occasional']
            if self.days_per_occurrence:
                parts.append(f'{self.days_per_occurrence} days each time')
            if self.max_occurrences_lifetime:
                parts.append(f'{self.max_occurrences_lifetime}x in a career')
            else:
                parts.append('no limit on occurrences')
            return ', '.join(parts)

        parts = [f'{self.days_per_year} days a year']
        if self.carry_forward == self.CarryForward.NONE:
            parts.append('expires at year end')
        elif self.carry_forward == self.CarryForward.CAPPED:
            parts.append(f'accumulates up to {self.max_accumulation_days}')
        else:
            parts.append('accumulates without limit')
        return ', '.join(parts)

    def clean(self):
        errors = {}
        if self.carry_forward == self.CarryForward.CAPPED:
            if not self.max_accumulation_days:
                errors['max_accumulation_days'] = (
                    'A ceiling is required when the leave accumulates up to a ceiling.'
                )
            elif self.days_per_year and self.max_accumulation_days < self.days_per_year:
                errors['max_accumulation_days'] = (
                    'The ceiling cannot be lower than the days credited each year.'
                )
        if self.is_occasional and not self.days_per_occurrence:
            errors['days_per_occurrence'] = (
                'Occasional leave needs the number of days granted each time.'
            )
        if self.is_occasional and self.days_per_year:
            errors['days_per_year'] = (
                'Occasional leave is not credited yearly - leave this at 0.'
            )
        for field in ('days_per_year', 'max_accumulation_days',
                      'days_per_occurrence', 'max_occurrences_lifetime'):
            value = getattr(self, field)
            if value is not None and value < 0:
                errors[field] = 'This cannot be negative.'
        if errors:
            raise ValidationError(errors)


class LeaveBalance(models.Model):
    """One employee's entitlement to one leave type in one year.

    The row keeps the arithmetic visible rather than just its answer: what was
    carried in from last year, what this year credited, what the ceiling threw
    away, and what has been taken. An employee who is told "you have 160 days
    of home leave" is entitled to see how the number was arrived at.
    """
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='leave_balances')
    leave_type = models.ForeignKey(LeaveType, on_delete=models.CASCADE, related_name='balances')
    year = models.IntegerField()
    opening_days = models.DecimalField(
        max_digits=6, decimal_places=1, default=0,
        help_text="Carried forward from the previous year.",
    )
    accrued_days = models.DecimalField(
        max_digits=6, decimal_places=1, default=0,
        help_text="Credited by this year's entitlement.",
    )
    lapsed_days = models.DecimalField(
        max_digits=6, decimal_places=1, default=0,
        help_text="Dropped because the balance hit its ceiling, or because the leave expires.",
    )
    total_days = models.DecimalField(max_digits=6, decimal_places=1, default=0)
    used_days = models.DecimalField(max_digits=6, decimal_places=1, default=0)
    remaining_days = models.DecimalField(max_digits=6, decimal_places=1, default=0)
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
        """Total leave days in the range, excluding the configured weekly off days."""
        from core.workweek import working_days_between
        return working_days_between(self.start_date, self.end_date)

    def save(self, *args, **kwargs):
        # Recompute whenever the dates change, not only on first save, so an
        # edited request does not keep the day count from its old dates.
        if self.start_date and self.end_date:
            self.total_days = self.calculate_days()
        elif not self.total_days:
            self.total_days = 0
        super().save(*args, **kwargs)
