from django import forms
from .models import TravelOrder


class TravelOrderForm(forms.ModelForm):
    """Form for creating travel orders"""

    class Meta:
        model = TravelOrder
        fields = ['travel_type', 'destination', 'purpose', 'start_date', 'end_date', 'estimated_cost', 'attachment']
        widgets = {
            'travel_type': forms.Select(attrs={
                'class': 'form-select',
            }),
            'destination': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., New York, London, Tokyo'
            }),
            'purpose': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Describe the purpose of this travel'
            }),
            'start_date': forms.DateTimeInput(attrs={
                'class': 'form-control',
                'type': 'datetime-local'
            }),
            'end_date': forms.DateTimeInput(attrs={
                'class': 'form-control',
                'type': 'datetime-local'
            }),
            'estimated_cost': forms.NumberInput(attrs={
                'class': 'form-control',
                'placeholder': '0.00',
                'step': '0.01'
            }),
            'attachment': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': '.pdf,.doc,.docx'
            })
        }
        labels = {
            'travel_type': 'Travel Type',
            'destination': 'Destination',
            'purpose': 'Purpose of Travel',
            'start_date': 'Start Date & Time',
            'end_date': 'End Date & Time',
            'estimated_cost': 'Estimated Cost (Optional)',
            'attachment': 'Supporting Document (Optional)'
        }

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')

        if start_date and end_date:
            if end_date <= start_date:
                raise forms.ValidationError('End date must be after start date.')

        return cleaned_data
