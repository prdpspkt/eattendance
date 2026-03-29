from celery import shared_task
from django.utils import timezone
from datetime import date, timedelta
import logging

logger = logging.getLogger(__name__)


@shared_task(name='attendance.tasks.process_all_daily_attendance')
def process_all_daily_attendance(target_date=None):
    """
    Process daily attendance for all employees
    Runs daily at 1:00 AM via Celery Beat
    """
    from .models import Attendance, DailyAttendance
    from core.models import Employee

    if not target_date:
        target_date = date.today()

    active_employees = Employee.objects.filter(employment_status='ACTIVE')
    processed_count = 0

    for employee in active_employees:
        try:
            # Check if there are any attendance records for this date
            attendances = Attendance.objects.filter(
                employee=employee,
                timestamp__date=target_date
            )

            if attendances.exists():
                # Process daily attendance
                daily_att = Attendance.process_daily_attendance(employee, target_date)
                if daily_att:
                    processed_count += 1
                    logger.info(f"Processed attendance for {employee.user.get_full_name()} on {target_date}")
        except Exception as e:
            logger.error(f"Error processing attendance for {employee.user.get_full_name()}: {str(e)}")

    return {
        'date': target_date.isoformat(),
        'total_employees': active_employees.count(),
        'processed': processed_count,
        'timestamp': timezone.now().isoformat()
    }


@shared_task(name='attendance.tasks.process_employee_attendance')
def process_employee_attendance(employee_id, target_date=None):
    """Process attendance for a specific employee"""
    from .models import Attendance
    from core.models import Employee

    try:
        employee = Employee.objects.get(id=employee_id)

        if not target_date:
            target_date = date.today()

        daily_att = Attendance.process_daily_attendance(employee, target_date)

        return {
            'employee_id': employee_id,
            'employee_name': employee.user.get_full_name(),
            'date': target_date.isoformat(),
            'success': daily_att is not None,
            'timestamp': timezone.now().isoformat()
        }
    except Employee.DoesNotExist:
        return {
            'employee_id': employee_id,
            'success': False,
            'error': 'Employee not found',
            'timestamp': timezone.now().isoformat()
        }
    except Exception as e:
        return {
            'employee_id': employee_id,
            'success': False,
            'error': str(e),
            'timestamp': timezone.now().isoformat()
        }
