from django import forms
from .models import LeaveType


class LeaveTypeForm(forms.ModelForm):
    """Form for Leave Type model"""

    class Meta:
        model = LeaveType
        fields = ['name', 'code', 'description', 'days_per_year', 'is_paid', 'requires_approval', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., Annual Leave, Sick Leave, Casual Leave'
            }),
            'code': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., AL, SL, CL'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Describe this leave type'
            }),
            'days_per_year': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0',
                'value': '0'
            }),
            'is_paid': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
            'requires_approval': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'form-check-input'
            })
        }
        labels = {
            'name': 'Leave Type Name',
            'code': 'Code',
            'description': 'Description',
            'days_per_year': 'Days Per Year',
            'is_paid': 'Is Paid Leave',
            'requires_approval': 'Requires Approval',
            'is_active': 'Active'
        }
        help_texts = {
            'code': 'Short code for this leave type (e.g., AL for Annual Leave)',
            'days_per_year': 'Number of days allowed per year for this leave type',
            'is_paid': 'Whether this leave type is paid or unpaid',
            'requires_approval': 'Whether requests for this leave type require approval',
            'is_active': 'Uncheck to disable this leave type'
        }
