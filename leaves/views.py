from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib import messages
from django.urls import reverse
from django.utils import timezone
from . import policy
from .models import LeaveBalance, LeaveType, LeaveRequest
from .forms import LeaveTypeForm
from core.models import Employee


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def leave_requests_management(request):
    """Manage all leave requests (admin view)"""
    # Get filter parameters
    status_filter = request.GET.get('status', '')
    employee_id = request.GET.get('employee_id', '')

    # Get all leave requests
    leave_requests = LeaveRequest.objects.all().order_by('-created_at')

    # Apply filters
    if status_filter:
        leave_requests = leave_requests.filter(status=status_filter)

    if employee_id:
        leave_requests = leave_requests.filter(employee_id=employee_id)

    # Get all employees for filter dropdown
    employees = Employee.objects.filter(employment_status='ACTIVE')

    context = {
        'leave_requests': leave_requests,
        'employees': employees,
        'status_filter': status_filter,
        'employee_filter': employee_id,
    }
    return render(request, 'leaves/leave_requests_management.html', context)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def leave_request_approve(request, leave_request_id):
    """Approve a leave request"""
    leave_request = get_object_or_404(LeaveRequest, id=leave_request_id)

    if request.method == 'POST':
        # Check the entitlement again here, not only when the request was
        # made: approvals happen days later, and other leave may have been
        # approved in between.
        problems = policy.check_request(
            leave_request.employee, leave_request.leave_type,
            leave_request.start_date, leave_request.end_date,
            leave_request.total_days, exclude_request=leave_request,
        )
        if problems and not request.POST.get('override'):
            for problem in problems:
                messages.error(request, problem)
            messages.info(
                request,
                'Approve again with "Grant beyond the entitlement" if the office '
                'is granting this as an exception.'
            )
            return redirect('leave_requests_management')

        leave_request.status = 'APPROVED'
        leave_request.approved_by = request.user
        leave_request.approved_at = timezone.now()
        leave_request.save()

        # The balance is derived from approved requests, so it has to follow.
        policy.rebuild_for_employee(leave_request.employee)

        messages.success(request, f'Leave request for {leave_request.employee.user.get_full_name()} approved successfully!')
        if problems:
            messages.warning(request, 'Approved beyond the normal entitlement.')

    return redirect('leave_requests_management')


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def leave_request_reject(request, leave_request_id):
    """Reject a leave request"""
    leave_request = get_object_or_404(LeaveRequest, id=leave_request_id)

    if request.method == 'POST':
        rejection_reason = request.POST.get('rejection_reason', '')
        leave_request.status = 'REJECTED'
        leave_request.rejection_reason = rejection_reason
        leave_request.approved_by = request.user
        leave_request.approved_at = timezone.now()
        leave_request.save()

        messages.success(request, f'Leave request for {leave_request.employee.user.get_full_name()} rejected!')

    return redirect('leave_requests_management')


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def leave_type_management(request):
    """Manage leave types"""
    leave_types = LeaveType.objects.all().order_by('name')

    # Count employees with each leave type
    from leaves.models import LeaveBalance
    leave_type_stats = {}
    for leave_type in leave_types:
        active_balances = LeaveBalance.objects.filter(
            leave_type=leave_type
        ).count()
        leave_type_stats[leave_type.id] = active_balances

    context = {
        'leave_types': leave_types,
        'leave_type_stats': leave_type_stats,
    }
    return render(request, 'leaves/leave_type_management.html', context)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def leave_type_create(request):
    """Create a new leave type"""
    if request.method == 'POST':
        form = LeaveTypeForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, f"Leave type '{form.cleaned_data['name']}' created successfully!")
            return redirect('leave_type_management')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = LeaveTypeForm()

    context = {'form': form}
    return render(request, 'leaves/leave_type_form.html', context)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def leave_type_edit(request, leave_type_id):
    """Edit an existing leave type"""
    leave_type = get_object_or_404(LeaveType, id=leave_type_id)

    if request.method == 'POST':
        form = LeaveTypeForm(request.POST, instance=leave_type)
        if form.is_valid():
            form.save()
            messages.success(request, f"Leave type '{leave_type.name}' updated successfully!")
            return redirect('leave_type_management')
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = LeaveTypeForm(instance=leave_type)

    context = {'form': form, 'leave_type': leave_type, 'is_edit': True}
    return render(request, 'leaves/leave_type_form.html', context)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def leave_type_delete(request, leave_type_id):
    """Delete a leave type"""
    leave_type = get_object_or_404(LeaveType, id=leave_type_id)

    if request.method == 'POST':
        leave_type_name = leave_type.name
        leave_type.delete()
        messages.success(request, f"Leave type '{leave_type_name}' deleted successfully!")
        return redirect('leave_type_management')

    context = {'leave_type': leave_type}
    return render(request, 'leaves/leave_type_confirm_delete.html', context)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def leave_balance_management(request):
    """Who has how much leave left, and why.

    Balances are derived, never typed in: the page recomputes them from the
    leave types and the approved requests. That is what makes a change to a
    government limit take effect on the years already on file - edit the leave
    type, rebuild, and every balance follows.
    """
    current_year = policy.current_leave_year()
    try:
        year = int(request.GET.get('year') or current_year)
    except (TypeError, ValueError):
        year = current_year

    employee_id = request.GET.get('employee_id', '')
    department_id = request.GET.get('department_id', '')

    employees = Employee.objects.filter(
        employment_status='ACTIVE'
    ).select_related('user', 'department').order_by('employee_id')
    if employee_id:
        employees = employees.filter(id=employee_id)
    if department_id:
        employees = employees.filter(department_id=department_id)

    yearly_types = list(LeaveType.objects.filter(
        is_active=True, accrual=LeaveType.Accrual.YEARLY
    ).order_by('name'))
    occasional_types = list(LeaveType.objects.filter(
        is_active=True, accrual=LeaveType.Accrual.OCCASIONAL
    ).order_by('name'))

    balances = {
        (balance.employee_id, balance.leave_type_id): balance
        for balance in LeaveBalance.objects.filter(year=year, leave_type__in=yearly_types)
    }

    rows = []
    for employee in employees:
        rows.append({
            'employee': employee,
            'balances': [balances.get((employee.id, t.id)) for t in yearly_types],
        })

    from core.models import Department
    context = {
        'year': year,
        'current_year': current_year,
        'years': range(current_year + 1, current_year - 6, -1),
        'yearly_types': yearly_types,
        'occasional_types': occasional_types,
        'rows': rows,
        'employees': Employee.objects.filter(employment_status='ACTIVE').select_related('user'),
        'departments': Department.objects.order_by('name'),
        'employee_filter': employee_id,
        'department_filter': department_id,
        'has_balances': bool(balances),
    }
    return render(request, 'leaves/leave_balance_management.html', context)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def leave_balance_rebuild(request):
    """Recompute balances from the leave types and the approved requests."""
    if request.method != 'POST':
        return redirect('leave_balance_management')

    current_year = policy.current_leave_year()
    try:
        year = int(request.POST.get('year') or current_year)
    except (TypeError, ValueError):
        year = current_year

    employee_id = request.POST.get('employee_id') or ''
    employees = None
    if employee_id:
        employees = Employee.objects.filter(id=employee_id).select_related('user')

    written = policy.rebuild_all(upto_year=year, employees=employees)
    messages.success(
        request,
        f'Leave balances rebuilt up to {year} - {written} entitlement rows written.'
    )

    target = f"{reverse('leave_balance_management')}?year={year}"
    if employee_id:
        target += f'&employee_id={employee_id}'
    return redirect(target)


