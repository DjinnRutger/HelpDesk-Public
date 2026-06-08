"""Machine-client ticket intake API (the "DjinnWish" desktop client).

Implements the authoritative wire contract documented in Helpdesk.md:
a single `POST /api/tickets` endpoint that accepts multipart/form-data with a
JSON `payload` part and a `screenshot` PNG part, authenticates via an
admin-configured scheme, and opens a ticket with the screenshot attached.

This endpoint is intentionally outside the session/CSRF/login model used by the
rest of the app: it is called by unattended desktop clients over Netbird.
"""
import html as _html
import json
from datetime import datetime
from pathlib import Path

import bleach
from flask import Blueprint, request, jsonify, current_app, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from .. import db, csrf
from ..models import Setting, Ticket, TicketAttachment, ApiToken, Contact

client_api_bp = Blueprint('client_api', __name__, url_prefix='/api')


@client_api_bp.errorhandler(RequestEntityTooLarge)
def _handle_too_large(e):
    """Return JSON (not Werkzeug's HTML page) when a part/body exceeds a limit."""
    return jsonify(ok=False, error='Payload too large'), 413

# PNG file signature
_PNG_MAGIC = b'\x89PNG\r\n\x1a\n'

_TRUE_VALUES = ('1', 'true', 'on', 'yes')


def _setting_bool(key: str, default: str = '0') -> bool:
    return (Setting.get(key, default) or default).strip().lower() in _TRUE_VALUES


def _set_target_rel(attrs, new=False):
    """Linkify callback: open links in a new tab safely (mirrors email_poll)."""
    attrs[(None, 'target')] = '_blank'
    attrs[(None, 'rel')] = 'noopener noreferrer'
    return attrs


def _authenticate(req):
    """Return (ok, token_or_none). Honors the admin-selected scheme.

    Schemes: 'None' (no auth), 'Bearer' (Authorization: Bearer <tok>),
    'ApiKeyHeader' (<HeaderName>: <tok>, default header X-Api-Key).
    """
    scheme = (Setting.get('CLIENTAPI_AUTH_SCHEME', 'Bearer') or 'Bearer').strip()
    if scheme == 'None':
        return True, None

    presented = None
    if scheme == 'Bearer':
        auth = req.headers.get('Authorization', '') or ''
        if auth.lower().startswith('bearer '):
            presented = auth[7:].strip()
    elif scheme == 'ApiKeyHeader':
        header_name = (Setting.get('CLIENTAPI_HEADER_NAME', 'X-Api-Key') or 'X-Api-Key').strip()
        presented = (req.headers.get(header_name, '') or '').strip()

    tok = ApiToken.verify(presented) if presented else None
    return (tok is not None), tok


def _render_body(description: str, sysinfo: dict) -> str:
    """Build sanitized HTML: the user's description plus a System Information block.

    Free-text from the client is untrusted: every value is HTML-escaped. The
    description is escaped + linkified like inbound email notes.
    """
    parts = []
    desc = (description or '').strip()
    if desc:
        parts.append(bleach.linkify(_html.escape(desc).replace('\n', '<br>'), callbacks=[_set_target_rel]))

    sysinfo = sysinfo or {}

    def esc(v):
        if v is None or v == '':
            return '—'
        if isinstance(v, (list, tuple)):
            v = ', '.join(str(x) for x in v)
        return _html.escape(str(v))

    nb = sysinfo.get('netbird') or {}
    nb_connected = bool(nb.get('isConnected'))
    nb_badge = 'success' if nb_connected else 'secondary'
    nb_summary = esc(nb.get('summary') or ('Connected' if nb_connected else 'Not connected'))

    rows = [
        ('Computer name', esc(sysinfo.get('computerName'))),
        ('User name', esc(sysinfo.get('userName'))),
        ('User email', esc(sysinfo.get('userEmail'))),
        ('IP addresses', esc(sysinfo.get('ipAddresses'))),
        ('OS version', esc(sysinfo.get('osVersion'))),
        ('OS build', esc(sysinfo.get('osBuild'))),
        ('Last boot (local)', esc(sysinfo.get('lastBootTimeLocal'))),
        ('Uptime', esc(sysinfo.get('uptime'))),
        ('CPU', esc(sysinfo.get('cpuModel'))),
        ('Logical processors', esc(sysinfo.get('logicalProcessors'))),
        ('Total RAM (MB)', esc(sysinfo.get('totalRamMb'))),
        ('Client version', esc(sysinfo.get('clientVersion'))),
        ('Captured (UTC)', esc(sysinfo.get('capturedAtUtc'))),
    ]
    table_rows = ''.join(
        f'<tr><th style="text-align:left;padding-right:12px;white-space:nowrap;">{label}</th><td>{value}</td></tr>'
        for label, value in rows
    )

    netbird_block = (
        '<p style="margin:8px 0;">'
        f'<strong>Netbird:</strong> <span class="badge bg-{nb_badge}">{nb_summary}</span>'
        f' &nbsp; <span class="text-muted">IP: {esc(nb.get("netbirdIp"))}</span>'
        f' &nbsp; <span class="text-muted">Installed: {esc(nb.get("isInstalled"))}</span>'
        '</p>'
    )

    parts.append(
        '<hr><h5>System Information</h5>'
        + netbird_block
        + f'<table class="table table-sm">{table_rows}</table>'
    )
    return ''.join(parts)


