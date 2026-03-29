# E-Attendance Management System

A comprehensive remote e-attendance management system using ZKTeco biometric devices with Django, featuring multi-device management, auto-sync, employee management, leave management, travel orders, and advanced reporting.

## Features

### Core Features
- **Multi-Device Management**: Support for multiple ZKTeco biometric devices
- **Auto Data Sync**: Automatic attendance fetching every 5 minutes from all devices
- **Employee Management**: Complete employee lifecycle management
- **Shift Management**: Flexible shift scheduling with grace periods
- **Attendance Tracking**: Real-time attendance processing with overtime calculation
- **Leave Management**: Comprehensive leave system with approval workflow
- **Travel Order Management**: Travel requests with itinerary and expense tracking
- **Advanced Reporting**: Monthly attendance, overtime, contact sheets, travel reports

### User Roles
- **Superuser**: Full system access
- **Office Admin**: Manage employees, approve requests, manage devices
- **Employee**: Self-service portal for leave/travel requests

## Tech Stack

- **Backend**: Django 5.2, Django REST Framework
- **Database**: SQLite (default), PostgreSQL (recommended for production)
- **Task Queue**: Celery with Redis
- **Device Communication**: pyzk library for ZKTeco devices
- **Reporting**: openpyxl (Excel), reportlab (PDF)
- **Frontend**: Django Admin with Bootstrap 5

## Installation

### Prerequisites
- Python 3.8+
- Redis server (for Celery)
- ZKTeco biometric device(s) on the network

### Step 1: Clone the repository
```bash
cd eattendance
```

### Step 2: Install dependencies
```bash
pip install django djangorestframework celery redis python-decouple pyzk openpyxl reportlab django-celery-beat
```

### Step 3: Configure environment variables (optional)
Edit `ehajiri/settings.py`:
```python
# Change timezone if needed
TIME_ZONE = 'Asia/Dhaka'  # Change to your timezone

# Configure Redis for Celery
CELERY_BROKER_URL = 'redis://localhost:6379/0'
CELERY_RESULT_BACKEND = 'redis://localhost:6379/0'
```

### Step 4: Run migrations
```bash
python manage.py makemigrations
python manage.py migrate
```

### Step 5: Create superuser
```bash
python manage.py createsuperuser
# Or use the default: username=admin, password=admin123
```

### Step 6: Start Redis server
```bash
redis-server
```

### Step 7: Start Celery worker and beat
```bash
# Terminal 1: Start Celery worker
celery -A ehajiri worker -l info

# Terminal 2: Start Celery beat (scheduler)
celery -A ehajiri beat -l info
```

### Step 8: Run Django development server
```bash
python manage.py runserver
```

### Step 9: Access the application
- Admin Panel: http://localhost:8000/admin/
- Login with superuser credentials

## Initial Setup

### 1. Create Departments
Navigate to: Admin → Departments → Add Department

### 2. Create Shifts
Navigate to: Admin → Shifts → Add Shift
Example:
- Name: "Morning Shift"
- Start Time: 09:00
- End Time: 18:00
- Late Grace Minutes: 15
- Early Exit Minutes: 15
- Break Duration Minutes: 60

### 3. Create Leave Types
Navigate to: Admin → Leave Types → Add Leave Type
Examples:
- Annual Leave (20 days/year, paid)
- Sick Leave (14 days/year, paid)
- Casual Leave (10 days/year, paid)
- Unpaid Leave (0 days/year, unpaid)

### 4. Add ZKTeco Devices
Navigate to: Admin → Devices → Add Device
- Name: "Office Main Door"
- IP Address: 192.168.1.201 (your device IP)
- Port: 4370 (default)
- Password: 0 (default)

Test connection and sync attendance.

### 5. Create Users and Employees
Navigate to: Admin → Users → Add User
- Set role (SUPERUSER, OFFICE_ADMIN, or EMPLOYEE)
- Create user account

Navigate to: Admin → Employees → Add Employee
- Link to User
- Enter Employee ID (must match device UID)
- Assign Department
- Set Join Date
- Set Device UID (must match ZKTeco device user ID)

### 6. Assign Shifts to Employees
Navigate to: Admin → Employee Shifts → Add Employee Shift
- Select Employee
- Select Shift
- Set Effective Date

