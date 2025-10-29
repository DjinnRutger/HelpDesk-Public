from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from ..models import Contact, Ticket, Asset
from sqlalchemy import func, case
from .. import db


users_bp = Blueprint('users', __name__, url_prefix='/users')


@users_bp.route('/')
@login_required
def list_users():
    q = (request.args.get('q') or '').strip()
    query = Contact.query
    if q:
        like = f"%{q}%"
        query = query.filter((Contact.name.ilike(like)) | (Contact.email.ilike(like)))
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
    return render_template('users/list.html', contacts=contacts, q=q)


@users_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_user():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        email = (request.form.get('email') or '').strip().lower()
        notes = request.form.get('notes')
        inventory_url = request.form.get('inventory_url')
        ninja_url = request.form.get('ninja_url')
        if not email:
            flash('Email is required.', 'danger')
            return render_template('users/new.html', name=name, email=email, notes=notes, inventory_url=inventory_url, ninja_url=ninja_url)
        # Ensure unique email
        if Contact.query.filter_by(email=email).first():
            flash('A user with that email already exists.', 'danger')
            return render_template('users/new.html', name=name, email=email, notes=notes, inventory_url=inventory_url, ninja_url=ninja_url)
        c = Contact(name=name or None, email=email, notes=notes, inventory_url=inventory_url, ninja_url=ninja_url)
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
