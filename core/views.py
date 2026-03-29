from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout, update_session_auth_hash, authenticate, login
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.forms import PasswordChangeForm
from django.utils import timezone
from datetime import date, datetime, timedelta
from django.db.models import Sum, Q, Count
from django.contrib import messages

from .models import Employee, Shift
from .forms import ShiftForm
from leaves.models import LeaveRequest, LeaveType, LeaveBalance
from travel_orders.models import TravelOrder
from attendance.models import DailyAttendance


def custom_login(request):
    """Custom login view that redirects superusers to device management"""
    if request.user.is_authenticated:
        # User is already logged in, redirect to dashboard
        return redirect('dashboard')

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            return redirect('dashboard')
        else:
            messages.error(request, 'Invalid username or password.')

    return render(request, 'registration/login.html')


def dashboard(request):
    """Employee dashboard view"""
    # Check if user is authenticated
    if not request.user.is_authenticated:
        return redirect('login')

    # Get employee profile
    employee = None
    has_employee_profile = False

    try:
        employee = request.user.employee
        has_employee_profile = True
    except Employee.DoesNotExist:
        # User doesn't have an employee profile
        messages.warning(request, "No employee profile is associated with your account. Please contact the administrator.")

    # Get current date info
    today = date.today()
    current_month = today.month
    current_year = today.year

    # Initialize variables
    today_attendance = None
    monthly_stats = {
        'total_days': 0,
        'present_days': 0,
        'late_days': 0,
        'total_overtime': 0,
    }
    leave_balances = []
    pending_leaves = []
    pending_travels = []
    recent_attendance = []

    if has_employee_profile:
        # Get today's attendance
        today_attendance = DailyAttendance.objects.filter(
            employee=employee,
            date=today
        ).first()

        # Get this month's attendance
        monthly_attendance = DailyAttendance.objects.filter(
            employee=employee,
            date__month=current_month,
            date__year=current_year
        )

        # Calculate monthly stats
        total_days = monthly_attendance.count()
        present_days = monthly_attendance.filter(status='PRESENT').count()
        late_days = monthly_attendance.filter(status='LATE').count()
        total_overtime = monthly_attendance.aggregate(
            total=Sum('overtime_hours')
        )['total'] or 0

        monthly_stats = {
            'total_days': total_days,
            'present_days': present_days,
            'late_days': late_days,
            'total_overtime': total_overtime,
        }

        # Get leave balances
        leave_balances = LeaveBalance.objects.filter(
            employee=employee,
            year=current_year
        )

        # Get pending leave requests
        pending_leaves = LeaveRequest.objects.filter(
            employee=employee,
            status='PENDING'
        ).order_by('-created_at')[:5]

        # Get pending travel orders
        pending_travels = TravelOrder.objects.filter(
            employee=employee,
            status='PENDING'
        ).order_by('-created_at')[:5]

        # Recent attendance (last 7 days)
        recent_attendance = DailyAttendance.objects.filter(
            employee=employee,
            date__gte=today - timedelta(days=7)
        ).order_by('-date')

    context = {
        'employee': employee,
        'has_employee_profile': has_employee_profile,
        'today_attendance': today_attendance,
        'monthly_stats': monthly_stats,
        'leave_balances': leave_balances,
        'pending_leaves': pending_leaves,
        'pending_travels': pending_travels,
        'recent_attendance': recent_attendance,
    }

    return render(request, 'core/dashboard.html', context)


@login_required
def profile(request):
    """Employee profile view"""
    try:
        employee = request.user.employee
    except Employee.DoesNotExist:
        messages.error(request, "No employee profile found.")
        return redirect('dashboard')

    if request.method == 'POST':
        # Update profile
        employee.user.phone = request.POST.get('phone')
        employee.user.email = request.POST.get('email')
        employee.address = request.POST.get('address')
        employee.city = request.POST.get('city')
        employee.postal_code = request.POST.get('postal_code')
        employee.emergency_contact_name = request.POST.get('emergency_contact_name')
        employee.emergency_contact_phone = request.POST.get('emergency_contact_phone')

        if 'profile_picture' in request.FILES:
            employee.user.profile_picture = request.FILES['profile_picture']

        employee.user.save()
        employee.save()
        messages.success(request, 'Profile updated successfully!')
        return redirect('profile')

    context = {
        'employee': employee,
    }
    return render(request, 'core/profile.html', context)


