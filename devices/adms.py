"""ADMS / WDMS push protocol server.

Works with any terminal that speaks ADMS/WDMS. The protocol originated with
ZKTeco and is implemented by many vendors under both names (and sometimes as
"Cloud Server" or "Push SDK" in a terminal's own menus), so nothing here
assumes a particular manufacturer.

In *pull* mode this project opens a TCP connection to each device (pyzk) and
asks for its records. That needs the device to be reachable from the server, a
Celery beat schedule, and it re-reads the whole log every cycle.

In *push* mode the device is the client. Point the terminal at this server
(Comm. -> Ethernet -> Cloud Server / ADMS: server address + port, path
``/iclock/``) and it opens outbound HTTP requests to us, so devices behind NAT
or on a remote branch network work with no inbound firewall rules, and punches
arrive within seconds instead of at the next poll.

Endpoints (PUSH SDK 2.x):

    GET  /iclock/cdata?SN=..&options=all&pushver=..   handshake, returns config
    POST /iclock/cdata?SN=..&table=ATTLOG             attendance log upload
    POST /iclock/cdata?SN=..&table=OPERLOG            user/operation log upload
    GET  /iclock/getrequest?SN=..                     device polls for commands
    POST /iclock/devicecmd?SN=..                      device reports command results
    GET  /iclock/ping?SN=..                           heartbeat

Responses are plain text. ``OK`` means "accepted"; any non-OK/error status
makes the device retry later, which is what we want when we are not ready to
accept its data, because the device keeps the records in its own memory.
"""
import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseForbidden
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .ingest import parse_device_datetime, process_touched_days, record_punches, resolve_enrollment
from .models import Device, DeviceCommand

logger = logging.getLogger(__name__)

SERVER_VERSION = '2.2.14'


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _ok(count=None):
    """The device treats a bare 'OK' (or 'OK: n') as a successful handover."""
    body = 'OK' if count is None else f'OK: {count}'
    return HttpResponse(body, content_type='text/plain')


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _timezone_offset_hours():
    """Server UTC offset in hours, which the device uses to set its clock."""
    offset = timezone.localtime().utcoffset()
    if offset is None:
        return 0
    hours = offset.total_seconds() / 3600
    return int(hours) if hours == int(hours) else round(hours, 2)


def device_cache_key(serial):
    return f'adms:device:{serial}'


def _last_seen_cache_key(device_id):
    return f'adms:seen:{device_id}'


def _touch_last_seen(device, extra_updates=(), ip=None):
    """Record that the device just contacted us, without writing every time.

    Terminals poll /iclock/getrequest every few seconds. Persisting last_seen
    on each contact turns a read-only poll into a database write, and SQLite
    serialises writers - so a handful of chatty devices can occupy the single
    write lock doing nothing but heartbeats, starving the punch inserts that
    actually matter.

    The write is therefore rate limited per device to
    ADMS_LAST_SEEN_WRITE_SECONDS. The stored value lags reality by at most
    that much, which the online/offline display already tolerates: it treats a
    device as online for ADMS_OFFLINE_AFTER_SECONDS (180s by default) after
    last_seen, an order of magnitude more slack than the 30s write interval.

    ``extra_updates`` forces a write regardless, for fields that must not be
    dropped (push_enabled on a device's first contact).
    """
    now = timezone.now()
    device.last_seen = now
    updates = ['last_seen', *extra_updates]

    # Record where the device reached us from. Informational only - it is not
    # an identity in push mode - so it rides along with whatever write the
    # throttle below allows rather than forcing one of its own.
    if ip and ip != device.last_ip:
        device.last_ip = ip
        updates.append('last_ip')

    if not extra_updates:
        interval = getattr(settings, 'ADMS_LAST_SEEN_WRITE_SECONDS', 30)
        if interval:
            key = _last_seen_cache_key(device.pk)
            written_at = cache.get(key)
            if written_at is not None and (time.monotonic() - written_at) < interval:
                return
            cache.set(key, time.monotonic(), interval * 4)

    device.save(update_fields=updates)


