# Quick Reference - E-Attendance System

## 🚀 Start the System

### **Step 1: Start Redis**
```bash
redis-server
```

### **Step 2: Start Celery Worker** (in new terminal)
```bash
celery -A ehajiri worker -l info
```

### **Step 3: Start Celery Beat** (in new terminal)
```bash
celery -A ehajiri beat -l info
```

### **Step 4: Start Django Server** (in new terminal)
```bash
python manage.py runserver
```

---

## 🌐 Access the Application

| URL | Description | Login |
|-----|-------------|-------|
| `http://localhost:8000/login/` | Login Page | All users |
| `http://localhost:8000/dashboard/` | Employee Dashboard | Employees, Admins |
| `http://localhost:8000/admin/` | Django Admin | Superuser, Office Admin |

**Default Login:**
- Username: `admin`
- Password: [Set with: `python manage.py changepassword admin`]

---

## 📋 Quick Tasks

### **Create New Employee**

**1. Create User Account:**
- Go to: `/admin/core/user/`
- Click "Add User"
- Fill in: Username, Email, First Name, Last Name, Password
- Set Role: `EMPLOYEE`
- Save

**2. Create Employee Profile:**
- Go to: `/admin/core/employee/`
- Click "Add Employee"
- Link to the user you just created
- Fill in: Employee ID, Department, Join Date
- **Important:** Set Device UID (must match ZKTeco device user ID)
- Save

**3. Assign Shift:**
- Go to: `/admin/core/employeeshift/`
- Click "Add Employee Shift"
- Select Employee, Shift, Effective Date
- Save

**4. Enroll Fingerprint on Device:**
- On ZKTeco device, enroll employee's fingerprint
- Make sure User ID on device matches Device UID in system

---

### **Add ZKTeco Device**

1. Go to: `/admin/devices/device/`
2. Click "Add Device"
3. Fill in:
   - Name: "Office Main Door"
   - IP Address: `192.168.1.201` (or your device IP)
   - Port: `4370` (default)
   - Password: `0` (default)
4. Save
5. Click "Test Connection" to verify
6. Click "Sync Now" to fetch attendance

---

### **Approve Leave Request**

1. Go to: `/admin/leaves/leaverequest/`
2. Select pending requests
3. Choose "Approve selected leave requests" from action menu
4. Click "Go"
5. Leave balance automatically updated

---

### **Approve Travel Order**

1. Go to: `/admin/travel_orders/travelorder/`
2. Select pending requests
3. Choose "Approve selected travel orders" from action menu
4. Click "Go"

---

## 🔧 Common Commands

### **Database Operations**
```bash
# Create migrations
python manage.py makemigrations

# Apply migrations
python manage.py migrate

# Initialize sample data
python manage.py init_sample_data

# Create superuser
python manage.py createsuperuser

# Change password
python manage.py changepassword admin
```

### **Server Operations**
```bash
# Start development server
python manage.py runserver

# Start on specific port
python manage.py runserver 8080

# Check for errors
python manage.py check

# Open Django shell
python manage.py shell
```

### **Celery Operations**
```bash
# Start worker
celery -A ehajiri worker -l info

# Start beat scheduler
celery -A ehajiri beat -l info

# Start both (one terminal)
celery -A ehajiri worker -B -l info
```

---

## 📊 Important URLs

| Purpose | URL |
|---------|-----|
| Login | `/login/` |
| Dashboard | `/dashboard/` |
| My Attendance | `/my-attendance/` |
| My Leaves | `/my-leaves/` |
| Request Leave | `/request-leave/` |
| My Travel Orders | `/my-travel-orders/` |
| Profile | `/profile/` |
| Logout | `/logout/` |
| Admin Panel | `/admin/` |
| Admin - Users | `/admin/core/user/` |
| Admin - Employees | `/admin/core/employee/` |
| Admin - Devices | `/admin/devices/device/` |
| Admin - Attendance | `/admin/attendance/dailyattendance/` |
| Admin - Leaves | `/admin/leaves/leaverequest/` |
| Admin - Travel Orders | `/admin/travel_orders/travelorder/` |

---

## ⚡ Troubleshooting

