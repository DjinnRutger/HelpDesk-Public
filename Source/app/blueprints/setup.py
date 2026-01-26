from flask import Blueprint, render_template, redirect, url_for, flash, request, current_app
from ..models import (
    User, Setting, Ticket, Asset, PurchaseOrder, OrderItem, Vendor, Company, ShippingLocation, Contact
)
from .. import db
from ..utils.security import hash_password
import tempfile
import shutil
import sqlite3
from datetime import datetime
import os

setup_bp = Blueprint('setup', __name__, url_prefix='/setup')


def needs_setup():
    """Check if the application needs initial setup (no users exist)"""
    try:
        user_count = User.query.count()
        return user_count == 0
    except Exception:
        # Connection might be stale after a restore - try fresh connection
        try:
            db.session.remove()
            db.engine.dispose()
            user_count = User.query.count()
            return user_count == 0
        except Exception:
            # If we still can't query, assume we need setup
            return True


@setup_bp.route('/')
def index():
    """Welcome/setup page - only accessible if no users exist"""
    if not needs_setup():
        return redirect(url_for('auth.login'))
    
    # Available themes
    themes = [
        {'key': 'light', 'name': 'Light', 'description': 'Clean and bright'},
        {'key': 'dark', 'name': 'Dark', 'description': 'Easy on the eyes'},
        {'key': 'ocean', 'name': 'Ocean', 'description': 'Cool blue tones'},
        {'key': 'fallout', 'name': 'Fallout', 'description': 'Retro terminal green'}
    ]
    
    return render_template('setup/welcome.html', themes=themes)


@setup_bp.route('/create', methods=['POST'])
def create_initial_user():
    """Create the first admin user and initialize settings"""
    if not needs_setup():
        flash('Setup has already been completed.', 'info')
        return redirect(url_for('auth.login'))
    
    email = (request.form.get('email') or '').strip().lower()
    password = (request.form.get('password') or '').strip()
    name = (request.form.get('name') or '').strip()
    theme = (request.form.get('theme') or 'light').strip()
    load_demo = (request.form.get('load_demo') or '').strip().lower() in ('1', 'true', 'yes', 'on')
    
    # Validation
    if not email or not password or not name:
        flash('Name, email, and password are required.', 'danger')
        return redirect(url_for('setup.index'))
    
    if len(password) < 8:
        flash('Password must be at least 8 characters.', 'danger')
        return redirect(url_for('setup.index'))
    
    if '@' not in email:
        flash('Please provide a valid email address.', 'danger')
        return redirect(url_for('setup.index'))
    
    try:
        # Create the first admin user
        user = User(
            email=email,
            name=name,
            password_hash=hash_password(password),
            role='admin',
            is_active=True,
            theme=theme
        )
        db.session.add(user)
        db.session.commit()

        # Optionally seed demo data
        if load_demo:
            try:
                _seed_demo_data()
                Setting.set('DEMO_DATA_LOADED', 'true')
                Setting.set('DEMO_MODE', '1')
            except Exception as se:
                # Don't block setup if demo data fails; log and continue
                try:
                    current_app.logger.exception('Demo data seeding failed: %s', se)
                except Exception:
                    pass
                flash('Admin created, but demo data failed to load. You can use the app normally.', 'warning')

        # Set initial application settings
        Setting.set('SETUP_COMPLETED', 'true')
        Setting.set('SETUP_DATE', datetime.utcnow().isoformat())

        flash(f'Welcome, {name}! Your HelpDesk system is ready. Please log in.', 'success')
        return redirect(url_for('auth.login'))

    except Exception as e:
        db.session.rollback()
        flash(f'Error creating user: {str(e)}', 'danger')
        return redirect(url_for('setup.index'))


