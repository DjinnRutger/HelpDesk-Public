import hashlib
import json
import secrets
from datetime import datetime
from flask_login import UserMixin
from . import db, login_manager

class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=True)

    @staticmethod
    def get(key: str, default=None):
        s = Setting.query.filter_by(key=key).first()
        if not s:
            return default
        value = s.value
        # Automatically decrypt sensitive settings
        from .utils.security import SENSITIVE_SETTING_KEYS, decrypt_value
        if key in SENSITIVE_SETTING_KEYS and value:
            value = decrypt_value(value)
        return value if value else default

    @staticmethod
    def set(key: str, value: str):
        # Automatically encrypt sensitive settings
        from .utils.security import SENSITIVE_SETTING_KEYS, encrypt_value
        if key in SENSITIVE_SETTING_KEYS and value:
            value = encrypt_value(value)
        s = Setting.query.filter_by(key=key).first()
        if not s:
            s = Setting(key=key, value=value)
            db.session.add(s)
        else:
            s.value = value
        db.session.commit()

    @staticmethod
    def get_raw(key: str, default=None):
        """Get the raw (possibly encrypted) value without decryption."""
        s = Setting.query.filter_by(key=key).first()
        return s.value if s else default


class Role(db.Model):
    """A named set of per-module access levels (see app/permissions.py).

    permissions_json maps module key -> level int, e.g. {"tickets": 4, "assets": 1}.
    Missing keys mean No Access (fail closed). The built-in Administrator role
    bypasses the stored map entirely and always has full access.
    """
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    builtin_key = db.Column(db.String(40), nullable=True, unique=True)  # 'administrator' | 'technician' | None
    is_system = db.Column(db.Boolean, default=False, nullable=False)
    permissions_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def permissions(self) -> dict:
        try:
            data = json.loads(self.permissions_json) if self.permissions_json else {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def level(self, module_key: str) -> int:
        if self.builtin_key == 'administrator':
            from .permissions import DELETE
            return DELETE
        try:
            return int(self.permissions.get(module_key, 0))
        except (TypeError, ValueError):
            return 0

    def set_permissions(self, perms: dict):
        self.permissions_json = json.dumps(perms or {})


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    # Legacy/derived column kept in sync by set_role(); do not check directly —
    # use can()/is_administrator instead.
    role = db.Column(db.String(50), default="tech")  # 'admin' or 'tech'
    role_id = db.Column(db.Integer, db.ForeignKey('role.id'), nullable=True)
    role_obj = db.relationship('Role')
    is_active = db.Column(db.Boolean, default=True)
    theme = db.Column(db.String(20), default="light")  # 'light', 'dark', or custom keys
    tickets_view_pref = db.Column(db.String(40), default="any")  # 'any', 'me', 'me_or_unassigned'
    signature = db.Column(db.String(500), nullable=True)  # Email signature for public notes
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @property
    def effective_signature(self):
        """Return the signature or default to '-name' format."""
        if self.signature:
            return self.signature
        return f"-{self.name}" if self.name else ""

    def get_id(self):
        return str(self.id)

    def set_role(self, role: "Role"):
        """Assign a Role and keep the legacy role string in sync."""
        self.role_id = role.id if role else None
        self.role_obj = role
        self.role = 'admin' if (role and role.builtin_key == 'administrator') else 'tech'

    def permission_level(self, module_key: str) -> int:
        from .permissions import get_level
        return get_level(self, module_key)

    def can(self, module_key: str, level: int) -> bool:
        from .permissions import has_permission
        return has_permission(self, module_key, level)

    @property
    def is_administrator(self) -> bool:
        if self.role_obj is not None:
            return self.role_obj.builtin_key == 'administrator'
        return self.role == 'admin'


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class ApiToken(db.Model):
    """Opaque bearer/API-key tokens for machine clients (e.g. the DjinnWish desktop client).

    Only a SHA-256 hash of the token is stored. The token is high-entropy
    (>=32 random bytes) so a plain hash is not brute-forceable; the plaintext is
    shown once at generation and never persisted.
    """
    __tablename__ = 'api_token'
    id = db.Column(db.Integer, primary_key=True)
    label = db.Column(db.String(200), nullable=True)
    token_hash = db.Column(db.String(128), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_used_at = db.Column(db.DateTime, nullable=True)
    revoked = db.Column(db.Boolean, default=False, nullable=False)

    @staticmethod
    def _hash(plaintext: str) -> str:
        return hashlib.sha256((plaintext or '').encode('utf-8')).hexdigest()

    @classmethod
    def generate(cls, label: str = None):
        """Create a new token. Returns (ApiToken, plaintext). Show plaintext ONCE."""
        plaintext = secrets.token_urlsafe(32)
        tok = cls(label=label, token_hash=cls._hash(plaintext))
        db.session.add(tok)
        db.session.commit()
        return tok, plaintext

    @classmethod
    def verify(cls, plaintext: str):
        """Return the matching active token row, or None. Updates nothing."""
        if not plaintext:
            return None
        return cls.query.filter_by(token_hash=cls._hash(plaintext), revoked=False).first()


# --- Tag System ---
# M2M association tables (defined before Tag so FKs resolve)
ticket_tags = db.Table(
    'ticket_tags',
    db.Column('ticket_id', db.Integer, db.ForeignKey('ticket.id', ondelete='CASCADE'), primary_key=True),
    db.Column('tag_id',    db.Integer, db.ForeignKey('tag.id',    ondelete='CASCADE'), primary_key=True),
)
asset_tags = db.Table(
    'asset_tags',
    db.Column('asset_id', db.Integer, db.ForeignKey('asset.id', ondelete='CASCADE'), primary_key=True),
    db.Column('tag_id',   db.Integer, db.ForeignKey('tag.id',   ondelete='CASCADE'), primary_key=True),
)


class Tag(db.Model):
    __tablename__ = 'tag'
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(100), nullable=False)
    # Bootstrap color name (primary, success, danger, warning, info, secondary)
    color      = db.Column(db.String(30), nullable=True)
    parent_id  = db.Column(db.Integer, db.ForeignKey('tag.id'), nullable=True)
    position   = db.Column(db.Integer, default=0)
    # Comma-separated phrases used to auto-suggest this tag from ticket text
    keywords   = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    children = db.relationship(
        'Tag',
        backref=db.backref('parent', remote_side=[id]),
        order_by='Tag.position',
        cascade='all, delete-orphan',
    )
    tickets = db.relationship('Ticket', secondary=ticket_tags, back_populates='tags')
    assets  = db.relationship('Asset',  secondary=asset_tags,  back_populates='tags')

    @property
    def effective_color(self):
        """Return this tag's color, or inherit from parent."""
        if self.color:
            return self.color
        return self.parent.effective_color if self.parent else 'secondary'

    @property
    def full_path(self):
        """Return 'Parent › Child' or just 'Name' for root tags."""
        return f"{self.parent.name} \u203a {self.name}" if self.parent else self.name

    @property
    def keyword_list(self):
        if not self.keywords:
            return []
        seen = []
        for k in self.keywords.split(','):
            k = k.strip().lower()
            if k and k not in seen:
                seen.append(k)
        return seen


class Ticket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(200), unique=True, nullable=True)
    subject = db.Column(db.String(300), nullable=False)
    # Legacy field kept for backwards-compatibility; use requester_name/requester_email going forward
    requester = db.Column(db.String(255), nullable=True)
    requester_name = db.Column(db.String(255), nullable=True)
    requester_email = db.Column(db.String(255), nullable=True)
    body = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default="new")  # new, open, in_progress, closed
    priority = db.Column(db.String(20), default="medium")  # low, medium, high
    assignee_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    assignee = db.relationship('User', foreign_keys=[assignee_id])
    co_assignee_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    co_assignee = db.relationship('User', foreign_keys=[co_assignee_id])
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    closed_at = db.Column(db.DateTime, nullable=True)
    source = db.Column(db.String(50), default="email")
    # Optional association to a Project (project tickets are grouped and hidden from normal lists)
    project_id = db.Column(db.Integer, db.ForeignKey('project.id'), nullable=True)
    # Ordering within a project
    project_position = db.Column(db.Integer, default=0)
    # Optional associated asset (device related to the ticket)
    asset_id = db.Column(db.Integer, db.ForeignKey('asset.id'), nullable=True)
    # Snooze: when set in the future, hide from default Dashboard and Tickets list
    snoozed_until = db.Column(db.DateTime, nullable=True)
    # Creator (tech) when manually added via UI; null for email-imported tickets
    created_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    # Raw systemInfo JSON archived from machine-client (DjinnWish) submissions; null otherwise
    system_info_json = db.Column(db.Text, nullable=True)
    # Merge-to-ticket: when set, this ticket is hidden from all lists/counts
    # and appears only under its parent ticket's "Merged Tickets" section
    merged_into_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=True)
    merged_at = db.Column(db.DateTime, nullable=True)

    @property
    def age_hours(self) -> float:
        # For closed tickets, calculate age from opened to closed, not to current time
        end_time = self.closed_at if self.is_closed and self.closed_at else datetime.utcnow()
        delta = (end_time - self.created_at)
        return round(delta.total_seconds() / 3600.0, 2)

    @property
    def age_days(self) -> float:
        # For closed tickets, calculate age from opened to closed, not to current time
        end_time = self.closed_at if self.is_closed and self.closed_at else datetime.utcnow()
        delta = (end_time - self.created_at)
        return round(delta.total_seconds() / 86400.0, 2)

    @property
    def is_closed(self) -> bool:
        """Check if this ticket's status is a closed status."""
        return TicketStatus.is_status_closed(self.status)

    @property
    def is_snoozed(self) -> bool:
        try:
            return bool(self.snoozed_until and self.snoozed_until > datetime.utcnow())
        except Exception:
            return False

    def bump_new_to_open(self) -> bool:
        """Auto-transition 'new' -> 'open' when a technician acts on the ticket.

        Returns True if the status changed; the caller commits. No-op unless
        the current status key is 'new' and an 'open' TicketStatus row exists
        (admin renames/deletes of either key silently disable this). Both
        statuses are non-closed, so closed_at is untouched. Never raises.
        """
        try:
            if (self.status or '').strip().lower() != 'new':
                return False
            if TicketStatus.query.filter_by(name='open').first() is None:
                return False
            self.status = 'open'
            return True
        except Exception:
            return False

    # Notes relationship
    notes = db.relationship('TicketNote', backref='ticket', lazy='dynamic', cascade='all, delete-orphan')
    attachments = db.relationship('TicketAttachment', backref='ticket', cascade='all, delete-orphan')
    processes = db.relationship('TicketProcess', backref='ticket', cascade='all, delete-orphan')
    # Simple tasks attached directly to a ticket
    tasks = db.relationship('TicketTask', backref='ticket', cascade='all, delete-orphan', order_by='TicketTask.position')
    # Relationship to asset (defined after Asset model as well)
    asset = db.relationship('Asset', foreign_keys=[asset_id])
    # Relationship to creator (tech)
    created_by = db.relationship('User', foreign_keys=[created_by_user_id])
    # Tags (many-to-many)
    tags = db.relationship('Tag', secondary=ticket_tags, back_populates='tickets')
    # Tickets merged under this one (self-referential; no cascade delete —
    # children are released, not deleted, when the parent goes away)
    merged_children = db.relationship(
        'Ticket',
        backref=db.backref('merged_into', remote_side=[id]),
        foreign_keys=[merged_into_id],
        order_by='Ticket.merged_at',
    )
    # AI assistant artifacts (one row each)
    ai_embedding = db.relationship('TicketEmbedding', backref='ticket', uselist=False, cascade='all, delete-orphan')
    ai_suggestion = db.relationship('TicketAISuggestion', backref='ticket', uselist=False, cascade='all, delete-orphan')


class TicketNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    content = db.Column(db.Text, nullable=False)
    # True if the note was marked private by a tech. Notes created from incoming emails are considered received.
    is_private = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Relationship to author (tech) for display
    author = db.relationship('User', foreign_keys=[author_id])


class TicketAttachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=False)
    filename = db.Column(db.String(500), nullable=False)
    content_type = db.Column(db.String(200), nullable=True)
    # relative path under static for serving, e.g., attachments/<ticket_id>/<filename>
    static_path = db.Column(db.String(1000), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class TicketEmbedding(db.Model):
    """Vector embedding of a ticket's text, computed by the configured AI server.

    vector holds L2-normalized float32 values packed little-endian, so cosine
    similarity between two rows reduces to a dot product.
    """
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=False, unique=True, index=True)
    model = db.Column(db.String(100), nullable=True)
    # sha256 of the embedded text; unchanged hash means no re-embed needed
    content_hash = db.Column(db.String(64), nullable=True)
    vector = db.Column(db.LargeBinary, nullable=False)
    dim = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TicketAISuggestion(db.Model):
    """AI-drafted reply for a ticket; the tech approves, edits, or dismisses it."""
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=False, unique=True, index=True)
    # sanitized HTML (sanitize_rich_text applied before storage)
    content = db.Column(db.Text, nullable=True)
    model = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(20), nullable=False, default='pending')  # pending|ready|failed|dismissed
    error = db.Column(db.Text, nullable=True)
    # JSON list of {"id": int, "name": str} documents fed into this suggestion
    sources_json = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# --- Simple per-ticket Tasks ---
class TicketTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=False)
    # Optional grouping name for the batch/list this task belongs to
    list_name = db.Column(db.String(200), nullable=True)
    label = db.Column(db.String(300), nullable=False)
    assigned_tech_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    assigned_tech = db.relationship('User', foreign_keys=[assigned_tech_id])
    position = db.Column(db.Integer, nullable=False, default=0)
    checked = db.Column(db.Boolean, default=False)
    checked_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    checked_by = db.relationship('User', foreign_keys=[checked_by_user_id])
    checked_at = db.Column(db.DateTime, nullable=True)
    # Optional link to Asset (used for spot check tasks)
    asset_id = db.Column(db.Integer, db.ForeignKey('asset.id'), nullable=True)
    asset = db.relationship('Asset', foreign_keys=[asset_id])
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# --- Scheduled Tickets (Admin-defined recurring tickets) ---
class ScheduledTicket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    subject = db.Column(db.String(300), nullable=False)
    body = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default='new')  # default status for created tickets
    priority = db.Column(db.String(20), default='medium')
    assignee_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    assignee = db.relationship('User', foreign_keys=[assignee_id])
    # Newline-separated tasks to create with each instance
    tasks_text = db.Column(db.Text, nullable=True)
    # Scheduling
    schedule_type = db.Column(db.String(20), nullable=False, default='daily')  # daily, weekly, monthly
    day_of_week = db.Column(db.Integer, nullable=True)  # 0=Mon .. 6=Sun
    day_of_month = db.Column(db.Integer, nullable=True)  # 1..31
    schedule_time = db.Column(db.String(5), nullable=True)  # HH:MM (24h) local time
    active = db.Column(db.Boolean, default=True)
    last_run_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# --- Automated Reports (Admin-defined scheduled reports) ---
