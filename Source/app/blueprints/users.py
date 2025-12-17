from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_required
from ..models import Contact, Ticket, Asset, AssetAudit
from sqlalchemy import func, case, or_
from .. import db


users_bp = Blueprint('users', __name__, url_prefix='/users')


@users_bp.route('/api/search')
@login_required
def search_contacts():
    """API endpoint for searching contacts (used for manager selection dropdown)."""
    q = (request.args.get('q') or '').strip()
    exclude_id = request.args.get('exclude_id', type=int)  # Exclude a contact from results (e.g., when editing self)
    
    query = Contact.query
    if q:
        like = f"%{q}%"
        query = query.filter((Contact.name.ilike(like)) | (Contact.email.ilike(like)))
    if exclude_id:
        query = query.filter(Contact.id != exclude_id)
    
    contacts = query.order_by(func.lower(Contact.name).asc(), func.lower(Contact.email).asc()).limit(20).all()
    
    return jsonify([
        {
            'id': c.id,
            'name': c.name or '',
            'email': c.email,
            'display': f"{c.name} ({c.email})" if c.name else c.email
        }
        for c in contacts
    ])


@users_bp.route('/')
@login_required
def list_users():
    q = (request.args.get('q') or '').strip()
    show_all = request.args.get('show_all', '0') == '1'
    query = Contact.query
    if q:
        like = f"%{q}%"
        query = query.filter((Contact.name.ilike(like)) | (Contact.email.ilike(like)))
    # By default, hide archived users unless show_all is enabled
    if not show_all:
        query = query.filter((Contact.archived == False) | (Contact.archived == None))
    # Order alphabetically by first name (first word in name), case-insensitive.
    # If no space in name, use the entire name; if name is NULL, fall back to email.
    first_name = case(
        (func.instr(Contact.name, ' ') > 0, func.substr(Contact.name, 1, func.instr(Contact.name, ' ') - 1)),
        else_=Contact.name
    )
    contacts = (
        query
        .order_by(func.lower(first_name).asc(), func.lower(Contact.name).asc(), func.lower(Contact.email).asc())
        .limit(500)
        .all()
    )
    return render_template('users/list.html', contacts=contacts, q=q, show_all=show_all)


@users_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_user():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        notes = request.form.get('notes')
        inventory_url = request.form.get('inventory_url')
        ninja_url = request.form.get('ninja_url')
        manager_id = request.form.get('manager_id', type=int)
        if not email:
            flash('Email is required.', 'danger')
            return render_template('users/new.html', name=name, email=email, notes=notes, inventory_url=inventory_url, ninja_url=ninja_url, manager_id=manager_id)
        # Ensure unique email
        if Contact.query.filter_by(email=email).first():
            flash('A user with that email already exists.', 'danger')
            return render_template('users/new.html', name=name, email=email, notes=notes, inventory_url=inventory_url, ninja_url=ninja_url, manager_id=manager_id)
        c = Contact(name=name or None, email=email, notes=notes, inventory_url=inventory_url, ninja_url=ninja_url, manager_id=manager_id if manager_id else None)
        db.session.add(c)
        db.session.commit()
        flash('User added.', 'success')
        return redirect(url_for('users.show_user', contact_id=c.id))
    return render_template('users/new.html')