@login_required
def my_attendance(request):
    """View attendance history"""
    try:
        employee = request.user.employee
    except Employee.DoesNotExist:
        messages.error(request, "No employee profile found.")
        return redirect('dashboard')

    # Get filter parameters
    month = request.GET.get('month')
    year = request.GET.get('year')

    # Get ordering parameter
    order_by = request.GET.get('order_by', 'date')
    order_direction = request.GET.get('order_direction', 'desc')

    # Validate order_by field
    valid_order_fields = ['date', 'check_in', 'check_out', 'status', 'overtime_hours']
    if order_by not in valid_order_fields:
        order_by = 'date'

    # Apply ordering
    order_prefix = '-' if order_direction == 'desc' else ''
    attendances = DailyAttendance.objects.filter(employee=employee)

    if month and year:
        attendances = attendances.filter(date__month=month, date__year=year)

    attendances = attendances.order_by(f'{order_prefix}{order_by}')[:50]  # Last 50 records

    context = {
        'employee': employee,
        'attendances': attendances,
        'selected_month': month,
        'selected_year': year,
        'order_by': order_by,
        'order_direction': order_direction,
    }
    return render(request, 'core/my_attendance.html', context)


@login_required
def attendance_calendar(request):
    """View attendance calendar"""
    # Check if viewing another employee's calendar
    employee_id = request.GET.get('employee_id')

    if employee_id:
        # Admin viewing another employee's calendar
        if not (request.user.is_superuser or request.user.role == 'OFFICE_ADMIN'):
            messages.error(request, "You don't have permission to view other employees' calendars.")
            return redirect('dashboard')
        employee = get_object_or_404(Employee, id=employee_id)
    else:
        # Employee viewing their own calendar
        try:
            employee = request.user.employee
        except Employee.DoesNotExist:
            messages.error(request, "No employee profile found.")
            return redirect('dashboard')

    # Get month and year from request, default to current month
    current_date = date.today()
    month = int(request.GET.get('month', current_date.month))
    year = int(request.GET.get('year', current_date.year))

    # Validate month and year
    if month < 1 or month > 12:
        month = current_date.month
    if year < 2020 or year > 2030:
        year = current_date.year

    # Get attendance for the selected month
    attendances = DailyAttendance.objects.filter(
        employee=employee,
        date__year=year,
        date__month=month
    )

    # Create a dictionary of attendance data by date
    attendance_by_date = {
        attendance.date.day: attendance
        for attendance in attendances
    }

    # Also create a list for easier template iteration
    attendance_list = list(attendances)

    # Get leaves for the selected month
    from leaves.models import LeaveRequest
    leaves = LeaveRequest.objects.filter(
        employee=employee,
        start_date__lte=date(year, month, 31),
        end_date__gte=date(year, month, 1),
        status='APPROVED'
    )

    # Create a dictionary to track which dates have leave
    leave_by_date = {}
    for leave in leaves:
        current = leave.start_date
        while current <= leave.end_date:
            if current.month == month and current.year == year:
                leave_by_date[current.day] = leave
            current += timedelta(days=1)

    # Get todos/tasks for the selected month (if you have a Task model)
    # For now, we'll create a placeholder structure
    todos_by_date = {}

    # Get calendar data
    import calendar
    cal = calendar.monthcalendar(year, month)
    month_name = calendar.month_name[month]

    # Get previous and next month navigation
    if month == 1:
        prev_month, prev_year = 12, year - 1
    else:
        prev_month, prev_year = month - 1, year

    if month == 12:
        next_month, next_year = 1, year + 1
    else:
        next_month, next_year = month + 1, year

    # Calculate statistics
    stats = {
        'present': attendances.filter(status='PRESENT').count(),
        'late': attendances.filter(status='LATE').count(),
        'absent': attendances.filter(status='ABSENT').count(),
        'half_day': attendances.filter(status='HALF_DAY').count(),
        'on_leave': attendances.filter(status='ON_LEAVE').count(),
        'total_overtime': attendances.aggregate(total=Sum('overtime_hours'))['total'] or 0,
        'total_working_hours': attendances.aggregate(total=Sum('working_hours'))['total'] or 0,
        'leave_days': len(leave_by_date),
    }

    context = {
        'employee': employee,
        'cal': cal,
        'month': month,
        'year': year,
        'month_name': month_name,
        'attendance_by_date': attendance_by_date,
        'attendance_list': attendance_list,
        'leave_by_date': leave_by_date,
        'todos_by_date': todos_by_date,
        'leaves': leaves,
        'prev_month': prev_month,
        'prev_year': prev_year,
        'next_month': next_month,
        'next_year': next_year,
        'stats': stats,
        'viewing_other': employee_id is not None,
    }
    return render(request, 'core/attendance_calendar.html', context)


