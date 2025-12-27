from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login, logout
from django.db.models import Q, Sum, F, Count
from django.utils import timezone
from datetime import datetime, timedelta
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
import json
import uuid
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from decimal import Decimal

# Import your models
from .models import (
    User, Product, Sale, SaleItem, Payment, Category, 
    Supplier, StockMovement, PendingCart, SavedCart, RefundRequest
)

# =================== AUTHENTICATION VIEWS ===================
def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)
        
        if user is not None:
            login(request, user)
            if user.role == 'admin' or user.is_superuser:
                return redirect('admin_dashboard')
            else:
                return redirect('home')
        else:
            messages.error(request, 'Invalid username or password')
    
    return render(request, 'login.html')

@login_required
def logout_view(request):
    logout(request)
    return redirect('login')

# =================== HOME / POS VIEWS ===================
@login_required
def home(request):
    products = Product.objects.filter(quantity__gt=0).order_by('name')
    categories = Category.objects.all()
    
    pending_cart = PendingCart.objects.filter(staff=request.user).first()
    
    context = {
        'products': products,
        'categories': categories,
        'pending_cart': pending_cart.cart_data if pending_cart else None,
        'now': timezone.now()
    }
    return render(request, 'home.html', context)

@login_required
def search_products(request):
    """API endpoint for POS product search"""
    query = request.GET.get('q', '')
    
    if query:
        products = Product.objects.filter(
            Q(name__icontains=query) |
            Q(sku__icontains=query) |
            Q(category__name__icontains=query)
        ).filter(quantity__gt=0)[:20]
    else:
        products = Product.objects.filter(quantity__gt=0)[:20]
    
    results = []
    for product in products:
        results.append({
            'id': product.id,
            'name': product.name,
            'sku': product.sku,
            'price': float(product.price),
            'quantity': product.quantity,
            'image': product.image.url if product.image else None,
            'category': product.category.name if product.category else '',
        })
    
    return JsonResponse({'products': results})