@users_bp.route('/<int:contact_id>', methods=['GET', 'POST'])
@login_required
def show_user(contact_id):
    c = Contact.query.get_or_404(contact_id)
    if request.method == 'POST':
        # Allow updating name and email with uniqueness check
        new_name = (request.form.get('name') or '').strip()
        new_email = (request.form.get('email') or '').strip().lower()
        if not new_email:
            flash('Email is required.', 'danger')
            return redirect(url_for('users.show_user', contact_id=c.id, edit='1'))
        existing = Contact.query.filter(Contact.email == new_email, Contact.id != c.id).first()
        if existing:
            flash('Another user already uses that email.', 'danger')
            return redirect(url_for('users.show_user', contact_id=c.id, edit='1'))
        c.name = new_name or None
        c.email = new_email
        c.notes = request.form.get('notes')
        c.inventory_url = request.form.get('inventory_url')
        c.ninja_url = request.form.get('ninja_url')
        # Handle manager assignment
        manager_id = request.form.get('manager_id', type=int)
        # Prevent setting self as manager
        if manager_id and manager_id != c.id:
            c.manager_id = manager_id
        elif manager_id == 0 or manager_id is None or request.form.get('manager_id') == '':
            c.manager_id = None
        db.session.commit()
        flash('User info updated', 'success')
        return redirect(url_for('users.show_user', contact_id=c.id))
    tickets = Ticket.query.filter((Ticket.requester_email == c.email) | (Ticket.requester_name == c.name)).order_by(Ticket.created_at.desc()).all()
    assets = Asset.query.filter_by(assigned_contact_id=c.id).order_by(Asset.name.asc()).all()
    edit = (request.args.get('edit') == '1')
    
    return render_template('users/detail.html', contact=c, tickets=tickets, assets=assets, edit=edit)


@users_bp.route('/<int:contact_id>/delete', methods=['POST'])
@login_required
def delete_user(contact_id):
    c = Contact.query.get_or_404(contact_id)
    # Prevent delete if any assets are still checked out to this contact
    asset_count = Asset.query.filter_by(assigned_contact_id=c.id).count()
    if asset_count:
        flash(f'Cannot delete: user has {asset_count} asset(s) checked out. Check them in first.', 'danger')
        return redirect(url_for('users.show_user', contact_id=c.id, edit='1'))
    try:
        # Just delete the contact; tickets remain with historical requester info (email/name stored on tickets)
        from .. import db
        db.session.delete(c)
        db.session.commit()
        flash('User deleted.', 'success')
        return redirect(url_for('users.list_users'))
    except Exception as e:
        from .. import db
        db.session.rollback()
        flash(f'Failed to delete user: {e}', 'danger')
        return redirect(url_for('users.show_user', contact_id=c.id, edit='1'))


@users_bp.route('/<int:contact_id>/archive', methods=['POST'])
@login_required
def archive_user(contact_id):
    """Toggle archive status for a contact."""
    c = Contact.query.get_or_404(contact_id)
    # Prevent archiving if user has assets checked out (but allow unarchiving)
    if not c.archived:
        asset_count = Asset.query.filter_by(assigned_contact_id=c.id).count()
        if asset_count:
            flash(f'Cannot archive: user has {asset_count} asset(s) checked out. Check them in first.', 'danger')
            return redirect(url_for('users.show_user', contact_id=c.id, edit='1'))
    c.archived = not c.archived
    db.session.commit()
    if c.archived:
        flash('User has been archived.', 'success')
    else:
        flash('User has been unarchived.', 'success')
    return redirect(url_for('users.show_user', contact_id=c.id, edit='1'))


@users_bp.route('/<int:contact_id>/asset-log')
@login_required
def asset_log_api(contact_id):
    """API endpoint for fetching asset log entries with pagination (returns HTML fragment)."""
    c = Contact.query.get_or_404(contact_id)
    contact_id_str = str(c.id)
    page = request.args.get('page', 1, type=int)
    per_page = 5
    
    asset_log_query = AssetAudit.query.filter(
        AssetAudit.action.in_(['checkout', 'checkin']),
        or_(
            AssetAudit.old_value == contact_id_str,
            AssetAudit.new_value == contact_id_str
        )
    ).order_by(AssetAudit.created_at.desc())
    
    pagination = asset_log_query.paginate(page=page, per_page=per_page, error_out=False)
    
    return render_template('users/_asset_log_modal_content.html', 
                           contact=c,
                           asset_log_entries=pagination.items,
                           asset_log_pagination=pagination)
