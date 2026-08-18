# E-Attendance Management System

A comprehensive remote e-attendance management system using ZKTeco biometric devices with Django, featuring multi-device management, auto-sync, employee management, leave management, travel orders, and advanced reporting.

## Features

### Core Features
- **Multi-Device Management**: Support for multiple ZKTeco biometric devices
- **Two sync transports**: real-time **ADMS/WDMS push** (devices post to the server) or
  scheduled **pull** over the ZK SDK (server polls devices)
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

## Device Sync: Push (ADMS/WDMS) vs Pull (SDK)

The system supports both transports at once; a device can use either.

| | Push (ADMS/WDMS) | Pull (ZK SDK) |
|---|---|---|
| Who connects | Device → server (outbound HTTP) | Server → device (TCP 4370) |
| Works behind NAT / remote branch | Yes | Only with port forwarding / VPN |
| Latency | Seconds (real time) | Up to the poll interval (5 min) |
| Needs Celery | No | Yes, for scheduled polling |
| Identified by | Serial number (SN) | IP address |

Both transports share one ingestion path (`devices/ingest.py`), so de-duplication,
unlinked enrolments and the roll-up into daily summaries behave identically.

### Configuring a device for push mode

1. On the terminal: **Menu → Comm. → Ethernet → Cloud Server / ADMS** (wording varies
   by firmware; it may be called *Server Setting*, *Cloud Server*, or *ADMS*).
2. Set:
   - **Server address**: the public hostname, `attendance.thedeepit.com` — the
     terminals sit at remote sites and reach the server over the internet
   - **Server port**: `80`. This is the port nginx answers on, not the gunicorn
     port behind it
   - **Enable Proxy Server**: off
   - **HTTPS**: off unless the firmware handles TLS properly. With Cloudflare in
     front, "Always Use HTTPS" must then be turned off for `/iclock/*` with a
     Configuration Rule, or the 301 leaves the terminal retrying forever
3. Save and reboot the terminal.
4. The device calls `GET /iclock/cdata?SN=...`. Because the serial number is unknown,
   it is registered automatically as an **inactive** device and its data is refused
   with HTTP 401 — the device keeps its records and retries, so nothing is lost.
5. In Django admin → **Devices**, open the new entry (named *Unregistered device
   &lt;SN&gt;*), give it a real name and location, tick **is_active**, and save.
6. On the device's next retry its backlog uploads. The **Mode** column shows
   *Push - online* while the device is checking in.

To refuse unknown devices outright instead of registering them, set
`ADMS_AUTO_REGISTER_DEVICES = False`.

### Endpoints

The device firmware fixes these paths; they are mounted at `/iclock/`.

| Endpoint | Purpose |
|---|---|
| `GET /iclock/cdata?SN=..&options=all` | Handshake; server returns operating parameters |
| `POST /iclock/cdata?SN=..&table=ATTLOG` | Attendance punches |
| `POST /iclock/cdata?SN=..&table=OPERLOG` | User enrolments and operation logs |
| `GET /iclock/getrequest?SN=..` | Device polls for queued commands |
| `POST /iclock/devicecmd?SN=..` | Device reports command results |
| `GET /iclock/ping?SN=..` | Heartbeat |

These views are unauthenticated and CSRF-exempt by protocol design — devices cannot
carry a session. Access is controlled by serial number, and an unknown or inactive
device is refused.

That serial number is the whole credential, so anyone who learns one can post punches
for it. On a LAN or VPN that is tolerable; this deployment cannot use one, because the
terminals are at remote sites and reach `/iclock/` across the internet. What guards it
instead, strongest first:

1. **`ADMS_AUTO_REGISTER_DEVICES = False`**, set once the real terminals are enrolled.
   Unknown serials are then refused outright rather than creating a row to approve.
2. **A Cloudflare WAF rule on `/iclock/*`** allowing only the sites' public IPs, where
   those are static. This is the only control that stops an attacker before it reaches
   Django.