@login_required
def my_leaves(request):
    """View leave requests and balances"""
    try:
        employee = request.user.employee
    except Employee.DoesNotExist:
        messages.error(request, "No employee profile found.")
        return redirect('dashboard')

    current_year = date.today().year

    # Get leave balances
    leave_balances = LeaveBalance.objects.filter(
        employee=employee,
        year=current_year
    )

    # Get leave requests
    leave_requests = LeaveRequest.objects.filter(
        employee=employee
    ).order_by('-created_at')

    context = {
        'employee': employee,
        'leave_balances': leave_balances,
        'leave_requests': leave_requests,
    }
    return render(request, 'core/my_leaves.html', context)


@login_required
def request_leave(request):
    """Submit leave request"""
    # Check if creating leave for specific employee (admin function)
    employee_id = request.GET.get('employee_id')

    if employee_id:
        # Admin creating leave for another employee
        if not (request.user.is_superuser or request.user.role == 'OFFICE_ADMIN'):
            messages.error(request, "You don't have permission to create leave for other employees.")
            return redirect('dashboard')
        employee = get_object_or_404(Employee, id=employee_id)
    else:
        # Employee creating their own leave request
        try:
            employee = request.user.employee
        except Employee.DoesNotExist:
            messages.error(request, "No employee profile found.")
            return redirect('dashboard')

    leave_types = LeaveType.objects.filter(is_active=True)

    if request.method == 'POST':
        leave_type_id = request.POST.get('leave_type')
        start_date_str = request.POST.get('start_date')
        end_date_str = request.POST.get('end_date')
        reason = request.POST.get('reason')

        try:
            from datetime import datetime
            leave_type = LeaveType.objects.get(id=leave_type_id)

            # Parse date strings to date objects
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()

            # Check for overlapping leave requests
            overlapping_leaves = LeaveRequest.objects.filter(
                employee=employee,
                status__in=['PENDING', 'APPROVED']
            ).filter(
                Q(start_date__lte=start_date, end_date__gte=start_date) |
                Q(start_date__lte=end_date, end_date__gte=end_date) |
                Q(start_date__gte=start_date, end_date__lte=end_date)
            )

            if overlapping_leaves.exists():
                overlapping = overlapping_leaves.first()
                messages.error(request, f'You already have a leave request during this period ({overlapping.start_date} to {overlapping.end_date}). Please choose different dates.')
                context = {
                    'employee': employee,
                    'leave_types': leave_types,
                    'viewing_other': employee_id is not None,
                }
                return render(request, 'core/request_leave.html', context)

            # Check for overlapping travel orders
            from travel_orders.models import TravelOrder
            overlapping_travels = TravelOrder.objects.filter(
                employee=employee,
                status__in=['PENDING', 'APPROVED']
            ).filter(
                Q(start_date__date__lte=start_date, end_date__date__gte=start_date) |
                Q(start_date__date__lte=end_date, end_date__date__gte=end_date) |
                Q(start_date__date__gte=start_date, end_date__date__lte=end_date)
            )

            if overlapping_travels.exists():
                overlapping = overlapping_travels.first()
                messages.error(request, f'You already have a travel order during this period ({overlapping.start_date|date:"d M Y"} to {overlapping.end_date|date:"d M Y"}). Please choose different dates.')
                context = {
                    'employee': employee,
                    'leave_types': leave_types,
                    'viewing_other': employee_id is not None,
                }
                return render(request, 'core/request_leave.html', context)

            # Create leave request
            leave_request = LeaveRequest.objects.create(
                employee=employee,
                leave_type=leave_type,
                start_date=start_date,
                end_date=end_date,
                reason=reason
            )

            # Handle attachment
            if 'attachment' in request.FILES:
                leave_request.attachment = request.FILES['attachment']
                leave_request.save()

            messages.success(request, f'Leave request for {employee.user.get_full_name()} submitted successfully!')
            if employee_id:
                return redirect(f"{reverse('my_leaves')}?employee_id={employee_id}")
            return redirect('my_leaves')

        except Exception as e:
            messages.error(request, f'Error submitting leave request: {str(e)}')

    context = {
        'employee': employee,
        'leave_types': leave_types,
        'viewing_other': employee_id is not None,
    }
    return render(request, 'core/request_leave.html', context)


