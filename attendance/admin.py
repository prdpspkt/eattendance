from django.contrib import admin
from .models import Attendance, DailyAttendance, Absence

@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ['employee', 'device', 'timestamp', 'punch_type', 'is_processed']
    list_filter = ['device', 'punch_type', 'is_processed', 'timestamp']
    search_fields = ['employee__user__first_name', 'employee__user__last_name', 'employee__employee_id']
    date_hierarchy = 'timestamp'
    ordering = ['-timestamp']
    readonly_fields = ['timestamp', 'created_at']


@admin.register(DailyAttendance)
class DailyAttendanceAdmin(admin.ModelAdmin):
    list_display = ['employee', 'date', 'shift', 'check_in', 'check_out', 'working_hours', 'overtime_hours', 'status']
    list_filter = ['status', 'shift', 'date']
    search_fields = ['employee__user__first_name', 'employee__user__last_name', 'employee__employee_id']
    date_hierarchy = 'date'
    ordering = ['-date']
    readonly_fields = ['working_hours', 'overtime_hours', 'late_minutes', 'early_exit_minutes']

    actions = ['process_daily_attendances', 'mark_present', 'mark_absent']

    def process_daily_attendances(self, request, queryset):
        """Process attendance records"""
        from django.contrib import messages
        processed = 0
        for daily_att in queryset:
            Attendance.process_daily_attendance(daily_att.employee, daily_att.date)
            processed += 1
        messages.success(request, f"Processed {processed} daily attendance records")
    process_daily_attendances.short_description = "Process selected daily attendances"

    def mark_present(self, request, queryset):
        """Mark as present"""
        queryset.update(status='PRESENT')
        self.message_user(request, "Marked selected records as PRESENT")
    mark_present.short_description = "Mark as Present"

    def mark_absent(self, request, queryset):
        """Mark as absent"""
        queryset.update(status='ABSENT')
        self.message_user(request, "Marked selected records as ABSENT")
    mark_absent.short_description = "Mark as Absent"


@admin.register(Absence)
class AbsenceAdmin(admin.ModelAdmin):
    list_display = ['employee', 'date', 'status', 'approved_by', 'created_at']
    list_filter = ['status', 'date', 'created_at']
    search_fields = ['employee__user__first_name', 'employee__user__last_name', 'employee__employee_id']
    date_hierarchy = 'date'
    ordering = ['-date']

    actions = ['approve_absences', 'reject_absences']

    def approve_absences(self, request, queryset):
        """Approve selected absences"""
        from django.contrib import messages
        from django.utils import timezone
        updated = queryset.filter(status='PENDING').update(
            status='APPROVED',
            approved_by=request.user,
            approved_at=timezone.now()
        )
        messages.success(request, f"Approved {updated} absence(s)")
    approve_absences.short_description = "Approve selected absences"

    def reject_absences(self, request, queryset):
        """Reject selected absences"""
        from django.contrib import messages
        updated = queryset.filter(status='PENDING').update(status='REJECTED')
        messages.success(request, f"Rejected {updated} absence(s)")
    reject_absences.short_description = "Reject selected absences"
