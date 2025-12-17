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
        'created_at': "DATETIME",
        'updated_at': "DATETIME",
        'closed_at': "DATETIME",
    'source': "TEXT",
    'project_id': "INTEGER",
    'project_position': "INTEGER",
    'asset_id': "INTEGER",
    'snoozed_until': "DATETIME",
    'created_by_user_id': "INTEGER",
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
    }

    with engine.connect() as conn:
        rows = conn.execute(text("PRAGMA table_info('user')")).fetchall()
        existing = {row[1] for row in rows}
        for col, coltype in required.items():
            if col not in existing:
                conn.execute(text(f"ALTER TABLE user ADD COLUMN {col} {coltype}"))
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
    """Create document_category and document tables if missing."""
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
                    name TEXT UNIQUE NOT NULL,
                    created_at DATETIME
                )
                """
            ))
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
    """Ensure Contact table has manager_id and archived columns."""
    required = {
        'manager_id': 'INTEGER',
        'archived': 'BOOLEAN',
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
