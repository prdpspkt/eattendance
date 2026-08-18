"""Forms for administrator corrections and overtime sign-off."""
from django import forms
from django.utils import timezone

from .models import Attendance, OvertimeRecord


class ManualAttendanceForm(forms.ModelForm):
    """Record a punch that the terminal never captured.

    Stored as a punch, not as an edit to the daily summary: the summary is
    rebuilt from punches whenever a device pushes, so an edited summary would
    not survive the next scan. See ``Attendance``.
    """

    class Meta:
        model = Attendance
        fields = ['employee', 'timestamp', 'punch_type', 'reason']
        widgets = {
            'employee': forms.Select(attrs={'class': 'form-select'}),
            'timestamp': forms.DateTimeInput(
                attrs={'class': 'form-control', 'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M',
            ),
            'punch_type': forms.Select(attrs={'class': 'form-select'}),
            'reason': forms.Textarea(attrs={
                'class': 'form-control', 'rows': 3,
                'placeholder': 'Forgot to scan on arrival; confirmed with the supervisor.',
            }),
        }
        labels = {'timestamp': 'Date and time', 'punch_type': 'Punch'}
        help_texts = {
            'timestamp': 'The moment the person actually arrived or left, in local time.',
            'reason': 'Recorded against the entry permanently. Required.',
        }

    def __init__(self, *args, recorded_by=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.recorded_by = recorded_by
        self.fields['timestamp'].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M']
        self.fields['reason'].required = True
        self.fields['employee'].queryset = self.fields['employee'].queryset.select_related('user')

    def clean_reason(self):
        reason = (self.cleaned_data.get('reason') or '').strip()
        if not reason:
            # Also guarded by a database constraint; this is the message a
            # person should see rather than an IntegrityError.
            raise forms.ValidationError(
                'Give a reason. A manual entry changes someone\'s attendance record, '
                'and the record should say why.'
            )
        return reason

    def clean_timestamp(self):
        timestamp = self.cleaned_data.get('timestamp')
        if timestamp and timestamp > timezone.now():
            raise forms.ValidationError('That is in the future. Attendance is recorded after the fact.')
        return timestamp

    def clean(self):
        cleaned = super().clean()
        employee, timestamp = cleaned.get('employee'), cleaned.get('timestamp')
        if employee and timestamp:
            clash = Attendance.objects.filter(
                employee=employee, timestamp=timestamp,
            ).exclude(pk=self.instance.pk).first()
            if clash:
                raise forms.ValidationError(
                    f'{employee.user.get_full_name()} already has a punch at exactly this '
                    f'time ({clash.get_punch_type_display()}, from {clash.source_label}).'
                )
        return cleaned

    def save(self, commit=True):
        punch = super().save(commit=False)
        punch.source = Attendance.SOURCE_MANUAL
        punch.device = None
        punch.uid = None
        if self.recorded_by is not None:
            punch.recorded_by = self.recorded_by
        if commit:
            punch.save()
            # Roll the day up immediately, so the correction is visible on the
            # dashboard rather than waiting for the next device push.
            Attendance.process_daily_attendance(
                punch.employee, timezone.localtime(punch.timestamp).date()
            )
        return punch


class OvertimeRecordForm(forms.ModelForm):
    """Annotate computed overtime with the job performed, and price it."""

    class Meta:
        model = OvertimeRecord
        fields = ['start_at', 'end_at', 'job_performed', 'hourly_rate', 'notes']
        widgets = {
            'start_at': forms.DateTimeInput(
                attrs={'class': 'form-control', 'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M',
            ),
            'end_at': forms.DateTimeInput(
                attrs={'class': 'form-control', 'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M',
            ),
            'job_performed': forms.Textarea(attrs={
                'class': 'form-control', 'rows': 3,
                'placeholder': 'Stock count for the quarterly audit; loading bay.',
            }),
            'hourly_rate': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'notes': forms.Textarea(attrs={'class': 'form-control', 'rows': 2}),
        }
        labels = {
            'start_at': 'Overtime started',
            'end_at': 'Overtime ended',
            'job_performed': 'Job performed',
            'hourly_rate': 'Rate per hour',
        }
        help_texts = {
            'job_performed': 'Printed on the overtime report next to the hours.',
            'hourly_rate': "Defaults to the employee's rate when approved. Kept with the record afterwards.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ('start_at', 'end_at'):
            self.fields[name].input_formats = ['%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M']
        if self.instance.pk and self.instance.hourly_rate is None:
            self.fields['hourly_rate'].initial = self.instance.employee.overtime_hourly_rate

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('start_at'), cleaned.get('end_at')
        if start and end and end <= start:
            self.add_error('end_at', 'Overtime cannot end before it started.')
        return cleaned
