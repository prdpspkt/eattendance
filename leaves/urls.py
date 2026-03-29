from django.urls import path
from . import views

urlpatterns = [
    path('leave-requests/', views.leave_requests_management, name='leave_requests_management'),
    path('leave-requests/<int:leave_request_id>/approve/', views.leave_request_approve, name='leave_request_approve'),
    path('leave-requests/<int:leave_request_id>/reject/', views.leave_request_reject, name='leave_request_reject'),
    path('leave-requests/<int:leave_request_id>/delete/', views.leave_request_delete, name='leave_request_delete'),
    path('leave-types/', views.leave_type_management, name='leave_type_management'),
    path('leave-types/create/', views.leave_type_create, name='leave_type_create'),
    path('leave-types/<int:leave_type_id>/edit/', views.leave_type_edit, name='leave_type_edit'),
    path('leave-types/<int:leave_type_id>/delete/', views.leave_type_delete, name='leave_type_delete'),
]
