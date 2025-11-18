from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.db.models import Q, Sum, F
from django.http import JsonResponse, HttpResponse
from django.utils import timezone
from datetime import datetime, timedelta
from decimal import Decimal
import json
from django.views.decorators.csrf import csrf_protect

from .models import (User, Product, Supplier, Category, Sale, SaleItem, 
                    StockMovement, Payment, PendingCart, SavedCart, RefundRequest)
from .forms import (StaffRegistrationForm, PaymentForm, ProductForm, RefundRequestForm)

def is_admin(user):
    return user.is_authenticated and (user.role == 'admin' or user.is_superuser)

def is_staff_or_admin(user):
    return user.is_authenticated and user.role in ['admin', 'staff', 'manager']

# Authentication Views
def login_view(request):
    if request.user.is_authenticated:
        return redirect('home')
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            login(request, user)
            messages.success(request, f'Welcome back, {user.first_name or user.username}!')
            return redirect('home')
        else:
            messages.error(request, 'Invalid username or password.')
    
    return render(request, 'login.html')

@login_required
def logout_view(request):
    logout(request)
    messages.success(request, 'You have been logged out successfully.')
    return redirect('login')

# Home/POS View
@login_required
@user_passes_test(is_staff_or_admin)
def home(request):
    return render(request, 'home.html')

# Product Search API
@login_required
def search_products(request):
    query = request.GET.get('q', '')
    if query:
        products = Product.objects.filter(
            Q(name__icontains=query) | 
            Q(description__icontains=query)
        )[:20]
        
        data = [{
            'id': p.id,
            'name': p.name,
            'sku': p.sku,
            'price': str(p.price),
            'quantity': p.quantity,
            'image': p.image.url if p.image else None
        } for p in products]
        
        return JsonResponse(data, safe=False)
    return JsonResponse([], safe=False)

