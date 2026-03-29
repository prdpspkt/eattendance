from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib import messages
from django.utils import timezone
from .models import TravelOrder
from .forms import TravelOrderForm
from core.models import Employee


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
            return create_travel_order_for_employee(request, employee)
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
    return render(request, 'travel_orders/my_travel_orders.html', context)


@login_required
def create_travel_order_for_employee(request, employee):
    """Create travel order for a specific employee (admin function)"""

    if request.method == 'POST':
        form = TravelOrderForm(request.POST, request.FILES)
        if form.is_valid():
            from django.db.models import Q
            from leaves.models import LeaveRequest

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
                messages.error(request, f'Employee already has a leave request during this period ({overlapping.start_date} to {overlapping.end_date}). Please choose different dates.')
                context = {
                    'employee': employee,
                    'form': form,
                    'viewing_other': True,
                }
                return render(request, 'travel_orders/create_travel_order.html', context)

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
                messages.error(request, f'Employee already has a travel order during this period ({overlapping.start_date|date:"d M Y"} to {overlapping.end_date|date:"d M Y"}). Please choose different dates.')
                context = {
                    'employee': employee,
                    'form': form,
                    'viewing_other': True,
                }
                return render(request, 'travel_orders/create_travel_order.html', context)

            travel_order = form.save(commit=False)
            travel_order.employee = employee
            travel_order.save()
            messages.success(request, f'Travel order for {employee.user.get_full_name()} created successfully!')
            return redirect(f"{request.path}?employee_id={employee.id}")
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")
    else:
        form = TravelOrderForm()

    context = {
        'employee': employee,
        'form': form,
        'viewing_other': True,
    }
    return render(request, 'travel_orders/create_travel_order.html', context)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def travel_orders_management(request):
    """Manage all travel orders (admin view)"""
    # Get filter parameters
    status_filter = request.GET.get('status', '')
    employee_id = request.GET.get('employee_id', '')
    travel_type = request.GET.get('travel_type', '')

    # Get all travel orders
    travel_orders = TravelOrder.objects.all().order_by('-created_at')

    # Apply filters
    if status_filter:
        travel_orders = travel_orders.filter(status=status_filter)

    if employee_id:
        travel_orders = travel_orders.filter(employee_id=employee_id)

    if travel_type:
        travel_orders = travel_orders.filter(travel_type=travel_type)

    # Get all employees for filter dropdown
    employees = Employee.objects.filter(employment_status='ACTIVE')

    context = {
        'travel_orders': travel_orders,
        'employees': employees,
        'status_filter': status_filter,
        'employee_filter': employee_id,
        'travel_type_filter': travel_type,
    }
    return render(request, 'travel_orders/travel_orders_management.html', context)


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def travel_order_approve(request, travel_order_id):
    """Approve a travel order"""
    travel_order = get_object_or_404(TravelOrder, id=travel_order_id)

    if request.method == 'POST':
        travel_order.status = 'APPROVED'
        travel_order.approved_by = request.user
        travel_order.approved_at = timezone.now()
        travel_order.save()

        messages.success(request, f'Travel order for {travel_order.employee.user.get_full_name()} approved successfully!')

    return redirect('travel_orders_management')


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def travel_order_reject(request, travel_order_id):
    """Reject a travel order"""
    travel_order = get_object_or_404(TravelOrder, id=travel_order_id)

    if request.method == 'POST':
        rejection_reason = request.POST.get('rejection_reason', '')
        travel_order.status = 'REJECTED'
        travel_order.rejection_reason = rejection_reason
        travel_order.approved_by = request.user
        travel_order.approved_at = timezone.now()
        travel_order.save()

        messages.success(request, f'Travel order for {travel_order.employee.user.get_full_name()} rejected!')

    return redirect('travel_orders_management')


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def travel_order_delete(request, travel_order_id):
    """Delete a travel order"""
    travel_order = get_object_or_404(TravelOrder, id=travel_order_id)

    # Prevent deletion of approved travel orders
    if travel_order.status == 'APPROVED':
        messages.error(request, f"Cannot delete approved travel order. Please cancel it instead.")
        return redirect('travel_orders_management')

    if request.method == 'POST':
        travel_order.delete()
        messages.success(request, f"Travel order deleted successfully!")
        return redirect('travel_orders_management')

    context = {'travel_order': travel_order}
    return render(request, 'travel_orders/travel_order_confirm_delete.html', context)


