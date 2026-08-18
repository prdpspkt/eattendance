"""Builders for ZKTeco PUSH SDK (ADMS/WDMS) commands.

In push mode the server cannot reach the device, so anything we want it to do
is queued and collected on its next ``/iclock/getrequest`` poll. This module is
the single place that knows the wire syntax, so firmware quirks get fixed once
rather than in every caller.

Wire format is handled by ``DeviceCommand.as_wire_format`` (``C:<id>:<body>``);
what the functions here return is the body.

A caution that applies to everything below: **the exact syntax varies between
firmware versions.** The forms used here are the common PUSH SDK 2.x ones and
are what the ZKTeco documentation specifies, but a given terminal may want a
different separator or ignore a field outright. The queued command's
``return_code`` tells you which happened - 0 (some firmware 1) means the device
accepted it, anything else means it did not. Check Device Commands in the admin
after queuing something new.

Fields within a command are separated by tabs, not spaces: a value like an
employee name can itself contain spaces.
"""

# Simple, argument-free commands.
CHECK = 'CHECK'          # re-send anything the server has not acknowledged
INFO = 'INFO'            # report firmware, capacity and counters
REBOOT = 'REBOOT'        # restart; also forces a fresh handshake
LOG = 'LOG'              # upload the device's own operation log

FIELD_SEP = '\t'


def query_userinfo(pin=None):
    """Ask the device to upload its user table.

    Without ``pin`` the device sends every enrolled user; with one it sends
    just that user. The records arrive as an OPERLOG upload and are parsed by
    ``devices.adms.parse_operlog_users``, which turns unknown PINs into
    *unlinked* enrolments for an admin to attach to an employee.

    This is the "pull employees" operation: in push mode there is no way to
    read the user table directly, so you ask and wait for the upload.
    """
    if pin is None:
        return 'DATA QUERY USERINFO'
    return f'DATA QUERY USERINFO{FIELD_SEP}PIN={pin}'


def query_attlog(start, end):
    """Ask the device to re-upload attendance between two datetimes.

    ``start`` and ``end`` are date or datetime objects. A date is widened to
    cover the whole day, so passing the same date twice asks for that one day.

    Use this for a bounded backfill - a device that was offline over a
    weekend, say. To re-request *everything*, resetting the stamp
    (``Device.request_all_attendance``) is the better tool, because it works
    through the resume mechanism the protocol was designed around instead of
    relying on the device implementing this query.
    """
    start_text = _as_datetime_text(start, end_of_day=False)
    end_text = _as_datetime_text(end, end_of_day=True)
    return (
        f'DATA QUERY ATTLOG{FIELD_SEP}'
        f'StartTime={start_text}{FIELD_SEP}'
        f'EndTime={end_text}'
    )


def _as_datetime_text(value, end_of_day):
    """Render a date or datetime the way the device expects it."""
    if hasattr(value, 'hour'):  # datetime
        return value.strftime('%Y-%m-%d %H:%M:%S')
    return value.strftime('%Y-%m-%d ') + ('23:59:59' if end_of_day else '00:00:00')
