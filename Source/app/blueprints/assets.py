from flask import Blueprint, render_template, request, redirect, url_for, flash, send_file, jsonify
from flask_login import login_required, current_user
from ..models import Asset, Contact, Ticket, AssetAudit, AssetCategory, AssetManufacturer, AssetCondition, AssetLocation
from .. import db
import csv
import io
from sqlalchemy.exc import IntegrityError
from datetime import datetime
from types import SimpleNamespace
from flask_login import current_user

assets_bp = Blueprint('assets', __name__, url_prefix='/assets')


def parse_date(val: str):
    if not val or not val.strip():
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%m/%d/%Y', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(val.strip(), fmt)
        except Exception:
            continue
    return None


@assets_bp.route('/')
@login_required
def index():
    q = (request.args.get('q') or '').strip()
    status = (request.args.get('status') or '').strip()
    category = (request.args.get('category') or '').strip()
    # Pagination
    try:
        page = max(1, int(request.args.get('page', '1')))
    except ValueError:
        page = 1
    try:
        per_page = int(request.args.get('per_page', '20'))
    except ValueError:
        per_page = 20
    if per_page not in (20, 100):
        per_page = 20

    # Base query
    query = Asset.query
    if q:
        like = f"%{q}%"
        # First search by asset fields
        asset_match = ((Asset.name.ilike(like)) | (Asset.asset_tag.ilike(like)) | (Asset.serial_number.ilike(like)) |
                       (Asset.category.ilike(like)) | (Asset.manufacturer.ilike(like)) | (Asset.model_name.ilike(like)))
        query = query.filter(asset_match)
        # Also support searching by assigned user's name or email: if q matches a contact, show all assets assigned to them
        # Determine matching contact ids
        matching_contacts = Contact.query.filter(
            (Contact.name.ilike(like)) | (Contact.email.ilike(like))
        ).with_entities(Contact.id).all()
        if matching_contacts:
            contact_ids = [cid for (cid,) in matching_contacts]
            # Union: assets that matched fields OR are assigned to any matching contact
            # Implement as OR condition on assigned_contact_id
            query = Asset.query.filter(
                asset_match | (Asset.assigned_contact_id.in_(contact_ids))
            )
    if status:
        query = query.filter(Asset.status.ilike(status))
    if category:
        # Exact match to picklist category name stored in Asset.category
        query = query.filter(Asset.category == category)
    # Categories list for filter dropdown
    categories = AssetCategory.query.order_by(AssetCategory.name.asc()).all()
    total = query.count()
    pages = max(1, (total + per_page - 1) // per_page)
    if page > pages:
        page = pages
    offset = (page - 1) * per_page
    assets = query.order_by(Asset.name.asc()).offset(offset).limit(per_page).all()
    return render_template('assets/index.html', assets=assets, q=q, status=status, category=category, categories=categories, page=page, per_page=per_page, total=total, pages=pages)


@assets_bp.route('/new', methods=['GET','POST'])
@login_required
def new():
    # Basic role check
    if getattr(current_user, 'role', 'user') not in ('admin','tech','manager'):
        flash('Not authorized to create assets.', 'danger')
        return redirect(url_for('assets.index'))
    if request.method == 'POST':
        form = request.form
        name = (form.get('name') or '').strip()
        if not name:
            flash('Name is required.', 'danger')
            return redirect(url_for('assets.new'))
        a = Asset(
            name=name,
            asset_tag=(form.get('asset_tag') or '').strip() or None,
            serial_number=(form.get('serial_number') or '').strip() or None,
            category=(form.get('category') or '').strip() or None,
            manufacturer=(form.get('manufacturer') or '').strip() or None,
            model_name=(form.get('model_name') or '').strip() or None,
            model_no=(form.get('model_no') or '').strip() or None,
            location=(form.get('location') or '').strip() or None,
            default_location=(form.get('default_location') or '').strip() or None,
            status=(form.get('status') or 'available').strip() or 'available',
            physical_condition=(form.get('physical_condition') or '').strip() or None,
            url=(form.get('url') or '').strip() or None,
            notes=(form.get('notes') or '').strip() or None,
            specs=(form.get('specs') or '').strip() or None,
        )
        # Pre-validate uniqueness of asset_tag if provided
        if a.asset_tag and Asset.query.filter_by(asset_tag=a.asset_tag).first():
            flash('Asset Tag already exists. Please choose a unique tag.', 'warning')
            contacts = Contact.query.order_by(Contact.name.asc()).limit(500).all()
            categories = AssetCategory.query.order_by(AssetCategory.name.asc()).all()
            manufacturers = AssetManufacturer.query.order_by(AssetManufacturer.name.asc()).all()
            conditions = AssetCondition.query.order_by(AssetCondition.name.asc()).all()
            locations = AssetLocation.query.order_by(AssetLocation.name.asc()).all()
            return render_template('assets/detail.html', asset=a, contacts=contacts, edit=True, categories=categories, manufacturers=manufacturers, conditions=conditions, locations=locations, is_new=True)

        cost_raw = (form.get('cost') or '').replace(',', '').strip()
        try:
            a.cost = float(cost_raw) if cost_raw else None
        except Exception:
            a.cost = None
        a.purchased_at = parse_date(form.get('purchased_at')) if form.get('purchased_at') else None
        a.warranty_expires = parse_date(form.get('warranty_expires')) if form.get('warranty_expires') else None
        a.eol_date = parse_date(form.get('eol_date')) if form.get('eol_date') else None
        db.session.add(a)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash('Could not create asset due to a uniqueness constraint (likely Asset Tag). Please adjust and try again.', 'danger')
            contacts = Contact.query.order_by(Contact.name.asc()).limit(500).all()
            categories = AssetCategory.query.order_by(AssetCategory.name.asc()).all()
            manufacturers = AssetManufacturer.query.order_by(AssetManufacturer.name.asc()).all()
            conditions = AssetCondition.query.order_by(AssetCondition.name.asc()).all()
            locations = AssetLocation.query.order_by(AssetLocation.name.asc()).all()
            return render_template('assets/detail.html', asset=a, contacts=contacts, edit=True, categories=categories, manufacturers=manufacturers, conditions=conditions, locations=locations, is_new=True)
        db.session.add(AssetAudit(asset_id=a.id, user_id=getattr(current_user,'id',None), action='edit', field='create', old_value=None, new_value=a.name))
        db.session.commit()
        flash('Asset created.', 'success')
        return redirect(url_for('assets.detail', asset_id=a.id))
    # GET
    contacts = Contact.query.order_by(Contact.name.asc()).limit(500).all()
    categories = AssetCategory.query.order_by(AssetCategory.name.asc()).all()
    manufacturers = AssetManufacturer.query.order_by(AssetManufacturer.name.asc()).all()
    conditions = AssetCondition.query.order_by(AssetCondition.name.asc()).all()
    locations = AssetLocation.query.order_by(AssetLocation.name.asc()).all()
    # Render the detail template in edit mode but with no asset yet is tricky; create a minimal placeholder
    asset = SimpleNamespace(
        id=None,
        name='',
        asset_tag='',
        serial_number='',
        category='',
        manufacturer='',
        model_name='',
        model_no='',
        location='',
        default_location='',
        status='available',
        physical_condition='',
        url='',
        notes='',
        specs='',
        purchased_at=None,
        warranty_expires=None,
        eol_date=None,
        cost=None,
    )
    return render_template('assets/detail.html', asset=asset, contacts=contacts, edit=True, categories=categories, manufacturers=manufacturers, conditions=conditions, locations=locations, is_new=True)


@assets_bp.route('/<int:asset_id>')
@login_required
def detail(asset_id):
    a = Asset.query.get_or_404(asset_id)
    contacts = Contact.query.order_by(Contact.name.asc()).limit(500).all()
    # Locations list for modal defaults/selects
    locations = AssetLocation.query.order_by(AssetLocation.name.asc()).all()
    # Get tickets associated with this asset
    asset_tickets = a.tickets.order_by(Ticket.created_at.desc()).limit(10).all()
    # Asset audit log pagination
    try:
        audit_page = int(request.args.get('audit_page', '1'))
    except ValueError:
        audit_page = 1
    if audit_page < 1:
        audit_page = 1
    per_page = 20
    base_q = AssetAudit.query.filter_by(asset_id=a.id).order_by(AssetAudit.created_at.desc())
    audit_rows = base_q.offset((audit_page - 1) * per_page).limit(per_page + 1).all()
    has_next = len(audit_rows) > per_page
    has_prev = audit_page > 1
    audit_logs = audit_rows[:per_page]
    return render_template('assets/detail.html', asset=a, contacts=contacts, asset_tickets=asset_tickets, audit_logs=audit_logs, audit_page=audit_page, has_next=has_next, has_prev=has_prev, edit=False, locations=locations)


@assets_bp.route('/<int:asset_id>/status', methods=['POST'])
@login_required
def update_status(asset_id):
    a = Asset.query.get_or_404(asset_id)
    new_status = (request.form.get('status') or '').strip().lower()
    if not new_status:
        flash('Select a status to save.', 'warning')
        return redirect(url_for('assets.detail', asset_id=a.id))
    old_status = a.status or ''
    if old_status != new_status:
        a.status = new_status
        db.session.add(AssetAudit(asset_id=a.id, user_id=getattr(current_user,'id',None), action='status_change', field='status', old_value=old_status, new_value=new_status))
        db.session.commit()
        flash('Asset status updated.', 'success')
    return redirect(url_for('assets.detail', asset_id=a.id))


@assets_bp.route('/<int:asset_id>/hard_delete', methods=['POST'])
@login_required
def hard_delete(asset_id):
    a = Asset.query.get_or_404(asset_id)
    if (a.status or '').lower() != 'archived':
        flash('Asset must be archived before it can be hard deleted.', 'warning')
        return redirect(url_for('assets.detail', asset_id=a.id))
    name = a.name
    asset_id_val = a.id
    db.session.add(AssetAudit(asset_id=asset_id_val, user_id=getattr(current_user,'id',None), action='delete', field='hard_delete', old_value=name, new_value=None))
    db.session.delete(a)
    db.session.commit()
    flash('Asset permanently deleted.', 'success')
    return redirect(url_for('assets.index'))


@assets_bp.route('/<int:asset_id>/edit', methods=['GET','POST'])
@login_required
def edit(asset_id):
    a = Asset.query.get_or_404(asset_id)
    # Basic role check (adjust as needed)
    if getattr(current_user, 'role', 'user') not in ('admin','tech','manager'):
        flash('Not authorized to edit assets.', 'danger')
        return redirect(url_for('assets.detail', asset_id=a.id))
    if request.method == 'POST':
        try:
            form = request.form
            changes = []
            def track(field, new_raw, transform=lambda v: v):
                old_val = getattr(a, field)
                new_val = transform(new_raw)
                if (old_val or '') != (new_val or ''):
                    changes.append((field, old_val, new_val))
                    setattr(a, field, new_val)
            track('name', form.get('name'))
            track('asset_tag', form.get('asset_tag') or None)
            track('serial_number', form.get('serial_number') or None)
            track('category', form.get('category') or None)
            track('manufacturer', form.get('manufacturer') or None)
            track('model_name', form.get('model_name') or None)
            track('model_no', form.get('model_no') or None)
            track('location', form.get('location') or None)
            track('default_location', form.get('default_location') or None)
            track('status', form.get('status') or a.status)
            track('physical_condition', form.get('physical_condition') or None)
            track('url', form.get('url') or None)
            track('notes', form.get('notes') or None)
            track('specs', form.get('specs') or None)
            a.name = (form.get('name') or '').strip() or a.name
            a.asset_tag = (form.get('asset_tag') or '').strip() or None
            a.serial_number = (form.get('serial_number') or '').strip() or None
            a.category = (form.get('category') or '').strip() or None
            a.manufacturer = (form.get('manufacturer') or '').strip() or None
            a.model_name = (form.get('model_name') or '').strip() or None
            a.model_no = (form.get('model_no') or '').strip() or None
            a.location = (form.get('location') or '').strip() or None
            a.default_location = (form.get('default_location') or '').strip() or None
            a.status = (form.get('status') or '').strip() or a.status
            a.physical_condition = (form.get('physical_condition') or '').strip() or None
            a.url = (form.get('url') or '').strip() or None
            a.notes = (form.get('notes') or '').strip() or None
            a.specs = (form.get('specs') or '').strip() or None
            # Numeric / date conversions
            cost_raw = (form.get('cost') or '').replace(',', '').strip()
            try:
                a.cost = float(cost_raw) if cost_raw else None
            except Exception:
                pass
            a.purchased_at = parse_date(form.get('purchased_at')) if form.get('purchased_at') else a.purchased_at if form.get('purchased_at') != '' else None
            a.warranty_expires = parse_date(form.get('warranty_expires')) if form.get('warranty_expires') else None
            a.eol_date = parse_date(form.get('eol_date')) if form.get('eol_date') else None
            db.session.commit()
            # Write audits after commit of base changes
            for field, old_val, new_val in changes:
                audit = AssetAudit(
                    asset_id=a.id,
                    user_id=getattr(current_user, 'id', None),
                    action='edit',
                    field=field,
                    old_value=str(old_val) if old_val is not None else None,
                    new_value=str(new_val) if new_val is not None else None
                )
                db.session.add(audit)
            if changes:
                db.session.commit()
            flash('Asset updated.', 'success')
            return redirect(url_for('assets.detail', asset_id=a.id))
        except Exception as e:
            db.session.rollback()
            flash(f'Update failed: {e}', 'danger')
    contacts = Contact.query.order_by(Contact.name.asc()).limit(500).all()
    categories = AssetCategory.query.order_by(AssetCategory.name.asc()).all()
    manufacturers = AssetManufacturer.query.order_by(AssetManufacturer.name.asc()).all()
    conditions = AssetCondition.query.order_by(AssetCondition.name.asc()).all()
    locations = AssetLocation.query.order_by(AssetLocation.name.asc()).all()
    return render_template('assets/detail.html', asset=a, contacts=contacts, edit=True, categories=categories, manufacturers=manufacturers, conditions=conditions, locations=locations)


@assets_bp.route('/<int:asset_id>/checkout', methods=['POST'])
@login_required
def checkout(asset_id):
    a = Asset.query.get_or_404(asset_id)
    contact_id = request.form.get('contact_id')
    expected = request.form.get('expected_return')
    contact = Contact.query.get(contact_id) if contact_id else None
    if not contact:
        flash('Contact not found', 'danger')
        return redirect(url_for('assets.detail', asset_id=a.id))
    prev_assigned = a.assigned_contact_id
    prev_status = a.status or ''
    prev_location = a.location or ''
    a.checkout(contact, expected=parse_date(expected) if expected else None)
    # Status: default to 'deployed' if not provided, but allow override
    new_status = (request.form.get('status') or '').strip().lower()
    a.status = new_status if new_status else 'deployed'
    new_location = (request.form.get('location') or '').strip()
    if new_location:
        a.location = new_location
    db.session.commit()
    # Audit entries
    db.session.add(AssetAudit(asset_id=a.id, user_id=getattr(current_user,'id',None), action='checkout', field='assigned_contact_id', old_value=str(prev_assigned) if prev_assigned else None, new_value=str(a.assigned_contact_id)))
    if (a.status or '') != prev_status:
        db.session.add(AssetAudit(asset_id=a.id, user_id=getattr(current_user,'id',None), action='status_change', field='status', old_value=prev_status or None, new_value=a.status or None))
    if (a.location or '') != prev_location:
        db.session.add(AssetAudit(asset_id=a.id, user_id=getattr(current_user,'id',None), action='edit', field='location', old_value=prev_location or None, new_value=a.location or None))
    db.session.commit()
    flash('Asset checked out', 'success')
    return redirect(url_for('assets.detail', asset_id=a.id))


@assets_bp.route('/<int:asset_id>/checkin', methods=['POST'])
@login_required
def checkin(asset_id):
    a = Asset.query.get_or_404(asset_id)
    prev_assigned = a.assigned_contact_id
    prev_status = a.status or ''
    prev_location = a.location or ''
    # perform base checkin
    a.checkin()
    # Apply optional overrides from modal (default status to 'available')
    new_status = (request.form.get('status') or '').strip().lower()
    a.status = new_status if new_status else 'available'
    new_location = (request.form.get('location') or '').strip()
    if new_location:
        a.location = new_location
    db.session.commit()
    # audits
    db.session.add(AssetAudit(asset_id=a.id, user_id=getattr(current_user,'id',None), action='checkin', field='assigned_contact_id', old_value=str(prev_assigned) if prev_assigned else None, new_value=None))
    if (a.status or '') != prev_status:
        db.session.add(AssetAudit(asset_id=a.id, user_id=getattr(current_user,'id',None), action='status_change', field='status', old_value=prev_status or None, new_value=a.status or None))
    if (a.location or '') != prev_location:
        db.session.add(AssetAudit(asset_id=a.id, user_id=getattr(current_user,'id',None), action='edit', field='location', old_value=prev_location or None, new_value=a.location or None))
    db.session.commit()
    # If AJAX / JSON request, return JSON so UI can update without navigation
    wants_json = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or 'application/json' in (request.headers.get('Accept') or '')
    if wants_json:
        return jsonify({'success': True, 'asset_id': a.id})
    flash('Asset checked in', 'success')
    next_url = request.form.get('next') or request.args.get('next')
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    return redirect(url_for('assets.detail', asset_id=a.id))


@assets_bp.route('/import', methods=['POST'])
@login_required
def import_csv():
    if current_user.role != 'admin':
        flash('Only admins can import assets', 'danger')
        return redirect(url_for('assets.index'))
    file = request.files.get('file')
    if not file:
        flash('No file uploaded', 'danger')
        return redirect(url_for('assets.index'))
    try:
        stream = io.StringIO(file.stream.read().decode('utf-8', errors='ignore'))
        reader = csv.DictReader(stream)
        added = 0
        updated = 0
        for row in reader:
            # Map CSV fields
            legacy_id = int(row.get('ID') or 0) or None
            asset_tag = (row.get('Asset Tag') or '').strip() or None
            serial = (row.get('Serial') or '').strip() or None
            name = (row.get('Asset Name') or '').strip() or 'Unnamed Asset'
            existing = None
            if asset_tag:
                existing = Asset.query.filter_by(asset_tag=asset_tag).first()
            if not existing and serial:
                existing = Asset.query.filter_by(serial_number=serial).first()
            target = existing or Asset()
            target.source_id = legacy_id
            target.company = row.get('Company') or None
            target.name = name
            target.asset_tag = asset_tag
            target.model_name = row.get('Model') or None
            target.model_no = row.get('Model No.') or None
            target.category = row.get('Category') or None
            target.manufacturer = row.get('Manufacturer') or None
            target.serial_number = serial
            target.purchased_at = parse_date(row.get('Purchased'))
            cost_raw = (row.get('Cost') or '').replace(',', '')
            try:
                target.cost = float(cost_raw) if cost_raw else None
            except Exception:
                target.cost = None
            target.eol_date = parse_date(row.get('EOL'))
            target.warranty_months = int(row.get('Warranty') or 0) or None
            target.warranty_expires = parse_date(row.get('Warranty Expires'))
            current_val_raw = (row.get('Current Value') or '').replace(',', '')
            try:
                target.current_value = float(current_val_raw) if current_val_raw else None
            except Exception:
                target.current_value = None
            target.fully_depreciated = (row.get('Fully Depreciated') or '').strip() == '1'
            target.order_number = row.get('Order Number') or None
            target.supplier = row.get('Supplier') or None
            target.location = row.get('Location') or None
            target.default_location = row.get('Default Location') or None
            target.status = (row.get('Status') or '').split('(')[0].strip() or 'available'
            target.checkout_date = parse_date(row.get('Checkout Date'))
            target.last_checkin_date = parse_date(row.get('Last Checkin Date'))
            target.expected_checkin_date = parse_date(row.get('Expected Checkin Date'))
            target.created_at_legacy = parse_date(row.get('Created At'))
            target.updated_at_legacy = parse_date(row.get('Updated at'))
            target.last_audit = parse_date(row.get('Last Audit'))
            target.next_audit_date = parse_date(row.get('Next Audit Date'))
            target.notes = row.get('Notes') or None
            target.url = row.get('URL') or None
            target.specs = row.get('Specs') or None
            target.physical_condition = row.get('Physical Condition') or None
            target.end_of_life_text = row.get('End of Life') or None
            # Assignment intentionally disabled per requirements: do not auto-create or assign users during import
            # Preserve any existing assignment on updates; skip processing 'Checked Out' / 'Username' columns.
            if not existing:
                db.session.add(target)
                added += 1
            else:
                updated += 1
        db.session.commit()
        flash(f'Import complete: added {added}, updated {updated}', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Import failed: {e}', 'danger')
    return redirect(url_for('assets.index'))


@assets_bp.route('/purge', methods=['POST'])
@login_required
def purge():
    if current_user.role != 'admin':
        flash('Only admins can purge assets', 'danger')
        return redirect(url_for('assets.index'))
    try:
        deleted = Asset.query.delete()
        db.session.commit()
        flash(f'All assets purged (deleted {deleted}).', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Purge failed: {e}', 'danger')
    return redirect(url_for('assets.index'))


@assets_bp.route('/bulk_checkin/contact/<int:contact_id>', methods=['POST'])
@login_required
def bulk_checkin_contact(contact_id):
    if current_user.role not in ('admin','tech','manager'):  # basic role check (expand as needed)
        flash('Not authorized.', 'danger')
        return redirect(url_for('users.show_user', contact_id=contact_id))
    assets = Asset.query.filter_by(assigned_contact_id=contact_id).all()
    if not assets:
        flash('No assets assigned to user.', 'info')
        return redirect(url_for('users.show_user', contact_id=contact_id))
    changed = 0
    for a in assets:
        prev_assigned = a.assigned_contact_id
        prev_status = a.status or ''
        a.checkin()
        # Log audit entry for each asset (same as individual checkin)
        db.session.add(AssetAudit(asset_id=a.id, user_id=getattr(current_user,'id',None), action='checkin', field='assigned_contact_id', old_value=str(prev_assigned) if prev_assigned else None, new_value=None))
        if (a.status or '') != prev_status:
            db.session.add(AssetAudit(asset_id=a.id, user_id=getattr(current_user,'id',None), action='status_change', field='status', old_value=prev_status or None, new_value=a.status or None))
        changed += 1
    db.session.commit()
    flash(f'Checked in {changed} asset(s).', 'success')
    next_url = request.form.get('next')
    if next_url and next_url.startswith('/'):
        return redirect(next_url)
    return redirect(url_for('users.show_user', contact_id=contact_id))


@assets_bp.route('/export')
@login_required
def export_csv():
    if current_user.role != 'admin':
        flash('Only admins can export assets', 'danger')
        return redirect(url_for('assets.index'))
    output = io.StringIO()
    fieldnames = [
        'ID','Company','Asset Name','Asset Tag','Model','Model No.','Category','Manufacturer','Serial','Purchased','Cost','EOL','Warranty','Warranty Expires','Current Value','Fully Depreciated','Order Number','Supplier','Location','Default Location','Status','Checkout Date','Last Checkin Date','Expected Checkin Date','Created At','Updated at','Last Audit','Next Audit Date','Notes','URL','Specs','Physical Condition','End of Life'
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for a in Asset.query.order_by(Asset.id.asc()).all():
        writer.writerow({
            'ID': a.source_id or a.id,
            'Company': a.company or '',
            'Asset Name': a.name,
            'Asset Tag': a.asset_tag or '',
            'Model': a.model_name or '',
            'Model No.': a.model_no or '',
            'Category': a.category or '',
            'Manufacturer': a.manufacturer or '',
            'Serial': a.serial_number or '',
            'Purchased': a.purchased_at.strftime('%Y-%m-%d %H:%M:%S') if a.purchased_at else '',
            'Cost': f"{a.cost:.2f}" if a.cost is not None else '',
            'EOL': a.eol_date.strftime('%Y-%m-%d') if a.eol_date else '',
            'Warranty': a.warranty_months or '',
            'Warranty Expires': a.warranty_expires.strftime('%Y-%m-%d') if a.warranty_expires else '',
            'Current Value': f"{a.current_value:.2f}" if a.current_value is not None else '',
            'Fully Depreciated': '1' if a.fully_depreciated else '0',
            'Order Number': a.order_number or '',
            'Supplier': a.supplier or '',
            'Location': a.location or '',
            'Default Location': a.default_location or '',
            'Status': a.status or '',
            'Checkout Date': a.checkout_date.strftime('%Y-%m-%d %H:%M:%S') if a.checkout_date else '',
            'Last Checkin Date': a.last_checkin_date.strftime('%Y-%m-%d %H:%M:%S') if a.last_checkin_date else '',
            'Expected Checkin Date': a.expected_checkin_date.strftime('%Y-%m-%d %H:%M:%S') if a.expected_checkin_date else '',
            'Created At': a.created_at_legacy.strftime('%Y-%m-%d %H:%M:%S') if a.created_at_legacy else '',
            'Updated at': a.updated_at_legacy.strftime('%Y-%m-%d %H:%M:%S') if a.updated_at_legacy else '',
            'Last Audit': a.last_audit.strftime('%Y-%m-%d %H:%M:%S') if a.last_audit else '',
            'Next Audit Date': a.next_audit_date.strftime('%Y-%m-%d %H:%M:%S') if a.next_audit_date else '',
            'Notes': a.notes or '',
            'URL': a.url or '',
            'Specs': a.specs or '',
            'Physical Condition': a.physical_condition or '',
            'End of Life': a.end_of_life_text or '',
        })
    mem = io.BytesIO()
    mem.write(output.getvalue().encode('utf-8'))
    mem.seek(0)
    return send_file(mem, mimetype='text/csv', as_attachment=True, download_name='assets_export.csv')


@assets_bp.route('/api/search')
@login_required
def api_search():
    q = (request.args.get('q') or '').strip()
    query = Asset.query
    if q:
        like = f"%{q}%"
        query = query.filter((Asset.name.ilike(like)) | (Asset.asset_tag.ilike(like)) | (Asset.serial_number.ilike(like)))
    assets = query.order_by(Asset.name.asc()).limit(50).all()
    return jsonify([
        { 'id': a.id, 'name': a.name, 'asset_tag': a.asset_tag, 'serial': a.serial_number, 'status': a.status } for a in assets
    ])
