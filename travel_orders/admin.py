from django.contrib import admin
from .models import TravelOrder, TravelItinerary, TravelExpense


class TravelItineraryInline(admin.TabularInline):
    model = TravelItinerary
    extra = 0
    fk_name = 'travel_order'


class TravelExpenseInline(admin.StackedInline):
    model = TravelExpense
    extra = 0
    fk_name = 'travel_order'
    classes = ('collapse',)


@admin.register(TravelOrder)
class TravelOrderAdmin(admin.ModelAdmin):
    list_display = ['employee', 'destination', 'travel_type', 'start_date', 'end_date', 'total_days', 'estimated_cost', 'status', 'approved_by']
    list_filter = ['status', 'travel_type', 'start_date', 'created_at']
    search_fields = ['employee__user__first_name', 'employee__user__last_name', 'employee__employee_id', 'destination', 'purpose']
    date_hierarchy = 'start_date'
    ordering = ['-created_at']
    readonly_fields = ['total_days', 'approved_at', 'created_at', 'updated_at']
    inlines = [TravelItineraryInline, TravelExpenseInline]

    fieldsets = (
        ('Travel Information', {
            'fields': ('employee', 'travel_type', 'destination', 'purpose', 'start_date', 'end_date', 'total_days', 'estimated_cost')
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

    actions = ['approve_travel_orders', 'reject_travel_orders', 'cancel_travel_orders']

    def approve_travel_orders(self, request, queryset):
        """Approve travel orders"""
        from django.contrib import messages
        from django.utils import timezone
        updated = queryset.filter(status='PENDING').update(
            status='APPROVED',
            approved_by=request.user,
            approved_at=timezone.now()
        )
        messages.success(request, f"Approved {updated} travel order(s)")
    approve_travel_orders.short_description = "Approve selected travel orders"

    def reject_travel_orders(self, request, queryset):
        """Reject travel orders"""
        from django.contrib import messages
        updated = queryset.filter(status='PENDING').update(status='REJECTED')
        messages.success(request, f"Rejected {updated} travel order(s)")
    reject_travel_orders.short_description = "Reject selected travel orders"

    def cancel_travel_orders(self, request, queryset):
        """Cancel travel orders"""
        from django.contrib import messages
        updated = queryset.filter(status__in=['PENDING', 'APPROVED']).update(status='CANCELLED')
        messages.success(request, f"Cancelled {updated} travel order(s)")
    cancel_travel_orders.short_description = "Cancel selected travel orders"


@admin.register(TravelItinerary)
class TravelItineraryAdmin(admin.ModelAdmin):
    list_display = ['travel_order', 'date_time', 'activity', 'location']
    list_filter = ['date_time']
    search_fields = ['activity', 'location', 'travel_order__employee__user__first_name']
    date_hierarchy = 'date_time'
    ordering = ['date_time']


@admin.register(TravelExpense)
class TravelExpenseAdmin(admin.ModelAdmin):
    list_display = ['travel_order', 'total_expense', 'transportation', 'accommodation', 'meals', 'status', 'approved_by', 'payment_date']
    list_filter = ['status', 'approved_at', 'payment_date', 'created_at']
    search_fields = ['travel_order__employee__user__first_name', 'travel_order__destination']
    date_hierarchy = 'created_at'
    ordering = ['-created_at']
    readonly_fields = ['total_expense', 'approved_at', 'created_at', 'updated_at']

    actions = ['approve_expenses', 'reject_expenses', 'mark_as_paid']

    def approve_expenses(self, request, queryset):
        """Approve expenses"""
        from django.contrib import messages
        from django.utils import timezone
        updated = queryset.filter(status='PENDING').update(
            status='APPROVED',
            approved_by=request.user,
            approved_at=timezone.now()
        )
        messages.success(request, f"Approved {updated} expense(s)")
    approve_expenses.short_description = "Approve selected expenses"

    def reject_expenses(self, request, queryset):
        """Reject expenses"""
        from django.contrib import messages
        updated = queryset.filter(status='PENDING').update(status='REJECTED')
        messages.success(request, f"Rejected {updated} expense(s)")
    reject_expenses.short_description = "Reject selected expenses"

    def mark_as_paid(self, request, queryset):
        """Mark expenses as paid"""
        from django.contrib import messages
        from django.utils import timezone
        updated = queryset.filter(status='APPROVED').update(
            payment_date=timezone.now()
        )
        messages.success(request, f"Marked {updated} expense(s) as paid")
    mark_as_paid.short_description = "Mark selected expenses as paid"
