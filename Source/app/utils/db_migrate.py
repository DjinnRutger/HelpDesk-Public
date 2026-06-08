from sqlalchemy import text


def ensure_ticket_columns(engine):
    required = {
        'external_id': "TEXT",
        'requester': "TEXT",
        'requester_name': "TEXT",
        'requester_email': "TEXT",
        'body': "TEXT",
        'status': "TEXT",
        'priority': "TEXT",
        'assignee_id': "INTEGER",
        'co_assignee_id': "INTEGER",
        'created_at': "DATETIME",
        'updated_at': "DATETIME",
        'closed_at': "DATETIME",
    'source': "TEXT",
    'project_id': "INTEGER",
    'project_position': "INTEGER",
    'asset_id': "INTEGER",
    'snoozed_until': "DATETIME",
    'created_by_user_id': "INTEGER",
    'system_info_json': "TEXT",
    }

    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info('ticket')")).fetchall()
        existing = {row[1] for row in rows}
        for col, coltype in required.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE ticket ADD COLUMN {col} {coltype}"))
        conn.commit()


def ensure_user_columns(engine):
    required = {
        'theme': "TEXT",
    'tickets_view_pref': "TEXT",
        'signature': "TEXT",
    }

    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info('user')")).fetchall()
        existing = {row[1] for row in rows}
        for col, coltype in required.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE user ADD COLUMN {col} {coltype}"))
        conn.commit()


def ensure_api_token_table(engine):
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='api_token'"))
        exists = rows.fetchone() is not None
        if not exists:
            conn.execute(text(
                """
                CREATE TABLE api_token (
                    id INTEGER PRIMARY KEY,
                    label TEXT,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at DATETIME,
                    last_used_at DATETIME,
                    revoked BOOLEAN DEFAULT 0 NOT NULL
                )
                """
            ))
        conn.commit()