# Process Sale - UPDATED: Fix saved cart deletion
@login_required
def process_sale(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            items = data.get('items', [])
            customer_name = data.get('customer_name', '').strip()
            customer_phone = data.get('customer_phone', '').strip()
            amount_paid = Decimal(data.get('amount_paid', 0))
            payment_method = data.get('payment_method', 'cash')
            saved_cart_id = data.get('saved_cart_id')  # Get saved cart ID if provided
            
            if not items:
                return JsonResponse({'success': False, 'error': 'No items in cart'})

            if amount_paid < Decimal(data.get('total_amount', 0)):
                if not customer_name:
                    return JsonResponse({'success': False, 'error': 'Customer name is required for installment payment'})
                
                if not customer_phone:
                    return JsonResponse({'success': False, 'error': 'Customer phone is required for installment payment'})
            else:
                if not customer_name:
                    customer_name = 'Walk-in Customer'

            # Validate products and stock
            for item in items:
                try:
                    product = Product.objects.get(id=item['product_id'])
                    if product.quantity == 0:
                        return JsonResponse({
                            'success': False,
                            'error': f'{product.name} is OUT OF STOCK'
                        })
                    if product.quantity < item['quantity']:
                        return JsonResponse({
                            'success': False, 
                            'error': f'{product.name} has insufficient stock. Available: {product.quantity}, Requested: {item["quantity"]}'
                        })
                except Product.DoesNotExist:
                    return JsonResponse({'success': False, 'error': 'Product not found'})
            
            # Generate invoice number
            last_sale = Sale.objects.order_by('-id').first()
            invoice_num = f"INV-{(last_sale.id + 1) if last_sale else 1:06d}"
            
            # Calculate totals
            subtotal = sum(Decimal(item['total']) + Decimal(item['discount']) for item in items)
            total_discount = sum(Decimal(item['discount']) for item in items)
            total = subtotal - total_discount
            balance = total - amount_paid
            
            # Determine payment status
            if balance <= 0:
                payment_status = 'paid'
                balance = 0
            elif amount_paid > 0:
                payment_status = 'partial'
            else:
                payment_status = 'unpaid'
            
            # Create sale
            sale = Sale.objects.create(
                invoice_number=invoice_num,
                staff=request.user,
                customer_name=customer_name,
                customer_phone=customer_phone,
                subtotal=subtotal,
                discount=total_discount,
                total=total,
                amount_paid=amount_paid,
                balance=balance,
                payment_status=payment_status
            )
            
            # Create sale items and update stock
            for item in items:
                product = Product.objects.get(id=item['product_id'])
                
                SaleItem.objects.create(
                    sale=sale,
                    product=product,
                    product_name=product.name,
                    quantity=item['quantity'],
                    price=Decimal(item['price']),
                    discount=Decimal(item['discount']),
                    total=Decimal(item['total'])
                )
                
                # Update product quantity
                product.quantity -= item['quantity']
                product.save()
                
                # Record stock movement
                StockMovement.objects.create(
                    product=product,
                    movement_type='out',
                    quantity=-item['quantity'],
                    reference=invoice_num,
                    notes=f'Sale to {customer_name}',
                    created_by=request.user
                )
            
            # Record payment if any
            if amount_paid > 0:
                Payment.objects.create(
                    sale=sale,
                    amount=amount_paid,
                    payment_method=payment_method,
                    created_by=request.user
                )
            
            # Clear pending cart after successful sale
            try:
                PendingCart.objects.filter(staff=request.user).delete()
            except Exception as e:
                print(f"Error clearing pending cart: {e}")
            
            # DELETE SAVED CART if this sale came from a saved cart
            if saved_cart_id:
                try:
                    # Delete the saved cart from database
                    deleted_count = SavedCart.objects.filter(id=saved_cart_id, staff=request.user).delete()
                    if deleted_count[0] > 0:
                        print(f"Saved cart ID {saved_cart_id} deleted successfully after sale")
                    else:
                        print(f"Saved cart ID {saved_cart_id} not found or already deleted")
                except Exception as e:
                    print(f"Error deleting saved cart: {e}")
            
            return JsonResponse({
                'success': True,
                'invoice_number': invoice_num,
                'sale_id': sale.id
            })
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request'})

# Admin Dashboard
@login_required
@user_passes_test(is_admin)
def admin_dashboard(request):
    # Get date filter from request
    date_filter = request.GET.get('date_filter', 'today')
    custom_start = request.GET.get('custom_start')
    custom_end = request.GET.get('custom_end')
    
    # Calculate date range based on filter
    today = timezone.now().date()
    
    if date_filter == 'today':
        start_date = today
        end_date = today
    elif date_filter == 'week':
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=6)
    elif date_filter == 'month':
        start_date = today.replace(day=1)
        end_date = (start_date + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    elif date_filter == 'year':
        start_date = today.replace(month=1, day=1)
        end_date = today.replace(month=12, day=31)
    elif date_filter == 'custom' and custom_start and custom_end:
        start_date = datetime.strptime(custom_start, '%Y-%m-%d').date()
        end_date = datetime.strptime(custom_end, '%Y-%m-%d').date()
    else:
        # Default to today
        start_date = today
        end_date = today
        date_filter = 'today'
    
    # Filter sales by date range
    sales_in_period = Sale.objects.filter(
        created_at__date__gte=start_date,
        created_at__date__lte=end_date
    )
    
    # Basic statistics
    total_products = Product.objects.count()
    low_stock_products = Product.objects.filter(quantity__lte=F('reorder_level')).count()
    total_sales = sales_in_period.count()
    total_revenue = sales_in_period.aggregate(Sum('total'))['total__sum'] or 0
    debtors_count = sales_in_period.filter(balance__gt=0).count()
    
    # Payment method statistics
    payments_in_period = Payment.objects.filter(
        created_at__date__gte=start_date,
        created_at__date__lte=end_date
    )
    
    cash_payments = payments_in_period.filter(payment_method='cash').aggregate(Sum('amount'))['amount__sum'] or 0
    transfer_payments = payments_in_period.filter(payment_method='transfer').aggregate(Sum('amount'))['amount__sum'] or 0
    card_payments = payments_in_period.filter(payment_method='card').aggregate(Sum('amount'))['amount__sum'] or 0
    
    # Recent sales (last 10 regardless of filter)
    recent_sales = Sale.objects.select_related('staff').prefetch_related('items').order_by('-created_at')[:10]
    
    # Low stock alert
    low_stock = Product.objects.filter(quantity__lte=F('reorder_level'))[:10]
    
    context = {
        'total_products': total_products,
        'low_stock_products': low_stock_products,
        'total_sales': total_sales,
        'total_revenue': total_revenue,
        'debtors_count': debtors_count,
        'recent_sales': recent_sales,
        'low_stock': low_stock,
        'cash_payments': cash_payments,
        'transfer_payments': transfer_payments,
        'card_payments': card_payments,
        'date_filter': date_filter,
        'start_date': start_date,
        'end_date': end_date,
        'today': today,
    }
    return render(request, 'admin_dashboard.html', context)

# Staff Management
@login_required
@user_passes_test(is_admin)
def register_staff(request):
    if request.method == 'POST':
        form = StaffRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, f'Staff {user.username} registered successfully!')
            return redirect('staff_list')
    else:
        form = StaffRegistrationForm()
    
    return render(request, 'register_staff.html', {'form': form})

