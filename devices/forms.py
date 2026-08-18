from django import forms

from .models import Device, EmployeeDevice


class DeviceForm(forms.ModelForm):
    """Register a device by the identity its transport actually uses.

    The old form asked for an IP address, port, password and timeout for every
    device, which are pull-SDK concepts: the server opens a TCP connection to
    the terminal. For a push (ADMS/WDMS) device none of them apply - the device
    connects to *us*, from whatever address NAT gives it, and identifies itself
    by serial number. Asking for an IP there is asking for a value that is
    unknowable in advance and useless once known.

    So the form asks which transport first, then asks only for what that
    transport needs.
    """

    MODE_PUSH = 'PUSH'
    MODE_PULL = 'PULL'
    MODE_CHOICES = [
        (MODE_PUSH, 'Push (ADMS/WDMS) - the device connects to this server'),
        (MODE_PULL, 'Pull (ZK SDK) - this server connects to the device'),
    ]

    mode = forms.ChoiceField(
        choices=MODE_CHOICES,
        initial=MODE_PUSH,
        widget=forms.RadioSelect,
        label='How does this device communicate?',
        help_text=(
            'Push is the right answer for terminals at remote sites or behind NAT, '
            'and needs no inbound firewall rules. Pull requires the server to reach '
            'the device on its IP address, port 4370.'
        ),
    )

    class Meta:
        model = Device
        fields = [
            'name', 'location', 'is_active',
            'serial_number',
            'ip_address', 'port', 'password', 'connection_timeout',
        ]
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Main Entrance'}),
            'location': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Head office, ground floor'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'serial_number': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'ZK8000123456'}),
            'ip_address': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '192.168.1.201'}),
            'port': forms.NumberInput(attrs={'class': 'form-control'}),
            'password': forms.NumberInput(attrs={'class': 'form-control'}),
            'connection_timeout': forms.NumberInput(attrs={'class': 'form-control', 'min': '1', 'max': '60'}),
        }
        help_texts = {
            'serial_number': (
                'Read it from the terminal under Menu &rarr; System Info &rarr; Device Info. '
                'This is the only identifier a device sends when it connects.'
            ),
            'is_active': 'Inactive devices are refused when they push data.',
        }
        labels = {
            'serial_number': 'Serial number (SN)',
            'ip_address': 'IP address',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Nothing below is required at the form level; which fields are needed
        # depends on the mode, and clean() decides that.
        for name in ('serial_number', 'ip_address', 'port', 'password', 'connection_timeout'):
            self.fields[name].required = False

        self.fields['port'].initial = 4370
        self.fields['password'].initial = 0
        self.fields['connection_timeout'].initial = 5

        # Editing an existing device: preselect the mode it is already using.
        instance = kwargs.get('instance') or getattr(self, 'instance', None)
        if instance is not None and instance.pk:
            self.fields['mode'].initial = (
                self.MODE_PUSH if instance.push_enabled or not instance.ip_address
                else self.MODE_PULL
            )

    def clean_serial_number(self):
        # Empty strings would collide on the unique index the moment a second
        # device is added without one; the column stores NULL for "unknown".
        return (self.cleaned_data.get('serial_number') or '').strip() or None

    def clean_ip_address(self):
        return (self.cleaned_data.get('ip_address') or '').strip() or None

    def clean(self):
        cleaned = super().clean()
        mode = cleaned.get('mode')

        if mode == self.MODE_PULL:
            if not cleaned.get('ip_address'):
                self.add_error(
                    'ip_address',
                    'Required for pull mode: the server needs an address to connect to.',
                )
            # Fall back to the ZK pull-SDK defaults rather than failing. These
            # are specific to that protocol, which is what pull mode speaks.
            cleaned.setdefault('port', 4370)
            cleaned['port'] = cleaned.get('port') or 4370
            cleaned['password'] = cleaned.get('password') if cleaned.get('password') is not None else 0
            cleaned['connection_timeout'] = cleaned.get('connection_timeout') or 5
        else:
            # Push mode. Never invent an IP address for it...
            cleaned['ip_address'] = None
            # ...and require the serial, because it is the only thing a device
            # is matched on when it connects. A push record without one can
            # never be matched to anything, which is why the database rejects
            # it too (Device.Meta constraint device_has_ip_or_serial). The
            # alternative to typing it is not to pre-register at all: an
            # unknown device registers itself on first contact.
            if not cleaned.get('serial_number'):
                self.add_error(
                    'serial_number',
                    'Required for push mode - it is the only identifier a device sends. '
                    'Read it from the terminal (Menu > System Info > Device Info), or skip '
                    'registering it here and let the device add itself when it first connects.',
                )

        return cleaned

    def save(self, commit=True):
        device = super().save(commit=False)
        # push_enabled is otherwise only set when a device first contacts the
        # ADMS endpoint, which leaves a pre-registered terminal looking like a
        # pull device until it happens to call in.
        device.push_enabled = self.cleaned_data.get('mode') == self.MODE_PUSH
        if commit:
            device.save()
        return device


class EmployeeDeviceForm(forms.ModelForm):
    class Meta:
        model = EmployeeDevice
        fields = ['device', 'device_uid']
        widgets = {
            'device': forms.Select(attrs={'class': 'form-select'}),
            'device_uid': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'User ID on device'}),
        }