def ensure_project_table(engine):
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='project'"))
        exists = rows.fetchone() is not None
        if not exists:
            conn.execute(text(
                """
                CREATE TABLE project (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    status TEXT,
                    closed_at DATETIME,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            ))
        else:
            # Ensure new columns exist if table already present
            info = conn.execute(text("PRAGMA table_info('project')")).fetchall()
            existing = {row[1] for row in info}
            if 'status' not in existing:
                conn.execute(text("ALTER TABLE project ADD COLUMN status TEXT"))
            if 'closed_at' not in existing:
                conn.execute(text("ALTER TABLE project ADD COLUMN closed_at DATETIME"))
        conn.commit()


def ensure_ticket_process_item_columns(engine):
    required = {
        'checked_by_user_id': 'INTEGER',
        'checked_at': 'DATETIME',
    }
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info('ticket_process_item')")).fetchall()
        existing = {row[1] for row in rows}
        for col, coltype in required.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE ticket_process_item ADD COLUMN {col} {coltype}"))
        conn.commit()


def ensure_ticket_note_columns(engine):
    required = {
        'is_private': 'BOOLEAN',
    }
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info('ticket_note')")).fetchall()
        existing = {row[1] for row in rows}
        for col, coltype in required.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE ticket_note ADD COLUMN {col} {coltype}"))
        conn.commit()


def ensure_po_note_table(engine):
    """Ensure the po_note table exists with required columns (including is_private).
    Columns: id, po_id, author_id, content, is_private, created_at
    """
    with engine.connect() as conn:
        # Create if missing
        exists = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='po_note'"))\
            .fetchone() is not None
        if not exists:
            conn.execute(text(
                """
                CREATE TABLE po_note (
                    id INTEGER PRIMARY KEY,
                    po_id INTEGER NOT NULL,
                    author_id INTEGER,
                    content TEXT NOT NULL,
                    is_private BOOLEAN,
                    created_at DATETIME
                )
                """
            ))
            conn.commit()
            return
        # Table exists: ensure columns added in upgrades
        info = conn.execute(text("PRAGMA table_info('po_note')")).fetchall()
        existing = {row[1] for row in info}
        required = {
            'po_id': 'INTEGER',
            'author_id': 'INTEGER',
            'content': 'TEXT',
            'is_private': 'BOOLEAN',
            'created_at': 'DATETIME',
        }
        for col, coltype in required.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE po_note ADD COLUMN {col} {coltype}"))
        conn.commit()


def ensure_ticket_task_table(engine):
    """Ensure ticket_task table exists and has required columns (including list_name)."""
    with engine.connect() as conn:
        # Does table exist?
        exists = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='ticket_task'"))\
            .fetchone() is not None
        if not exists:
            conn.execute(text(
                """
                CREATE TABLE ticket_task (
                    id INTEGER PRIMARY KEY,
                    ticket_id INTEGER NOT NULL,
                    list_name TEXT,
                    label TEXT NOT NULL,
                    assigned_tech_id INTEGER,
                    position INTEGER NOT NULL DEFAULT 0,
                    checked BOOLEAN NOT NULL DEFAULT 0,
                    checked_by_user_id INTEGER,
                    checked_at DATETIME,
                    asset_id INTEGER,
                    created_at DATETIME
                )
                """
            ))
            conn.commit()
            return
        # Table exists: ensure columns
        info = conn.execute(text("PRAGMA table_info('ticket_task')")).fetchall()
        existing = {row[1] for row in info}
        required = {
            'list_name': 'TEXT',
            'asset_id': 'INTEGER',
        }
        for col, coltype in required.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE ticket_task ADD COLUMN {col} {coltype}"))
        conn.commit()


def ensure_order_tables(engine):
    """Create purchase_order and order_item tables if they do not exist; add missing columns if added later."""
    with engine.connect() as conn:
        # purchase_order
        exists_po = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='purchase_order'")).fetchone() is not None
        if not exists_po:
            conn.execute(text(
                """
                CREATE TABLE purchase_order (
                    id INTEGER PRIMARY KEY,
                    po_number TEXT UNIQUE,
                    quote_number TEXT,
                    vendor_name TEXT NOT NULL,
                    vendor_id INTEGER,
                    status TEXT,
                    created_at DATETIME,
                    updated_at DATETIME,
                    ordered_at DATETIME,
                    notes TEXT
                )
                """
            ))
        else:
            # ensure vendor_id column
            info = conn.execute(text("PRAGMA table_info('purchase_order')")).fetchall()
            existing_po_cols = {row[1] for row in info}
            if 'vendor_id' not in existing_po_cols:
                conn.execute(text("ALTER TABLE purchase_order ADD COLUMN vendor_id INTEGER"))
            for col, ddl in [
                ('quote_number', 'TEXT'),
                ('vendor_contact_name', 'TEXT'),
                ('vendor_email', 'TEXT'),
                ('vendor_address', 'TEXT'),
                ('vendor_phone', 'TEXT'),
                ('company_id', 'INTEGER'),
                ('company_name', 'TEXT'),
                ('company_address', 'TEXT'),
                ('company_city', 'TEXT'),
                ('company_state', 'TEXT'),
                ('company_zip', 'TEXT'),
                ('shipping_location_id', 'INTEGER'),
                ('shipping_name', 'TEXT'),
                ('shipping_address', 'TEXT'),
                ('shipping_city', 'TEXT'),
                ('shipping_state', 'TEXT'),
                ('shipping_zip', 'TEXT'),
                ('shipping_cost', 'REAL'),
            ]:
                if col not in existing_po_cols:
                    conn.execute(text(f"ALTER TABLE purchase_order ADD COLUMN {col} {ddl}"))
        # order_item
        exists_item = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='order_item'")).fetchone() is not None
        if not exists_item:
            conn.execute(text(
                """
                CREATE TABLE order_item (
                    id INTEGER PRIMARY KEY,
                    description TEXT NOT NULL,
                    quantity INTEGER NOT NULL DEFAULT 1,
                    target_vendor TEXT,
                    source_url TEXT,
                    est_unit_cost REAL,
                    status TEXT,
                    dept_code TEXT,
                    needed_by DATETIME,
                    needed_by_text TEXT,
                    ticket_id INTEGER,
                    po_id INTEGER,
                    received_at DATETIME,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            ))
        else:
            info = conn.execute(text("PRAGMA table_info('order_item')")).fetchall()
            existing_item_cols = {row[1] for row in info}
            if 'needed_by_text' not in existing_item_cols:
                conn.execute(text("ALTER TABLE order_item ADD COLUMN needed_by_text TEXT"))
            if 'dept_code' not in existing_item_cols:
                conn.execute(text("ALTER TABLE order_item ADD COLUMN dept_code TEXT"))
        conn.commit()

def ensure_vendor_table(engine):
    with engine.connect() as conn:
        exists = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='vendor'")).fetchone() is not None
        if not exists:
            conn.execute(text(
                """
                CREATE TABLE vendor (
                    id INTEGER PRIMARY KEY,
                    company_name TEXT UNIQUE NOT NULL,
                    contact_name TEXT,
                    email TEXT,
                    address TEXT,
                    phone TEXT,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            ))
        conn.commit()

def ensure_company_shipping_tables(engine):
    with engine.connect() as conn:
        # company
        exists = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='company'")).fetchone() is not None
        if not exists:
            conn.execute(text(
                """
                CREATE TABLE company (
                    id INTEGER PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    address TEXT,
                    city TEXT,
                    state TEXT,
                    zip_code TEXT,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            ))
        # shipping_location
        exists = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='shipping_location'")).fetchone() is not None
        if not exists:
            conn.execute(text(
                """
                CREATE TABLE shipping_location (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    address TEXT,
                    city TEXT,
                    state TEXT,
                    zip_code TEXT,
                    tax_rate REAL DEFAULT 0.0,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            ))
        else:
            # Ensure tax_rate exists
            info = conn.execute(text("PRAGMA table_info('shipping_location')")).fetchall()
            existing = {row[1] for row in info}
            if 'tax_rate' not in existing:
                conn.execute(text("ALTER TABLE shipping_location ADD COLUMN tax_rate REAL DEFAULT 0.0"))
        conn.commit()


def ensure_documents_tables(engine):
    """Create document_category and document tables if missing; add parent_id for sub-categories."""
    from sqlalchemy import text
    with engine.connect() as conn:
        # document_category
        exists_cat = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='document_category'"))\
            .fetchone() is not None
        if not exists_cat:
            conn.execute(text(
                """
                CREATE TABLE document_category (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    parent_id INTEGER REFERENCES document_category(id),
                    created_at DATETIME
                )
                """
            ))
        else:
            # Add columns if upgrading from older versions
            info = conn.execute(text("PRAGMA table_info('document_category')")).fetchall()
            existing = {row[1] for row in info}
            if 'parent_id' not in existing:
                conn.execute(text("ALTER TABLE document_category ADD COLUMN parent_id INTEGER REFERENCES document_category(id)"))
            if 'position' not in existing:
                conn.execute(text("ALTER TABLE document_category ADD COLUMN position INTEGER NOT NULL DEFAULT 0"))
        # document
        exists_doc = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='document'"))\
            .fetchone() is not None
        if not exists_doc:
            conn.execute(text(
                """
                CREATE TABLE document (
                    id INTEGER PRIMARY KEY,
                    category_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    body TEXT,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            ))
        conn.commit()


def ensure_document_favorites_table(engine):
    """Create document_favorite table if missing (per-user document favorites)."""
    from sqlalchemy import text
    with engine.connect() as conn:
        exists = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='document_favorite'")).fetchone() is not None
        if not exists:
            conn.execute(text(
                """
                CREATE TABLE document_favorite (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    document_id INTEGER NOT NULL,
                    created_at DATETIME,
                    UNIQUE(user_id, document_id)
                )
                """
            ))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_document_favorite_user_id ON document_favorite(user_id)"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_document_favorite_document_id ON document_favorite(document_id)"))
        conn.commit()


def ensure_scheduled_tickets_table(engine):
    with engine.connect() as conn:
        exists = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='scheduled_ticket'")).fetchone() is not None
        if not exists:
            conn.execute(text(
                """
                CREATE TABLE scheduled_ticket (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    body TEXT,
                    status TEXT,
                    priority TEXT,
                    assignee_id INTEGER,
                    tasks_text TEXT,
                    schedule_type TEXT,
                    day_of_week INTEGER,
                    day_of_month INTEGER,
                    schedule_time TEXT,
                    active BOOLEAN,
                    last_run_at DATETIME,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            ))
        conn.commit()


def ensure_assets_table(engine):
    """Create asset table if missing; add newly introduced columns if upgrading."""
    from sqlalchemy import text
    with engine.connect() as conn:
        exists = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='asset'"))\
            .fetchone() is not None
        if not exists:
            conn.execute(text(
                """
                CREATE TABLE asset (
                    id INTEGER PRIMARY KEY,
                    source_id INTEGER,
                    company TEXT,
                    name TEXT NOT NULL,
                    asset_tag TEXT UNIQUE,
                    model_name TEXT,
                    model_no TEXT,
                    category TEXT,
                    manufacturer TEXT,
                    serial_number TEXT,
                    purchased_at DATETIME,
                    cost REAL,
                    warranty_months INTEGER,
                    warranty_expires DATETIME,
                    eol_date DATETIME,
                    current_value REAL,
                    fully_depreciated BOOLEAN,
                    supplier TEXT,
                    order_number TEXT,
                    location TEXT,
                    default_location TEXT,
                    status TEXT,
                    notes TEXT,
                    specs TEXT,
                    physical_condition TEXT,
                    end_of_life_text TEXT,
                    url TEXT,
                    assigned_contact_id INTEGER,
                    checkout_date DATETIME,
                    expected_checkin_date DATETIME,
                    last_checkin_date DATETIME,
                    last_audit DATETIME,
                    next_audit_date DATETIME,
                    deleted_flag BOOLEAN,
                    created_at_legacy DATETIME,
                    updated_at_legacy DATETIME,
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            ))
        else:
            info = conn.execute(text("PRAGMA table_info('asset')")).fetchall()
            existing = {row[1] for row in info}
            for col, ddl in [
                ('source_id', 'INTEGER'), ('company', 'TEXT'), ('asset_tag', 'TEXT'), ('model_name', 'TEXT'),
                ('model_no', 'TEXT'), ('category', 'TEXT'), ('manufacturer', 'TEXT'), ('serial_number', 'TEXT'),
                ('purchased_at', 'DATETIME'), ('cost', 'REAL'), ('warranty_months', 'INTEGER'),
                ('warranty_expires', 'DATETIME'), ('eol_date', 'DATETIME'), ('current_value', 'REAL'),
                ('fully_depreciated', 'BOOLEAN'), ('supplier', 'TEXT'), ('order_number', 'TEXT'), ('location', 'TEXT'),
                ('default_location', 'TEXT'), ('status', 'TEXT'), ('notes', 'TEXT'), ('specs', 'TEXT'),
                ('physical_condition', 'TEXT'), ('end_of_life_text', 'TEXT'), ('url', 'TEXT'),
                ('assigned_contact_id', 'INTEGER'), ('checkout_date', 'DATETIME'), ('expected_checkin_date', 'DATETIME'),
                ('last_checkin_date', 'DATETIME'), ('last_audit', 'DATETIME'), ('next_audit_date', 'DATETIME'),
                ('last_spot_check', 'DATETIME'),
                ('deleted_flag', 'BOOLEAN'), ('created_at_legacy', 'DATETIME'), ('updated_at_legacy', 'DATETIME'),
                ('created_at', 'DATETIME'), ('updated_at', 'DATETIME'),
                ('purchase_order_id', 'INTEGER'), ('order_item_id', 'INTEGER')
            ]:
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE asset ADD COLUMN {col} {ddl}"))
        conn.commit()


def ensure_asset_picklists(engine):
    """Create asset picklist tables (category/manufacturer/condition/location) if missing."""
    with engine.connect() as conn:
        for table, ddl in [
            ('asset_category', "CREATE TABLE asset_category (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, created_at DATETIME)"),
            ('asset_manufacturer', "CREATE TABLE asset_manufacturer (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, created_at DATETIME)"),
            ('asset_condition', "CREATE TABLE asset_condition (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, created_at DATETIME)"),
            ('asset_location', "CREATE TABLE asset_location (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, created_at DATETIME)"),
        ]:
            exists = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"), { 't': table }).fetchone() is not None
            if not exists:
                conn.execute(text(ddl))
        conn.commit()


def ensure_contact_columns(engine):
    """Ensure Contact table has manager_id, archived, and password expiry columns."""
    required = {
        'manager_id': 'INTEGER',
        'archived': 'BOOLEAN',
        'password_expires_days': 'INTEGER',
        'password_checked_at': 'DATETIME',
        'password_notification_sent_at': 'DATETIME',
        'last_notification_days_before': 'INTEGER',
        'ad_disabled': 'BOOLEAN',
        'last_checkin_at': 'DATETIME',
        'last_checkin_computer': 'TEXT',
        'last_checkin_ip': 'TEXT',
        'last_checkin_client_version': 'TEXT',
    }
    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info('contact')")).fetchall()
        existing = {row[1] for row in rows}
        for col, coltype in required.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE contact ADD COLUMN {col} {coltype}"))
        conn.commit()


def ensure_approval_request_table(engine):
    """Create ApprovalRequest table if it doesn't exist."""
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='approval_request'"))
        exists = rows.fetchone() is not None
        if not exists:
            conn.execute(text("""
                CREATE TABLE approval_request (
                    id INTEGER PRIMARY KEY,
                    ticket_id INTEGER NOT NULL,
                    requester_contact_id INTEGER,
                    manager_contact_id INTEGER NOT NULL,
                    requesting_tech_id INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',
                    request_note TEXT,
                    items_snapshot TEXT,
                    response_note TEXT,
                    responded_at DATETIME,
                    created_at DATETIME,
                    FOREIGN KEY (ticket_id) REFERENCES ticket(id),
                    FOREIGN KEY (requester_contact_id) REFERENCES contact(id),
                    FOREIGN KEY (manager_contact_id) REFERENCES contact(id),
                    FOREIGN KEY (requesting_tech_id) REFERENCES user(id)
                )
            """))
        else:
            # Check if items_snapshot column exists
            cols = conn.execute(text("PRAGMA table_info(approval_request)"))
            existing = {r[1] for r in cols.fetchall()}
            if 'items_snapshot' not in existing:
                conn.execute(text("ALTER TABLE approval_request ADD COLUMN items_snapshot TEXT"))
        conn.commit()


def ensure_tag_columns(engine):
    """Add newer columns to the tag table when upgrading from older versions."""
    required = {
        'keywords': 'TEXT',
    }
    with engine.connect() as conn:
        exists = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='tag'")).fetchone() is not None
        if not exists:
            return
        rows = conn.execute(text("PRAGMA table_info('tag')")).fetchall()
        existing = {row[1] for row in rows}
        for col, coltype in required.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE tag ADD COLUMN {col} {coltype}"))
        conn.commit()


def ensure_tags_tables(engine):
    """Create tag, ticket_tags, and asset_tags tables if they don't exist."""
    with engine.connect() as conn:
        # tag table
        exists = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='tag'")).fetchone() is not None
        if not exists:
            conn.execute(text(
                """
                CREATE TABLE tag (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    color TEXT,
                    parent_id INTEGER REFERENCES tag(id),
                    position INTEGER NOT NULL DEFAULT 0,
                    keywords TEXT,
                    created_at DATETIME
                )
                """
            ))
        # ticket_tags association table
        exists = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='ticket_tags'")).fetchone() is not None
        if not exists:
            conn.execute(text(
                """
                CREATE TABLE ticket_tags (
                    ticket_id INTEGER NOT NULL REFERENCES ticket(id) ON DELETE CASCADE,
                    tag_id INTEGER NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
                    PRIMARY KEY (ticket_id, tag_id)
                )
                """
            ))
        # asset_tags association table
        exists = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='asset_tags'")).fetchone() is not None
        if not exists:
            conn.execute(text(
                """
                CREATE TABLE asset_tags (
                    asset_id INTEGER NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
                    tag_id INTEGER NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
                    PRIMARY KEY (asset_id, tag_id)
                )
                """
            ))
        conn.commit()


def seed_default_tags(db):
    """Seed predefined IT helpdesk tags if none exist. Idempotent."""
    from ..models import Tag
    if Tag.query.count() > 0:
        return

    from datetime import datetime as _dt

    def _make(name, color=None, parent=None, pos=0, keywords=None):
        t = Tag(
            name=name,
            color=color,
            parent_id=parent.id if parent else None,
            position=pos,
            keywords=keywords,
            created_at=_dt.utcnow(),
        )
        db.session.add(t)
        db.session.flush()
        return t

    # Root categories
    hardware = _make('Hardware', 'primary', pos=0,
                     keywords='hardware, device, equipment, peripheral')
    software = _make('Software', 'success', pos=1,
                     keywords='software, application, program, app, install')
    network  = _make('Network', 'info', pos=2,
                     keywords='network, connection, connectivity, internet, offline')
    account  = _make('Account & Access', 'warning', pos=3,
                     keywords='account, access, login, credentials, permission')
    security = _make('Security', 'danger', pos=4,
                     keywords='security, threat, incident, suspicious, compromised')
    general  = _make('General', 'secondary', pos=5,
                     keywords='general, misc, question, other')

    # Hardware children
    hardware_children = [
        ('Laptop',            'laptop, notebook, portable, macbook, thinkpad'),
        ('Desktop',           'desktop, workstation, tower, pc, optiplex'),
        ('Monitor',           'monitor, screen, display, second screen, resolution'),
        ('Printer',           'printer, print, toner, ink, scan, jam'),
        ('Docking Station',   'dock, docking, station, thunderbolt, usb-c hub'),
        ('Keyboard / Mouse',  'keyboard, mouse, typing, clicking, wireless mouse'),
        ('Mobile Device',     'phone, mobile, iphone, android, tablet, ipad'),
        ('Server',            'server, vm, virtual machine, hyper-v, host'),
    ]
    for i, (name, kw) in enumerate(hardware_children):
        _make(name, parent=hardware, pos=i, keywords=kw)

    # Software children (with sub-children)
    m365    = _make('Microsoft 365', parent=software, pos=0,
                    keywords='m365, microsoft 365, o365, office 365, sharepoint')
    os_tag  = _make('Operating System', parent=software, pos=1,
                    keywords='os, operating system, boot, bsod, crash')
    _make('VPN / Remote Access', parent=software, pos=2,
          keywords='vpn, remote, rdp, forticlient, globalprotect, anyconnect')
    _make('Browser', parent=software, pos=3,
          keywords='browser, chrome, edge, firefox, safari, extension')
    # Microsoft 365 sub-children
    m365_children = [
        ('Teams',       'teams, meeting, chat, call, huddle'),
        ('Outlook',     'outlook, email, calendar, inbox, signature, mailbox'),
        ('Office Apps', 'word, excel, powerpoint, onedrive, onenote'),
    ]
    for i, (name, kw) in enumerate(m365_children):
        _make(name, parent=m365, pos=i, keywords=kw)
    # OS sub-children
    os_children = [
        ('Windows', 'windows, win10, win11, update, feature update'),
        ('macOS',   'mac, macos, osx, finder, ventura, sonoma'),
    ]
    for i, (name, kw) in enumerate(os_children):
        _make(name, parent=os_tag, pos=i, keywords=kw)

    # Network children
    network_children = [
        ('WiFi / Wireless',       'wifi, wireless, signal, ssid, access point'),
        ('Internet Connectivity', 'internet, connection, offline, down, latency, slow'),
    ]
    for i, (name, kw) in enumerate(network_children):
        _make(name, parent=network, pos=i, keywords=kw)

    # Account & Access children
    account_children = [
        ('Password Reset',       'password, reset, forgot, locked out, unlock'),
        ('New User Setup',       'new user, onboarding, new hire, setup, account creation'),
        ('Permissions / Access', 'permission, access denied, shared folder, group, role'),
        ('MFA / Two-Factor',     'mfa, 2fa, authenticator, duo, two-factor, token'),
    ]
    for i, (name, kw) in enumerate(account_children):
        _make(name, parent=account, pos=i, keywords=kw)

    # Security children
    security_children = [
        ('Virus / Malware',    'virus, malware, infected, ransomware, trojan'),
        ('Phishing',           'phishing, scam, suspicious email, spoof, impersonation'),
        ('Data Breach / Loss', 'breach, leak, data loss, exposed, compromised'),
    ]
    for i, (name, kw) in enumerate(security_children):
        _make(name, parent=security, pos=i, keywords=kw)

    # General children
    general_children = [
        ('Training Request',    'training, how to, tutorial, learn, documentation'),
        ('Procurement / Order', 'order, purchase, buy, request, quote, procurement'),
        ('Warranty / Repair',   'warranty, repair, broken, rma, replacement'),
    ]
    for i, (name, kw) in enumerate(general_children):
        _make(name, parent=general, pos=i, keywords=kw)

    db.session.commit()


def ensure_email_templates_tables(engine):
    """Create EmailTemplate and PasswordExpiryNotification tables if they don't exist."""
    with engine.connect() as conn:
        # Create email_templates table
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='email_templates'"))
        if not rows.fetchone():
            conn.execute(text("""
                CREATE TABLE email_templates (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """))
        
        # Create password_expiry_notifications table
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='password_expiry_notifications'"))
        if not rows.fetchone():
            conn.execute(text("""
                CREATE TABLE password_expiry_notifications (
                    id INTEGER PRIMARY KEY,
                    days_before INTEGER NOT NULL,
                    template_id INTEGER NOT NULL,
                    enabled BOOLEAN DEFAULT 1,
                    created_at DATETIME,
                    FOREIGN KEY (template_id) REFERENCES email_templates(id)
                )
            """))
        conn.commit()


def ensure_report_tables(engine):
    """Create report and report_run tables for the automated reports feature."""
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='report'"))
        if not rows.fetchone():
            conn.execute(text("""
                CREATE TABLE report (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    report_type TEXT NOT NULL DEFAULT 'executive',
                    is_active BOOLEAN DEFAULT 1,
                    schedule_frequency TEXT NOT NULL DEFAULT 'weekly',
                    schedule_time TEXT NOT NULL DEFAULT '07:00',
                    schedule_day_of_week INTEGER,
                    schedule_day_of_month INTEGER,
                    recipient_user_ids TEXT,
                    recipient_emails TEXT,
                    sections TEXT,
                    last_run_at DATETIME,
                    last_run_status TEXT,
                    created_at DATETIME,
                    updated_at DATETIME
                )
            """))
        else:
            info = conn.execute(text("PRAGMA table_info('report')")).fetchall()
            existing = {row[1] for row in info}
            required = {
                'name': 'TEXT',
                'description': 'TEXT',
                'report_type': "TEXT DEFAULT 'executive'",
                'is_active': 'BOOLEAN DEFAULT 1',
                'schedule_frequency': "TEXT DEFAULT 'weekly'",
                'schedule_time': "TEXT DEFAULT '07:00'",
                'schedule_day_of_week': 'INTEGER',
                'schedule_day_of_month': 'INTEGER',
                'recipient_user_ids': 'TEXT',
                'recipient_emails': 'TEXT',
                'sections': 'TEXT',
                'last_run_at': 'DATETIME',
                'last_run_status': 'TEXT',
                'created_at': 'DATETIME',
                'updated_at': 'DATETIME',
            }
            for col, coltype in required.items():
                if col not in existing:
                    conn.execute(text(f"ALTER TABLE report ADD COLUMN {col} {coltype}"))

        rows = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='report_run'"))
        if not rows.fetchone():
            conn.execute(text("""
                CREATE TABLE report_run (
                    id INTEGER PRIMARY KEY,
                    report_id INTEGER NOT NULL,
                    run_at DATETIME,
                    triggered_by TEXT NOT NULL DEFAULT 'schedule',
                    recipients_count INTEGER DEFAULT 0,
                    success BOOLEAN DEFAULT 0,
                    error TEXT,
                    FOREIGN KEY (report_id) REFERENCES report(id) ON DELETE CASCADE
                )
            """))
        conn.commit()
