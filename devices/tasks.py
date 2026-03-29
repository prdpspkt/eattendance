from celery import shared_task
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


@shared_task(name='devices.tasks.sync_all_devices')
def sync_all_devices():
    """
    Sync attendance from all active devices
    Runs every 5 minutes via Celery Beat
    """
    from .models import Device

    active_devices = Device.objects.filter(is_active=True)
    success_count = 0
    error_count = 0

    for device in active_devices:
        try:
            success, message = device.sync_attendance()
            if success:
                success_count += 1
                logger.info(f"Successfully synced device {device.name}: {message}")
            else:
                error_count += 1
                logger.error(f"Failed to sync device {device.name}: {message}")
        except Exception as e:
            error_count += 1
            logger.error(f"Error syncing device {device.name}: {str(e)}")

    return {
        'timestamp': timezone.now().isoformat(),
        'total_devices': active_devices.count(),
        'success': success_count,
        'errors': error_count
    }


@shared_task(name='devices.tasks.test_device_connection')
def test_device_connection(device_id):
    """Test connection to a specific device"""
    from .models import Device

    try:
        device = Device.objects.get(id=device_id)
        success, message = device.test_connection()
        return {
            'device_id': device_id,
            'device_name': device.name,
            'success': success,
            'message': message,
            'timestamp': timezone.now().isoformat()
        }
    except Device.DoesNotExist:
        return {
            'device_id': device_id,
            'success': False,
            'message': 'Device not found',
            'timestamp': timezone.now().isoformat()
        }
    except Exception as e:
        return {
            'device_id': device_id,
            'success': False,
            'message': str(e),
            'timestamp': timezone.now().isoformat()
        }
