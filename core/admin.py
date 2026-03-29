from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, Department, Employee, Shift, EmployeeShift

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ['username', 'email', 'first_name', 'last_name', 'role', 'is_active', 'created_at']
    list_filter = ['role', 'is_active', 'created_at']
    search_fields = ['username', 'email', 'first_name', 'last_name']
    ordering = ['-created_at']

    fieldsets = BaseUserAdmin.fieldsets + (
        ('Additional Info', {'fields': ('role', 'phone', 'profile_picture')}),
    )

    add_fieldsets = BaseUserAdmin.add_fieldsets + (
        ('Additional Info', {'fields': ('role', 'phone', 'email')}),
    )


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ['name', 'description', 'created_at']
    search_fields = ['name']
    ordering = ['name']


class EmployeeShiftInline(admin.TabularInline):
    model = EmployeeShift
    extra = 0
    fk_name = 'employee'


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ['get_full_name', 'employee_id', 'department', 'get_status', 'join_date', 'created_at']
    list_filter = ['employment_status', 'department', 'gender', 'join_date']
    search_fields = ['employee_id', 'user__first_name', 'user__last_name', 'user__email']
    ordering = ['-join_date']
    inlines = [EmployeeShiftInline]

    def get_full_name(self, obj):
        return obj.user.get_full_name()
    get_full_name.short_description = 'Name'

    def get_status(self, obj):
        return obj.employment_status
    get_status.short_description = 'Status'


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ['name', 'start_time', 'end_time', 'late_grace_minutes', 'early_exit_minutes', 'break_duration_minutes', 'is_active']
    list_filter = ['is_active']
    search_fields = ['name']
    ordering = ['start_time']


@admin.register(EmployeeShift)
class EmployeeShiftAdmin(admin.ModelAdmin):
    list_display = ['employee', 'shift', 'effective_date', 'end_date', 'is_active']
    list_filter = ['is_active', 'effective_date', 'shift']
    search_fields = ['employee__user__first_name', 'employee__user__last_name', 'employee__employee_id']
    ordering = ['-effective_date']
