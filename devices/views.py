from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.contrib.auth import get_user_model
from .models import Device, EmployeeDevice
from .forms import DeviceForm
from core.models import Employee
from core.forms import EmployeeCreateForm, EmployeeUserForm, EmployeeForm


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def device_list(request):
    """List all devices"""
    devices = Device.objects.all()
    employee_devices = EmployeeDevice.objects.select_related('employee__user', 'device').all()

    context = {
        'devices': devices,
        'employee_devices': employee_devices,
        'user': request.user,
    }
    return render(request, 'devices/device_list.html', context)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def device_create(request):
    """Create a new device"""

    if request.method == 'POST':
        form = DeviceForm(request.POST)
        if form.is_valid():
            device = form.save()
            messages.success(request, f"Device '{device.name}' created successfully!")
            return redirect('devices:device_detail', device_id=device.id)
        else:
            # Form has errors, print them for debugging
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = DeviceForm()

    context = {'form': form}
    return render(request, 'devices/device_form.html', context)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def device_update(request, device_id):
    """Update device"""

    device = get_object_or_404(Device, id=device_id)

    if request.method == 'POST':
        form = DeviceForm(request.POST, instance=device)
        if form.is_valid():
            form.save()
            messages.success(request, f"Device '{device.name}' updated successfully!")
            return redirect('devices:device_detail', device_id=device.id)
    else:
        form = DeviceForm(instance=device)

    context = {'form': form, 'device': device}
    return render(request, 'devices/device_form.html', context)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def device_detail(request, device_id):
    """Device detail page"""

    device = get_object_or_404(Device, id=device_id)
    employee_devices = EmployeeDevice.objects.filter(device=device).select_related('employee__user')

    context = {
        'device': device,
        'employee_devices': employee_devices,
    }
    return render(request, 'devices/device_detail.html', context)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
@require_POST
def sync_users(request, device_id):
    """Sync users from device"""

    device = get_object_or_404(Device, id=device_id)
    success, message = device.sync_users()

    if success:
        messages.success(request, message)
    else:
        messages.error(request, message)

    return redirect('devices:device_detail', device_id=device_id)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
@require_POST
def sync_attendance(request, device_id):
    """Sync attendance from device"""

    device = get_object_or_404(Device, id=device_id)
    success, message = device.sync_attendance()

    if success:
        messages.success(request, message)
    else:
        messages.error(request, message)

    return redirect('devices:device_detail', device_id=device_id)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
@require_POST
def test_connection(request, device_id):
    """Test connection to device"""

    device = get_object_or_404(Device, id=device_id)
    success, message = device.test_connection()

    if success:
        messages.success(request, f"{device.name}: {message}")
    else:
        messages.error(request, f"{device.name}: {message}")

    return redirect('devices:device_detail', device_id=device_id)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def unlinked_enrollments(request):
    """List unlinked enrollments"""

    unlinked = EmployeeDevice.objects.filter(employee__isnull=True).select_related('device')
    employees = Employee.objects.select_related('user').all()

    context = {
        'unlinked_enrollments': unlinked,
        'employees': employees,
    }
    return render(request, 'devices/unlinked_enrollments.html', context)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
@require_POST
def link_employee(request, enrollment_id):
    """Link an unlinked enrollment to an employee"""

    enrollment = get_object_or_404(EmployeeDevice, id=enrollment_id)
    employee_id = request.POST.get('employee_id')

    if not employee_id:
        messages.error(request, "Please select an employee")
        return redirect('devices:unlinked_enrollments')

    employee = get_object_or_404(Employee, id=employee_id)
    enrollment.employee = employee
    enrollment.save()

    messages.success(request, f"Successfully linked {employee.user.get_full_name()} to device {enrollment.device.name}")
    return redirect('devices:unlinked_enrollments')


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
@require_POST
def unlink_employee(request, enrollment_id):
    """Unlink an employee from a device"""

    enrollment = get_object_or_404(EmployeeDevice, id=enrollment_id)
    device_name = enrollment.device.name
    employee_name = enrollment.employee.user.get_full_name() if enrollment.employee else 'Unknown'

    enrollment.employee = None
    enrollment.save()

    messages.success(request, f"Unlinked {employee_name} from {device_name}")
    return redirect('devices:device_detail', device_id=enrollment.device.id)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def employee_management(request):
    """Employee management page"""

    # Get ordering parameter
    order_by = request.GET.get('order_by', 'user__first_name')
    order_direction = request.GET.get('order_direction', 'asc')

    # Validate order_by field to prevent SQL injection
    valid_order_fields = [
        'user__first_name', 'user__last_name', 'user__email',
        'employee_id', 'department__name', 'join_date',
        'employment_status'
    ]

    if order_by not in valid_order_fields:
        order_by = 'user__first_name'

    # Apply ordering
    order_prefix = '-' if order_direction == 'desc' else ''
    employees = Employee.objects.select_related('user', 'department').order_by(
        f'{order_prefix}{order_by}'
    )
    devices = Device.objects.all()

    context = {
        'employees': employees,
        'devices': devices,
        'order_by': order_by,
        'order_direction': order_direction,
    }
    return render(request, 'devices/employee_management.html', context)


