from django.urls import path
from . import views

urlpatterns = [
    path('travel-orders/', views.travel_orders_management, name='travel_orders_management'),
    path('travel-orders/<int:travel_order_id>/approve/', views.travel_order_approve, name='travel_order_approve'),
    path('travel-orders/<int:travel_order_id>/reject/', views.travel_order_reject, name='travel_order_reject'),
    path('travel-orders/<int:travel_order_id>/delete/', views.travel_order_delete, name='travel_order_delete'),
]