3. **Rate limiting in nginx**, keyed on `CF-Connecting-IP` — see
   `deploy/nginx/attendance.thedeepit.com.conf`. It caps how fast serials can be
   guessed; it does not stop someone who already knows one.

Note that a device is matched to an existing record by client IP only when its serial
is not yet known (`devices/adms.py`). Reaching the server over the internet, that IP is
the site's public address, so a `Device` row created for pull mode with a LAN address
will not be adopted — a new inactive row appears instead. Either approve that row, or
fill the serial number onto the existing one before the terminal first connects.

### Sending commands to a device

In push mode the server cannot reach the device, so commands are queued and collected
on the device's next poll. From Django admin → Devices, select devices and use
*Push: queue CHECK / INFO / REBOOT*. Results appear under **Device Commands**
(`return_code` 0 = success).

```python
device.queue_command('CHECK')                 # re-send anything unacknowledged
device.push_user(employee_device)             # write a user onto the device
```

## Processing attendance

Raw punches are not usable until they are rolled up into `DailyAttendance` rows,
which is what every screen reads. This happens automatically when punches arrive
(push or pull), and nightly via Celery. To run it by hand or backfill history:

```bash
python manage.py process_attendance                          # yesterday
python manage.py process_attendance --date 2026-03-29
python manage.py process_attendance --from 2024-09-01 --to 2026-03-31
python manage.py process_attendance --all                    # everything on record
python manage.py process_attendance --days 30 --employee EMP0001
```

Re-running is safe: a day is recomputed from its punches each time.

### Attendance rules (settings.py)

| Setting | Default | Meaning |
|---|---|---|
| `WEEKEND_DAYS` | `[5, 6]` | Weekly off days (Mon=0 … Sun=6). Work on these days counts entirely as overtime. |
| `MINIMUM_PUNCH_GAP_MINUTES` | `5` | Two scans closer than this are one scan, not in + out. |
| `OVERTIME_MINIMUM_MINUTES` | `30` | Overruns shorter than this earn no overtime. |
| `OVERTIME_ROUNDING_MINUTES` | `15` | Overtime rounds down to this increment. |
| `OVERTIME_AFTER_SHIFT_END_ONLY` | `True` | Overtime is time past the shift end; when `False`, it is net time beyond the shift's scheduled hours. |
| `HALF_DAY_MAX_HOURS` | `4` | Net worked hours at or below this are a half day. |
| `ADMS_PROCESS_ON_PUSH` | `True` | Roll punches up as they arrive, so no Celery worker is required. |

Overtime needs a shift to measure against: an employee with no `EmployeeShift`
assignment gets hours recorded but no late/early/overtime figures.

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

`STATIC_ROOT` defaults to `/var/www/<SITE_DOMAIN>`. Create it and hand it to the
deploy user before the first collect, otherwise the command fails with
`PermissionError` because `/var/www` is root-owned:

```bash
sudo mkdir -p /var/www/attendance.thedeepit.com
sudo chown -R $USER:www-data /var/www/attendance.thedeepit.com
sudo chmod -R 755 /var/www/attendance.thedeepit.com
sudo chmod g+s /var/www/attendance.thedeepit.com   # new files keep the group
```

```bash
python manage.py collectstatic --noinput
```

Do not run collectstatic with `sudo`: it leaves root-owned files that the next
deploy cannot overwrite, and runs project code as root.

To collect somewhere else (a dev box, or a host without `/var/www`):

```bash
STATIC_ROOT=./staticfiles python manage.py collectstatic --noinput
```

Uploads are separate. `MEDIA_ROOT` defaults to `BASE_DIR/media`, which nginx
usually cannot read under `/home`. Either relax the home directory, or move it:

```bash
sudo mkdir -p /var/www/attendance.thedeepit.com-media
sudo chown -R $USER:www-data /var/www/attendance.thedeepit.com-media
# then set MEDIA_ROOT=/var/www/attendance.thedeepit.com-media in the environment
```

Keep media *outside* `STATIC_ROOT`: `collectstatic --clear` deletes everything
in that directory, which would take uploaded documents with it.

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