class Report(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    report_type = db.Column(db.String(50), nullable=False, default='executive')
    is_active = db.Column(db.Boolean, default=True)
    # Scheduling
    schedule_frequency = db.Column(db.String(20), nullable=False, default='weekly')  # daily, weekly, monthly
    schedule_time = db.Column(db.String(5), nullable=False, default='07:00')  # HH:MM (24h) local time
    schedule_day_of_week = db.Column(db.Integer, nullable=True)  # 0=Mon .. 6=Sun (for weekly)
    schedule_day_of_month = db.Column(db.Integer, nullable=True)  # 1..28 (for monthly)
    # Recipients
    recipient_user_ids = db.Column(db.Text, nullable=True)  # JSON array of User.id
    recipient_emails = db.Column(db.Text, nullable=True)  # comma-separated free-text emails
    # Content toggles (JSON dict of section keys -> bool)
    sections = db.Column(db.Text, nullable=True)
    # Run tracking
    last_run_at = db.Column(db.DateTime, nullable=True)
    last_run_status = db.Column(db.String(20), nullable=True)  # 'success' | 'partial' | 'error'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    runs = db.relationship('ReportRun', backref='report', cascade='all, delete-orphan', order_by='ReportRun.run_at.desc()')


class ReportRun(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    report_id = db.Column(db.Integer, db.ForeignKey('report.id', ondelete='CASCADE'), nullable=False)
    run_at = db.Column(db.DateTime, default=datetime.utcnow)
    triggered_by = db.Column(db.String(20), nullable=False, default='schedule')  # 'schedule' | 'manual'
    recipients_count = db.Column(db.Integer, default=0)
    success = db.Column(db.Boolean, default=False)
    error = db.Column(db.Text, nullable=True)


# --- Process Templates ---
class ProcessTemplate(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, unique=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    items = db.relationship('ProcessTemplateItem', backref='template', cascade='all, delete-orphan', order_by='ProcessTemplateItem.position')


class ProcessTemplateItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey('process_template.id'), nullable=False)
    # type: 'text' or 'checkbox'
    type = db.Column(db.String(20), nullable=False, default='checkbox')
    label = db.Column(db.String(300), nullable=False)
    assigned_tech_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    assigned_tech = db.relationship('User', foreign_keys=[assigned_tech_id])
    position = db.Column(db.Integer, nullable=False, default=0)


# --- Ticket-attached Process Instances ---
class TicketProcess(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=False)
    template_id = db.Column(db.Integer, db.ForeignKey('process_template.id'), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    template = db.relationship('ProcessTemplate')
    items = db.relationship('TicketProcessItem', backref='ticket_process', cascade='all, delete-orphan', order_by='TicketProcessItem.position')


class TicketProcessItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_process_id = db.Column(db.Integer, db.ForeignKey('ticket_process.id'), nullable=False)
    type = db.Column(db.String(20), nullable=False, default='checkbox')
    label = db.Column(db.String(300), nullable=False)
    assigned_tech_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    assigned_tech = db.relationship('User', foreign_keys=[assigned_tech_id])
    position = db.Column(db.Integer, nullable=False, default=0)
    # For checkbox: checked; For text: value
    checked = db.Column(db.Boolean, default=False)
    text_value = db.Column(db.Text, nullable=True)
    # Audit: who checked and when (for checkbox items)
    checked_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    checked_by = db.relationship('User', foreign_keys=[checked_by_user_id])
    checked_at = db.Column(db.DateTime, nullable=True)


# --- Email settings ---
class AllowedDomain(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    domain = db.Column(db.String(255), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class DenyFilter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    phrase = db.Column(db.String(255), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# --- Contacts (requesters) ---
class Contact(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    notes = db.Column(db.Text, nullable=True)
    inventory_url = db.Column(db.String(1000), nullable=True)
    ninja_url = db.Column(db.String(1000), nullable=True)
    # Manager relationship for approval workflows
    manager_id = db.Column(db.Integer, db.ForeignKey('contact.id'), nullable=True)
    manager = db.relationship('Contact', remote_side='Contact.id', backref='direct_reports', foreign_keys=[manager_id])
    # Archived users are hidden from the default users list
    archived = db.Column(db.Boolean, default=False)
    # AD Password expiry tracking
    password_expires_days = db.Column(db.Integer, nullable=True)  # Days until password expires (null=not checked, -1=never expires, negative=expired)
    password_checked_at = db.Column(db.DateTime, nullable=True)  # Last time password expiry was checked
    password_notification_sent_at = db.Column(db.DateTime, nullable=True)  # Last time a password expiry notification was sent
    last_notification_days_before = db.Column(db.Integer, nullable=True)  # The days_before tier of the last notification sent
    ad_disabled = db.Column(db.Boolean, nullable=True)  # True if AD account is disabled, None if not checked
    # Desktop-client check-in tracking (HelpfulDjinn Client posts a status on an interval)
    last_checkin_at = db.Column(db.DateTime, nullable=True)  # UTC of the most recent check-in
    last_checkin_computer = db.Column(db.String(255), nullable=True)  # reported computer name
    last_checkin_ip = db.Column(db.String(255), nullable=True)  # reported IP address(es)
    last_checkin_client_version = db.Column(db.String(50), nullable=True)  # reported client version
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# --- Approval Requests ---
class ApprovalRequest(db.Model):
    """Tracks approval requests sent to managers for order items on tickets."""
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=False)
    requester_contact_id = db.Column(db.Integer, db.ForeignKey('contact.id'), nullable=True)
    manager_contact_id = db.Column(db.Integer, db.ForeignKey('contact.id'), nullable=False)
    requesting_tech_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    # Status: pending, approved, denied
    status = db.Column(db.String(20), default='pending')
    # Note from tech when requesting approval
    request_note = db.Column(db.Text, nullable=True)
    # Snapshot of items at time of request (short description)
    items_snapshot = db.Column(db.Text, nullable=True)
    # Response note from manager (if any)
    response_note = db.Column(db.Text, nullable=True)
    responded_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    ticket = db.relationship('Ticket', backref=db.backref('approval_requests', lazy='dynamic'))
    requester_contact = db.relationship('Contact', foreign_keys=[requester_contact_id])
    manager_contact = db.relationship('Contact', foreign_keys=[manager_contact_id])
    requesting_tech = db.relationship('User', foreign_keys=[requesting_tech_id])


class Project(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), default='open')  # open, closed
    closed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Tickets associated to this project
    tickets = db.relationship('Ticket', backref='project', cascade='all, delete-orphan', lazy='dynamic')


# --- Vendors ---
class Vendor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    company_name = db.Column(db.String(255), unique=True, nullable=False)
    contact_name = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(255), nullable=True)
    address = db.Column(db.Text, nullable=True)
    phone = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Company(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)
    address = db.Column(db.Text, nullable=True)
    city = db.Column(db.String(120), nullable=True)
    state = db.Column(db.String(50), nullable=True)
    zip_code = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ShippingLocation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)  # e.g., Main Warehouse, Office
    address = db.Column(db.Text, nullable=True)
    city = db.Column(db.String(120), nullable=True)
    state = db.Column(db.String(50), nullable=True)
    zip_code = db.Column(db.String(20), nullable=True)
    # Location-specific sales tax rate as a decimal (e.g., 0.075 for 7.5%)
    tax_rate = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# --- Orders / Purchase Orders ---
class PurchaseOrder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    po_number = db.Column(db.String(50), unique=True, nullable=True)  # assigned when sent
    # Optional vendor quote reference for this PO (editable in draft)
    quote_number = db.Column(db.String(120), nullable=True)
    vendor_name = db.Column(db.String(255), nullable=False)
    vendor_id = db.Column(db.Integer, db.ForeignKey('vendor.id'), nullable=True)
    # Snapshot of vendor details at time of order to preserve history
    vendor_contact_name = db.Column(db.String(255), nullable=True)
    vendor_email = db.Column(db.String(255), nullable=True)
    vendor_address = db.Column(db.Text, nullable=True)
    vendor_phone = db.Column(db.String(100), nullable=True)
    # Company this PO is for
    company_id = db.Column(db.Integer, db.ForeignKey('company.id'), nullable=True)
    company_name = db.Column(db.String(255), nullable=True)
    company_address = db.Column(db.Text, nullable=True)
    company_city = db.Column(db.String(120), nullable=True)
    company_state = db.Column(db.String(50), nullable=True)
    company_zip = db.Column(db.String(20), nullable=True)
    # Shipping destination for this PO
    shipping_location_id = db.Column(db.Integer, db.ForeignKey('shipping_location.id'), nullable=True)
    shipping_name = db.Column(db.String(255), nullable=True)
    shipping_address = db.Column(db.Text, nullable=True)
    shipping_city = db.Column(db.String(120), nullable=True)
    shipping_state = db.Column(db.String(50), nullable=True)
    shipping_zip = db.Column(db.String(20), nullable=True)
    # Optional shipping cost for this PO
    shipping_cost = db.Column(db.Float, default=0.0)
    status = db.Column(db.String(30), default='draft')  # draft, sent, partially_received, complete, canceled
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    ordered_at = db.Column(db.DateTime, nullable=True)  # when status transitioned to sent
    notes = db.Column(db.Text, nullable=True)

    # Relationship to items
    items = db.relationship('OrderItem', backref='purchase_order', cascade='all, delete-orphan')
    # PoNote entries (separate from the simple notes text field)
    po_notes = db.relationship('PoNote', backref='purchase_order', lazy='dynamic', cascade='all, delete-orphan')
    vendor = db.relationship('Vendor', backref=db.backref('purchase_orders', lazy='dynamic'))
    company = db.relationship('Company', backref=db.backref('purchase_orders', lazy='dynamic'))
    shipping_location = db.relationship('ShippingLocation', backref=db.backref('purchase_orders', lazy='dynamic'))

    @property
    def total_subtotal(self):
        return sum((it.est_unit_cost or 0) * (it.quantity or 0) for it in self.items)

    @property
    def effective_tax_rate(self) -> float:
        try:
            return float(getattr(self.shipping_location, 'tax_rate', 0.0) or 0.0)
        except Exception:
            return 0.0

    @property
    def total_tax(self) -> float:
        try:
            return (self.total_subtotal or 0.0) * (self.effective_tax_rate or 0.0)
        except Exception:
            return 0.0

    @property
    def total_shipping(self) -> float:
        try:
            return float(self.shipping_cost or 0.0)
        except Exception:
            return 0.0

    @property
    def grand_total(self) -> float:
        return (self.total_subtotal or 0.0) + (self.total_tax or 0.0) + (self.total_shipping or 0.0)


class OrderItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.String(500), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    target_vendor = db.Column(db.String(255), nullable=True)
    source_url = db.Column(db.String(1000), nullable=True)
    est_unit_cost = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(30), default='planned')  # planned, ordered, backordered, received, canceled
    dept_code = db.Column(db.String(100), nullable=True)
    needed_by = db.Column(db.DateTime, nullable=True)
    needed_by_text = db.Column(db.String(20), nullable=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=True)
    po_id = db.Column(db.Integer, db.ForeignKey('purchase_order.id'), nullable=True)
    received_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Convenience link to ticket
    ticket = db.relationship('Ticket', backref=db.backref('order_items', lazy='dynamic'))

    def mark_received(self):
        self.status = 'received'
        self.received_at = datetime.utcnow()


# --- PO Notes ---
class PoNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    po_id = db.Column(db.Integer, db.ForeignKey('purchase_order.id'), nullable=False)
    author_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    content = db.Column(db.Text, nullable=False)
    is_private = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Author relationship for display
    author = db.relationship('User', foreign_keys=[author_id])


# --- Documents ---
class DocumentCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('document_category.id'), nullable=True)
    position = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    documents = db.relationship('Document', backref='category', cascade='all, delete-orphan', lazy='dynamic')
    subcategories = db.relationship(
        'DocumentCategory',
        backref=db.backref('parent', remote_side=[id]),
        cascade='all, delete-orphan',
        lazy='select'
    )


class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('document_category.id'), nullable=False)
    name = db.Column(db.String(300), nullable=False)
    body = db.Column(db.Text, nullable=True)  # rich text / HTML allowed
    # Excluded documents are never embedded or fed to AI suggestions
    ai_excluded = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    ai_embedding = db.relationship('DocumentEmbedding', backref='document',
                                   uselist=False, cascade='all, delete-orphan')