@login_required
def my_travel_orders(request):
    """View travel orders"""
    # Check if viewing specific employee's travel orders (admin view)
    employee_id = request.GET.get('employee_id')

    if employee_id:
        # Admin viewing another employee's travel orders
        if not (request.user.is_superuser or request.user.role == 'OFFICE_ADMIN'):
            messages.error(request, "You don't have permission to view other employees' travel orders.")
            return redirect('dashboard')
        employee = get_object_or_404(Employee, id=employee_id)
        # Check if this is a request to create a new travel order
        if request.GET.get('create') == 'true':
            return request_travel_order(request)
    else:
        # Employee viewing their own travel orders
        try:
            employee = request.user.employee
        except Employee.DoesNotExist:
            messages.error(request, "No employee profile found.")
            return redirect('dashboard')

    travel_orders = TravelOrder.objects.filter(
        employee=employee
    ).order_by('-created_at')

    context = {
        'employee': employee,
        'travel_orders': travel_orders,
        'viewing_other': employee_id is not None,
    }
    return render(request, 'core/my_travel_orders.html', context)


@login_required
def request_travel_order(request):
    """Submit travel order request"""
    # Check if creating travel order for specific employee (admin function)
    employee_id = request.GET.get('employee_id')

    if employee_id:
        # Admin creating travel order for another employee
        if not (request.user.is_superuser or request.user.role == 'OFFICE_ADMIN'):
            messages.error(request, "You don't have permission to create travel orders for other employees.")
            return redirect('dashboard')
        employee = get_object_or_404(Employee, id=employee_id)
    else:
        # Employee creating their own travel order
        try:
            employee = request.user.employee
        except Employee.DoesNotExist:
            messages.error(request, "No employee profile found.")
            return redirect('dashboard')

    from travel_orders.forms import TravelOrderForm

    if request.method == 'POST':
        form = TravelOrderForm(request.POST, request.FILES)
        if form.is_valid():
            from datetime import datetime

            # Get dates from form
            start_date = form.cleaned_data['start_date']
            end_date = form.cleaned_data['end_date']

            # Check for overlapping leave requests
            overlapping_leaves = LeaveRequest.objects.filter(
                employee=employee,
                status__in=['PENDING', 'APPROVED']
            ).filter(
                Q(start_date__lte=start_date.date(), end_date__gte=start_date.date()) |
                Q(start_date__lte=end_date.date(), end_date__gte=end_date.date()) |
                Q(start_date__gte=start_date.date(), end_date__lte=end_date.date())
            )

            if overlapping_leaves.exists():
                overlapping = overlapping_leaves.first()
                messages.error(request, f'You already have a leave request during this period ({overlapping.start_date} to {overlapping.end_date}). Please choose different dates.')
                context = {
                    'employee': employee,
                    'form': form,
                    'viewing_other': employee_id is not None,
                }
                return render(request, 'core/request_travel_order.html', context)

            # Check for overlapping travel orders
            overlapping_travels = TravelOrder.objects.filter(
                employee=employee,
                status__in=['PENDING', 'APPROVED']
            ).filter(
                Q(start_date__lte=start_date, end_date__gte=start_date) |
                Q(start_date__lte=end_date, end_date__gte=end_date) |
                Q(start_date__gte=start_date, end_date__lte=end_date)
            )

            if overlapping_travels.exists():
                overlapping = overlapping_travels.first()
                messages.error(request, f'You already have a travel order during this period ({overlapping.start_date|date:"d M Y"} to {overlapping.end_date|date:"d M Y"}). Please choose different dates.')
                context = {
                    'employee': employee,
                    'form': form,
                    'viewing_other': employee_id is not None,
                }
                return render(request, 'core/request_travel_order.html', context)

            travel_order = form.save(commit=False)
            travel_order.employee = employee
            travel_order.save()

            messages.success(request, f'Travel order for {employee.user.get_full_name()} submitted successfully!')
            if employee_id:
                return redirect(f"{reverse('my_travel_orders')}?employee_id={employee_id}")
            return redirect('my_travel_orders')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = TravelOrderForm()

    context = {
        'employee': employee,
        'form': form,
        'viewing_other': employee_id is not None,
    }
    return render(request, 'core/request_travel_order.html', context)