def _save_screenshot(ticket_id: int, file_storage, max_bytes: int):
    """Persist the uploaded PNG using the same scheme as email attachments."""
    data = file_storage.read()
    if not data:
        return
    if len(data) > max_bytes:
        # Defense in depth if Content-Length was absent/understated.
        raise ValueError('screenshot exceeds max upload size')
    if not data.startswith(_PNG_MAGIC):
        current_app.logger.warning('[CLIENT API] screenshot is not a valid PNG; storing anyway')

    subdir = (Setting.get('ATTACHMENTS_DIR_REL', 'attachments') or 'attachments').strip()
    subdir = subdir.replace('\\', '/').lstrip('/') or 'attachments'
    base = (Setting.get('ATTACHMENTS_BASE', 'instance') or 'instance').strip().lower()
    root = current_app.static_folder if base == 'static' else current_app.instance_path
    save_dir = Path(root) / subdir / str(ticket_id)
    save_dir.mkdir(parents=True, exist_ok=True)

    name = 'screenshot.png'
    target = save_dir / name
    i = 1
    while target.exists():
        target = save_dir / f"screenshot_{i}.png"
        i += 1
    target.write_bytes(data)
    rel_path = f"{subdir}/{ticket_id}/{target.name}"
    db.session.add(TicketAttachment(
        ticket_id=ticket_id,
        filename=target.name,
        content_type='image/png',
        static_path=rel_path,
        size_bytes=len(data),
    ))


@client_api_bp.route('/tickets', methods=['POST'])
@csrf.exempt
def intake_ticket():
    # 1. Intake enabled?
    if not _setting_bool('CLIENTAPI_ENABLED', '0'):
        return jsonify(ok=False, error='Intake disabled'), 503

    # 2. Optional HTTPS enforcement (off by default; HTTP is fine behind Netbird).
    if _setting_bool('CLIENTAPI_REQUIRE_HTTPS', '0') and not request.is_secure:
        return jsonify(ok=False, error='HTTPS required'), 400

    # 3. Authenticate.
    ok, token = _authenticate(request)
    if not ok:
        current_app.logger.warning(f'[CLIENT API] auth failure from {request.remote_addr}')
        return jsonify(ok=False, error='Invalid token'), 401

    # 4. Enforce max upload size before buffering the body.
    try:
        max_mb = int(Setting.get('CLIENTAPI_MAX_UPLOAD_MB', '25') or '25')
    except Exception:
        max_mb = 25
    max_bytes = max_mb * 1024 * 1024
    if request.content_length is not None and request.content_length > max_bytes:
        return jsonify(ok=False, error='Payload too large'), 413

    # Werkzeug 3.1 caps in-memory form parts at 500 KB by default. A screenshot
    # part sent WITHOUT a filename is parsed in-memory and would 413 well below
    # our configured limit, so raise the ceiling to max_bytes before parsing.
    # (A correctly-formed file part with a filename is spooled to disk and is
    # unaffected by this.) Must be set before the first request.form access.
    request.max_form_memory_size = max_bytes

    # 5. Validate multipart + parse JSON payload.
    payload_raw = request.form.get('payload')
    if payload_raw is None:
        return jsonify(ok=False, error='Missing payload part'), 422
    try:
        data = json.loads(payload_raw)
        if not isinstance(data, dict):
            raise ValueError('payload is not an object')
    except Exception:
        return jsonify(ok=False, error='Malformed JSON payload'), 422

    sysinfo = data.get('systemInfo') or {}
    if not isinstance(sysinfo, dict):
        sysinfo = {}

    computer = (sysinfo.get('computerName') or '').strip()
    win_user = (sysinfo.get('userName') or '').strip()
    user_email = (sysinfo.get('userEmail') or '').strip().lower()

    # 6. Build the ticket.
    title = (data.get('title') or '').strip()
    if not title:
        title = f"Screenshot ticket from {computer or 'unknown'} ({win_user or 'unknown'})"

    body_html = _render_body(data.get('description'), sysinfo)

    ticket = Ticket(
        subject=title[:300],
        body=body_html,
        status='new',
        priority=(Setting.get('CLIENTAPI_DEFAULT_PRIORITY', 'medium') or 'medium'),
        source='DjinnWish',
        system_info_json=json.dumps(sysinfo, ensure_ascii=False),
    )

    # Map requester to an existing Contact by email when the client provides one.
    if user_email:
        contact = Contact.query.filter_by(email=user_email).first()
        if not contact:
            contact = Contact(email=user_email, name=(win_user or None))
            db.session.add(contact)
        ticket.requester_email = user_email
        ticket.requester_name = (contact.name or win_user or user_email)
    else:
        # No email: keep a human-readable identity; full details live in system_info_json.
        if win_user or computer:
            ticket.requester_name = f"{win_user}@{computer}".strip('@')

    # Optional default assignee.
    assignee_raw = (Setting.get('CLIENTAPI_DEFAULT_ASSIGNEE_ID', '') or '').strip()
    if assignee_raw.isdigit() and int(assignee_raw) > 0:
        ticket.assignee_id = int(assignee_raw)

    db.session.add(ticket)
    db.session.flush()  # assign ticket.id for the attachment path

    # 7. Save the screenshot (small enough to do synchronously within the client timeout).
    screenshot = request.files.get('screenshot')
    if screenshot and screenshot.filename:
        try:
            _save_screenshot(ticket.id, screenshot, max_bytes)
        except ValueError:
            db.session.rollback()
            return jsonify(ok=False, error='Payload too large'), 413
        except Exception as e:
            current_app.logger.warning(f'[CLIENT API] failed saving screenshot: {e}')
    elif 'screenshot' in request.form:
        # The client sent the screenshot as a plain form field (no filename), so
        # the bytes can't be stored as an attachment. Ticket is still created.
        current_app.logger.warning(
            '[CLIENT API] screenshot received without a filename; it must be sent as a '
            'file part (Content-Disposition filename="screenshot.png") to be attached.'
        )

    # Record token usage.
    if token is not None:
        token.last_used_at = datetime.utcnow()

    db.session.commit()

    current_app.logger.info(
        f'[CLIENT API] Ticket #{ticket.id} created from {computer or "?"}/{win_user or "?"} '
        f'token={getattr(token, "id", "none")} src=DjinnWish'
    )

    try:
        url = url_for('tickets.show_ticket', ticket_id=ticket.id, _external=True)
    except Exception:
        url = None
    return jsonify(ok=True, ticketId=ticket.id, ticketNumber=f'HD-{ticket.id}', url=url), 201


