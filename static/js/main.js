let cart = [];
let searchTimeout;

document.getElementById('productSearch').addEventListener('keyup', function(e) {
    if (e.key === 'Enter' && this.value.trim()) {
        searchProducts(this.value.trim());
    } else {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            if (this.value.trim()) {
                searchProducts(this.value.trim());
            } else {
                document.getElementById('searchResults').style.display = 'none';
            }
        }, 300);
    }
});

async function searchProducts(query) {
    const response = await fetch(`/api/search-products/?q=${encodeURIComponent(query)}`);
    const products = await response.json();
    
    const resultsDiv = document.getElementById('searchResults');
    
    if (products.length === 0) {
        resultsDiv.innerHTML = '<div style="padding: 1rem; text-align: center;">No products found</div>';
        resultsDiv.style.display = 'block';
        return;
    }
    
    resultsDiv.innerHTML = products.map(p => `
        <div class="search-result-item" onclick='addToCart(${JSON.stringify(p)})'>
            <img src="${p.image || '/static/placeholder.png'}" alt="${p.name}">
            <div>
                <strong>${p.name}</strong><br>
                <small>SKU: ${p.sku} | Price: ₦${p.price} | Stock: ${p.quantity}</small>
            </div>
        </div>
    `).join('');
    
    resultsDiv.style.display = 'block';
}

function addToCart(product) {
    const existing = cart.find(item => item.product_id === product.id);
    
    if (existing) {
        if (existing.quantity < product.quantity) {
            existing.quantity++;
        } else {
            alert('Not enough stock available!');
            return;
        }
    } else {
        cart.push({
            product_id: product.id,
            name: product.name,
            sku: product.sku,
            price: parseFloat(product.price),
            quantity: 1,
            discount: 0,
            image: product.image,
            max_quantity: product.quantity
        });
    }
    
    document.getElementById('searchResults').style.display = 'none';
    document.getElementById('productSearch').value = '';
    updateCart();
}

function updateCart() {
    const tbody = document.getElementById('cartItems');
    
    if (cart.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="7" style="text-align: center; padding: 2rem;">
                    <p style="color: #999;">Cart is empty. Search for products to add.</p>
                </td>
            </tr>
        `;
        updateTotals();
        return;
    }
    
    tbody.innerHTML = cart.map((item, index) => {
        const total = (item.price * item.quantity) - item.discount;
        return `
            <tr>
                <td><img src="${item.image || '/static/placeholder.png'}" alt="${item.name}"></td>
                <td>
                    <strong>${item.name}</strong><br>
                    <small>${item.sku}</small>
                </td>
                <td>₦${item.price.toFixed(2)}</td>
                <td>
                    <div class="quantity-control">
                        <button onclick="updateQuantity(${index}, -1)">-</button>
                        <input type="number" value="${item.quantity}" min="1" max="${item.max_quantity}"
                               onchange="setQuantity(${index}, this.value)">
                        <button onclick="updateQuantity(${index}, 1)">+</button>
                    </div>
                </td>
                <td>
                    <input type="number" value="${item.discount}" min="0" step="0.01"
                           onchange="setDiscount(${index}, this.value)"
                           style="width: 80px; padding: 0.3rem;">
                </td>
                <td><strong>₦${total.toFixed(2)}</strong></td>
                <td>
                    <button onclick="removeFromCart(${index})" class="btn btn-danger" style="padding: 0.3rem 0.6rem;">
                        🗑️
                    </button>
                </td>
            </tr>
        `;
    }).join('');
    
    updateTotals();
}

function updateQuantity(index, change) {
    const item = cart[index];
    const newQty = item.quantity + change;
    
    if (newQty < 1) {
        removeFromCart(index);
        return;
    }
    
    if (newQty > item.max_quantity) {
        alert('Not enough stock available!');
        return;
    }
    
    item.quantity = newQty;
    updateCart();
}

function setQuantity(index, value) {
    const qty = parseInt(value);
    const item = cart[index];
    
    if (qty < 1 || qty > item.max_quantity) {
        alert('Invalid quantity!');
        updateCart();
        return;
    }
    
    item.quantity = qty;
    updateCart();
}

function setDiscount(index, value) {
    const discount = parseFloat(value) || 0;
    cart[index].discount = discount;
    updateCart();
}

function removeFromCart(index) {
    cart.splice(index, 1);
    updateCart();
}

function clearCart() {
    if (confirm('Clear all items from cart?')) {
        cart = [];
        updateCart();
    }
}

function updateTotals() {
    const subtotal = cart.reduce((sum, item) => sum + (item.price * item.quantity), 0);
    const totalDiscount = cart.reduce((sum, item) => sum + item.discount, 0);
    const grandTotal = subtotal - totalDiscount;
    
    document.getElementById('subtotal').textContent = `₦${subtotal.toFixed(2)}`;
    document.getElementById('totalDiscount').textContent = `₦${totalDiscount.toFixed(2)}`;
    document.getElementById('grandTotal').textContent = `₦${grandTotal.toFixed(2)}`;
}

async function processSale() {
    if (cart.length === 0) {
        alert('Cart is empty!');
        return;
    }
    
    const customerName = document.getElementById('customerName').value;
    const customerPhone = document.getElementById('customerPhone').value;
    const amountPaid = parseFloat(document.getElementById('amountPaid').value) || 0;
    
    const items = cart.map(item => ({
        product_id: item.product_id,
        quantity: item.quantity,
        price: item.price,
        discount: item.discount,
        total: (item.price * item.quantity) - item.discount
    }));
    
    try {
        const response = await fetch('/api/process-sale/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': getCookie('csrftoken')
            },
            body: JSON.stringify({
                items: items,
                customer_name: customerName,
                customer_phone: customerPhone,
                amount_paid: amountPaid
            })
        });
        
        const result = await response.json();
        
        if (result.success) {
            alert(`Sale completed! Invoice: ${result.invoice_number}`);
            window.open(`/receipt/${result.sale_id}/`, '_blank');
            cart = [];
            updateCart();
            document.getElementById('customerName').value = '';
            document.getElementById('customerPhone').value = '';
            document.getElementById('amountPaid').value = '0';
        } else {
            alert('Error: ' + result.error);
        }
    } catch (error) {
        alert('Error processing sale: ' + error);
    }
}

function getCookie(name) {
    let cookieValue = null;
    if (document.cookie && document.cookie !== '') {
        const cookies = document.cookie.split(';');
        for (let i = 0; i < cookies.length; i++) {
            const cookie = cookies[i].trim();
            if (cookie.substring(0, name.length + 1) === (name + '=')) {
                cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                break;
            }
        }
    }
    return cookieValue;
}

document.addEventListener('click', function(e) {
    if (!e.target.closest('.search-box')) {
        document.getElementById('searchResults').style.display = 'none';
    }
});