def get_device(request):
    """Resolve the device behind a request by serial number.

    Returns ``(device, error_response)``. Exactly one is not None.

    An unknown serial is auto-registered as an **inactive** device so it shows
    up in the admin for approval; its data is refused until someone activates
    it. Refusing with a non-OK response matters: the device keeps the records
    and retries, so nothing is lost while approval is pending.

    The lookup is cached for ADMS_DEVICE_CACHE_SECONDS because it is the one
    query every ADMS request must make before it can do anything else. The
    cache is invalidated whenever a Device is saved (see devices/signals.py),
    so an activation or a rename takes effect immediately in the worker that
    made the change; with the default local-memory cache the other workers pick
    it up when their own copy expires.
    """
    serial = (request.GET.get('SN') or request.GET.get('sn') or '').strip()
    if not serial:
        logger.warning("ADMS request without SN from %s", _client_ip(request))
        return None, HttpResponseForbidden('SN required')

    cache_seconds = getattr(settings, 'ADMS_DEVICE_CACHE_SECONDS', 60)
    cache_key = device_cache_key(serial)

    device = cache.get(cache_key) if cache_seconds else None
    if device is not None:
        return _check_active(device, _client_ip(request))

    device = Device.objects.filter(serial_number=serial).first()

    if device is None:
        # A device previously configured for pull mode, now pushing: adopt it
        # by IP so history and enrolments carry over instead of splitting in two.
        ip = _client_ip(request)
        device = Device.objects.filter(ip_address=ip, serial_number__isnull=True).first()
        if device:
            device.serial_number = serial
            device.save(update_fields=['serial_number'])
            logger.info("Adopted existing device %s for serial %s", device, serial)

    if device is None:
        if not getattr(settings, 'ADMS_AUTO_REGISTER_DEVICES', True):
            logger.warning("Rejected unknown device SN=%s from %s", serial, _client_ip(request))
            return None, HttpResponseForbidden('Unregistered device')

        # No ip_address: a push device is identified by its serial number, and
        # the address it happens to reach us from is NAT's business, not ours.
        # (This used to invent values like "unknown-<serial>" to satisfy a
        # required unique column - the column is now optional instead.)
        device = Device.objects.create(
            name=f'Unregistered device {serial}',
            last_ip=_client_ip(request) or None,
            serial_number=serial,
            is_active=False,
            push_enabled=True,
        )
        logger.warning(
            "New device SN=%s registered from %s and left inactive pending approval",
            serial, _client_ip(request),
        )

    if cache_seconds:
        cache.set(cache_key, device, cache_seconds)

    return _check_active(device, _client_ip(request))


def _check_active(device, ip=None):
    """Gate a resolved device on its approval flag and stamp last_seen."""
    if not device.is_active:
        _touch_last_seen(device, ip=ip)
        # 401 rather than OK: the device holds its records and retries.
        return None, HttpResponse('Device not activated', status=401, content_type='text/plain')

    if not device.push_enabled:
        device.push_enabled = True
        # Forced write: this one must not be dropped by the heartbeat throttle,
        # or the device would never be marked as pushing.
        _touch_last_seen(device, extra_updates=['push_enabled'], ip=ip)
    else:
        _touch_last_seen(device, ip=ip)

    return device, None


# ---------------------------------------------------------------------------
# parsers
# ---------------------------------------------------------------------------
def parse_attlog(body):
    """Parse an ATTLOG payload into punch dicts.

    Each line is tab separated:
        PIN <TAB> YYYY-MM-DD HH:MM:SS <TAB> status <TAB> verify <TAB> workcode ...
    """
    punches = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split('\t')
        if len(parts) < 2:
            parts = line.split()
            if len(parts) >= 3:
                # Space separated variant: PIN date time status ...
                parts = [parts[0], f'{parts[1]} {parts[2]}'] + parts[3:]
            else:
                logger.warning("Skipping malformed ATTLOG line: %r", line)
                continue

        timestamp = parse_device_datetime(parts[1])
        if timestamp is None:
            logger.warning("Skipping ATTLOG line with unparsable time: %r", line)
            continue

        punches.append({
            'device_uid': parts[0].strip(),
            'timestamp': timestamp,
            'punch_type': parts[2].strip() if len(parts) > 2 else 0,
            'verify_mode': parts[3].strip() if len(parts) > 3 else None,
        })
    return punches


