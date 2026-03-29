from django import forms
from .models import Device, EmployeeDevice


class DeviceForm(forms.ModelForm):
    class Meta:
        model = Device
        fields = ['name', 'ip_address', 'port', 'password', 'location', 'is_active', 'connection_timeout']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter device name'}),
            'ip_address': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '192.168.1.100'}),
            'port': forms.NumberInput(attrs={'class': 'form-control', 'value': '4370'}),
            'password': forms.NumberInput(attrs={'class': 'form-control', 'value': '0'}),
            'location': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Office Entrance'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'connection_timeout': forms.NumberInput(attrs={'class': 'form-control', 'value': '5', 'min': '1', 'max': '60'}),
        }


class EmployeeDeviceForm(forms.ModelForm):
    class Meta:
        model = EmployeeDevice
        fields = ['device', 'device_uid']
        widgets = {
            'device': forms.Select(attrs={'class': 'form-select'}),
            'device_uid': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'User ID on device'}),
        }
