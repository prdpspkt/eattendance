from django.urls import path
from . import views

app_name = 'devices'

urlpatterns = [
    path('', views.device_list, name='device_list'),
    path('create/', views.device_create, name='device_create'),
    path('device/<int:device_id>/', views.device_detail, name='device_detail'),
    path('device/<int:device_id>/edit/', views.device_update, name='device_update'),
    path('device/<int:device_id>/sync-users/', views.sync_users, name='sync_users'),
    path('device/<int:device_id>/sync-attendance/', views.sync_attendance, name='sync_attendance'),
    path('device/<int:device_id>/test-connection/', views.test_connection, name='test_connection'),
    path('link/<int:enrollment_id>/', views.link_employee, name='link_employee'),
    path('unlink/<int:enrollment_id>/', views.unlink_employee, name='unlink_employee'),
    path('todays-attendance/', views.todays_attendance, name='todays_attendance'),
    path('employees/', views.employee_management, name='employee_management'),
    path('employees/create/', views.employee_create, name='employee_create'),
    path('employees/<int:employee_id>/', views.employee_detail, name='employee_detail'),
    path('employees/<int:employee_id>/edit/', views.employee_edit, name='employee_edit'),
]
