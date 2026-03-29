import os
from celery import Celery
from celery.schedules import crontab

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ehajiri.settings')

app = Celery('ehajiri')

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# Celery Beat Schedule for automatic device synchronization
app.conf.beat_schedule = {
    'sync-all-devices-every-5-minutes': {
        'task': 'devices.tasks.sync_all_devices',
        'schedule': 300.0,  # Run every 300 seconds (5 minutes)
    },
    'process-daily-attendance-daily': {
        'task': 'attendance.tasks.process_all_daily_attendance',
        'schedule': crontab(hour=1, minute=0),  # Run daily at 1:00 AM
    },
}

@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
