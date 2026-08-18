"""Administrator views: manual attendance corrections and the overtime report."""
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from core.models import Department, Employee

from .forms import ManualAttendanceForm, OvertimeRecordForm
from .models import Attendance, OvertimeRecord


def _parse_date(value, fallback=None):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return fallback


# ---------------------------------------------------------------------------
# Manual attendance
# ---------------------------------------------------------------------------
@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def manual_attendance(request):
    """Record a punch a terminal never captured, with the reason it is needed."""
    if request.method == 'POST':
        form = ManualAttendanceForm(request.POST, recorded_by=request.user)
        if form.is_valid():
            punch = form.save()
            messages.success(
                request,
                f'Recorded {punch.get_punch_type_display().lower()} for '
                f'{punch.employee.user.get_full_name()} at '
                f'{timezone.localtime(punch.timestamp):%d %b %Y, %H:%M}. '
                f'The day has been recalculated.'
            )
            return redirect('attendance:manual_attendance')
    else:
        form = ManualAttendanceForm(recorded_by=request.user)

    recent = (
        Attendance.objects
        .filter(source=Attendance.SOURCE_MANUAL)
        .select_related('employee__user', 'recorded_by')
        .order_by('-created_at')[:15]
    )
    return render(request, 'attendance/manual_attendance.html', {
        'form': form,
        'recent_manual': recent,
    })


# ---------------------------------------------------------------------------
# Overtime
# ---------------------------------------------------------------------------
def _overtime_queryset(request):
    """Apply the report's filters, and report back what was applied."""
    today = timezone.localdate()
    start = _parse_date(request.GET.get('start'), today.replace(day=1))
    end = _parse_date(request.GET.get('end'), today)

    records = (
        OvertimeRecord.objects
        .filter(date__gte=start, date__lte=end)
        .select_related('employee__user', 'employee__department', 'approved_by')
        .order_by('date', 'employee__employee_id')
    )

    employee_id = request.GET.get('employee') or ''
    if employee_id.isdigit():
        records = records.filter(employee_id=int(employee_id))

    department_id = request.GET.get('department') or ''
    if department_id.isdigit():
        records = records.filter(employee__department_id=int(department_id))

    status = request.GET.get('status') or ''
    if status in dict(OvertimeRecord.STATUS_CHOICES):
        records = records.filter(status=status)

    return records, {
        'start': start, 'end': end,
        'employee': employee_id, 'department': department_id, 'status': status,
    }


def _overtime_totals(records):
    """Hours and money for the filtered set. Takes an already-evaluated list.

    Both totals are accumulated per record rather than aggregated in SQL. For
    the amount that is a correctness requirement, not a preference: each record
    snapshots its own rate at approval, so rows in one report legitimately
    carry different rates and (total hours x one rate) would misprice them.
    Summing the hours in the same loop then costs nothing and avoids a second
    query over rows already in memory.
    """
    hours = Decimal('0')
    amount = Decimal('0')
    priced = unpriced = 0
    for record in records:
        hours += record.hours or Decimal('0')
        value = record.amount
        if value is None:
            unpriced += 1
        else:
            amount += value
            priced += 1
    return {
        'hours': hours,
        # None, not zero: "nothing is priced yet" and "the total is zero" are
        # different answers, and a payment report must not blur them.
        'amount': amount if priced else None,
        'priced': priced,
        'unpriced': unpriced,
        'count': len(records),
    }


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def overtime_report(request):
    """Overtime for a period: hours, when they were worked, and on what."""
    records, filters = _overtime_queryset(request)
    records = list(records)

    return render(request, 'attendance/overtime_report.html', {
        'records': records,
        'filters': filters,
        'totals': _overtime_totals(records),
        'employees': Employee.objects.select_related('user').order_by('user__first_name'),
        'departments': Department.objects.order_by('name'),
        'status_choices': OvertimeRecord.STATUS_CHOICES,
        'undescribed': sum(1 for r in records if r.needs_job_description),
    })


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def overtime_edit(request, record_id):
    """Write down what was worked on, and set the rate."""
    record = get_object_or_404(
        OvertimeRecord.objects.select_related('employee__user'), pk=record_id,
    )

    if request.method == 'POST':
        form = OvertimeRecordForm(request.POST, instance=record)
        if form.is_valid():
            form.save()
            messages.success(request, 'Overtime record updated.')
            return redirect('attendance:overtime_report')
    else:
        form = OvertimeRecordForm(instance=record)

    return render(request, 'attendance/overtime_form.html', {'form': form, 'record': record})


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def overtime_approve(request, record_id):
    """Sign a record off. Refuses if nobody has said what the work was."""
    record = get_object_or_404(OvertimeRecord, pk=record_id)

    if request.method != 'POST':
        return redirect('attendance:overtime_report')

    if record.needs_job_description:
        messages.error(
            request,
            'Add the job performed before approving. Overtime is paid on the strength '
            'of the work described.'
        )
    else:
        record.approve(request.user)
        messages.success(
            request,
            f'Approved {record.hours}h for {record.employee.user.get_full_name()} '
            f'on {record.date:%d %b %Y}. The hours are now fixed.'
        )
    return redirect(f"{request.META.get('HTTP_REFERER') or '/attendance/overtime/'}")