@login_required
def leave_entitlement_detail(request, employee_id=None):
    """One employee's entitlement: days left, and occasional leave used up.

    Employees may look at their own; only an administrator may look at
    somebody else's.
    """
    if employee_id:
        if not (request.user.is_superuser or request.user.role == 'OFFICE_ADMIN'):
            messages.error(request, "You don't have permission to view other employees' leave balances.")
            return redirect('dashboard')
        employee = get_object_or_404(Employee, id=employee_id)
    else:
        try:
            employee = request.user.employee
        except Employee.DoesNotExist:
            messages.error(request, 'No employee profile found.')
            return redirect('dashboard')

    current_year = policy.current_leave_year()
    try:
        year = int(request.GET.get('year') or current_year)
    except (TypeError, ValueError):
        year = current_year

    overview = policy.entitlement_overview(employee, year=year)
    context = {
        'employee': employee,
        'year': year,
        'current_year': current_year,
        'years': range(current_year + 1, current_year - 6, -1),
        'yearly': overview['yearly'],
        'occasional': overview['occasional'],
        'viewing_other': employee_id is not None,
    }
    return render(request, 'leaves/leave_entitlement_detail.html', context)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def leave_request_delete(request, leave_request_id):
    """Delete a leave request"""
    leave_request = get_object_or_404(LeaveRequest, id=leave_request_id)

    # Prevent deletion of approved leave requests
    if leave_request.status == 'APPROVED':
        messages.error(request, f"Cannot delete approved leave request. Please cancel it instead.")
        return redirect('leave_requests_management')

    if request.method == 'POST':
        leave_request.delete()
        messages.success(request, f"Leave request deleted successfully!")
        return redirect('leave_requests_management')

    context = {'leave_request': leave_request}
    return render(request, 'leaves/leave_request_confirm_delete.html', context)

