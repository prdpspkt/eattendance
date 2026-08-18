from django.contrib import admin
from django.utils import timezone

from .models import Attendance, DailyAttendance, Absence, OvertimeRecord

@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ['employee', 'origin', 'timestamp', 'punch_type', 'is_processed']
    list_filter = ['source', 'device', 'punch_type', 'is_processed', 'timestamp']
    search_fields = ['employee__user__first_name', 'employee__user__last_name',
                     'employee__employee_id', 'reason']
    date_hierarchy = 'timestamp'
    ordering = ['-timestamp']
    # timestamp stays editable: correcting a mistyped manual entry is the
    # whole point. Device-reported punches should not be edited, but that is a
    # judgement for the person, not something to block here.
    readonly_fields = ['created_at', 'source', 'recorded_by']

    def origin(self, obj):
        return obj.source_label
    origin.short_description = 'Source'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'employee__user', 'device', 'recorded_by'
        )

    def save_model(self, request, obj, form, change):
        """A punch created here by hand is a manual entry, and says so."""
        if not change and obj.device_id is None:
            obj.source = Attendance.SOURCE_MANUAL
            obj.recorded_by = request.user
        super().save_model(request, obj, form, change)
        # Rebuild the day so the correction shows up immediately.
        Attendance.process_daily_attendance(
            obj.employee, timezone.localtime(obj.timestamp).date()
        )


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


@admin.register(OvertimeRecord)
class OvertimeRecordAdmin(admin.ModelAdmin):
    """Overtime awaiting a job description and a signature.

    Records are created automatically by the daily rollup; what a person adds
    is the job performed, and then approval.
    """
    list_display = ['employee', 'date', 'start_at', 'end_at', 'hours',
                    'job_summary', 'hourly_rate', 'payable', 'status']
    list_filter = ['status', 'date', 'employee__department']
    search_fields = ['employee__user__first_name', 'employee__user__last_name',
                     'employee__employee_id', 'job_performed']
    date_hierarchy = 'date'
    ordering = ['-date']
    readonly_fields = ['created_at', 'updated_at', 'approved_by', 'approved_at', 'payable']

    fieldsets = (
        ('Period', {
            'fields': ('employee', 'date', 'start_at', 'end_at', 'hours'),
            'description': (
                'Hours are computed from the punches by the daily rollup, and refresh '
                'while the record is pending. Approving freezes them.'
            ),
        }),
        ('Justification', {
            'fields': ('job_performed', 'notes'),
            'description': 'What was worked on. This prints on the overtime report.',
        }),
        ('Payment', {'fields': ('hourly_rate', 'payable', 'status', 'approved_by', 'approved_at')}),
    )

    actions = ['approve_overtime', 'reject_overtime', 'reopen_overtime']

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('employee__user', 'approved_by')

    def job_summary(self, obj):
        if obj.needs_job_description:
            return '— not described'
        return obj.job_performed[:48] + ('…' if len(obj.job_performed) > 48 else '')
    job_summary.short_description = 'Job performed'

    def payable(self, obj):
        amount = obj.amount
        return '—' if amount is None else f'{amount}'
    payable.short_description = 'Amount'

    def approve_overtime(self, request, queryset):
        """Sign off, refusing anything that has no stated job.

        Overtime is paid on the strength of the work described; approving a
        blank one converts an unexplained gap into money.
        """
        approved = skipped = 0
        for record in queryset:
            if record.needs_job_description:
                skipped += 1
                continue
            record.approve(request.user)
            approved += 1
        if approved:
            self.message_user(request, f'Approved {approved} overtime record(s).')
        if skipped:
            self.message_user(
                request,
                f'{skipped} record(s) skipped: no job performed recorded. Add one, then approve.',
                level='warning',
            )
    approve_overtime.short_description = 'Approve overtime (requires a job description)'

    def reject_overtime(self, request, queryset):
        updated = queryset.update(
            status=OvertimeRecord.STATUS_REJECTED, approved_by=request.user,
            approved_at=timezone.now(),
        )
        self.message_user(request, f'Rejected {updated} overtime record(s).')
    reject_overtime.short_description = 'Reject overtime'

    def reopen_overtime(self, request, queryset):
        """Unfreeze a record so the rollup tracks it again."""
        updated = queryset.update(
            status=OvertimeRecord.STATUS_PENDING, approved_by=None, approved_at=None,
        )
        self.message_user(
            request,
            f'Reopened {updated} record(s). Their hours will follow the punches again '
            f'until they are approved.',
        )
    reopen_overtime.short_description = 'Reopen (back to pending)'