def parse_operlog_users(body):
    """Pull ``USER PIN=..`` records out of an OPERLOG payload."""
    users = []
    for line in body.splitlines():
        line = line.strip()
        if not line.upper().startswith('USER '):
            continue
        fields = {}
        for chunk in line[5:].split('\t'):
            if '=' in chunk:
                key, _, value = chunk.partition('=')
                fields[key.strip().upper()] = value.strip()
        if 'PIN' in fields:
            users.append({'device_uid': fields['PIN'], 'user_name': fields.get('NAME') or None})
    return users


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------
@csrf_exempt
def cdata(request):
    """Handshake (GET) and data upload (POST)."""
    device, error = get_device(request)
    if error:
        return error

    if request.method == 'GET':
        return _handshake(request, device)
    return _upload(request, device)


def _handshake(request, device):
    """Answer the device's start-up request with its operating parameters."""
    logger.info("ADMS handshake from %s (SN=%s)", device.name, device.serial_number)

    options = [
        f'GET OPTION FROM: {device.serial_number}',
        f'ATTLOGStamp={device.attlog_stamp or "0"}',
        f'OPERLOGStamp={device.operlog_stamp or "0"}',
        'ATTPHOTOStamp=0',
        'ErrorDelay=30',       # seconds to wait after a failed request
        'Delay=10',            # seconds between idle polls
        'TransTimes=00:00;14:00',
        'TransInterval=1',
        'TransFlag=TransData AttLog OpLog AttPhoto EnrollUser ChgUser EnrollFP ChgFP UserPic',
        f'TimeZone={_timezone_offset_hours()}',
        'Realtime=1',          # push each punch as it happens
        'Encrypt=0',
        f'ServerVer={SERVER_VERSION}',
    ]
    return HttpResponse('\n'.join(options) + '\n', content_type='text/plain')


def _upload(request, device):
    """Handle POSTed device data (attendance, users, operations)."""
    table = (request.GET.get('table') or '').upper()
    body = request.body.decode('utf-8', errors='replace')
    stamp = request.GET.get('Stamp') or request.GET.get('stamp')

    logger.debug("ADMS upload table=%s from %s: %r", table, device.serial_number, body[:500])

    if table == 'ATTLOG':
        return _upload_attlog(device, body, stamp)
    if table == 'OPERLOG':
        return _upload_operlog(device, body, stamp)
    if table == 'OPTIONS':
        return _upload_options(device, body)

    # ATTPHOTO, BIODATA and friends: acknowledge so the device moves on rather
    # than retrying forever with data we do not store.
    logger.info("Acknowledged unhandled ADMS table %r from %s", table, device.serial_number)
    return _ok()


def _upload_attlog(device, body, stamp):
    punches = parse_attlog(body)
    counts, touched = record_punches(device, punches)

    if stamp:
        device.attlog_stamp = str(stamp)[:32]
    device.last_sync = timezone.now()
    device.last_sync_status = (
        f"Push: {counts['created']} new"
        + (f", {counts['duplicate']} dup" if counts['duplicate'] else '')
        + (f", {counts['unlinked']} unlinked" if counts['unlinked'] else '')
    )[:200]
    device.save(update_fields=['attlog_stamp', 'last_sync', 'last_sync_status'])

    if counts['created'] and getattr(settings, 'ADMS_PROCESS_ON_PUSH', True):
        process_touched_days(touched)

    logger.info("ATTLOG from %s: %s", device.serial_number, counts)
    return _ok(len(punches))


def _upload_operlog(device, body, stamp):
    users = parse_operlog_users(body)
    for user in users:
        resolve_enrollment(
            device,
            int(user['device_uid']) if str(user['device_uid']).isdigit() else None,
            user_name=user['user_name'],
        )

    if stamp:
        device.operlog_stamp = str(stamp)[:32]
        device.save(update_fields=['operlog_stamp'])

    if users:
        logger.info("OPERLOG from %s: %s user record(s)", device.serial_number, len(users))
    return _ok(len(users))


def parse_options(body):
    """Parse an OPTIONS/INFO payload into a dict of upper-case keys.

    Devices send ``key=value`` pairs separated by commas or newlines, and
    prefix some keys with a tilde (``~SerialNumber``). Both are normalised
    away so callers can just look up ``SERIALNUMBER``.
    """
    fields = {}
    for chunk in body.replace('\n', ',').split(','):
        if '=' in chunk:
            key, _, value = chunk.partition('=')
            fields[key.strip().lstrip('~').upper()] = value.strip()
    return fields


