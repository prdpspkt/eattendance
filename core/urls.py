from django.urls import path
from . import views
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('login/', views.custom_login, name='login'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('profile/', views.profile, name='profile'),
    path('change-password/', views.change_password, name='change_password'),
    path('my-attendance/', views.my_attendance, name='my_attendance'),
    path('attendance-calendar/', views.attendance_calendar, name='attendance_calendar'),
    path('my-leaves/', views.my_leaves, name='my_leaves'),
    path('request-leave/', views.request_leave, name='request_leave'),
    path('my-travel-orders/', views.my_travel_orders, name='my_travel_orders'),
    path('request-travel-order/', views.request_travel_order, name='request_travel_order'),
    path('shifts/', views.shift_management, name='shift_management'),
    path('shifts/create/', views.shift_create, name='shift_create'),
    path('shifts/<int:shift_id>/edit/', views.shift_edit, name='shift_edit'),
    path('shifts/<int:shift_id>/delete/', views.shift_delete, name='shift_delete'),
    path('logout/', views.custom_logout, name='logout'),
]
