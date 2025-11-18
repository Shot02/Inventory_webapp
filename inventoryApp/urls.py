from django.urls import path
from . import views

urlpatterns = [
    # Auth
    path('', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    
    # Home/POS
    path('home/', views.home, name='home'),
    path('api/search-products/', views.search_products, name='search_products'),
    path('api/process-sale/', views.process_sale, name='process_sale'),
    path('receipt/<int:sale_id>/', views.view_receipt, name='view_receipt'),
    path('receipt/<int:sale_id>/edit/', views.edit_receipt, name='edit_receipt'),
    
    # Admin
    path('admin_dashboard/', views.admin_dashboard, name='admin_dashboard'),
    
    # Staff Management
    path('register_staff/', views.register_staff, name='register_staff'),
    path('staff/', views.staff_list, name='staff_list'),
    path('staff/edit/', views.edit_staff, name='edit_staff'),
    
    # Products
    path('products/', views.product_list, name='product_list'),
    path('products/add/', views.add_product, name='add_product'),
    path('products/edit/<int:pk>/', views.edit_product, name='edit_product'),
    path('products/delete/<int:pk>/', views.delete_product, name='delete_product'),
    
    # Debtors
    path('debtors/', views.debtors_list, name='debtors_list'),
    path('debtors/payment/<int:sale_id>/', views.record_payment, name='record_payment'),
    path('debtors/history/<int:sale_id>/', views.debtor_payment_history, name='debtor_payment_history'),
    path('receipt/<int:sale_id>/edit/', views.edit_receipt, name='edit_receipt'),

    # Pending Cart URLs
    path('api/save-pending-cart/', views.save_pending_cart, name='save_pending_cart'),
    path('api/load-pending-cart/', views.load_pending_cart, name='load_pending_cart'),
    path('api/delete-pending-cart/', views.delete_pending_cart, name='delete_pending_cart'),

    # Saved Carts URLs
    path('saved-carts/', views.saved_carts_list, name='saved_carts_list'),
    path('api/save-cart/', views.save_cart, name='save_cart'),
    path('api/load-saved-cart/<int:cart_id>/', views.load_saved_cart, name='load_saved_cart'),
    path('api/delete-saved-cart/<int:cart_id>/', views.delete_saved_cart, name='delete_saved_cart'),
    path('saved-cart/<int:cart_id>/', views.view_saved_cart, name='view_saved_cart'),

    # Refund Requests
    path('refund-requests/', views.refund_requests_list, name='refund_requests_list'),
    path('refund-requests/create/', views.create_refund_request, name='create_refund_request'),
    path('refund-requests/edit/<int:pk>/', views.edit_refund_request, name='edit_refund_request'),
    path('refund-requests/approve/<int:pk>/', views.approve_refund_request, name='approve_refund_request'),
    path('refund-requests/decline/<int:pk>/', views.decline_refund_request, name='decline_refund_request'),
]