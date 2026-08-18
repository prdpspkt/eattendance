from django.contrib import admin
from django.utils.html import format_html
from .models import Device, DeviceCommand, EmployeeDevice

@admin.register(EmployeeDevice)
class EmployeeDeviceAdmin(admin.ModelAdmin):
    """Admin interface for EmployeeDevice mapping"""
    list_display = ['device', 'device_uid', 'employee', 'created_at']
    list_filter = ['device', 'created_at']
    search_fields = ['device_uid', 'employee__user__first_name', 'employee__user__last_name', 'employee__employee_id']
    ordering = ['-created_at']

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('employee__user', 'device')


@admin.register(DeviceCommand)
class DeviceCommandAdmin(admin.ModelAdmin):
    """Commands queued for devices to collect over the ADMS push protocol."""
    list_display = ['device', 'short_command', 'status', 'return_code', 'created_at', 'completed_at']
    list_filter = ['status', 'device', 'created_at']
    search_fields = ['command', 'device__name', 'device__serial_number']
    readonly_fields = ['status', 'return_code', 'response', 'sent_at', 'completed_at', 'created_at']
    ordering = ['-created_at']

    def short_command(self, obj):
        return obj.command[:60] + ('...' if len(obj.command) > 60 else '')
    short_command.short_description = 'Command'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('device')


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ['name', 'ip_address', 'serial_number', 'connection_mode', 'location', 'is_active', 'last_sync', 'last_sync_status', 'sync_buttons']
    list_filter = ['is_active', 'push_enabled', 'created_at']
    search_fields = ['name', 'ip_address', 'location', 'serial_number',
                     'device_id', 'mac_address']
    ordering = ['name']

    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'ip_address', 'port', 'password', 'location')
        }),
        ('Configuration', {
            'fields': ('is_active', 'connection_timeout')
        }),
        ('Push Protocol (ADMS/WDMS)', {
            'fields': ('serial_number', 'device_id', 'mac_address', 'push_enabled',
                       'last_seen', 'last_ip', 'firmware_version',
                       'device_info', 'attlog_stamp', 'operlog_stamp'),
            'description': (
                'Point the terminal at this server under Comm. &rarr; Ethernet &rarr; '
                'Cloud Server / ADMS. Only the serial number identifies a device when '
                'it connects - it is the one value every request carries. Device ID and '
                'MAC address are reported by the terminal afterwards and are there to '
                'help you match this record to a physical unit.'
            ),
        }),
        ('Sync Information', {
            'fields': ('last_sync', 'last_sync_status',),
            'classes': ('collapse',)
        }),
    )

    # Everything the device reports about itself is read-only: typing a value
    # here would just be overwritten on the terminal's next upload.
    readonly_fields = ['last_sync', 'last_sync_status', 'last_seen', 'last_ip',
                       'push_enabled', 'device_id', 'mac_address',
                       'firmware_version', 'device_info', 'attlog_stamp', 'operlog_stamp']

    def connection_mode(self, obj):
        if not obj.push_enabled:
            return 'Pull (SDK)'
        return 'Push - online' if obj.is_online else 'Push - offline'
    connection_mode.short_description = 'Mode'

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

    actions = ['test_connection', 'sync_users_devices', 'sync_attendance_devices',
               'queue_check', 'queue_info', 'queue_reboot',
               'queue_user_query', 'request_all_attendance']

    def _for_push_devices(self, request, queryset, action, label):
        """Run ``action(device)`` on each selected device that is in push mode.

        A device still on the pull SDK has no command queue to put anything in,
        so it is reported rather than silently skipped.
        """
        from django.contrib import messages
        queued = 0
        for device in queryset:
            if not device.push_enabled:
                messages.warning(
                    request,
                    f"{device.name}: not using the push protocol, command not queued."
                )
                continue
            action(device)
            queued += 1
        if queued:
            messages.success(
                request,
                f"Queued '{label}' on {queued} device(s). "
                "They will pick it up on their next poll, usually within seconds."
            )
        return queued

    def _queue(self, request, queryset, command, label):
        """Queue a plain ADMS command on each selected push-enabled device."""
        return self._for_push_devices(
            request, queryset,
            lambda device: device.queue_command(command, created_by=request.user),
            label,
        )

    def queue_check(self, request, queryset):
        """Ask the device to re-send anything the server has not acknowledged."""
        self._queue(request, queryset, 'CHECK', 'CHECK')
    queue_check.short_description = "Push: queue CHECK (re-send pending data)"

    def queue_info(self, request, queryset):
        self._queue(request, queryset, 'INFO', 'INFO')
    queue_info.short_description = "Push: queue INFO (report device status)"

    def queue_reboot(self, request, queryset):
        self._queue(request, queryset, 'REBOOT', 'REBOOT')
    queue_reboot.short_description = "Push: queue REBOOT"

    def queue_user_query(self, request, queryset):
        """Pull the device's user table.

        This is the push-mode equivalent of "Sync Users": the server cannot
        read the device, so it asks and the device uploads. Users it does not
        recognise land in Unlinked Enrollments to be attached to an employee.
        """
        self._for_push_devices(
            request, queryset,
            lambda device: device.queue_user_query(created_by=request.user),
            'DATA QUERY USERINFO',
        )
    queue_user_query.short_description = "Push: pull employee/user list from device"

    def request_all_attendance(self, request, queryset):
        """Re-request the device's whole attendance log by clearing the stamp."""
        from django.contrib import messages

        queued = self._for_push_devices(
            request, queryset,
            lambda device: device.request_all_attendance(created_by=request.user),
            'CHECK (attendance stamp reset)',
        )
        if queued:
            # Say this plainly: the stamp is only consulted at a handshake, so
            # the replay may not begin until the device next reconnects.
            messages.info(
                request,
                "The resume point was cleared, so each device will re-send its full "
                "attendance log. Devices read that point when they handshake, which "
                "may not be immediate - queue a REBOOT as well to force one. "
                "Re-sent punches are de-duplicated, so nothing will be recorded twice."
            )
    request_all_attendance.short_description = "Push: re-request ALL attendance (reset resume point)"

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