@login_required
@csrf_exempt
def process_sale(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            # Validate data
            if not data.get('items'):
                return JsonResponse({'success': False, 'error': 'No items in cart'})
            
            # Calculate totals correctly
            subtotal = sum(
                Decimal(str(item['price'])) * Decimal(str(item['quantity'])) - Decimal(str(item.get('discount', 0)))
                for item in data['items']
            )
            
            # Apply discount if provided
            discount = Decimal(str(data.get('discount', 0)))
            total = subtotal - discount
            amount_paid = Decimal(str(data.get('amount_paid', 0)))
            balance = total - amount_paid
            
            # Validate stock before processing
            for item in data['items']:
                try:
                    product = Product.objects.get(id=item['product_id'])
                    if product.quantity < item['quantity']:
                        return JsonResponse({
                            'success': False, 
                            'error': f'Insufficient stock for {product.name}. Available: {product.quantity}, Requested: {item["quantity"]}'
                        })
                except Product.DoesNotExist:
                    return JsonResponse({'success': False, 'error': f'Product ID {item["product_id"]} not found'})
            
            # Generate invoice number
            today_str = timezone.now().strftime('%Y%m%d')
            invoice_number = f"INV-{today_str}-{uuid.uuid4().hex[:6].upper()}"
            
            # Determine payment status
            if balance <= 0:
                payment_status = 'paid'
            elif balance < total:
                payment_status = 'partial'
            else:
                payment_status = 'unpaid'
            
            # Create sale
            sale = Sale.objects.create(
                invoice_number=invoice_number,
                staff=request.user,
                customer_name=data.get('customer_name', ''),
                customer_phone=data.get('customer_phone', ''),
                subtotal=subtotal,
                discount=discount,
                total=total,
                amount_paid=amount_paid,
                balance=balance,
                payment_status=payment_status
            )
            
            # Create sale items and update stock
            for item in data.get('items', []):
                product = Product.objects.get(id=item['product_id'])
                
                # Create sale item
                SaleItem.objects.create(
                    sale=sale,
                    product=product,
                    product_name=product.name,
                    quantity=item['quantity'],
                    price=item['price'],
                    discount=item.get('discount', 0),
                    total=(Decimal(str(item['price'])) * Decimal(str(item['quantity']))) - Decimal(str(item.get('discount', 0)))
                )
                
                # Update product quantity
                product.quantity -= item['quantity']
                product.save()
                
                # Create stock movement record
                StockMovement.objects.create(
                    product=product,
                    movement_type='out',
                    quantity=item['quantity'],
                    reference=invoice_number,
                    notes=f"Sold in invoice {invoice_number}",
                    created_by=request.user
                )
            
            # Create payment record if payment made
            if amount_paid > 0:
                Payment.objects.create(
                    sale=sale,
                    amount=amount_paid,
                    payment_method=data.get('payment_method', 'cash'),
                    reference=data.get('reference', ''),
                    notes=data.get('notes', ''),
                    created_by=request.user
                )
            
            # Clear pending cart
            PendingCart.objects.filter(staff=request.user).delete()
            
            return JsonResponse({
                'success': True,
                'sale_id': sale.id,
                'invoice_number': invoice_number,
                'total': float(total),
                'balance': float(balance)
            })
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

@login_required
def view_receipt(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)
    items = sale.items.all()
    payments = sale.payments.all()
    
    # Get payment method from latest payment
    payment_method = payments.last().payment_method if payments.exists() else 'cash'
    
    context = {
        'sale': sale,
        'items': items,
        'payments': payments,
        'payment_method': payment_method
    }
    return render(request, 'receipt.html', context)

# =================== DASHBOARD VIEWS ===================
@login_required
def admin_dashboard(request):
    # Get date filter from request
    date_filter = request.GET.get('date_filter', 'today')
    
    # Calculate date range correctly
    today = timezone.now().date()
    
    if date_filter == 'today':
        start_date = today
        end_date = today + timedelta(days=1)  # Include today fully
    elif date_filter == 'week':
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=7)
    elif date_filter == 'month':
        start_date = today.replace(day=1)
        # Get last day of month
        if start_date.month == 12:
            end_date = start_date.replace(year=start_date.year + 1, month=1, day=1)
        else:
            end_date = start_date.replace(month=start_date.month + 1, day=1)
    elif date_filter == 'year':
        start_date = today.replace(month=1, day=1)
        end_date = today.replace(year=today.year + 1, month=1, day=1)
    else:
        custom_start = request.GET.get('custom_start')
        custom_end = request.GET.get('custom_end')
        if custom_start and custom_end:
            try:
                start_date = datetime.strptime(custom_start, '%Y-%m-%d').date()
                end_date = datetime.strptime(custom_end, '%Y-%m-%d').date() + timedelta(days=1)
            except ValueError:
                start_date = today
                end_date = today + timedelta(days=1)
        else:
            start_date = today
            end_date = today + timedelta(days=1)
    
    # Statistics - FIXED calculations
    total_products = Product.objects.count()
    
    # Total sales count (only completed sales)
    total_sales = Sale.objects.filter(
        created_at__range=[start_date, end_date]
    ).count()
    
    # Low stock products
    low_stock_products = Product.objects.filter(
        quantity__lte=F('reorder_level'),
        quantity__gt=0
    ).count()
    
    # Debtors count (balance > 0) within date range
    debtors_count = Sale.objects.filter(
        balance__gt=0,
        created_at__range=[start_date, end_date]
    ).count()
    
    # Payment statistics - FIXED: Use payment dates, not sale dates
    cash_payments = Payment.objects.filter(
        payment_method='cash',
        created_at__range=[start_date, end_date]
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    transfer_payments = Payment.objects.filter(
        payment_method='transfer',
        created_at__range=[start_date, end_date]
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    card_payments = Payment.objects.filter(
        payment_method='card',
        created_at__range=[start_date, end_date]
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    # Total revenue - FIXED: Only count actual payments received
    total_revenue = Payment.objects.filter(
        created_at__range=[start_date, end_date]
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    
    # Recent sales with search and limit
    recent_sales = Sale.objects.filter(
        created_at__range=[start_date, end_date]
    ).select_related('staff').order_by('-created_at')
    
    sales_search = request.GET.get('sales_search', '')
    if sales_search:
        recent_sales = recent_sales.filter(
            Q(invoice_number__icontains=sales_search) |
            Q(customer_name__icontains=sales_search) |
            Q(staff__username__icontains=sales_search) |
            Q(customer_phone__icontains=sales_search)
        )[:50]
    else:
        recent_sales = recent_sales[:50]
    
    # Low stock items with search and limit
    low_stock = Product.objects.filter(
        quantity__lte=F('reorder_level'),
        quantity__gt=0
    ).select_related('category').order_by('quantity')
    
    stock_search = request.GET.get('stock_search', '')
    if stock_search:
        low_stock = low_stock.filter(
            Q(name__icontains=stock_search) |
            Q(sku__icontains=stock_search) |
            Q(category__name__icontains=stock_search)
        )[:50]
    else:
        low_stock = low_stock[:50]
    
    context = {
        'date_filter': date_filter,
        'start_date': start_date,
        'end_date': end_date - timedelta(days=1),  # Adjust for display
        'today': today,
        'total_products': total_products,
        'total_sales': total_sales,
        'low_stock_products': low_stock_products,
        'debtors_count': debtors_count,
        'cash_payments': cash_payments,
        'transfer_payments': transfer_payments,
        'card_payments': card_payments,
        'total_revenue': total_revenue,
        'recent_sales': recent_sales,
        'low_stock': low_stock,
        'sales_search': sales_search,
        'stock_search': stock_search,
    }
    
    return render(request, 'admin_dashboard.html', context)

# =================== PRODUCT VIEWS ===================
@login_required
def product_list(request):
    products = Product.objects.all().select_related('category', 'supplier').order_by('-created_at')
    
    search_query = request.GET.get('search', '')
    if search_query:
        products = products.filter(
            Q(name__icontains=search_query) |
            Q(sku__icontains=search_query) |
            Q(category__name__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(supplier__name__icontains=search_query)
        )[:50]
    
    categories = Category.objects.all()
    suppliers = Supplier.objects.all()
    
    context = {
        'products': products,
        'search_query': search_query,
        'categories': categories,
        'suppliers': suppliers,
    }
    return render(request, 'product_list.html', context)

@login_required
def add_product(request):
    if request.method == 'POST':
        try:
            # Get form data
            name = request.POST.get('name')
            category_id = request.POST.get('category')
            supplier_id = request.POST.get('supplier')
            description = request.POST.get('description', '')
            price = request.POST.get('price')
            cost_price = request.POST.get('cost_price', 0)
            quantity = request.POST.get('quantity', 0)
            reorder_level = request.POST.get('reorder_level', 10)
            image = request.FILES.get('image')
            
            # Validate required fields
            if not name or not price:
                messages.error(request, 'Name and price are required')
                return redirect('add_product')
            
            # Get category and supplier
            category = None
            if category_id:
                category = Category.objects.get(id=category_id)
            
            supplier = None
            if supplier_id:
                supplier = Supplier.objects.get(id=supplier_id)
            
            # Create product
            product = Product.objects.create(
                name=name,
                category=category,
                supplier=supplier,
                description=description,
                price=price,
                cost_price=cost_price,
                quantity=quantity,
                reorder_level=reorder_level,
            )
            
            # Handle image separately to avoid issues
            if image:
                product.image = image
                product.save()
            
            messages.success(request, f'Product "{name}" added successfully!')
            return redirect('product_list')
            
        except Exception as e:
            messages.error(request, f'Error adding product: {str(e)}')
            return redirect('add_product')
    
    categories = Category.objects.all()
    suppliers = Supplier.objects.all()
    
    context = {
        'categories': categories,
        'suppliers': suppliers,
    }
    return render(request, 'product_form.html', context)

@login_required
def edit_product(request, pk):
    product = get_object_or_404(Product, id=pk)
    
    if request.method == 'POST':
        try:
            # Update basic fields
            product.name = request.POST.get('name')
            product.description = request.POST.get('description', '')
            
            # Handle category
            category_id = request.POST.get('category')
            new_category = request.POST.get('new_category')
            
            if new_category:
                category, created = Category.objects.get_or_create(name=new_category)
                product.category = category
            elif category_id:
                try:
                    product.category = Category.objects.get(id=category_id)
                except Category.DoesNotExist:
                    product.category = None
            else:
                product.category = None
            
            # Handle supplier
            supplier_id = request.POST.get('supplier')
            new_supplier = request.POST.get('new_supplier')
            
            if new_supplier:
                supplier, created = Supplier.objects.get_or_create(name=new_supplier)
                product.supplier = supplier
            elif supplier_id:
                try:
                    product.supplier = Supplier.objects.get(id=supplier_id)
                except Supplier.DoesNotExist:
                    product.supplier = None
            else:
                product.supplier = None
            
            # Update numeric fields
            product.price = Decimal(request.POST.get('price'))
            product.cost_price = Decimal(request.POST.get('cost_price', 0))
            product.quantity = int(request.POST.get('quantity', 0))
            product.reorder_level = int(request.POST.get('reorder_level', 10))
            
            # Handle image upload
            if 'image' in request.FILES:
                # Delete old image if exists
                if product.image:
                    product.image.delete(save=False)
                product.image = request.FILES['image']
            
            # Clear image if requested
            if request.POST.get('clear_image') == '1':
                if product.image:
                    product.image.delete(save=False)
                product.image = None
            
            product.save()
            
            messages.success(request, f'Product "{product.name}" updated successfully!')
            
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': True})
            else:
                return redirect('product_list')
            
        except Exception as e:
            error_msg = f'Error updating product: {str(e)}'
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': False, 'error': error_msg})
            else:
                messages.error(request, error_msg)
                return redirect('edit_product', pk=pk)
    
    # Regular request - render template
    categories = Category.objects.all()
    suppliers = Supplier.objects.all()
    
    context = {
        'product': product,
        'categories': categories,
        'suppliers': suppliers,
        'action': 'Edit',
    }
    return render(request, 'product_form.html', context)

@login_required
def delete_product(request, pk):
    product = get_object_or_404(Product, id=pk)
    
    if request.method == 'POST':
        product_name = product.name
        # Delete image file if exists
        if product.image:
            product.image.delete(save=False)
        product.delete()
        messages.success(request, f'Product "{product_name}" deleted successfully!')
        return redirect('product_list')
    
    return render(request, 'product_confirm_delete.html', {'product': product})

# =================== DEBTORS VIEWS ===================
@login_required
def debtors_list(request):
    debtors = Sale.objects.filter(balance__gt=0).select_related('staff').prefetch_related('payments').order_by('-created_at')
    
    search_query = request.GET.get('search', '')
    if search_query:
        debtors = debtors.filter(
            Q(invoice_number__icontains=search_query) |
            Q(customer_name__icontains=search_query) |
            Q(customer_phone__icontains=search_query) |
            Q(staff__username__icontains=search_query)
        )[:50]
    
    context = {
        'debtors': debtors,
        'search_query': search_query,
    }
    return render(request, 'debtors_list.html', context)

@login_required
def record_payment(request, sale_id):
    sale = get_object_or_404(Sale, id=sale_id)
    
    if request.method == 'POST':
        try:
            amount = Decimal(request.POST.get('amount', 0))
            payment_method = request.POST.get('payment_method', 'cash')
            reference = request.POST.get('reference', '')
            notes = request.POST.get('notes', '')
            
            if amount <= 0:
                messages.error(request, 'Amount must be greater than 0')
                return redirect('record_payment', sale_id=sale_id)
            
            if amount > sale.balance:
                messages.error(request, f'Amount cannot exceed balance of ₦{sale.balance:,.2f}')
                return redirect('record_payment', sale_id=sale_id)
            
            # Create payment
            Payment.objects.create(
                sale=sale,
                amount=amount,
                payment_method=payment_method,
                reference=reference,
                notes=notes,
                created_by=request.user
            )
            
            # Update sale
            sale.amount_paid += amount
            sale.balance = sale.total - sale.amount_paid
            
            if sale.balance <= 0:
                sale.payment_status = 'paid'
            else:
                sale.payment_status = 'partial'
            
            sale.save()
            
            messages.success(request, f'Payment of ₦{amount:,.2f} recorded successfully!')
            return redirect('debtors_list')
            
        except Exception as e:
            messages.error(request, f'Error recording payment: {str(e)}')
    
    context = {
        'sale': sale,
    }
    return render(request, 'record_payment.html', context)

# =================== CART VIEWS ===================
@login_required
@csrf_exempt
def save_pending_cart(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            
            # Validate cart data
            if not data.get('items'):
                return JsonResponse({'success': False, 'error': 'Cart is empty'})
            
            # Calculate totals
            subtotal = Decimal('0')
            for item in data['items']:
                item_price = Decimal(str(item.get('price', 0)))
                item_quantity = Decimal(str(item.get('quantity', 1)))
                item_discount = Decimal(str(item.get('discount', 0)))
                subtotal += (item_price * item_quantity) - item_discount
            
            cart_data = {
                'items': data['items'],
                'customer_name': data.get('customer_name', ''),
                'customer_phone': data.get('customer_phone', ''),
                'payment_type': data.get('payment_type', 'full'),
                'payment_method': data.get('payment_method', 'cash'),
                'amount_paid': float(data.get('amount_paid', 0)),
                'subtotal': float(subtotal),
                'total': float(subtotal),
                'timestamp': timezone.now().isoformat()
            }
            
            # Delete existing pending cart
            PendingCart.objects.filter(staff=request.user).delete()
            
            # Create new pending cart
            PendingCart.objects.create(
                staff=request.user,
                cart_data=cart_data
            )
            
            return JsonResponse({'success': True})
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

@login_required
def load_pending_cart(request):
    try:
        pending_cart = PendingCart.objects.filter(staff=request.user).first()
        
        if pending_cart:
            return JsonResponse({
                'success': True,
                'cart_data': pending_cart.cart_data
            })
        else:
            return JsonResponse({
                'success': True,
                'cart_data': None
            })
            
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
@csrf_exempt
def delete_pending_cart(request):
    if request.method == 'POST':
        try:
            PendingCart.objects.filter(staff=request.user).delete()
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

@login_required
def saved_carts_list(request):
    saved_carts = SavedCart.objects.filter(staff=request.user).order_by('-created_at')
    
    context = {
        'saved_carts': saved_carts,
    }
    return render(request, 'saved_carts_list.html', context)

@login_required
@csrf_exempt
def save_cart(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            cart_name = data.get('cart_name', f'Cart {timezone.now().strftime("%Y-%m-%d %H:%M")}')
            
            # Validate cart data
            cart_data = data.get('cart_data', {})
            if not cart_data.get('items'):
                return JsonResponse({'success': False, 'error': 'Cart is empty'})
            
            # Calculate totals if not provided
            if 'subtotal' not in cart_data:
                subtotal = Decimal('0')
                for item in cart_data['items']:
                    item_price = Decimal(str(item.get('price', 0)))
                    item_quantity = Decimal(str(item.get('quantity', 1)))
                    item_discount = Decimal(str(item.get('discount', 0)))
                    subtotal += (item_price * item_quantity) - item_discount
                cart_data['subtotal'] = float(subtotal)
                cart_data['total'] = float(subtotal)
            
            # Save cart
            saved_cart = SavedCart.objects.create(
                staff=request.user,
                cart_name=cart_name,
                cart_data=cart_data
            )
            
            return JsonResponse({
                'success': True,
                'cart_id': saved_cart.id,
                'cart_name': saved_cart.cart_name
            })
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

@login_required
def load_saved_cart(request, cart_id):
    try:
        saved_cart = SavedCart.objects.get(id=cart_id, staff=request.user)
        
        return JsonResponse({
            'success': True,
            'cart_data': saved_cart.cart_data,
            'cart_name': saved_cart.cart_name
        })
        
    except SavedCart.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Cart not found'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
@csrf_exempt
def delete_saved_cart(request, cart_id):
    if request.method == 'POST':
        try:
            saved_cart = SavedCart.objects.get(id=cart_id, staff=request.user)
            saved_cart.delete()
            
            return JsonResponse({'success': True})
        except SavedCart.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Cart not found'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

@login_required
def view_saved_cart(request, cart_id):
    saved_cart = get_object_or_404(SavedCart, id=cart_id, staff=request.user)
    
    # Calculate totals for display
    cart_data = saved_cart.cart_data
    items_count = len(cart_data.get('items', []))
    total_amount = Decimal('0')
    for item in cart_data.get('items', []):
        item_price = Decimal(str(item.get('price', 0)))
        item_quantity = Decimal(str(item.get('quantity', 1)))
        item_discount = Decimal(str(item.get('discount', 0)))
        total_amount += (item_price * item_quantity) - item_discount
    
    context = {
        'saved_cart': saved_cart,
        'cart_data': cart_data,
        'items_count': items_count,
        'total_amount': total_amount,
    }
    return render(request, 'saved_cart_detail.html', context)

# =================== SALES HISTORY VIEWS ===================
@login_required
def sale_history(request):
    """Display all sales with pagination and real-time search"""
    search_query = request.GET.get('search', '')
    page_number = request.GET.get('page', 1)
    
    # Start with base queryset
    sales = Sale.objects.all().select_related('staff').order_by('-created_at')
    
    # Apply search filter if provided
    if search_query:
        sales = sales.filter(
            Q(invoice_number__icontains=search_query) |
            Q(customer_name__icontains=search_query) |
            Q(customer_phone__icontains=search_query) |
            Q(staff__username__icontains=search_query)
        )
    
    # Pagination - 50 per page
    paginator = Paginator(sales, 50)
    
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)
    
    # Calculate totals for the current page
    page_total = sum(float(sale.total) for sale in page_obj.object_list)
    
    context = {
        'page_obj': page_obj,
        'sales': page_obj.object_list,
        'search_query': search_query,
        'page_total': page_total,
        'total_sales_count': sales.count(),
    }
    
    return render(request, 'sale_history.html', context)

# =================== STAFF MANAGEMENT VIEWS ===================
@login_required
def register_staff(request):
    if not request.user.role == 'admin' and not request.user.is_superuser:
        messages.error(request, 'Only admins can register staff')
        return redirect('home')
    
    if request.method == 'POST':
        try:
            username = request.POST.get('username')
            email = request.POST.get('email')
            first_name = request.POST.get('first_name')
            last_name = request.POST.get('last_name')
            password = request.POST.get('password')
            role = request.POST.get('role', 'staff')
            phone = request.POST.get('phone', '')
            
            # Validate required fields
            if not username or not email or not password:
                messages.error(request, 'Username, email and password are required')
                return redirect('register_staff')
            
            # Check if username already exists
            if User.objects.filter(username=username).exists():
                messages.error(request, 'Username already exists')
                return redirect('register_staff')
            
            # Create user
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                first_name=first_name,
                last_name=last_name,
                role=role,
                phone=phone,
                is_staff=True
            )
            
            messages.success(request, f'Staff member "{username}" created successfully!')
            return redirect('staff_list')
            
        except Exception as e:
            messages.error(request, f'Error creating staff: {str(e)}')
    
    return render(request, 'register_staff.html')

@login_required
def staff_list(request):
    if not request.user.role == 'admin' and not request.user.is_superuser:
        messages.error(request, 'Only admins can view staff list')
        return redirect('home')
    
    staff = User.objects.filter(is_staff=True).exclude(id=request.user.id).order_by('-date_joined')
    
    search_query = request.GET.get('search', '')
    if search_query:
        staff = staff.filter(
            Q(username__icontains=search_query) |
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(email__icontains=search_query) |
            Q(phone__icontains=search_query) |
            Q(role__icontains=search_query)
        )[:50]
    
    context = {
        'staff': staff,
        'search_query': search_query,
    }
    return render(request, 'staff_list.html', context)

@login_required
@csrf_exempt
def edit_staff(request):
    """Handle AJAX request to edit staff member"""
    if not request.user.role == 'admin' and not request.user.is_superuser:
        return JsonResponse({'success': False, 'error': 'Only admins can edit staff'})
    
    if request.method == 'POST':
        try:
            user_id = request.POST.get('user_id')
            user = User.objects.get(id=user_id)
            
            # Update user fields
            user.username = request.POST.get('username')
            user.email = request.POST.get('email')
            user.first_name = request.POST.get('first_name', '')
            user.last_name = request.POST.get('last_name', '')
            user.phone = request.POST.get('phone', '')
            user.role = request.POST.get('role', 'staff')
            user.is_active = request.POST.get('is_active') == 'true'
            
            # Save password only if provided
            password = request.POST.get('password')
            if password:
                user.set_password(password)
            
            user.save()
            
            return JsonResponse({'success': True})
            
        except User.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'User not found'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

# =================== REFUND VIEWS ===================
@login_required
def refund_requests_list(request):
    if request.user.role == 'admin' or request.user.is_superuser:
        refunds = RefundRequest.objects.all().select_related('created_by', 'approved_by').order_by('-request_date')
    else:
        refunds = RefundRequest.objects.filter(created_by=request.user).order_by('-request_date')
    
    context = {
        'refunds': refunds,
    }
    return render(request, 'refund_requests_list.html', context)

@login_required
def create_refund_request(request):
    if request.method == 'POST':
        try:
            customer_name = request.POST.get('customer_name')
            customer_phone = request.POST.get('customer_phone')
            reason = request.POST.get('reason')
            amount = request.POST.get('amount')
            
            refund = RefundRequest.objects.create(
                customer_name=customer_name,
                customer_phone=customer_phone,
                reason=reason,
                amount=amount,
                created_by=request.user
            )
            
            messages.success(request, 'Refund request created successfully!')
            return redirect('refund_requests_list')
            
        except Exception as e:
            messages.error(request, f'Error creating refund request: {str(e)}')
    
    return render(request, 'refund_request_form.html')

@login_required
@csrf_exempt
def edit_refund_request(request, pk):
    refund = get_object_or_404(RefundRequest, id=pk)
    
    # Check if user can edit
    if not refund.can_edit() or (refund.created_by != request.user and request.user.role != 'admin'):
        return JsonResponse({'success': False, 'error': 'You cannot edit this refund request'})
    
    if request.method == 'POST':
        try:
            refund.customer_name = request.POST.get('customer_name')
            refund.customer_phone = request.POST.get('customer_phone')
            refund.reason = request.POST.get('reason')
            refund.amount = Decimal(request.POST.get('amount'))
            refund.save()
            
            return JsonResponse({'success': True})
            
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request method'})

@login_required
@csrf_exempt
def approve_refund_request(request, pk):
    if request.method == 'POST':
        try:
            refund = RefundRequest.objects.get(id=pk)
            
            if not refund.can_approve_decline(request.user):
                messages.error(request, 'You are not authorized to approve refunds')
                return redirect('refund_requests_list')
            
            refund.status = 'approved'
            refund.approved_by = request.user
            refund.approved_date = timezone.now()
            refund.save()
            
            messages.success(request, 'Refund request approved successfully!')
            
        except RefundRequest.DoesNotExist:
            messages.error(request, 'Refund request not found')
        except Exception as e:
            messages.error(request, f'Error approving refund: {str(e)}')
    
    return redirect('refund_requests_list')

@login_required
@csrf_exempt
def decline_refund_request(request, pk):
    if request.method == 'POST':
        try:
            refund = RefundRequest.objects.get(id=pk)
            
            if not refund.can_approve_decline(request.user):
                messages.error(request, 'You are not authorized to decline refunds')
                return redirect('refund_requests_list')
            
            refund.status = 'declined'
            refund.approved_by = request.user
            refund.approved_date = timezone.now()
            refund.save()
            
            messages.success(request, 'Refund request declined')
            
        except RefundRequest.DoesNotExist:
            messages.error(request, 'Refund request not found')
        except Exception as e:
            messages.error(request, f'Error declining refund: {str(e)}')
    
    return redirect('refund_requests_list')

# =================== REAL-TIME SEARCH API VIEWS ===================
@login_required
def search_sales_api(request):
    """API endpoint for real-time sales search in dashboard"""
    search_term = request.GET.get('q', '')
    date_filter = request.GET.get('date_filter', 'today')
    
    # Calculate date range
    today = timezone.now().date()
    
    if date_filter == 'today':
        start_date = today
        end_date = today + timedelta(days=1)
    elif date_filter == 'week':
        start_date = today - timedelta(days=today.weekday())
        end_date = start_date + timedelta(days=7)
    elif date_filter == 'month':
        start_date = today.replace(day=1)
        if start_date.month == 12:
            end_date = start_date.replace(year=start_date.year + 1, month=1, day=1)
        else:
            end_date = start_date.replace(month=start_date.month + 1, day=1)
    elif date_filter == 'year':
        start_date = today.replace(month=1, day=1)
        end_date = today.replace(year=today.year + 1, month=1, day=1)
    else:
        custom_start = request.GET.get('custom_start')
        custom_end = request.GET.get('custom_end')
        if custom_start and custom_end:
            try:
                start_date = datetime.strptime(custom_start, '%Y-%m-%d').date()
                end_date = datetime.strptime(custom_end, '%Y-%m-%d').date() + timedelta(days=1)
            except ValueError:
                start_date = today
                end_date = today + timedelta(days=1)
        else:
            start_date = today
            end_date = today + timedelta(days=1)
    
    # Filter sales
    sales = Sale.objects.filter(
        created_at__range=[start_date, end_date]
    ).select_related('staff').order_by('-created_at')
    
    if search_term:
        sales = sales.filter(
            Q(invoice_number__icontains=search_term) |
            Q(customer_name__icontains=search_term) |
            Q(staff__username__icontains=search_term) |
            Q(customer_phone__icontains=search_term)
        )[:50]
    
    # Serialize results
    results = []
    for sale in sales:
        results.append({
            'id': sale.id,
            'invoice_number': sale.invoice_number,
            'customer_name': sale.customer_name or 'Walk-in',
            'staff_name': sale.staff.username,
            'total': float(sale.total),
            'payment_status': sale.payment_status,
            'created_at': sale.created_at.isoformat(),
        })
    
    return JsonResponse({
        'success': True,
        'results': results,
        'count': len(results),
    })

@login_required
def search_stock_api(request):
    """API endpoint for real-time low stock search"""
    search_term = request.GET.get('q', '')
    
    products = Product.objects.filter(quantity__lte=F('reorder_level')).select_related('category').order_by('quantity')
    
    if search_term:
        products = products.filter(
            Q(name__icontains=search_term) |
            Q(sku__icontains=search_term) |
            Q(category__name__icontains=search_term)
        )[:50]
    
    # Serialize results
    results = []
    for product in products:
        results.append({
            'id': product.id,
            'name': product.name,
            'sku': product.sku,
            'quantity': product.quantity,
            'reorder_level': product.reorder_level,
            'category': product.category.name if product.category else 'N/A',
        })
    
    return JsonResponse({
        'success': True,
        'results': results,
        'count': len(results),
    })

@login_required
def search_products_api(request):
    """API endpoint for real-time products search"""
    search_term = request.GET.get('q', '')
    
    products = Product.objects.all().select_related('category', 'supplier').order_by('name')
    
    if search_term:
        products = products.filter(
            Q(name__icontains=search_term) |
            Q(sku__icontains=search_term) |
            Q(category__name__icontains=search_term) |
            Q(supplier__name__icontains=search_term)
        )[:50]
    
    # Serialize results
    results = []
    for product in products:
        results.append({
            'id': product.id,
            'name': product.name,
            'sku': product.sku,
            'category': product.category.name if product.category else 'N/A',
            'price': float(product.price),
            'quantity': product.quantity,
            'is_low_stock': product.quantity <= product.reorder_level,
        })
    
    return JsonResponse({
        'success': True,
        'results': results,
        'count': len(results),
    })

@login_required
def search_staff_api(request):
    """API endpoint for real-time staff search"""
    search_term = request.GET.get('q', '')
    
    staff = User.objects.filter(is_staff=True).order_by('username')
    
    if search_term:
        staff = staff.filter(
            Q(username__icontains=search_term) |
            Q(first_name__icontains=search_term) |
            Q(last_name__icontains=search_term) |
            Q(email__icontains=search_term) |
            Q(phone__icontains=search_term)
        )[:50]
    
    # Serialize results
    results = []
    for user in staff:
        results.append({
            'id': user.id,
            'username': user.username,
            'first_name': user.first_name or '',
            'last_name': user.last_name or '',
            'email': user.email,
            'phone': user.phone or '',
            'role': user.role,
            'is_active': user.is_active,
            'date_joined': user.date_joined.isoformat() if user.date_joined else None,
        })
    
    return JsonResponse({
        'success': True,
        'results': results,
        'count': len(results),
    })

@login_required
def search_debtors_api(request):
    """API endpoint for real-time debtors search"""
    search_term = request.GET.get('q', '')
    
    debtors = Sale.objects.filter(balance__gt=0).select_related('staff').order_by('-created_at')
    
    if search_term:
        debtors = debtors.filter(
            Q(invoice_number__icontains=search_term) |
            Q(customer_name__icontains=search_term) |
            Q(customer_phone__icontains=search_term) |
            Q(staff__username__icontains=search_term)
        )[:50]
    
    # Serialize results
    results = []
    for sale in debtors:
        # Get payments for this sale
        payments = sale.payments.all()
        payment_history = []
        for payment in payments:
            payment_history.append({
                'amount': float(payment.amount),
                'method': payment.payment_method,
                'reference': payment.reference,
                'date': payment.created_at.isoformat(),
            })
        
        results.append({
            'id': sale.id,
            'invoice_number': sale.invoice_number,
            'customer_name': sale.customer_name or 'Walk-in',
            'customer_phone': sale.customer_phone or '',
            'total': float(sale.total),
            'amount_paid': float(sale.amount_paid),
            'balance': float(sale.balance),
            'created_at': sale.created_at.isoformat(),
            'staff_name': sale.staff.username,
            'payments': payment_history,
        })
    
    return JsonResponse({
        'success': True,
        'results': results,
        'count': len(results),
    })

@login_required
def sales_history_api(request):
    """API endpoint for real-time sales history search"""
    search_term = request.GET.get('q', '')
    
    # Get date range from request (if needed)
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')
    
    # Filter sales
    sales = Sale.objects.all().select_related('staff').order_by('-created_at')
    
    # Apply date filter if provided
    if date_from and date_to:
        try:
            start_date = datetime.strptime(date_from, '%Y-%m-%d').date()
            end_date = datetime.strptime(date_to, '%Y-%m-%d').date() + timedelta(days=1)
            sales = sales.filter(created_at__range=[start_date, end_date])
        except ValueError:
            pass
    
    # Apply search filter
    if search_term:
        sales = sales.filter(
            Q(invoice_number__icontains=search_term) |
            Q(customer_name__icontains=search_term) |
            Q(customer_phone__icontains=search_term) |
            Q(staff__username__icontains=search_term)
        )[:100]  # Limit to 100 for API response
    
    # Serialize results
    results = []
    for sale in sales:
        results.append({
            'id': sale.id,
            'invoice_number': sale.invoice_number,
            'customer_name': sale.customer_name or 'Walk-in',
            'customer_phone': sale.customer_phone or '',
            'staff_name': sale.staff.username,
            'staff_full_name': f"{sale.staff.first_name or ''} {sale.staff.last_name or ''}".strip(),
            'subtotal': float(sale.subtotal),
            'discount': float(sale.discount),
            'total': float(sale.total),
            'amount_paid': float(sale.amount_paid),
            'balance': float(sale.balance),
            'payment_status': sale.payment_status,
            'created_at': sale.created_at.isoformat(),
            'formatted_date': sale.created_at.strftime('%b %d, %Y %I:%M %p'),
        })
    
    return JsonResponse({
        'success': True,
        'results': results,
        'count': len(results),
    })