@client_api_bp.route('/checkin', methods=['POST'])
@csrf.exempt
def checkin():
    """Periodic machine check-in.

    The desktop client POSTs a small JSON body ({"systemInfo": {...}, "source": "DjinnWish"})
    on an interval. We match the machine's user to a Contact by email and stamp the last check-in
    time + computer/IP/client version, so staff can see when a user's PC was last online on the
    User page. Shares the intake's enable switch and token auth.
    """
    # 1. Intake enabled? (same master switch as ticket intake)
    if not _setting_bool('CLIENTAPI_ENABLED', '0'):
        return jsonify(ok=False, error='Intake disabled'), 503

    # 2. Optional HTTPS enforcement (off by default; HTTP is fine behind Netbird).
    if _setting_bool('CLIENTAPI_REQUIRE_HTTPS', '0') and not request.is_secure:
        return jsonify(ok=False, error='HTTPS required'), 400

    # 3. Authenticate.
    ok, token = _authenticate(request)
    if not ok:
        current_app.logger.warning(f'[CLIENT API] check-in auth failure from {request.remote_addr}')
        return jsonify(ok=False, error='Invalid token'), 401

    # 4. Parse the JSON body.
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(ok=False, error='Malformed JSON payload'), 422

    sysinfo = data.get('systemInfo') or {}
    if not isinstance(sysinfo, dict):
        sysinfo = {}

    computer = (sysinfo.get('computerName') or '').strip()
    win_user = (sysinfo.get('userName') or '').strip()
    user_email = (sysinfo.get('userEmail') or '').strip().lower()
    client_version = (sysinfo.get('clientVersion') or '').strip()

    ips = sysinfo.get('ipAddresses')
    if isinstance(ips, (list, tuple)):
        ip_str = ', '.join(str(x) for x in ips)
    else:
        ip_str = str(ips).strip() if ips else ''

    now = datetime.utcnow()

    # 5. Match to a Contact by email and stamp the check-in.
    matched = False
    if user_email:
        contact = Contact.query.filter_by(email=user_email).first()
        if not contact:
            contact = Contact(email=user_email, name=(win_user or None))
            db.session.add(contact)
        contact.last_checkin_at = now
        contact.last_checkin_computer = computer or None
        contact.last_checkin_ip = ip_str or None
        contact.last_checkin_client_version = client_version or None
        matched = True

    # Record token usage.
    if token is not None:
        token.last_used_at = now

    db.session.commit()

    if matched:
        current_app.logger.info(f'[CLIENT API] check-in from {user_email} ({computer or "?"})')
    else:
        # No email to link to a Contact; still a valid check-in, just not attributable.
        current_app.logger.info(
            f'[CLIENT API] check-in from {computer or "?"}/{win_user or "?"} (no email; not linked)'
        )

    return jsonify(ok=True, matched=matched), 200
