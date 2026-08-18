from django.urls import path

from . import views

app_name = 'attendance'

urlpatterns = [
    path('manual-entry/', views.manual_attendance, name='manual_attendance'),
    path('overtime/', views.overtime_report, name='overtime_report'),
    path('overtime/export/', views.overtime_export, name='overtime_export'),
    path('overtime/<int:record_id>/edit/', views.overtime_edit, name='overtime_edit'),
    path('overtime/<int:record_id>/approve/', views.overtime_approve, name='overtime_approve'),
]