### **Device Not Syncing**
```bash
# Test connection
ping 192.168.1.201

# Check Celery is running
# Look for: "Task devices.tasks.sync_all_devices[...] succeeded"

# Manual sync
# Go to /admin/devices/device/ → Select device → "Sync attendance from selected devices"
```

### **Attendance Not Processing**
```bash
# Check employee has shift assigned
# Go to /admin/core/employeeshift/

# Verify device UID matches
# Employee Device UID == ZKTeco Device User ID

# Manual processing
python manage.py shell
>>> from attendance.tasks import process_all_daily_attendance
>>> from datetime import date
>>> process_all_daily_attendance.delay(target_date=date.today())
```

### **Celery Not Working**
```bash
# Check Redis
redis-cli ping
# Should return: PONG

# Restart Redis
redis-server

# Check Celery worker logs
# Look for errors in terminal

# Restart Celery
# Stop with Ctrl+C, then start again
```

### **Template Not Found**
```bash
# Check templates directory exists
ls templates/

# Check settings.py has:
# TEMPLATES = [{'DIRS': [BASE_DIR / 'templates'], ...}]

# Restart server
python manage.py runserver
```

---

## 🎯 Daily Workflow

### **For Office Admin:**

1. **Morning (9:00 AM)**
   - Check device sync status
   - Review yesterday's attendance
   - Approve pending leave requests
   - Approve pending travel orders

2. **During Day**
   - Monitor device connectivity
   - Process new employee requests
   - Generate reports as needed

3. **End of Day (6:00 PM)**
   - Review daily attendance summary
   - Check for any issues
   - Plan for next day

### **For Employee:**

1. **Check In** (at shift start)
   - Use fingerprint on ZKTeco device
   - Wait for confirmation beep

2. **During Day**
   - View attendance on dashboard
   - Submit leave requests if needed
   - Submit travel orders if needed

3. **Check Out** (at shift end)
   - Use fingerprint on ZKTeco device
   - Wait for confirmation beep

4. **View Dashboard**
   - Check today's attendance
   - View leave balance
   - View monthly statistics

---

## 📈 Monitoring

### **Check Device Sync Status**
```bash
# In Django shell
python manage.py shell
>>> from devices.models import Device
>>> for device in Device.objects.all():
...     print(f"{device.name}: Last sync {device.last_sync} - {device.last_sync_status}")
```

### **Check Today's Attendance**
```bash
python manage.py shell
>>> from attendance.models import DailyAttendance
>>> from datetime import date
>>> attendances = DailyAttendance.objects.filter(date=date.today())
>>> print(f"Total: {attendances.count()}")
>>> print(f"Present: {attendances.filter(status='PRESENT').count()}")
>>> print(f"Late: {attendances.filter(status='LATE').count()}")
```

### **Check Pending Requests**
```bash
python manage.py shell
>>> from leaves.models import LeaveRequest
>>> from travel_orders.models import TravelOrder
>>> print(f"Pending Leaves: {LeaveRequest.objects.filter(status='PENDING').count()}")
>>> print(f"Pending Travels: {TravelOrder.objects.filter(status='PENDING').count()}")
```

---

## 🔐 Security Reminders

- **Change default admin password** immediately
- **Use strong passwords** for all user accounts
- **Restrict admin access** to authorized personnel only
- **Keep device passwords secure**
- **Regular backups** of database
- **Update system** regularly

---

## 📞 Quick Help

| Issue | Solution |
|-------|----------|
| Can't login | Check username/password, try `/admin/` |
| No dashboard | User needs employee profile, contact admin |
| Device not found | Check IP address, test connection |
| Attendance not showing | Check sync status, verify device UID |
| Leave request pending | Contact office admin for approval |
| Can't check in | Verify fingerprint enrolled on device |

---

## ✅ System Checklist

### **Daily:**
- [ ] Device sync working (every 5 min)
- [ ] Attendance processing (1:00 AM)
- [ ] Review pending requests
- [ ] Check for errors in logs

### **Weekly:**
- [ ] Review all device sync status
- [ ] Generate attendance reports
- [ ] Check leave balances
- [ ] Backup database

### **Monthly:**
- [ ] Generate monthly reports
- [ ] Review system performance
- [ ] Update leave balances for new year
- [ ] Security audit

---

**🎉 All Systems Ready!**

The E-Attendance System is now fully operational. Follow this guide for daily operations.
