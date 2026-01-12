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


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), default="tech")  # 'admin' or 'tech'
    is_active = db.Column(db.Boolean, default=True)
    theme = db.Column(db.String(20), default="light")  # 'light', 'dark', or custom keys
    tickets_view_pref = db.Column(db.String(40), default="any")  # 'any', 'me', 'me_or_unassigned'
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def get_id(self):
        return str(self.id)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class Ticket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(200), unique=True, nullable=True)
    subject = db.Column(db.String(300), nullable=False)
    # Legacy field kept for backwards-compatibility; use requester_name/requester_email going forward
    requester = db.Column(db.String(255), nullable=True)
    requester_name = db.Column(db.String(255), nullable=True)
    requester_email = db.Column(db.String(255), nullable=True)
    body = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default="open")  # open, in_progress, closed
    priority = db.Column(db.String(20), default="medium")  # low, medium, high
    assignee_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    assignee = db.relationship('User', foreign_keys=[assignee_id])
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

    @property
    def age_hours(self) -> float:
        # For closed tickets, calculate age from opened to closed, not to current time
        end_time = self.closed_at if self.status == 'closed' and self.closed_at else datetime.utcnow()
        delta = (end_time - self.created_at)
        return round(delta.total_seconds() / 3600.0, 2)

    @property
    def age_days(self) -> float:
        # For closed tickets, calculate age from opened to closed, not to current time
        end_time = self.closed_at if self.status == 'closed' and self.closed_at else datetime.utcnow()
        delta = (end_time - self.created_at)
        return round(delta.total_seconds() / 86400.0, 2)

    @property
    def is_snoozed(self) -> bool:
        try:
            return bool(self.snoozed_until and self.snoozed_until > datetime.utcnow())
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
    status = db.Column(db.String(50), default='open')  # default status for created tickets
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
    name = db.Column(db.String(200), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    documents = db.relationship('Document', backref='category', cascade='all, delete-orphan', lazy='dynamic')


class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    category_id = db.Column(db.Integer, db.ForeignKey('document_category.id'), nullable=False)
    name = db.Column(db.String(300), nullable=False)
    body = db.Column(db.Text, nullable=True)  # rich text / HTML allowed
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