@setup_bp.route('/restore', methods=['POST'])
def restore_database():
    """Restore database from uploaded backup file"""
    if not needs_setup():
        flash('Setup has already been completed. Use Admin > Backup to restore.', 'info')
        return redirect(url_for('auth.login'))
    
    if 'backup_file' not in request.files:
        flash('No file uploaded.', 'danger')
        return redirect(url_for('setup.index'))
    
    file = request.files['backup_file']
    if not file or file.filename == '':
        flash('Please select a backup file to upload.', 'warning')
        return redirect(url_for('setup.index'))
    
    if db.engine.dialect.name != 'sqlite':
        flash('Restore is only supported for SQLite databases.', 'warning')
        return redirect(url_for('setup.index'))
    
    # Save upload to a temp file
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
    tmp_path = tmp.name
    tmp.close()
    file.save(tmp_path)
    
    # Validate it is a readable SQLite database
    try:
        with sqlite3.connect(tmp_path) as test:
            test.execute('PRAGMA schema_version;').fetchone()
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        flash('Uploaded file is not a valid SQLite database.', 'danger')
        return redirect(url_for('setup.index'))
    
    # Replace the live DB
    try:
        db_path = db.engine.url.database
        if not db_path:
            flash('Could not determine database file path.', 'danger')
            return redirect(url_for('setup.index'))
        
        # Dispose connections
        db.session.remove()
        db.engine.dispose()
        
        # Backup current (likely empty) DB
        if os.path.exists(db_path):
            backup_path = f"{db_path}.pre-restore-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
            try:
                shutil.copyfile(db_path, backup_path)
            except Exception:
                pass
        
        # Replace with uploaded
        shutil.copyfile(tmp_path, db_path)
        
        # Force SQLAlchemy to reconnect with the new database file
        # This ensures the next query uses fresh connections to the restored DB
        db.engine.dispose()
        
        # Verify the restored database has users before redirecting to login
        # This prevents being stuck in setup loop due to stale connection state
        try:
            with db.engine.connect() as conn:
                # Quote table name because "user" can be treated specially by some SQL dialects.
                result = conn.execute(db.text('SELECT COUNT(*) FROM "user"'))
                user_count = result.scalar()
                if user_count == 0:
                    flash('Restored database has no users. Please create an admin account.', 'warning')
                    return redirect(url_for('setup.index'))
        except Exception as verify_err:
            current_app.logger.warning(f'Could not verify restored database: {verify_err}')
        
        # Re-run lightweight migrations to ensure required columns exist
        try:
            from ..utils.db_migrate import (
                ensure_ticket_columns,
                ensure_user_columns,
                ensure_ticket_process_item_columns,
                ensure_ticket_note_columns,
                ensure_po_note_table,
                ensure_order_tables,
                ensure_company_shipping_tables,
                ensure_documents_tables,
                ensure_assets_table,
                ensure_asset_picklists,
            )
            ensure_ticket_columns(db.engine)
            ensure_user_columns(db.engine)
            ensure_ticket_process_item_columns(db.engine)
            ensure_ticket_note_columns(db.engine)
            ensure_po_note_table(db.engine)
            ensure_order_tables(db.engine)
            ensure_company_shipping_tables(db.engine)
            ensure_documents_tables(db.engine)
            ensure_assets_table(db.engine)
            ensure_asset_picklists(db.engine)
        except Exception:
            pass
        
        flash('Database restored successfully. Please log in.', 'success')
        return redirect(url_for('auth.login'))
    
    except Exception as e:
        try:
            current_app.logger.exception('Restore failed: %s', e)
        except Exception:
            pass
        flash(f'Restore failed: {str(e)}', 'danger')
        return redirect(url_for('setup.index'))
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def _seed_demo_data():
    """Populate the database with demo data: 20 users, 20 assets, 20 POs, 20 tickets.
    Relationships:
      - Assets assigned to demo users
      - Tickets related to demo users and assets
      - POs contain items describing some assets; assets reference supplier/order_number
    """
    import random
    from datetime import timedelta

    # Vendors
    vendors = []
    vendor_names = ['Acme Supplies', 'Globex Distribution', 'Initech Hardware', 'Umbrella Components']
    for vname in vendor_names:
        v = Vendor(company_name=vname, contact_name='Sales', email=f'sales@{vname.split()[0].lower()}.example.com',
                   address='123 Market St, Springfield', phone='555-0100')
        db.session.add(v)
        vendors.append(v)

    # Company
    comp = Company(name='Example Corp', address='100 Main St', city='Metropolis', state='IL', zip_code='60601')
    db.session.add(comp)

    # Shipping location
    ship = ShippingLocation(name='Main Warehouse', address='200 Warehouse Ave', city='Metropolis', state='IL', zip_code='60602', tax_rate=0.0825)
    db.session.add(ship)

    db.session.flush()

    # Demo tech users (20)
    demo_users = []
    for i in range(1, 21):
        u = User(
            email=f'tech{i:02d}@example.com',
            name=f'Tech {i:02d}',
            password_hash=hash_password('Demo12345!'),
            role='tech',
            is_active=True,
            theme='light'
        )
        db.session.add(u)
        demo_users.append(u)

    db.session.flush()

    # Corresponding Contacts for assignment context
    contacts_by_user = {}
    for u in demo_users:
        c = Contact(name=u.name, email=u.email)
        db.session.add(c)
        contacts_by_user[u.id] = c

    db.session.flush()

    # Assets (20), some assigned to users
    asset_names = [
        'Dell Latitude 55xx Laptop', 'HP ProDesk 400 G7', 'Lenovo ThinkPad T14', 'Apple MacBook Air M2',
        'Dell UltraSharp 27" Monitor', 'HP LaserJet Pro M404dn', 'Logitech MX Master 3 Mouse', 'Keychron K2 Keyboard',
        'Cisco Catalyst Switch', 'Ubiquiti UniFi AP', 'Synology NAS DS920+', 'Brother Label Printer',
        'APC UPS 1500VA', 'Samsung 27" Curved Monitor', 'Anker USB-C Dock', 'Sennheiser Headset',
        'iPad 10th Gen', 'Surface Pro 9', 'Raspberry Pi 5 Kit', 'Wacom Intuos Tablet'
    ]
    assets = []
    for i, nm in enumerate(asset_names, start=1):
        assigned_user = random.choice(demo_users) if random.random() < 0.7 else None
        a = Asset(
            name=nm,
            company=comp.name,
            supplier=random.choice(vendors).company_name,
            asset_tag=f'AST-{1000+i}',
            serial_number=f'SN-{1000+i:05d}',
            status='deployed' if assigned_user else 'available',
            assigned_contact_id=None
        )
        db.session.add(a)
        db.session.flush()
        if assigned_user:
            # Assign to corresponding Contact using model helper
            contact = contacts_by_user.get(assigned_user.id)
            if contact is not None:
                try:
                    a.checkout(contact)
                except Exception:
                    # Fallback to set fields manually if checkout fails for any reason
                    a.assigned_contact_id = contact.id
                    a.status = 'deployed'
        assets.append(a)

    db.session.flush()

    # Purchase Orders (20) with items; some items map to assets
    pos = []
    base_po = 1000
    now = datetime.utcnow()
    for i in range(1, 21):
        v = vendors[(i - 1) % len(vendors)]
        p = PurchaseOrder(
            po_number=str(base_po + i),
            vendor_name=v.company_name,
            vendor_id=v.id,
            vendor_contact_name=v.contact_name,
            vendor_email=v.email,
            vendor_address=v.address,
            vendor_phone=v.phone,
            company_id=comp.id,
            company_name=comp.name,
            shipping_location_id=ship.id,
            shipping_name=ship.name,
            shipping_address=ship.address,
            shipping_city=ship.city,
            shipping_state=ship.state,
            shipping_zip=ship.zip_code,
            shipping_cost=round(random.uniform(0, 50), 2),
            status='complete' if i % 3 == 0 else 'sent',
            created_at=now - timedelta(days=30 - i),
            ordered_at=now - timedelta(days=30 - i)
        )
        db.session.add(p)
        db.session.flush()
        # Items: 1-3 each
        num_items = random.randint(1, 3)
        # Map some assets to this PO items
        related_assets = random.sample(assets, k=min(num_items, len(assets)))
        for idx in range(num_items):
            if idx < len(related_assets) and random.random() < 0.7:
                asset = related_assets[idx]
                desc = asset.name
                asset.order_number = p.po_number
                asset.supplier = p.vendor_name
            else:
                desc = f"Office Supply Item {i}-{idx+1}"
            it = OrderItem(
                description=desc,
                quantity=1,
                est_unit_cost=round(random.uniform(25, 1500), 2),
                dept_code=random.choice(['IT', 'OPS', 'HR', 'FIN']),
                po_id=p.id,
                status='received' if p.status == 'complete' else 'ordered'
            )
            db.session.add(it)
        pos.append(p)

    db.session.flush()

    # Tickets (20) tied to users and assets
    subjects = [
        'Laptop running slow', 'Monitor flickering', 'Printer not responding', 'VPN connection issues',
        'Email sync problem', 'Software installation request', 'System blue screen', 'Keyboard not working',
        'Wi-Fi dropout in conference room', 'Backup failed last night', 'New hire setup', 'Access to shared drive',
        'Two-factor auth reset', 'Phone not ringing', 'USB-C dock power issue', 'Headset microphone crackle',
        'Tablet screen unresponsive', 'Surface pen not pairing', 'Server room temperature alert', 'Drawing tablet driver update'
    ]
    # Generic employee names for requesters (as asked: Bob, Bill, John, Joan, etc.)
    employee_names = [
        'Bob', 'Bill', 'John', 'Joan', 'Alice', 'Carol', 'Dave', 'Eve', 'Frank', 'Grace',
        'Heidi', 'Ivan', 'Judy', 'Mallory', 'Oscar', 'Peggy', 'Trent', 'Victor', 'Walter', 'Zoe'
    ]
    for i in range(1, 21):
        tech = random.choice(demo_users)
        asset = random.choice(assets) if random.random() < 0.75 else None
        status = random.choice(['open', 'in_progress', 'closed'])
        emp_name = employee_names[(i - 1) % len(employee_names)]
        emp_email = f"{emp_name.lower()}@example.com"
        t = Ticket(
            subject=subjects[i-1],
            body='Auto-generated demo ticket for onboarding.',
            status=status,
            priority=random.choice(['low', 'medium', 'high']),
            assignee_id=tech.id,
            requester_name=emp_name,
            requester_email=emp_email,
            asset_id=getattr(asset, 'id', None),
            source='demo'
        )
        if status == 'closed':
            t.closed_at = now
        db.session.add(t)

    db.session.commit()
