from django.contrib import admin
from django.utils.html import format_html
from .models import Device, EmployeeDevice

@admin.register(EmployeeDevice)
class EmployeeDeviceAdmin(admin.ModelAdmin):
    """Admin interface for EmployeeDevice mapping"""
    list_display = ['device', 'device_uid', 'employee', 'created_at']
    list_filter = ['device', 'created_at']
    search_fields = ['device_uid', 'employee__user__first_name', 'employee__user__last_name', 'employee__employee_id']
    ordering = ['-created_at']

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('employee__user', 'device')


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ['name', 'ip_address', 'port', 'location', 'is_active', 'last_sync', 'last_sync_status', 'sync_buttons']
    list_filter = ['is_active', 'created_at']
    search_fields = ['name', 'ip_address', 'location']
    ordering = ['name']

    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'ip_address', 'port', 'password', 'location')
        }),
        ('Configuration', {
            'fields': ('is_active', 'connection_timeout')
        }),
        ('Sync Information', {
            'fields': ('last_sync', 'last_sync_status',),
            'classes': ('collapse',)
        }),
    )

    readonly_fields = ['last_sync', 'last_sync_status']

    def sync_buttons(self, obj):
        """Custom buttons for syncing"""
        return format_html(
            '<a class="button" href="/admin/devices/device/{}/sync_users/">Sync Users</a> | '
            '<a class="button" href="/admin/devices/device/{}/sync/">Sync Attendance</a>',
            obj.id, obj.id
        )
    sync_buttons.short_description = 'Actions'

    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path('<path:device_id>/sync_users/', self.admin_site.admin_view(self.sync_users_view),
                 name='sync_users_device'),
            path('<path:device_id>/sync/', self.admin_site.admin_view(self.sync_attendance_view),
                 name='sync_device'),
        ]
        return custom_urls + urls

    def sync_users_view(self, request, device_id):
        """Custom view to sync users from device"""
        from django.shortcuts import redirect
        from django.contrib import messages

        try:
            device = Device.objects.get(pk=device_id)
            success, message = device.sync_users()
            if success:
                messages.success(request, message)
            else:
                messages.error(request, f"Sync failed: {message}")
        except Exception as e:
            messages.error(request, f"Error: {str(e)}")

        return redirect('/admin/devices/device/')

    def sync_attendance_view(self, request, device_id):
        """Custom view to sync attendance from device"""
        from django.shortcuts import redirect
        from django.contrib import messages

        try:
            device = Device.objects.get(pk=device_id)
            success, message = device.sync_attendance()
            if success:
                messages.success(request, message)
            else:
                messages.error(request, f"Sync failed: {message}")
        except Exception as e:
            messages.error(request, f"Error: {str(e)}")

        return redirect('/admin/devices/device/')

    actions = ['test_connection', 'sync_users_devices', 'sync_attendance_devices']

    def test_connection(self, request, queryset):
        """Admin action to test connection"""
        from django.contrib import messages
        for device in queryset:
            success, message = device.test_connection()
            if success:
                messages.success(request, f"{device.name}: {message}")
            else:
                messages.error(request, f"{device.name}: {message}")
    test_connection.short_description = "Test connection to selected devices"

    def sync_users_devices(self, request, queryset):
        """Admin action to sync users from selected devices"""
        from django.contrib import messages
        for device in queryset:
            success, message = device.sync_users()
            if success:
                messages.success(request, f"{device.name}: {message}")
            else:
                messages.error(request, f"{device.name}: {message}")
    sync_users_devices.short_description = "Sync users from selected devices"

    def sync_attendance_devices(self, request, queryset):
        """Admin action to sync attendance from selected devices"""
        from django.contrib import messages
        for device in queryset:
            success, message = device.sync_attendance()
            if success:
                messages.success(request, f"{device.name}: {message}")
            else:
                messages.error(request, f"{device.name}: {message}")
    sync_attendance_devices.short_description = "Sync attendance from selected devices"