@login_required
@user_passes_test(is_admin)
def staff_list(request):
    staff = User.objects.all().order_by('-date_joined')
    return render(request, 'staff_list.html', {'staff': staff})

# Product Management
@login_required
@user_passes_test(is_admin)
def product_list(request):
    products = Product.objects.select_related('category', 'supplier').all()
    return render(request, 'product_list.html', {'products': products})

@login_required
@user_passes_test(is_admin)
def add_product(request):
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES)
        if form.is_valid():
            product = form.save()
            messages.success(request, f'Product {product.name} added successfully!')
            return redirect('product_list')
    else:
        form = ProductForm()
    
    return render(request, 'product_form.html', {'form': form, 'action': 'Add'})

@login_required
@user_passes_test(is_admin)
def edit_product(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES, instance=product)
        if form.is_valid():
            form.save()
            messages.success(request, f'Product {product.name} updated successfully!')
            return redirect('product_list')
    else:
        form = ProductForm(instance=product)
    
    return render(request, 'product_form.html', {'form': form, 'action': 'Edit'})

@login_required
@user_passes_test(is_admin)
def delete_product(request, pk):
    product = get_object_or_404(Product, pk=pk)
    if request.method == 'POST':
        product.delete()
        messages.success(request, 'Product deleted successfully!')
        return redirect('product_list')
    return render(request, 'product_confirm_delete.html', {'product': product})

# Debtor Management
@login_required
@user_passes_test(is_staff_or_admin)
def debtors_list(request):
    debtors = Sale.objects.filter(balance__gt=0).select_related('staff').prefetch_related('payments').order_by('-created_at')
    return render(request, 'debtors_list.html', {'debtors': debtors})

@login_required
@user_passes_test(is_staff_or_admin)
def record_payment(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)
    
    if request.method == 'POST':
        form = PaymentForm(request.POST)
        if form.is_valid():
            payment = form.save(commit=False)
            payment.sale = sale
            payment.created_by = request.user
            
            # Validate payment amount
            if payment.amount > sale.balance:
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': False, 'error': f'Payment amount (₦{payment.amount}) cannot exceed balance of ₦{sale.balance}'})
                else:
                    messages.error(request, f'Payment amount (₦{payment.amount}) cannot exceed balance of ₦{sale.balance}')
                    return render(request, 'record_payment.html', {'form': form, 'sale': sale})
            
            if payment.amount <= 0:
                if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return JsonResponse({'success': False, 'error': 'Payment amount must be greater than zero'})
                else:
                    messages.error(request, 'Payment amount must be greater than zero')
                    return render(request, 'record_payment.html', {'form': form, 'sale': sale})
            
            payment.save()
            
            # Update sale balance
            sale.amount_paid += payment.amount
            sale.balance = sale.total - sale.amount_paid
            
            if sale.balance <= 0:
                sale.payment_status = 'paid'
                sale.balance = 0
            else:
                sale.payment_status = 'partial'
            
            sale.save()
            
            # Return JSON response for AJAX requests
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({
                    'success': True,
                    'message': f'Payment of ₦{payment.amount} recorded successfully!',
                    'new_balance': float(sale.balance),
                    'payment_status': sale.payment_status
                })
            else:
                messages.success(request, f'Payment of ₦{payment.amount} recorded successfully!')
                return redirect('view_receipt', sale_id=sale.id)
    else:
        form = PaymentForm()
    
    return render(request, 'record_payment.html', {'form': form, 'sale': sale})

# Payment History View
@login_required
@user_passes_test(is_staff_or_admin)
def debtor_payment_history(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)
    payments = sale.payments.all().order_by('-created_at')
    
    context = {
        'sale': sale,
        'payments': payments,
    }
    return render(request, 'debtor_payment_history.html', context)

