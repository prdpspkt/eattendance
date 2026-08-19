from django import forms

from .models import LeaveType


class LeaveTypeForm(forms.ModelForm):
    """The rule sheet for one leave type.

    This is where a change in government policy lands - the ceiling on home
    leave, the number of days for death rituals - so every limit on the model
    is editable here rather than hard-coded anywhere.
    """

    class Meta:
        model = LeaveType
        fields = [
            'name', 'code', 'description',
            'accrual', 'days_per_year', 'carry_forward', 'max_accumulation_days',
            'days_per_occurrence', 'max_occurrences_lifetime',
            'is_paid', 'requires_approval', 'is_active',
        ]
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., Home Leave, Sick Leave, Casual Leave'
            }),
            'code': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., HL, SL, CL'
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Describe this leave type'
            }),
            'accrual': forms.Select(attrs={
                'class': 'form-select',
                'data-leave-policy': 'accrual',
            }),
            'days_per_year': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0',
            }),
            'carry_forward': forms.Select(attrs={
                'class': 'form-select',
                'data-leave-policy': 'carry-forward',
            }),
            'max_accumulation_days': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0',
                'placeholder': 'e.g. 180',
            }),
            'days_per_occurrence': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0',
                'placeholder': 'e.g. 15',
            }),
            'max_occurrences_lifetime': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': '0',
                'placeholder': 'e.g. 2 - leave empty for no limit',
            }),
            'is_paid': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'requires_approval': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'name': 'Leave Type Name',
            'code': 'Code',
            'description': 'Description',
            'accrual': 'How it is earned',
            'days_per_year': 'Days credited per year',
            'carry_forward': 'At the end of the year',
            'max_accumulation_days': 'Accumulation ceiling (days)',
            'days_per_occurrence': 'Days granted each time',
            'max_occurrences_lifetime': 'Times allowed in a career',
            'is_paid': 'Is Paid Leave',
            'requires_approval': 'Requires Approval',
            'is_active': 'Active',
        }
        help_texts = {
            'code': 'Short code for this leave type (e.g., HL for Home Leave)',
            'days_per_year': 'Credited on the first day of each leave year. Leave at 0 for occasional leave.',
            'max_accumulation_days': 'The most that may stand to an employee\'s credit, '
                                     'e.g. 180 for home leave. Only used when the balance is capped.',
            'days_per_occurrence': 'Only used by occasional leave, e.g. 15 days per death in the family.',
            'max_occurrences_lifetime': 'Only used by occasional leave, e.g. maternity twice in a career. '
                                        'Empty means there is no limit.',
            'is_paid': 'Whether this leave type is paid or unpaid',
            'requires_approval': 'Whether requests for this leave type require approval',
            'is_active': 'Uncheck to disable this leave type',
        }

    def clean(self):
        cleaned = super().clean()
        # Model.clean() holds the rules; the form only has to make sure the
        # unused half of the sheet does not carry stale numbers into them.
        if cleaned.get('accrual') == LeaveType.Accrual.OCCASIONAL:
            cleaned['carry_forward'] = LeaveType.CarryForward.NONE
            cleaned['max_accumulation_days'] = None
            cleaned['days_per_year'] = 0
        else:
            cleaned['days_per_occurrence'] = None
            cleaned['max_occurrences_lifetime'] = None
            if cleaned.get('carry_forward') != LeaveType.CarryForward.CAPPED:
                cleaned['max_accumulation_days'] = None
        return cleaned