@login_required
def change_password(request):
    """Allow users to change their password"""
    if request.method == 'POST':
        form = PasswordChangeForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)  # Keep user logged in
            messages.success(request, 'Your password was successfully updated!')
            return redirect('profile')
        else:
            messages.error(request, 'Please correct the error(s) below.')
    else:
        form = PasswordChangeForm(request.user)

    return render(request, 'core/change_password.html', {'form': form})


def custom_logout(request):
    """Custom logout view"""
    logout(request)
    messages.success(request, 'You have been logged out successfully.')
    return redirect('/login/')


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def shift_management(request):
    """Manage work shifts"""
    shifts = Shift.objects.all().order_by('start_time')

    # Count employees assigned to each shift
    from core.models import EmployeeShift
    shift_stats = {}
    for shift in shifts:
        active_assignments = EmployeeShift.objects.filter(
            shift=shift,
            is_active=True
        ).count()
        shift_stats[shift.id] = active_assignments

    context = {
        'shifts': shifts,
        'shift_stats': shift_stats,
    }
    return render(request, 'core/shift_management.html', context)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def shift_create(request):
    """Create a new shift"""
    if request.method == 'POST':
        form = ShiftForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, f"Shift '{form.cleaned_data['name']}' created successfully!")
            return redirect('shift_management')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = ShiftForm()

    context = {'form': form}
    return render(request, 'core/shift_form.html', context)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def shift_edit(request, shift_id):
    """Edit an existing shift"""
    shift = get_object_or_404(Shift, id=shift_id)

    if request.method == 'POST':
        form = ShiftForm(request.POST, instance=shift)
        if form.is_valid():
            form.save()
            messages.success(request, f"Shift '{shift.name}' updated successfully!")
            return redirect('shift_management')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = ShiftForm(instance=shift)

    context = {'form': form, 'shift': shift, 'is_edit': True}
    return render(request, 'core/shift_form.html', context)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def shift_delete(request, shift_id):
    """Delete a shift"""
    shift = get_object_or_404(Shift, id=shift_id)

    if request.method == 'POST':
        shift_name = shift.name
        shift.delete()
        messages.success(request, f"Shift '{shift_name}' deleted successfully!")
        return redirect('shift_management')

    context = {'shift': shift}
    return render(request, 'core/shift_confirm_delete.html', context)
