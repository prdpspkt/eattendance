"""Template helpers shared across the UI.

The status-colour mapping in particular used to be an eight-branch ``{% if %}``
chain repeated in roughly ten templates, which is how the same status ended up
rendering in different colours on different pages.
"""
from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """
    Get an item from a dictionary using a variable key.
    Usage: {{ mydict|get_item:key }}
    """
    if dictionary is None:
        return None
    return dictionary.get(key)


# Tone per status value. Everything that renders a status goes through here so
# a colour change happens in one place.
STATUS_TONES = {
    # Daily attendance
    'PRESENT': ('success', 'Present'),
    'LATE': ('warning', 'Late'),
    'ABSENT': ('danger', 'Absent'),
    'HALF_DAY': ('info', 'Half Day'),
    'ON_LEAVE': ('primary', 'On Leave'),
    'HOLIDAY': ('neutral', 'Holiday'),
    'WEEKEND': ('neutral', 'Weekend'),
    # Requests and approvals
    'PENDING': ('warning', 'Pending'),
    'APPROVED': ('success', 'Approved'),
    'REJECTED': ('danger', 'Rejected'),
    'CANCELLED': ('neutral', 'Cancelled'),
    # Employment
    'ACTIVE': ('success', 'Active'),
    'SUSPENDED': ('warning', 'Suspended'),
    'TERMINATED': ('danger', 'Terminated'),
    'RESIGNED': ('neutral', 'Resigned'),
    # Device commands
    'SENT': ('info', 'Sent to device'),
    'DONE': ('success', 'Completed'),
    'FAILED': ('danger', 'Failed'),
}


@register.simple_tag
def status_badge(status, label=None):
    """Render a status as an accessible chip.

    Usage: {% status_badge attendance.status %}
           {% status_badge order.status order.get_status_display %}
    """
    if not status:
        return ''
    key = str(status).upper()
    tone, default_label = STATUS_TONES.get(key, ('neutral', key.replace('_', ' ').title()))
    return format_html(
        '<span class="chip chip--{}">{}</span>', tone, label or default_label
    )


@register.filter
def status_tone(status):
    """Just the tone name, for callers that need to style something else."""
    return STATUS_TONES.get(str(status).upper(), ('neutral', ''))[0]


@register.filter
def initials(user, limit=2):
    """Initials for an avatar placeholder, falling back to the username."""
    if user is None:
        return '?'
    name = ''
    get_full_name = getattr(user, 'get_full_name', None)
    if callable(get_full_name):
        name = (get_full_name() or '').strip()
    if not name:
        name = (getattr(user, 'username', '') or '').strip()
    if not name:
        return '?'
    parts = [part for part in name.split() if part]
    return ''.join(part[0].upper() for part in parts[:limit]) or '?'


@register.simple_tag(takes_context=True)
def query_replace(context, **kwargs):
    """Rebuild the current query string with some parameters changed.

    Sort links used to be hand-built, which silently dropped whatever filters
    were active. Usage: ?{% query_replace page=3 %}
    """
    request = context.get('request')
    params = request.GET.copy() if request else {}
    for key, value in kwargs.items():
        if value is None or value == '':
            params.pop(key, None)
        else:
            params[key] = value
    params.pop('_', None)
    return params.urlencode() if hasattr(params, 'urlencode') else ''


@register.simple_tag(takes_context=True)
def sort_link(context, field, label):
    """A table header that sorts by ``field`` and keeps current filters.

    Renders the arrow state and sets aria-sort on the way out, so screen
    readers get the same information sighted users get from the caret.
    """
    request = context.get('request')
    current_field = request.GET.get('order_by') if request else None
    current_direction = (request.GET.get('order_direction') or 'asc') if request else 'asc'

    is_active = current_field == field
    next_direction = 'desc' if (is_active and current_direction == 'asc') else 'asc'

    params = request.GET.copy() if request else {}
    params['order_by'] = field
    params['order_direction'] = next_direction

    modifier = ''
    if is_active:
        modifier = ' th-sort--asc' if current_direction == 'asc' else ' th-sort--desc'

    return format_html(
        '<a href="?{}" class="th-sort{}">{}</a>',
        params.urlencode() if hasattr(params, 'urlencode') else '',
        modifier,
        label,
    )


@register.filter
def aria_sort(field, request):
    """aria-sort value for a header cell: ascending, descending or none."""
    if not request or request.GET.get('order_by') != field:
        return 'none'
    return 'descending' if request.GET.get('order_direction') == 'desc' else 'ascending'


@register.filter
def hours(value):
    """Format a decimal hour count as '7h 30m'. Blank stays visibly blank."""
    if value in (None, ''):
        return mark_safe('<span class="cell-empty">&mdash;</span>')
    try:
        total_minutes = int(round(float(value) * 60))
    except (TypeError, ValueError):
        return value
    if total_minutes <= 0:
        return mark_safe('<span class="cell-empty">&mdash;</span>')
    whole_hours, minutes = divmod(total_minutes, 60)
    if whole_hours and minutes:
        return f'{whole_hours}h {minutes}m'
    if whole_hours:
        return f'{whole_hours}h'
    return f'{minutes}m'


@register.filter
def minutes_as_duration(value):
    """Format a minute count as '1h 05m', used for late/early figures."""
    if not value:
        return mark_safe('<span class="cell-empty">&mdash;</span>')
    try:
        total = int(value)
    except (TypeError, ValueError):
        return value
    whole_hours, minutes = divmod(total, 60)
    if whole_hours:
        return f'{whole_hours}h {minutes:02d}m'
    return f'{minutes}m'


@register.filter
def punch_is_in(punch_type):
    """True when a raw device punch code means "arriving".

    The device reports a numeric code and the meanings are not contiguous:
    check-in is 0 and overtime-in is 4, while 2 and 3 are the break codes.
    Templates previously inlined `punch_type == 0 or punch_type == 2`, which
    labelled every break-out as a check-in. Read the codes from the one place
    that defines them instead.
    """
    from attendance.models import CHECK_IN_CODES

    try:
        return int(punch_type) in CHECK_IN_CODES
    except (TypeError, ValueError):
        return False
