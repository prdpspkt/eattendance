"""ZKTeco ADMS / WDMS push protocol server.

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

from django.conf import settings
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


def get_device(request):
    """Resolve the device behind a request by serial number.

    Returns ``(device, error_response)``. Exactly one is not None.

    An unknown serial is auto-registered as an **inactive** device so it shows
    up in the admin for approval; its data is refused until someone activates
    it. Refusing with a non-OK response matters: the device keeps the records
    and retries, so nothing is lost while approval is pending.
    """
    serial = (request.GET.get('SN') or request.GET.get('sn') or '').strip()
    if not serial:
        logger.warning("ADMS request without SN from %s", _client_ip(request))
        return None, HttpResponseForbidden('SN required')

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

        ip = _client_ip(request) or f'unknown-{serial}'
        if Device.objects.filter(ip_address=ip).exists():
            ip = f'{ip}#{serial}'[:50]
        device = Device.objects.create(
            name=f'Unregistered device {serial}',
            ip_address=ip,
            serial_number=serial,
            is_active=False,
            push_enabled=True,
        )
        logger.warning(
            "New device SN=%s registered from %s and left inactive pending approval",
            serial, _client_ip(request),
        )

    if not device.is_active:
        device.last_seen = timezone.now()
        device.save(update_fields=['last_seen'])
        # 401 rather than OK: the device holds its records and retries.
        return None, HttpResponse('Device not activated', status=401, content_type='text/plain')

    updates = ['last_seen']
    device.last_seen = timezone.now()
    if not device.push_enabled:
        device.push_enabled = True
        updates.append('push_enabled')
    device.save(update_fields=updates)

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


def _upload_options(device, body):
    """The device reporting its own configuration/firmware after a handshake."""
    fields = {}
    for chunk in body.replace('\n', ',').split(','):
        if '=' in chunk:
            key, _, value = chunk.partition('=')
            fields[key.strip().lstrip('~').upper()] = value.strip()

    updates = []
    firmware = fields.get('FIRMVER') or fields.get('FIRMWAREVERSION')
    if firmware:
        device.firmware_version = firmware[:100]
        updates.append('firmware_version')
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

    info = request.GET.get('INFO')
    if info and info != device.device_info:
        device.device_info = info[:2000]
        device.save(update_fields=['device_info'])

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