# Receipt Views
@login_required
def view_receipt(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)
    return render(request, 'receipt.html', {'sale': sale})

# Edit Receipt View
@login_required
def edit_receipt(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)
    
    if request.method == 'POST':
        customer_name = request.POST.get('customer_name', '').strip()
        customer_phone = request.POST.get('customer_phone', '').strip()
        
        if not customer_name:
            messages.error(request, 'Customer name is required')
            return render(request, 'edit_receipt.html', {'sale': sale})
        
        if not customer_phone:
            messages.error(request, 'Customer phone is required')
            return render(request, 'edit_receipt.html', {'sale': sale})
        
        sale.customer_name = customer_name
        sale.customer_phone = customer_phone
        sale.save()
        
        messages.success(request, 'Receipt updated successfully!')
        return redirect('view_receipt', sale_id=sale.id)
    
    return render(request, 'edit_receipt.html', {'sale': sale})

@login_required
@user_passes_test(is_admin)
@csrf_protect
def edit_staff(request):
    if request.method == 'POST':
        try:
            user_id = request.POST.get('user_id')
            username = request.POST.get('username')
            email = request.POST.get('email')
            first_name = request.POST.get('first_name')
            last_name = request.POST.get('last_name')
            phone = request.POST.get('phone')
            role = request.POST.get('role')
            is_active = request.POST.get('is_active') == 'true'
            
            user = get_object_or_404(User, id=user_id)
            
            if User.objects.filter(username=username).exclude(id=user_id).exists():
                return JsonResponse({'success': False, 'error': 'Username already exists!'})
            
            if User.objects.filter(email=email).exclude(id=user_id).exists():
                return JsonResponse({'success': False, 'error': 'Email already exists!'})
            
            user.username = username
            user.email = email
            user.first_name = first_name
            user.last_name = last_name
            user.phone = phone
            user.role = role
            user.is_active = is_active
            user.save()
            
            return JsonResponse({'success': True})
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

