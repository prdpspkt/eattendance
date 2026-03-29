from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib import messages
from django.urls import reverse
from django.utils import timezone
from .models import LeaveType, LeaveRequest
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
        leave_request.status = 'APPROVED'
        leave_request.approved_by = request.user
        leave_request.approved_at = timezone.now()
        leave_request.save()

        messages.success(request, f'Leave request for {leave_request.employee.user.get_full_name()} approved successfully!')

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