@login_required
@permission_required('core.can_manage_devices', raise_exception=False, login_url='dashboard')
def overtime_export(request):
    """The same filtered report as an Excel sheet, for payroll."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    records, filters = _overtime_queryset(request)
    records = list(records)
    totals = _overtime_totals(records)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'Overtime'

    sheet['A1'] = 'Overtime report'
    sheet['A1'].font = Font(size=14, bold=True)
    sheet['A2'] = f"{filters['start']:%d %b %Y} to {filters['end']:%d %b %Y}"
    sheet['A2'].font = Font(italic=True, color='666666')

    headers = ['Employee ID', 'Employee', 'Department', 'Date', 'Started', 'Ended',
               'Hours', 'Job performed', 'Rate', 'Amount', 'Status', 'Approved by']
    header_row = 4
    for column, title in enumerate(headers, start=1):
        cell = sheet.cell(row=header_row, column=column, value=title)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', start_color='1E40AF')
        cell.alignment = Alignment(vertical='center')

    for offset, record in enumerate(records, start=header_row + 1):
        local_start = timezone.localtime(record.start_at)
        local_end = timezone.localtime(record.end_at)
        sheet.cell(row=offset, column=1, value=record.employee.employee_id)
        sheet.cell(row=offset, column=2, value=record.employee.user.get_full_name())
        sheet.cell(row=offset, column=3,
                   value=record.employee.department.name if record.employee.department else '')
        sheet.cell(row=offset, column=4, value=record.date.strftime('%Y-%m-%d'))
        sheet.cell(row=offset, column=5, value=local_start.strftime('%H:%M'))
        sheet.cell(row=offset, column=6, value=local_end.strftime('%H:%M'))
        sheet.cell(row=offset, column=7, value=float(record.hours))
        # Left blank rather than filled with a placeholder: an empty cell on a
        # payment sheet is a question someone has to answer, which is correct.
        sheet.cell(row=offset, column=8, value=record.job_performed or '')
        sheet.cell(row=offset, column=9,
                   value=float(record.hourly_rate) if record.hourly_rate is not None else '')
        amount = record.amount
        sheet.cell(row=offset, column=10, value=float(amount) if amount is not None else '')
        sheet.cell(row=offset, column=11, value=record.get_status_display())
        sheet.cell(row=offset, column=12,
                   value=record.approved_by.get_full_name() if record.approved_by else '')

    total_row = header_row + len(records) + 1
    sheet.cell(row=total_row, column=6, value='Total').font = Font(bold=True)
    sheet.cell(row=total_row, column=7, value=float(totals['hours'])).font = Font(bold=True)
    if totals['amount'] is not None:
        sheet.cell(row=total_row, column=10, value=float(totals['amount'])).font = Font(bold=True)
    if totals['unpriced']:
        sheet.cell(
            row=total_row + 1, column=1,
            value=f"{totals['unpriced']} record(s) have no rate set and are excluded from the total.",
        ).font = Font(italic=True, color='92400E')

    widths = [12, 24, 18, 12, 10, 10, 8, 46, 10, 12, 12, 20]
    for column, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width

    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )
    filename = f"overtime-{filters['start']:%Y%m%d}-{filters['end']:%Y%m%d}.xlsx"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    workbook.save(response)
    return response