@login_required
@user_passes_test(is_staff_or_admin)
def save_pending_cart(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            cart_data = {
                'items': data.get('items', []),
                'customer_name': data.get('customer_name', ''),
                'customer_phone': data.get('customer_phone', ''),
                'payment_type': data.get('payment_type', 'full'),
                'payment_method': data.get('payment_method', 'cash'),
                'amount_paid': data.get('amount_paid', 0),
            }
            
            # Delete any existing pending cart for this staff
            PendingCart.objects.filter(staff=request.user).delete()
            
            # Create new pending cart
            pending_cart = PendingCart.objects.create(
                staff=request.user,
                cart_data=cart_data
            )
            
            return JsonResponse({
                'success': True,
                'message': 'Cart saved successfully!',
                'cart_id': pending_cart.id
            })
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request'})

@login_required
@user_passes_test(is_staff_or_admin)
def load_pending_cart(request):
    try:
        pending_cart = PendingCart.objects.filter(staff=request.user).first()
        
        if pending_cart:
            return JsonResponse({
                'success': True,
                'cart_data': pending_cart.cart_data,
                'cart_id': pending_cart.id
            })
        else:
            return JsonResponse({'success': False, 'error': 'No pending cart found'})
            
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
@user_passes_test(is_staff_or_admin)
def delete_pending_cart(request):
    if request.method == 'POST':
        try:
            deleted_count = PendingCart.objects.filter(staff=request.user).delete()
            return JsonResponse({
                'success': True,
                'message': 'Pending cart cleared successfully!'
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request'})


@login_required
@user_passes_test(is_staff_or_admin)
def saved_carts_list(request):
    saved_carts = SavedCart.objects.filter(staff=request.user).order_by('-created_at')
    return render(request, 'saved_carts_list.html', {'saved_carts': saved_carts})

@login_required
@user_passes_test(is_staff_or_admin)
def save_cart(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            cart_name = data.get('cart_name', 'Unsaved Cart').strip()
            
            if not cart_name:
                cart_name = "Unsaved Cart"
            
            cart_data = {
                'items': data.get('items', []),
                'customer_name': data.get('customer_name', ''),
                'customer_phone': data.get('customer_phone', ''),
                'payment_type': data.get('payment_type', 'full'),
                'payment_method': data.get('payment_method', 'cash'),
                'amount_paid': data.get('amount_paid', 0),
            }
            
            saved_cart = SavedCart.objects.create(
                staff=request.user,
                cart_name=cart_name,
                cart_data=cart_data
            )
            
            return JsonResponse({
                'success': True,
                'message': 'Cart saved successfully!',
                'cart_id': saved_cart.id
            })
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request'})

@login_required
@user_passes_test(is_staff_or_admin)
def load_saved_cart(request, cart_id):
    try:
        saved_cart = get_object_or_404(SavedCart, id=cart_id, staff=request.user)
        
        return JsonResponse({
            'success': True,
            'cart_data': saved_cart.cart_data,
            'cart_name': saved_cart.cart_name
        })
            
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
@user_passes_test(is_staff_or_admin)
def delete_saved_cart(request, cart_id):
    if request.method == 'POST':
        try:
            saved_cart = get_object_or_404(SavedCart, id=cart_id, staff=request.user)
            cart_name = saved_cart.cart_name
            saved_cart.delete()
            
            return JsonResponse({
                'success': True,
                'message': f'Cart "{cart_name}" deleted successfully!'
            })
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request'})

@login_required
@user_passes_test(is_staff_or_admin)
def view_saved_cart(request, cart_id):
    saved_cart = get_object_or_404(SavedCart, id=cart_id, staff=request.user)
    return render(request, 'saved_cart_detail.html', {'saved_cart': saved_cart})

@login_required
@user_passes_test(is_staff_or_admin)
def refund_requests_list(request):
    refund_requests = RefundRequest.objects.all().order_by('-request_date')
    
    # Staff can only see their own requests, admin can see all
    if request.user.role != 'admin' and not request.user.is_superuser:
        refund_requests = refund_requests.filter(created_by=request.user)
    
    return render(request, 'refund_requests_list.html', {'refund_requests': refund_requests})

@login_required
@user_passes_test(is_staff_or_admin)
def create_refund_request(request):
    if request.method == 'POST':
        form = RefundRequestForm(request.POST)
        if form.is_valid():
            refund_request = form.save(commit=False)
            refund_request.created_by = request.user
            refund_request.save()
            
            messages.success(request, 'Refund request submitted successfully!')
            return redirect('refund_requests_list')
    else:
        form = RefundRequestForm()
    
    return render(request, 'refund_request_form.html', {'form': form, 'action': 'Create'})

@login_required
@user_passes_test(is_staff_or_admin)
def edit_refund_request(request, pk):
    refund_request = get_object_or_404(RefundRequest, pk=pk)
    
    # Check if user can edit this request
    if refund_request.created_by != request.user and request.user.role != 'admin':
        messages.error(request, 'You can only edit your own refund requests.')
        return redirect('refund_requests_list')
    
    if not refund_request.can_edit():
        messages.error(request, 'Cannot edit refund request that has already been processed.')
        return redirect('refund_requests_list')
    
    if request.method == 'POST':
        form = RefundRequestForm(request.POST, instance=refund_request)
        if form.is_valid():
            form.save()
            messages.success(request, 'Refund request updated successfully!')
            return redirect('refund_requests_list')
    else:
        form = RefundRequestForm(instance=refund_request)
    
    return render(request, 'refund_request_form.html', {'form': form, 'action': 'Edit', 'refund_request': refund_request})

@login_required
@user_passes_test(is_admin)
def approve_refund_request(request, pk):
    refund_request = get_object_or_404(RefundRequest, pk=pk)
    
    if not refund_request.can_approve_decline(request.user):
        messages.error(request, 'This refund request cannot be approved.')
        return redirect('refund_requests_list')
    
    refund_request.status = 'approved'
    refund_request.approved_by = request.user
    refund_request.approved_date = timezone.now()
    refund_request.save()
    
    messages.success(request, f'Refund request #{refund_request.id} approved successfully!')
    return redirect('refund_requests_list')

@login_required
@user_passes_test(is_admin)
def decline_refund_request(request, pk):
    refund_request = get_object_or_404(RefundRequest, pk=pk)
    
    if not refund_request.can_approve_decline(request.user):
        messages.error(request, 'This refund request cannot be declined.')
        return redirect('refund_requests_list')
    
    refund_request.status = 'declined'
    refund_request.approved_by = request.user
    refund_request.approved_date = timezone.now()
    refund_request.save()
    
    messages.success(request, f'Refund request #{refund_request.id} declined.')
    return redirect('refund_requests_list')