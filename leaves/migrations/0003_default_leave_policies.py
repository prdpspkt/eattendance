"""Give the leave types already on file the rules the office actually runs.

Before this migration a leave type knew only how many days a year it was worth,
so home leave that accumulates to 180 and casual leave that lapses every year
were stored identically. The rules below are the ones in force today; they are
data, and the Leave Types page is where they get changed when the government
changes them.

Only fields that were previously meaningless are set, and only for leave types
that are recognised by code - anything else keeps the safe default of a yearly
entitlement that expires at the year end.
"""
from django.db import migrations

YEARLY = 'YEARLY'
OCCASIONAL = 'OCCASIONAL'
NONE = 'NONE'
CAPPED = 'CAPPED'
UNLIMITED = 'UNLIMITED'

# code -> (accrual, days_per_year, carry_forward, ceiling, days_per_occurrence,
#          lifetime occurrences)
POLICIES = {
    # Accumulates to a ceiling: 30 a year, never standing above 180.
    'HL': (YEARLY, 30, CAPPED, 180, None, None),
    # Accumulates without limit.
    'SL': (YEARLY, 12, UNLIMITED, None, None, None),
    # Lapses at the year end.
    'CL': (YEARLY, 12, NONE, None, None, None),
    'FL': (YEARLY, None, NONE, None, None, None),
    'AL': (YEARLY, None, NONE, None, None, None),
    'UL': (YEARLY, None, NONE, None, None, None),
    'ST': (YEARLY, None, NONE, None, None, None),
    # Granted when the event happens, counted over a career.
    'ML': (OCCASIONAL, 0, NONE, None, None, 2),
    'ADL': (OCCASIONAL, 0, NONE, None, None, 2),
    'PL': (OCCASIONAL, 0, NONE, None, 15, 2),
    'SSL': (OCCASIONAL, 0, NONE, None, None, 2),
    'WL': (OCCASIONAL, 0, NONE, None, 5, None),
    'DR': (OCCASIONAL, 0, NONE, None, 15, 4),
}

# Types the office needs that may not exist yet.
NEW_TYPES = [
    {
        'code': 'WL',
        'name': 'Wedding Leave',
        'description': 'Granted for the employee\'s own wedding.',
        'days_per_occurrence': 5,
        'max_occurrences_lifetime': None,
    },
    {
        'code': 'DR',
        'name': 'Death Rituals Leave',
        'description': 'Granted for the funeral rites of a close family member.',
        'days_per_occurrence': 15,
        'max_occurrences_lifetime': 4,
    },
]


def apply_policies(apps, schema_editor):
    LeaveType = apps.get_model('leaves', 'LeaveType')

    for leave_type in LeaveType.objects.all():
        policy = POLICIES.get(leave_type.code.upper())
        if not policy:
            continue
        accrual, per_year, carry, ceiling, per_occurrence, lifetime = policy

        leave_type.accrual = accrual
        leave_type.carry_forward = carry
        leave_type.max_accumulation_days = ceiling
        leave_type.max_occurrences_lifetime = lifetime

        if accrual == OCCASIONAL:
            # The days figure was stored as a yearly entitlement because there
            # was nowhere else to put it. It was always a per-event figure.
            leave_type.days_per_occurrence = per_occurrence or leave_type.days_per_year or 15
            leave_type.days_per_year = 0
        elif per_year is not None:
            leave_type.days_per_year = per_year

        leave_type.save()

    for spec in NEW_TYPES:
        if LeaveType.objects.filter(code=spec['code']).exists():
            continue
        if LeaveType.objects.filter(name=spec['name']).exists():
            continue
        LeaveType.objects.create(
            code=spec['code'],
            name=spec['name'],
            description=spec['description'],
            accrual=OCCASIONAL,
            days_per_year=0,
            carry_forward=NONE,
            days_per_occurrence=spec['days_per_occurrence'],
            max_occurrences_lifetime=spec['max_occurrences_lifetime'],
            is_paid=True,
            requires_approval=True,
            is_active=True,
        )


def undo(apps, schema_editor):
    """Put the day counts back where they were, and drop the added types."""
    LeaveType = apps.get_model('leaves', 'LeaveType')
    for leave_type in LeaveType.objects.filter(accrual=OCCASIONAL):
        if leave_type.days_per_occurrence and not leave_type.days_per_year:
            leave_type.days_per_year = leave_type.days_per_occurrence
            leave_type.save()
    LeaveType.objects.filter(code__in=[spec['code'] for spec in NEW_TYPES]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('leaves', '0002_leavebalance_accrued_days_leavebalance_lapsed_days_and_more'),
    ]

    operations = [
        migrations.RunPython(apply_policies, undo),
    ]
