from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import (User, Product, Category, Supplier, Payment)
from .models import RefundRequest, User, Product

class StaffRegistrationForm(UserCreationForm):
    email = forms.EmailField(required=True)
    phone = forms.CharField(max_length=15, required=False)
    role = forms.ChoiceField(choices=User.ROLE_CHOICES)
    
    class Meta:
        model = User
        fields = ['username', 'email', 'first_name', 'last_name', 'phone', 'role', 'password1', 'password2']

class ProductForm(forms.ModelForm):
    new_category = forms.CharField(max_length=200, required=False, 
                                    widget=forms.TextInput(attrs={'placeholder': 'Or create new category'}))
    new_supplier = forms.CharField(max_length=200, required=False,
                                   widget=forms.TextInput(attrs={'placeholder': 'Or create new supplier'}))
    class Meta:
        model = Product
        fields = ['name', 'category', 'supplier', 'description', 'price', 
                  'cost_price', 'quantity', 'reorder_level', 'image']
        widgets = {
            'category': forms.Select(attrs={'class': 'form-control'}),
            'supplier': forms.Select(attrs={'class': 'form-control'}),
        }
    def clean(self):
        cleaned_data = super().clean()
        
        # Handle new category
        if cleaned_data.get('new_category'):
            category, created = Category.objects.get_or_create(
                name=cleaned_data['new_category']
            )
            cleaned_data['category'] = category
        
        # Handle new supplier
        if cleaned_data.get('new_supplier'):
            supplier, created = Supplier.objects.get_or_create(
                name=cleaned_data['new_supplier']
            )
            cleaned_data['supplier'] = supplier
        
        return cleaned_data
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add HTML5 validation attributes (to prevent nagative value entry at the client side)
        self.fields['price'].widget.attrs.update({'min': '0', 'step': '0.01'})
        self.fields['cost_price'].widget.attrs.update({'min': '0', 'step': '0.01'})
        self.fields['quantity'].widget.attrs.update({'min': '0'})
        self.fields['reorder_level'].widget.attrs.update({'min': '0'})
        self.fields['category'].required = False
        self.fields['supplier'].required = False

class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ['name', 'description']

class SupplierForm(forms.ModelForm):
    class Meta:
        model = Supplier
        fields = ['name', 'contact_person', 'email', 'phone', 'address']

class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['amount', 'payment_method', 'reference', 'notes']

class RefundRequestForm(forms.ModelForm):
    class Meta:
        model = RefundRequest
        fields = ['customer_name', 'customer_phone', 'reason', 'amount']
        widgets = {
            'customer_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter customer name'}),
            'customer_phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter customer phone'}),
            'reason': forms.Textarea(attrs={'class': 'form-control', 'placeholder': 'Enter reason for refund', 'rows': 4}),
            'amount': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Enter refund amount', 'step': '0.01', 'min': '0'}),
        }
    
    def clean_amount(self):
        amount = self.cleaned_data.get('amount')
        if amount <= 0:
            raise forms.ValidationError('Amount must be greater than zero.')
        return amount