@login_required
@permission_required('core.can_manage_employees', raise_exception=False, login_url='dashboard')
def employee_create(request):
    """Create a new employee with user account"""

    User = get_user_model()

    if request.method == 'POST':
        form = EmployeeCreateForm(request.POST, request.FILES)

        if form.is_valid():
            try:
                # Create user account
                user = User.objects.create_user(
                    username=form.cleaned_data['username'],
                    email=form.cleaned_data['email'],
                    password=form.cleaned_data.get('password') or User.objects.make_random_password(),
                    first_name=form.cleaned_data['first_name'],
                    last_name=form.cleaned_data['last_name'],
                    phone=form.cleaned_data.get('phone', ''),
                    role=form.cleaned_data['role']
                )

                # Handle profile picture
                if request.FILES.get('profile_picture'):
                    user.profile_picture = request.FILES['profile_picture']
                    user.save()

                # Create employee profile
                employee = Employee.objects.create(
                    user=user,
                    employee_id=form.cleaned_data['employee_id'],
                    department=form.cleaned_data.get('department'),
                    gender=form.cleaned_data.get('gender'),
                    date_of_birth=form.cleaned_data.get('date_of_birth'),
                    address=form.cleaned_data.get('address'),
                    city=form.cleaned_data.get('city'),
                    postal_code=form.cleaned_data.get('postal_code'),
                    emergency_contact_name=form.cleaned_data.get('emergency_contact_name'),
                    emergency_contact_phone=form.cleaned_data.get('emergency_contact_phone'),
                    blood_group=form.cleaned_data.get('blood_group'),
                    join_date=form.cleaned_data['join_date'],
                    employment_status=form.cleaned_data['employment_status'],
                    device_uid=form.cleaned_data.get('device_uid')
                )

                messages.success(
                    request,
                    f"Employee '{user.get_full_name()}' created successfully! "
                    f"Username: {user.username}"
                )
                return redirect('devices:employee_management')

            except Exception as e:
                messages.error(request, f"Error creating employee: {str(e)}")
                if user:
                    user.delete()
        else:
            # Form validation errors
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = EmployeeCreateForm()

    context = {'form': form}
    return render(request, 'devices/employee_form.html', context)


@login_required
@permission_required('core.can_manage_employees', raise_exception=False, login_url='dashboard')
def employee_edit(request, employee_id):
    """Edit an existing employee"""

    employee = get_object_or_404(Employee, id=employee_id)
    user = employee.user

    if request.method == 'POST':
        user_form = EmployeeUserForm(request.POST, request.FILES, instance=user)
        emp_form = EmployeeForm(request.POST, instance=employee)

        if user_form.is_valid() and emp_form.is_valid():
            try:
                # Update user
                user_form.save()

                # Update employee
                emp_form.save()

                messages.success(
                    request,
                    f"Employee '{user.get_full_name()}' updated successfully!"
                )
                return redirect('devices:employee_management')

            except Exception as e:
                messages.error(request, f"Error updating employee: {str(e)}")
        else:
            # Form validation errors
            for field, errors in user_form.errors.items():
                for error in errors:
                    messages.error(request, f"User {field}: {error}")
            for field, errors in emp_form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        user_form = EmployeeUserForm(instance=user)
        emp_form = EmployeeForm(instance=employee)

    context = {
        'user_form': user_form,
        'emp_form': emp_form,
        'employee': employee,
        'is_edit': True
    }
    return render(request, 'devices/employee_form.html', context)


@login_required
@permission_required('core.can_manage_employees', raise_exception=False, login_url='dashboard')
def employee_detail(request, employee_id):
    """View employee details"""

    employee = get_object_or_404(Employee, id=employee_id)
    employee_devices = EmployeeDevice.objects.filter(employee=employee).select_related('device')

    # Get attendance statistics
    from attendance.models import DailyAttendance
    from datetime import date
    from django.db.models import Sum, Count

    current_month = date.today().month
    current_year = date.today().year

    # This month's attendance
    monthly_attendance = DailyAttendance.objects.filter(
        employee=employee,
        date__month=current_month,
        date__year=current_year
    )

    # Calculate statistics
    stats = {
        'present_days': monthly_attendance.filter(status='PRESENT').count(),
        'late_days': monthly_attendance.filter(status='LATE').count(),
        'absent_days': monthly_attendance.filter(status='ABSENT').count(),
        'half_days': monthly_attendance.filter(status='HALF_DAY').count(),
        'total_working_hours': monthly_attendance.aggregate(total=Sum('working_hours'))['total'] or 0,
        'total_overtime': monthly_attendance.aggregate(total=Sum('overtime_hours'))['total'] or 0,
    }

    context = {
        'employee': employee,
        'employee_devices': employee_devices,
        'stats': stats,
        'current_month': current_month,
        'current_year': current_year,
    }
    return render(request, 'devices/employee_detail.html', context)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def todays_attendance(request):
    """View today's attendance log across all devices"""

    from attendance.models import Attendance, DailyAttendance
    from datetime import date, datetime

    today = date.today()

    # Get today's attendance records
    attendances = Attendance.objects.filter(
        timestamp__date=today
    ).select_related('employee__user', 'device').order_by('-timestamp')

    # Get today's daily attendance summaries
    daily_attendances = DailyAttendance.objects.filter(
        date=today
    ).select_related('employee__user', 'shift')

    # Calculate statistics
    total_employees = Employee.objects.filter(employment_status='ACTIVE').count()
    present_count = daily_attendances.filter(status__in=['PRESENT', 'LATE']).count()
    absent_count = daily_attendances.filter(status='ABSENT').count()
    late_count = daily_attendances.filter(status='LATE').count()

    # Get recent activity (last 20 punches)
    recent_activity = attendances[:20]

    # Group attendance by device for device summary
    from django.db.models import Count
    device_stats = attendances.values('device__name', 'device__location').annotate(
        count=Count('id')
    ).order_by('-count')

    context = {
        'today': today,
        'attendances': attendances,
        'daily_attendances': daily_attendances,
        'recent_activity': recent_activity,
        'device_stats': device_stats,
        'stats': {
            'total_employees': total_employees,
            'present': present_count,
            'absent': absent_count,
            'late': late_count,
            'pending': total_employees - present_count - absent_count,
        }
    }
    return render(request, 'devices/todays_attendance.html', context)

