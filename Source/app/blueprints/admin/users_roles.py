from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file, current_app, jsonify, make_response, session
from flask_login import login_required, current_user
from ...forms import MSGraphForm, TechForm, ProcessTemplateForm, ProcessTemplateItemForm, AllowedDomainForm, DenyFilterForm, ClientApiForm
from ...models import Setting, User, Role, ProcessTemplate, ProcessTemplateItem, AllowedDomain, DenyFilter, Vendor, PurchaseOrder, Company, ShippingLocation, DocumentCategory, AssetAudit, Asset, AssetCategory, AssetManufacturer, AssetCondition, AssetLocation, ScheduledTicket, Ticket, TicketTask, TicketStatus, Tag, Report, ReportRun, ApiToken
from ... import db
from ...permissions import (
    MODULES, LEVEL_CHOICES, VIEW, EDIT,
    has_permission, is_administrator,
)
from ...utils.security import hash_password
from ...services.email_poll import poll_ms_graph
from ...services.ms_graph import get_msal_app, get_access_token
import sqlite3
import io
import tempfile
import shutil
import zipfile
from datetime import datetime
import os
import requests
import ftplib

from . import admin_bp, admin_required, _bump_schedule_version  # noqa: F401


@admin_bp.route('/techs-data')
@login_required
def techs_data():
    techs = User.query.order_by(User.name.asc()).all()
    return jsonify([{'id': u.id, 'name': u.name} for u in techs])


def _other_active_administrators(exclude_user_id):
    """Count active Administrator-role users other than the given user."""
    admin_role = Role.query.filter_by(builtin_key='administrator').first()
    if not admin_role:
        return 0
    return User.query.filter(
        User.role_id == admin_role.id,
        User.is_active == True,  # noqa: E712
        User.id != exclude_user_id,
    ).count()


@admin_bp.route('/techs/new', methods=['GET', 'POST'])
@login_required
def tech_new():
    form = TechForm()
    if form.validate_on_submit():
        user = User(
            name=form.name.data,
            email=form.email.data.lower(),
            is_active=form.is_active.data,
        )
        user.set_role(Role.query.get(form.role_id.data))
        if form.password.data:
            user.password_hash = hash_password(form.password.data)
        else:
            user.password_hash = hash_password('Password#123')
        db.session.add(user)
        db.session.commit()
        flash('Tech created', 'success')
        return redirect(url_for('admin.index'))
    return render_template('admin/tech_form.html', form=form, action='New')


@admin_bp.route('/techs/<int:user_id>/edit', methods=['GET', 'POST'])
@login_required
def tech_edit(user_id):
    user = User.query.get_or_404(user_id)
    form = TechForm(obj=user)
    if request.method == 'GET':
        form.role_id.data = user.role_id
    if form.validate_on_submit():
        new_role = Role.query.get(form.role_id.data)
        # Lockout guard: don't allow removing/deactivating the last Administrator
        loses_admin = user.is_administrator and (
            not new_role or new_role.builtin_key != 'administrator' or not form.is_active.data
        )
        if loses_admin and _other_active_administrators(user.id) == 0:
            flash('Cannot demote or deactivate the last active Administrator.', 'danger')
            return render_template('admin/tech_form.html', form=form, action='Edit')
        user.name = form.name.data
        user.email = form.email.data.lower()
        user.set_role(new_role)
        user.is_active = form.is_active.data
        if form.password.data:
            user.password_hash = hash_password(form.password.data)
        db.session.commit()
        flash('Tech updated', 'success')
        return redirect(url_for('admin.index'))
    return render_template('admin/tech_form.html', form=form, action='Edit')


@admin_bp.route('/techs/<int:user_id>/delete', methods=['POST'])
@login_required
def tech_delete(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('You cannot delete your own account.', 'danger')
        return redirect(url_for('admin.index'))
    if user.is_administrator and _other_active_administrators(user.id) == 0:
        flash('Cannot delete the last active Administrator.', 'danger')
        return redirect(url_for('admin.index'))
    db.session.delete(user)
    db.session.commit()
    flash('Tech deleted', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/roles')
@login_required
def roles():
    all_roles = Role.query.order_by(Role.is_system.desc(), Role.name.asc()).all()
    user_counts = {
        r.id: User.query.filter_by(role_id=r.id).count() for r in all_roles
    }
    return render_template('admin/roles.html', roles=all_roles, user_counts=user_counts)


def _parse_role_permissions():
    """Read perm_<key> selects from the posted form, clamped to valid levels."""
    perms = {}
    for m in MODULES:
        try:
            level = int(request.form.get(f"perm_{m['key']}", 0))
        except (TypeError, ValueError):
            level = 0
        perms[m['key']] = max(0, min(4, level))
    return perms


@admin_bp.route('/roles/new', methods=['GET', 'POST'])
@login_required
def role_new():
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        if not name:
            flash('Role name is required.', 'danger')
        elif Role.query.filter(Role.name.ilike(name)).first():
            flash('A role with that name already exists.', 'danger')
        else:
            role = Role(name=name, is_system=False)
            role.set_permissions(_parse_role_permissions())
            db.session.add(role)
            db.session.commit()
            flash('Role created', 'success')
            return redirect(url_for('admin.roles'))
    return render_template('admin/role_form.html', role=None, action='New',
                           modules=MODULES, level_choices=LEVEL_CHOICES)


@admin_bp.route('/roles/<int:role_id>/edit', methods=['GET', 'POST'])
@login_required
def role_edit(role_id):
    role = Role.query.get_or_404(role_id)
    if request.method == 'POST':
        if role.builtin_key == 'administrator':
            flash('The Administrator role cannot be modified.', 'danger')
            return redirect(url_for('admin.roles'))
        if not role.is_system:
            name = (request.form.get('name') or '').strip()
            if not name:
                flash('Role name is required.', 'danger')
                return render_template('admin/role_form.html', role=role, action='Edit',
                                       modules=MODULES, level_choices=LEVEL_CHOICES)
            clash = Role.query.filter(Role.name.ilike(name), Role.id != role.id).first()
            if clash:
                flash('A role with that name already exists.', 'danger')
                return render_template('admin/role_form.html', role=role, action='Edit',
                                       modules=MODULES, level_choices=LEVEL_CHOICES)
            role.name = name
        role.set_permissions(_parse_role_permissions())
        db.session.commit()
        flash('Role updated', 'success')
        return redirect(url_for('admin.roles'))
    return render_template('admin/role_form.html', role=role, action='Edit',
                           modules=MODULES, level_choices=LEVEL_CHOICES)


@admin_bp.route('/roles/<int:role_id>/delete', methods=['POST'])
@login_required
def role_delete(role_id):
    role = Role.query.get_or_404(role_id)
    if role.is_system:
        flash('Built-in roles cannot be deleted.', 'danger')
        return redirect(url_for('admin.roles'))
    assigned = User.query.filter_by(role_id=role.id).count()
    if assigned:
        flash(f'Cannot delete "{role.name}" — {assigned} user(s) still assigned to it.', 'danger')
        return redirect(url_for('admin.roles'))
    db.session.delete(role)
    db.session.commit()
    flash('Role deleted', 'success')
    return redirect(url_for('admin.roles'))
