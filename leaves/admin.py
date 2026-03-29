from django.contrib import admin
from .models import LeaveType, LeaveBalance, LeaveRequest

@admin.register(LeaveType)
class LeaveTypeAdmin(admin.ModelAdmin):
    list_display = ['name', 'code', 'days_per_year', 'is_paid', 'requires_approval', 'is_active']
    list_filter = ['is_paid', 'requires_approval', 'is_active']
    search_fields = ['name', 'code']
    ordering = ['name']


@admin.register(LeaveBalance)
class LeaveBalanceAdmin(admin.ModelAdmin):
    list_display = ['employee', 'leave_type', 'year', 'total_days', 'used_days', 'remaining_days']
    list_filter = ['year', 'leave_type']
    search_fields = ['employee__user__first_name', 'employee__user__last_name', 'employee__employee_id']
    ordering = ['-year', 'employee']
    readonly_fields = ['used_days', 'remaining_days']


@admin.register(LeaveRequest)
class LeaveRequestAdmin(admin.ModelAdmin):
    list_display = ['employee', 'leave_type', 'start_date', 'end_date', 'total_days', 'status', 'approved_by', 'created_at']
    list_filter = ['status', 'leave_type', 'start_date', 'created_at']
    search_fields = ['employee__user__first_name', 'employee__user__last_name', 'employee__employee_id']
    date_hierarchy = 'start_date'
    ordering = ['-created_at']
    readonly_fields = ['total_days', 'approved_at', 'created_at', 'updated_at']

    fieldsets = (
        ('Leave Information', {
            'fields': ('employee', 'leave_type', 'start_date', 'end_date', 'total_days', 'reason')
        }),
        ('Status', {
            'fields': ('status', 'approved_by', 'approved_at', 'rejection_reason', 'admin_notes')
        }),
        ('Attachment', {
            'fields': ('attachment',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    actions = ['approve_leaves', 'reject_leaves', 'cancel_leaves']

    def approve_leaves(self, request, queryset):
        """Approve leave requests and update balance"""
        from django.contrib import messages
        from django.utils import timezone
        pending_requests = queryset.filter(status='PENDING')

        for leave_request in pending_requests:
            # Update leave request status
            leave_request.status = 'APPROVED'
            leave_request.approved_by = request.user
            leave_request.approved_at = timezone.now()
            leave_request.save()

            # Update leave balance
            try:
                balance = LeaveBalance.objects.get(
                    employee=leave_request.employee,
                    leave_type=leave_request.leave_type,
                    year=leave_request.start_date.year
                )
                balance.update_balance(leave_request.total_days)
            except LeaveBalance.DoesNotExist:
                # Create balance if doesn't exist
                LeaveBalance.objects.create(
                    employee=leave_request.employee,
                    leave_type=leave_request.leave_type,
                    year=leave_request.start_date.year,
                    total_days=leave_request.leave_type.days_per_year,
                    used_days=leave_request.total_days,
                    remaining_days=leave_request.leave_type.days_per_year - leave_request.total_days
                )

        messages.success(request, f"Approved {pending_requests.count()} leave request(s)")
    approve_leaves.short_description = "Approve selected leave requests"

    def reject_leaves(self, request, queryset):
        """Reject leave requests"""
        from django.contrib import messages
        updated = queryset.filter(status='PENDING').update(status='REJECTED')
        messages.success(request, f"Rejected {updated} leave request(s)")
    reject_leaves.short_description = "Reject selected leave requests"

    def cancel_leaves(self, request, queryset):
        """Cancel leave requests"""
        from django.contrib import messages
        updated = queryset.filter(status__in=['PENDING', 'APPROVED']).update(status='CANCELLED')
        messages.success(request, f"Cancelled {updated} leave request(s)")
    cancel_leaves.short_description = "Cancel selected leave requests"
