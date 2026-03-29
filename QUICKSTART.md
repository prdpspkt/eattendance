# Quick Start Guide - E-Attendance System

## Step-by-Step Setup Instructions

### Prerequisites
- Python 3.8+ installed
- Redis server installed and running
- ZKTeco biometric device(s) connected to your network

### Installation Steps

#### 1. Install Dependencies
```bash
pip install django djangorestframework celery redis python-decouple pyzk openpyxl reportlab django-celery-beat
```

#### 2. Database Setup
```bash
python manage.py makemigrations
python manage.py migrate
```

#### 3. Initialize Sample Data
```bash
python manage.py init_sample_data
```
This creates:
- 5 Departments (IT, HR, Finance, Marketing, Operations)
- 4 Shifts (Morning, General, Night, Flexi)
- 7 Leave Types (Annual, Sick, Casual, Maternity, Paternity, Unpaid, Study)

#### 4. Create Superuser
```bash
python manage.py createsuperuser
```
A default superuser has been created:
- Username: **admin**
- Email: **admin@example.com**
- Password: **You need to set this**

To set password for admin:
```bash
python manage.py changepassword admin
```

#### 5. Start Services

**Terminal 1 - Start Redis:**
```bash
redis-server
```

**Terminal 2 - Start Celery Worker:**
```bash
celery -A ehajiri worker -l info
```

**Terminal 3 - Start Celery Beat (Scheduler):**
```bash
celery -A ehajiri beat -l info
```

**Terminal 4 - Start Django Server:**
```bash
python manage.py runserver
```

#### 6. Access Application
Open browser and navigate to:
```
http://localhost:8000/admin/
```

Login with superuser credentials.

## Initial Configuration

### Step 1: Add ZKTeco Device
1. Go to **Devices** → **Add Device**
2. Fill in device information:
   - Name: "Office Main Door"
   - IP Address: 192.168.1.201 (or your device IP)
   - Port: 4370 (default)
   - Password: 0 (default, or your device password)
3. Save
4. Click **"Test Connection"** to verify
5. Click **"Sync Now"** to fetch initial attendance data

### Step 2: Create Office Admin User
1. Go to **Users** → **Add User**
2. Create user account:
   - Username: officeadmin
   - Email: admin@company.com
   - First Name: John
   - Last Name: Admin
   - Role: **OFFICE_ADMIN**
   - Set password
3. Save

### Step 3: Create Employee User
1. Go to **Users** → **Add User**
2. Create user account:
   - Username: jsmith
   - Email: john@company.com
   - First Name: John
   - Last Name: Smith
   - Role: **EMPLOYEE**
   - Set password
3. Save

### Step 4: Create Employee Record
1. Go to **Employees** → **Add Employee**
2. Link to the user you just created (John Smith)
3. Fill in employee details:
   - Employee ID: EMP001 (unique identifier)
   - Department: Information Technology
   - Join Date: Today's date
   - **Device UID**: 1 (Must match ZKTeco device user ID!)
4. Save

**Important**: The Device UID must match the user ID on the ZKTeco device. To find this:
- Log into the device's web interface
- Check the user list
- Note the UID number for each employee

### Step 5: Assign Shift to Employee
1. Go to **Employee Shifts** → **Add Employee Shift**
2. Fill in:
   - Employee: John Smith
   - Shift: Morning Shift
   - Effective Date: Today's date
3. Save

### Step 6: Enroll Employee Fingerprint on Device
1. On the ZKTeco device, enroll the employee's fingerprint
2. Make sure the User ID on device matches the Device UID in the system
3. Test by checking in/out on the device

### Step 7: Test Attendance Sync
1. Have employee check in on the device
2. Wait 5 minutes (auto-sync) OR manually sync:
   - Go to **Devices** → Select device → **Sync Now**
3. Check **Attendance** section to see synced records
4. Check **Daily Attendance** to see processed records

## Daily Operations

### For Office Admin

#### View Attendance
1. Go to **Daily Attendance**
2. Filter by date, employee, department
3. View check-in/out times, working hours, overtime

#### Approve Leave Requests
1. Go to **Leave Requests**
2. Select pending requests
3. Choose **"Approve selected leave requests"** from actions
4. Leave balance automatically updated

#### Approve Travel Orders
1. Go to **Travel Orders**
2. Select pending requests
3. Review details and attachments
4. Approve or reject

#### Manage Devices
1. Go to **Devices**
2. View sync status and last sync time
3. Test connection if needed
4. Manually sync if auto-sync is delayed

### For Employees (Future - Employee Dashboard)

Employees will be able to:
- Submit leave requests
- Submit travel orders
- View their attendance history
- View leave balance
- Check their shift schedule

## Automatic Processes

### Every 5 Minutes:
- Celery Beat triggers **sync_all_devices** task
- All active devices are synced
- New attendance records are created in database

### Daily at 1:00 AM:
- Celery Beat triggers **process_all_daily_attendance** task
- Raw attendance is processed into daily summaries
- Working hours, overtime, late arrivals calculated
- Daily attendance records created/updated

## Troubleshooting

### Device Not Syncing
1. Check device is reachable: `ping 192.168.1.201`
2. Check firewall allows port 4370
3. Test connection in admin panel
4. Check Celery worker logs for errors

### Attendance Not Processing
1. Check employee has shift assigned
2. Verify device UID matches device user ID
3. Manually trigger processing:
   ```python
   from attendance.tasks import process_all_daily_attendance
   from datetime import date
   process_all_daily_attendance.delay(target_date=date.today())
   ```

### Celery Not Working
1. Ensure Redis is running: `redis-cli ping` (should return PONG)
2. Check Celery worker is running
3. Check Celery beat is running
4. Review logs for errors

## Next Steps

1. **Create all employees**: Add user accounts and employee records for all staff
2. **Enroll fingerprints**: Register all employees on ZKTeco devices
3. **Set up shifts**: Create additional shifts if needed
4. **Customize leave types**: Adjust leave days per year as needed
5. **Test workflows**: Have employees submit leave/travel requests
6. **Monitor system**: Check sync status and attendance regularly

## Advanced Configuration

### Change Sync Interval
Edit `ehajiri/celery.py`:
```python
'sync-all-devices-every-5-minutes': {
    'task': 'devices.tasks.sync_all_devices',
    'schedule': 300.0,  # Change this value (in seconds)
},
```

### Add Multiple Devices
Simply add more devices via admin panel. All active devices will sync automatically.

### Export Reports
Use Django admin's export functionality or create custom reports using the data.

## Support

For detailed documentation, see **README.md**

For issues:
1. Check the troubleshooting section
2. Review Django and Celery logs
3. Consult pyzk documentation for device issues

---

**System Ready!** Your E-Attendance Management System is now configured and ready to use.