class DocumentEmbedding(db.Model):
    """Vector embedding of a document (category + name + body), same format as TicketEmbedding."""
    id = db.Column(db.Integer, primary_key=True)
    document_id = db.Column(db.Integer, db.ForeignKey('document.id'), nullable=False, unique=True, index=True)
    model = db.Column(db.String(100), nullable=True)
    # sha256 of the embedded text; unchanged hash means no re-embed needed
    content_hash = db.Column(db.String(64), nullable=True)
    vector = db.Column(db.LargeBinary, nullable=False)
    dim = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DocumentFavorite(db.Model):
    __tablename__ = 'document_favorite'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'), nullable=False, index=True)
    document_id = db.Column(db.Integer, db.ForeignKey('document.id', ondelete='CASCADE'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('user_id', 'document_id', name='uq_user_document_favorite'),)


# --- Assets ---
class AssetCategory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AssetManufacturer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AssetCondition(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class AssetLocation(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Asset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    # Original ID from legacy system (CSV first column)
    source_id = db.Column(db.Integer, nullable=True, index=True)
    company = db.Column(db.String(255), nullable=True)
    name = db.Column(db.String(255), nullable=False)  # Asset Name
    asset_tag = db.Column(db.String(120), unique=True, nullable=True)
    model_name = db.Column(db.String(255), nullable=True)
    model_no = db.Column(db.String(255), nullable=True)
    category = db.Column(db.String(120), nullable=True)
    manufacturer = db.Column(db.String(120), nullable=True)
    serial_number = db.Column(db.String(255), nullable=True, index=True)
    purchased_at = db.Column(db.DateTime, nullable=True)
    cost = db.Column(db.Float, nullable=True)
    warranty_months = db.Column(db.Integer, nullable=True)
    warranty_expires = db.Column(db.DateTime, nullable=True)
    eol_date = db.Column(db.DateTime, nullable=True)
    current_value = db.Column(db.Float, nullable=True)
    fully_depreciated = db.Column(db.Boolean, default=False)
    supplier = db.Column(db.String(255), nullable=True)
    order_number = db.Column(db.String(120), nullable=True)
    location = db.Column(db.String(255), nullable=True)
    default_location = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(50), default='available')  # available, deployed, maintenance, retired, lost, archived
    notes = db.Column(db.Text, nullable=True)
    specs = db.Column(db.Text, nullable=True)
    physical_condition = db.Column(db.String(120), nullable=True)
    end_of_life_text = db.Column(db.String(255), nullable=True)
    url = db.Column(db.String(1000), nullable=True)
    # Assignment (checkout) to Contact
    assigned_contact_id = db.Column(db.Integer, db.ForeignKey('contact.id'), nullable=True, index=True)
    checkout_date = db.Column(db.DateTime, nullable=True)
    expected_checkin_date = db.Column(db.DateTime, nullable=True)
    last_checkin_date = db.Column(db.DateTime, nullable=True)
    last_audit = db.Column(db.DateTime, nullable=True)
    next_audit_date = db.Column(db.DateTime, nullable=True)
    last_spot_check = db.Column(db.DateTime, nullable=True)  # When this asset was last verified in a spot check
    deleted_flag = db.Column(db.Boolean, default=False)
    created_at_legacy = db.Column(db.DateTime, nullable=True)
    updated_at_legacy = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # Link back to originating Purchase Order / Order Item if created from a PO line
    purchase_order_id = db.Column(db.Integer, db.ForeignKey('purchase_order.id'), nullable=True)
    order_item_id = db.Column(db.Integer, db.ForeignKey('order_item.id'), nullable=True)

    assigned_contact = db.relationship('Contact', foreign_keys=[assigned_contact_id])
    # Tickets associated with this asset (Ticket.asset_id FK). Using dynamic for further filtering.
    tickets = db.relationship('Ticket', foreign_keys='Ticket.asset_id', lazy='dynamic', overlaps="asset")
    purchase_order = db.relationship('PurchaseOrder', foreign_keys=[purchase_order_id])
    order_item = db.relationship('OrderItem', foreign_keys=[order_item_id])
    # Tags (many-to-many)
    tags = db.relationship('Tag', secondary=asset_tags, back_populates='assets')

    def checkout(self, contact, expected=None):
        from datetime import datetime as _dt
        self.assigned_contact = contact
        self.checkout_date = _dt.utcnow()
        self.expected_checkin_date = expected
        # If status currently available/deployable, mark deployed
        if self.status in (None, '', 'available', 'deployable', 'Active (deployable)'):
            self.status = 'deployed'

    def checkin(self):
        from datetime import datetime as _dt
        self.last_checkin_date = _dt.utcnow()
        self.assigned_contact = None
        self.assigned_contact_id = None
        self.checkout_date = None
        self.expected_checkin_date = None
        # Only set to available if not retired
        if self.status not in ('retired', 'archived'):
            self.status = 'available'

    @property
    def is_checked_out(self):
        return self.assigned_contact_id is not None


class AssetAudit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('asset.id'), index=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    action = db.Column(db.String(50), nullable=False)  # edit, checkout, checkin, assign, unassign, status_change
    field = db.Column(db.String(120), nullable=True)   # specific field changed (for edits)
    old_value = db.Column(db.Text, nullable=True)
    new_value = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    asset = db.relationship('Asset', foreign_keys=[asset_id])
    user = db.relationship('User', foreign_keys=[user_id])

    def to_dict(self):
        return {
            'id': self.id,
            'asset_id': self.asset_id,
            'user_id': self.user_id,
            'action': self.action,
            'field': self.field,
            'old_value': self.old_value,
            'new_value': self.new_value,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# --- Email Poll Logs ---
class EmailCheck(db.Model):
    __tablename__ = 'email_checks'
    id = db.Column(db.Integer, primary_key=True)
    checked_at = db.Column(db.DateTime, default=datetime.utcnow, index=True, nullable=False)
    new_count = db.Column(db.Integer, default=0, nullable=False)  # number of unread messages fetched

    entries = db.relationship(
        'EmailCheckEntry',
        backref='check',
        cascade='all, delete-orphan',
        lazy='selectin'
    )


class EmailCheckEntry(db.Model):
    __tablename__ = 'email_check_entries'
    id = db.Column(db.Integer, primary_key=True)
    check_id = db.Column(db.Integer, db.ForeignKey('email_checks.id'), nullable=False, index=True)
    sender = db.Column(db.String(255))
    subject = db.Column(db.String(500))
    action = db.Column(db.String(50))  # 'new_ticket' | 'append_ticket' | 'filtered_deny' | 'filtered_domain' | 'duplicate'
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=True)
    note = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


# --- Outgoing Email Logs ---
class OutgoingEmail(db.Model):
    """Tracks all outgoing emails sent via the system."""
    __tablename__ = 'outgoing_emails'
    id = db.Column(db.Integer, primary_key=True)
    to_address = db.Column(db.String(255), nullable=False)
    to_name = db.Column(db.String(255), nullable=True)
    subject = db.Column(db.String(500), nullable=False)
    # Category of email: 'ticket_note', 'ticket_assigned', 'ticket_watch', 'password_expiry', 'approval_request', 'po_sent', 'other'
    category = db.Column(db.String(50), default='other')
    # Related ticket ID if applicable
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=True)
    # Whether the send was successful
    success = db.Column(db.Boolean, default=True)
    # Optional error message if failed
    error_message = db.Column(db.String(500), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    # Relationship to ticket
    ticket = db.relationship('Ticket', foreign_keys=[ticket_id])


class EmailOutbox(db.Model):
    """Queued outbound email drained by the scheduler (see services/mailer.py).

    Rows are claimed atomically (status pending/failed -> sending) so the
    scheduler process and the dev-mode drain thread can never double-send.
    """
    __tablename__ = 'email_outbox'
    id = db.Column(db.Integer, primary_key=True)
    # JSON list of recipient addresses
    to_json = db.Column(db.Text, nullable=False)
    to_name = db.Column(db.String(255), nullable=True)
    subject = db.Column(db.String(500), nullable=False)
    html_body = db.Column(db.Text, nullable=True)
    # JSON list of Graph attachment dicts (name/contentType/contentBytes/...)
    attachments_json = db.Column(db.Text, nullable=True)
    save_to_sent = db.Column(db.Boolean, default=True)
    category = db.Column(db.String(50), default='other')
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=True)
    # pending | sending | sent | failed | dead
    status = db.Column(db.String(20), default='pending', index=True)
    attempts = db.Column(db.Integer, default=0)
    last_error = db.Column(db.String(1000), nullable=True)
    next_attempt_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    sent_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    ticket = db.relationship('Ticket', foreign_keys=[ticket_id])

    @property
    def recipients(self):
        try:
            return json.loads(self.to_json or '[]')
        except Exception:
            return []


# --- Email Templates ---
class EmailTemplate(db.Model):
    __tablename__ = 'email_templates'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    subject = db.Column(db.String(300), nullable=False)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    notifications = db.relationship(
        'PasswordExpiryNotification',
        backref='template',
        cascade='all, delete-orphan',
        lazy='selectin'
    )


# --- Password Expiry Notifications ---
class PasswordExpiryNotification(db.Model):
    __tablename__ = 'password_expiry_notifications'
    id = db.Column(db.Integer, primary_key=True)
    days_before = db.Column(db.Integer, nullable=False)  # Days before expiry to send notification
    template_id = db.Column(db.Integer, db.ForeignKey('email_templates.id'), nullable=False)
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# --- Ticket Statuses ---
class TicketStatus(db.Model):
    """Configurable ticket status options."""
    __tablename__ = 'ticket_statuses'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)  # internal key: open, in_progress, closed, etc.
    label = db.Column(db.String(100), nullable=False)  # display label: "Open", "In Progress", etc.
    color = db.Column(db.String(20), default='secondary')  # Bootstrap color: success, warning, danger, etc.
    is_closed = db.Column(db.Boolean, default=False)  # If True, ticket is considered closed
    position = db.Column(db.Integer, default=0)  # For ordering in dropdowns
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    @staticmethod
    def get_default_statuses():
        """Return default statuses if none exist in database."""
        return [
            {'name': 'new', 'label': 'New', 'color': 'info', 'is_closed': False, 'position': 0},
            {'name': 'open', 'label': 'Open', 'color': 'success', 'is_closed': False, 'position': 1},
            {'name': 'in_progress', 'label': 'In Progress', 'color': 'warning', 'is_closed': False, 'position': 2},
            {'name': 'closed', 'label': 'Closed', 'color': 'danger', 'is_closed': True, 'position': 3},
        ]

    @staticmethod
    def ensure_defaults():
        """Create default statuses if none exist, and ensure 'new' status exists."""
        if TicketStatus.query.count() == 0:
            for s in TicketStatus.get_default_statuses():
                status = TicketStatus(
                    name=s['name'],
                    label=s['label'],
                    color=s['color'],
                    is_closed=s['is_closed'],
                    position=s['position']
                )
                db.session.add(status)
            db.session.commit()
        else:
            # Ensure 'new' status exists for existing databases
            if not TicketStatus.query.filter_by(name='new').first():
                # Shift existing positions to make room for 'new' at position 0
                for s in TicketStatus.query.all():
                    s.position = s.position + 1
                new_status = TicketStatus(
                    name='new',
                    label='New',
                    color='info',
                    is_closed=False,
                    position=0
                )
                db.session.add(new_status)
                db.session.commit()

    @staticmethod
    def get_choices():
        """Return list of (name, label) tuples for form SelectField."""
        statuses = TicketStatus.query.order_by(TicketStatus.position).all()
        if not statuses:
            return [('new', 'New'), ('open', 'Open'), ('in_progress', 'In Progress'), ('closed', 'Closed')]
        return [(s.name, s.label) for s in statuses]

    @staticmethod
    def get_by_name(name):
        """Get a status by its name/key."""
        return TicketStatus.query.filter_by(name=name).first()

    @staticmethod
    def is_status_closed(status_name):
        """Check if a status name represents a closed ticket."""
        status = TicketStatus.query.filter_by(name=status_name).first()
        if status:
            return status.is_closed
        # Fallback for legacy 'closed' status
        return status_name == 'closed'


# --- Ticket Watchers ---
class TicketWatcher(db.Model):
    """Tracks techs watching tickets for update notifications."""
    __tablename__ = 'ticket_watchers'
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    ticket = db.relationship('Ticket', backref=db.backref('watchers', lazy='dynamic', cascade='all, delete-orphan'))
    user = db.relationship('User', backref=db.backref('watched_tickets', lazy='dynamic'))

    __table_args__ = (
        db.UniqueConstraint('ticket_id', 'user_id', name='uq_ticket_watcher'),
    )