# Identity and version details a device reports about itself, mapped to the
# model fields they populate. Firmware naming varies between versions, hence
# the alternatives.
_OPTION_FIELDS = (
    ('firmware_version', ('FIRMVER', 'FIRMWAREVERSION'), 100),
    ('device_id', ('DEVICEID', 'DEVICEIDENTIFY', 'MACHINENUMBER'), 32),
    ('mac_address', ('MAC', 'MACADDRESS', 'ETHERNETMAC'), 32),
)


def _apply_reported_identity(device, fields):
    """Copy the identifiers a device reports about itself onto its record.

    Only ever fills in or corrects what the device says; it does not clear a
    value the device stopped reporting, because firmware differs in which keys
    it sends and a missing key means "not mentioned", not "empty".
    """
    updates = []
    for attribute, keys, max_length in _OPTION_FIELDS:
        value = next((fields[key] for key in keys if fields.get(key)), None)
        if value and getattr(device, attribute) != value[:max_length]:
            setattr(device, attribute, value[:max_length])
            updates.append(attribute)

    # A device reporting a serial that differs from the one it connected with
    # is worth knowing about; it usually means two terminals were configured
    # from the same backup. Do not overwrite - the SN we matched on is the one
    # the protocol uses.
    reported_serial = fields.get('SERIALNUMBER')
    if reported_serial and device.serial_number and reported_serial != device.serial_number:
        logger.warning(
            "Device %s connected as SN=%s but reports SerialNumber=%s",
            device.name, device.serial_number, reported_serial,
        )

    return updates


def _upload_options(device, body):
    """The device reporting its own configuration/firmware after a handshake."""
    fields = parse_options(body)

    updates = _apply_reported_identity(device, fields)
    if body:
        device.device_info = body[:2000]
        updates.append('device_info')
    if updates:
        device.save(update_fields=updates)

    return _ok()


@csrf_exempt
@require_GET
def getrequest(request):
    """The device polling for work. Returns queued commands, or OK if idle."""
    device, error = get_device(request)
    if error:
        return error

    # Devices attach an INFO string to this poll carrying firmware, MAC and
    # device id, so identity details are picked up here too rather than only
    # on an OPTIONS upload - some firmware never sends one.
    info = request.GET.get('INFO')
    if info and info != device.device_info:
        updates = _apply_reported_identity(device, parse_options(info))
        device.device_info = info[:2000]
        updates.append('device_info')
        device.save(update_fields=updates)

    pending = list(
        DeviceCommand.objects.filter(
            device=device, status=DeviceCommand.STATUS_PENDING
        ).order_by('created_at')[:getattr(settings, 'ADMS_MAX_COMMANDS_PER_POLL', 10)]
    )

    if not pending:
        return _ok()

    lines = [command.as_wire_format() for command in pending]
    for command in pending:
        command.mark_sent()

    logger.info("Dispatched %s command(s) to %s", len(pending), device.serial_number)
    return HttpResponse('\n'.join(lines) + '\n', content_type='text/plain')


@csrf_exempt
@require_POST
def devicecmd(request):
    """The device reporting the outcome of commands it ran."""
    device, error = get_device(request)
    if error:
        return error

    body = request.body.decode('utf-8', errors='replace')
    handled = 0

    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = {}
        for chunk in line.split('&'):
            key, _, value = chunk.partition('=')
            fields[key.strip().upper()] = value.strip()

        command_id = fields.get('ID')
        if not command_id:
            continue

        command = DeviceCommand.objects.filter(id=command_id, device=device).first()
        if command is None:
            logger.warning("Result for unknown command %s from %s", command_id, device.serial_number)
            continue

        command.mark_result(fields.get('RETURN'), response=fields.get('CMD'))
        handled += 1

    logger.info("Recorded %s command result(s) from %s", handled, device.serial_number)
    return _ok()


@csrf_exempt
def ping(request):
    """Heartbeat/keep-alive."""
    device, error = get_device(request)
    if error:
        return error
    return _ok()


@csrf_exempt
def fdata(request):
    """Fingerprint/biometric template upload. Acknowledged but not stored."""
    device, error = get_device(request)
    if error:
        return error
    logger.info("Received biometric payload from %s (not stored)", device.serial_number)
    return _ok()