## Usage

### Managing Devices
1. Go to Admin → Devices
2. Click "Test Connection" to verify device connectivity
3. Click "Sync Now" to manually fetch attendance data
4. Automatic sync runs every 5 minutes via Celery

### Managing Leave Requests
1. Employee submits leave request through their dashboard
2. Admin approves/rejects via Admin → Leave Requests
3. Leave balance automatically updated on approval

### Managing Travel Orders
1. Employee submits travel order through dashboard
2. Admin approves/rejects via Admin → Travel Orders
3. Expenses can be claimed separately

### Viewing Reports
Navigate to Admin section for:
- Daily Attendance: View processed daily attendance records
- Attendance Raw Data: View raw attendance from devices
- Leave Requests: View all leave requests
- Travel Orders: View all travel orders

## Celery Tasks

### Scheduled Tasks
1. **sync_all_devices**: Runs every 5 minutes
   - Fetches attendance from all active devices
   - Creates attendance records in database

2. **process_all_daily_attendance**: Runs daily at 1:00 AM
   - Processes raw attendance into daily summaries
   - Calculates working hours, overtime, late arrivals
   - Updates daily attendance records

### Manual Task Execution
```python
# Sync all devices
from devices.tasks import sync_all_devices
sync_all_devices.delay()

# Process attendance for specific date
from attendance.tasks import process_all_daily_attendance
from datetime import date
process_all_daily_attendance.delay(target_date=date.today())
```

## Database Schema

### Core Models
- **User**: Custom user with role (SUPERUSER, OFFICE_ADMIN, EMPLOYEE)
- **Department**: Organizational departments
- **Employee**: Employee information linked to User
- **Shift**: Work shift definitions
- **EmployeeShift**: Shift assignments

### Device Models
- **Device**: ZKTeco biometric device configuration

### Attendance Models
- **Attendance**: Raw attendance from device
- **DailyAttendance**: Processed daily attendance summary
- **Absence**: Absence records with approval

### Leave Models
- **LeaveType**: Leave category configuration
- **LeaveBalance**: Employee leave balance tracking
- **LeaveRequest**: Leave requests with approval workflow

### Travel Order Models
- **TravelOrder**: Travel request
- **TravelItinerary**: Travel schedule details
- **TravelExpense**: Expense claims for travel

## API Endpoints (Future)

The system is designed to support REST API. To enable:
```python
# Add to INSTALLED_APPS in settings.py
'rest_framework',
'rest_framework.authtoken',
```

## Troubleshooting

### Device Connection Issues
1. Verify device IP address is correct
2. Ensure device is accessible from your network
3. Check firewall settings (port 4370)
4. Test connection: Admin → Devices → Test Connection

### Celery Not Working
1. Ensure Redis is running: `redis-server`
2. Check Celery worker: `celery -A ehajiri worker -l info`
3. Check Celery beat: `celery -A ehajiri beat -l info`
4. Check logs for errors

### Attendance Not Syncing
1. Check if device is active in Admin
2. Verify last_sync timestamp in Device model
3. Check Celery logs for sync errors
4. Manually trigger sync: Admin → Devices → Sync Now

## Production Deployment

### Database
Switch to PostgreSQL:
```python
# settings.py
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'eattendance',
        'USER': 'your_user',
        'PASSWORD': 'your_password',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

### Static Files
```bash
python manage.py collectstatic
```

### Web Server
Use Gunicorn with Nginx:
```bash
pip install gunicorn
gunicorn ehajiri.wsgi:application
```

### Celery as Service
Use supervisor or systemd to run Celery worker and beat as services.

## Contributing

This is a comprehensive e-attendance system. Feel free to extend it with:
- REST API endpoints
- Employee self-service frontend
- Mobile app
- Advanced analytics and dashboards
- Payroll integration
- Notification system (email/SMS)

## License

This project is open-source and available for educational and commercial use.

## Support

For issues and questions:
1. Check the Troubleshooting section
2. Review Django and Celery logs
3. Consult pyzk documentation for device-specific issues

## Credits

- Built with Django
- Device communication using [pyzk](https://github.com/fananimi/pyzk)
- Task scheduling with Celery
