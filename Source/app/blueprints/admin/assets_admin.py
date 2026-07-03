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


@admin_bp.route('/audits')
@login_required
def audits():
    q = (request.args.get('q') or '').strip()
    action = (request.args.get('action') or '').strip()
    asset_id = request.args.get('asset_id')
    query = AssetAudit.query
    if action:
        query = query.filter(AssetAudit.action == action)
    if asset_id and asset_id.isdigit():
        query = query.filter(AssetAudit.asset_id == int(asset_id))
    if q:
        like = f"%{q}%"
        # Search also in related asset name / tag
        matching_asset_ids = []
        try:
            matching_asset_ids = [a.id for a in Asset.query.filter(
                (Asset.name.ilike(like)) | (Asset.asset_tag.ilike(like))
            ).limit(500).all()]
        except Exception:
            matching_asset_ids = []
        if matching_asset_ids:
            query = query.filter(
                (AssetAudit.field.ilike(like)) |
                (AssetAudit.old_value.ilike(like)) |
                (AssetAudit.new_value.ilike(like)) |
                (AssetAudit.asset_id.in_(matching_asset_ids))
            )
        else:
            query = query.filter(
                (AssetAudit.field.ilike(like)) |
                (AssetAudit.old_value.ilike(like)) |
                (AssetAudit.new_value.ilike(like))
            )
    audits = query.order_by(AssetAudit.created_at.desc()).limit(500).all()
    asset_map = {a.id: a for a in Asset.query.filter(Asset.id.in_({a.asset_id for a in audits})).all()}
    users = {u.id: u for u in User.query.filter(User.id.in_({a.user_id for a in audits if a.user_id})).all()}
    return render_template('admin/audits.html', audits=audits, q=q, action=action, asset_id=asset_id, asset_map=asset_map, users=users)


def _picklist_model(kind: str):
    kind = (kind or '').lower()
    if kind == 'categories':
        return AssetCategory, 'Categories'
    if kind == 'manufacturers':
        return AssetManufacturer, 'Manufacturers'
    if kind == 'conditions':
        return AssetCondition, 'Conditions'
    if kind == 'locations':
        return AssetLocation, 'Locations'
    return None, ''


@admin_bp.route('/assets/picklists/<kind>', methods=['GET', 'POST'])
@login_required
def asset_picklist(kind):
    Model, title = _picklist_model(kind)
    if not Model:
        flash('Invalid picklist.', 'danger')
        return redirect(url_for('admin.index'))
    if request.method == 'POST':
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
        name = (request.form.get('name') or '').strip()
        if not name:
            if is_ajax:
                return jsonify({'success': False, 'error': 'Name is required.'}), 400
            flash('Name is required.', 'danger')
            return redirect(url_for('admin.asset_picklist', kind=kind))
        exists = Model.query.filter(Model.name.ilike(name)).first()
        if exists:
            if is_ajax:
                return jsonify({'success': False, 'error': 'That name already exists.'}), 400
            flash('That name already exists.', 'warning')
            return redirect(url_for('admin.asset_picklist', kind=kind))
        try:
            row = Model(name=name)
            db.session.add(row)
            db.session.commit()
            if is_ajax:
                return jsonify({'success': True, 'id': row.id})
            flash('Added.', 'success')
            return redirect(url_for('admin.asset_picklist', kind=kind))
        except Exception as e:
            db.session.rollback()
            if is_ajax:
                return jsonify({'success': False, 'error': str(e)}), 500
            flash('Failed to add item.', 'danger')
            return redirect(url_for('admin.asset_picklist', kind=kind))
    rows = Model.query.order_by(Model.name.asc()).all()
    return render_template('admin/asset_picklist.html', rows=rows, title=title, kind=kind)


@admin_bp.route('/assets/picklists/<kind>/<int:row_id>/delete', methods=['POST'])
@login_required
def asset_picklist_delete(kind, row_id):
    Model, _ = _picklist_model(kind)
    if not Model:
        flash('Invalid picklist.', 'danger')
        return redirect(url_for('admin.index'))
    row = Model.query.get_or_404(row_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    try:
        db.session.delete(row)
        db.session.commit()
        if is_ajax:
            return jsonify({'success': True})
        flash('Deleted.', 'success')
        return redirect(url_for('admin.asset_picklist', kind=kind))
    except Exception as e:
        db.session.rollback()
        if is_ajax:
            return jsonify({'success': False, 'error': str(e)}), 500
        flash('Failed to delete.', 'danger')
        return redirect(url_for('admin.asset_picklist', kind=kind))


@admin_bp.route('/assets/picklists/<kind>/<int:row_id>/edit', methods=['POST'])
@login_required
def asset_picklist_edit(kind, row_id):
    Model, _ = _picklist_model(kind)
    if not Model:
        flash('Invalid picklist.', 'danger')
        return redirect(url_for('admin.index'))
    row = Model.query.get_or_404(row_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    name = (request.form.get('name') or '').strip()
    if not name:
        if is_ajax:
            return jsonify({'success': False, 'error': 'Name is required.'}), 400
        flash('Name is required.', 'danger')
        return redirect(url_for('admin.asset_picklist', kind=kind))
    exists = Model.query.filter(Model.name.ilike(name), Model.id != row.id).first()
    if exists:
        if is_ajax:
            return jsonify({'success': False, 'error': 'That name already exists.'}), 400
        flash('That name already exists.', 'warning')
        return redirect(url_for('admin.asset_picklist', kind=kind))
    try:
        row.name = name
        db.session.commit()
        if is_ajax:
            return jsonify({'success': True})
        flash('Updated.', 'success')
        return redirect(url_for('admin.asset_picklist', kind=kind))
    except Exception as e:
        db.session.rollback()
        if is_ajax:
            return jsonify({'success': False, 'error': str(e)}), 500
        flash('Failed to update.', 'danger')
        return redirect(url_for('admin.asset_picklist', kind=kind))


@admin_bp.route('/assets/picklists-data')
@login_required
def assets_picklists_data():
    cats = AssetCategory.query.order_by(AssetCategory.name.asc()).all()
    mfgs = AssetManufacturer.query.order_by(AssetManufacturer.name.asc()).all()
    conds = AssetCondition.query.order_by(AssetCondition.name.asc()).all()
    locs = AssetLocation.query.order_by(AssetLocation.name.asc()).all()
    return jsonify({
        'categories': [{'id': c.id, 'name': c.name} for c in cats],
        'manufacturers': [{'id': m.id, 'name': m.name} for m in mfgs],
        'conditions': [{'id': c.id, 'name': c.name} for c in conds],
        'locations': [{'id': l.id, 'name': l.name} for l in locs]
    })


@admin_bp.route('/asset-spot-check/settings', methods=['POST'])
@login_required
def asset_spot_check_settings():
    if not admin_required():
        return redirect(url_for('dashboard.index'))
    # Read form values
    enabled = bool(request.form.get('spot_check_enabled'))
    frequency = (request.form.get('spot_check_frequency') or 'weekly').strip()
    if frequency not in ('weekly', 'monthly'):
        frequency = 'weekly'
    day_of_week = (request.form.get('spot_check_day_of_week') or '1').strip()
    day_of_month = (request.form.get('spot_check_day_of_month') or '1').strip()
    time_str = (request.form.get('spot_check_time') or '09:00').strip()
    mode = (request.form.get('spot_check_mode') or 'count').strip()
    if mode not in ('count', 'percent'):
        mode = 'count'
    count_val = (request.form.get('spot_check_count') or '10').strip()
    percent_val = (request.form.get('spot_check_percent') or '5').strip()
    assignee_id = (request.form.get('spot_check_assignee_id') or '').strip()
    # Validate day_of_week (0-6)
    try:
        dow = int(day_of_week)
        if dow < 0 or dow > 6:
            day_of_week = '1'
    except Exception:
        day_of_week = '1'
    # Validate day_of_month (1-31)
    try:
        dom = int(day_of_month)
        if dom < 1 or dom > 31:
            day_of_month = '1'
    except Exception:
        day_of_month = '1'
    # Normalize time HH:MM
    hh, mm = 9, 0
    try:
        parts = time_str.split(':')
        hh = int(parts[0] or 9)
        mm = int(parts[1] or 0)
        if hh < 0 or hh > 23 or mm < 0 or mm > 59:
            raise ValueError('invalid time')
    except Exception:
        time_str = '09:00'
        hh, mm = 9, 0
    # Validate count and percent
    try:
        count_int = max(1, int(count_val))
        count_val = str(count_int)
    except Exception:
        count_val = '10'
    try:
        percent_int = max(1, min(100, int(percent_val)))
        percent_val = str(percent_int)
    except Exception:
        percent_val = '5'
    # Persist settings
    Setting.set('ASSET_SPOT_CHECK_ENABLED', '1' if enabled else '0')
    Setting.set('ASSET_SPOT_CHECK_FREQUENCY', frequency)
    Setting.set('ASSET_SPOT_CHECK_DAY_OF_WEEK', day_of_week)
    Setting.set('ASSET_SPOT_CHECK_DAY_OF_MONTH', day_of_month)
    Setting.set('ASSET_SPOT_CHECK_TIME', time_str)
    Setting.set('ASSET_SPOT_CHECK_MODE', mode)
    Setting.set('ASSET_SPOT_CHECK_COUNT', count_val)
    Setting.set('ASSET_SPOT_CHECK_PERCENT', percent_val)
    Setting.set('ASSET_SPOT_CHECK_ASSIGNEE_ID', assignee_id if assignee_id.isdigit() else '')
    _bump_schedule_version()
    flash('Asset Spot Check settings updated.', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/asset-spot-check/run-now', methods=['POST'])
@login_required
def asset_spot_check_run_now():
    """Manually trigger an asset spot check."""
    if not admin_required():
        return redirect(url_for('dashboard.index'))
    try:
        app_obj = current_app._get_current_object()
        ticket_id = run_asset_spot_check(app_obj)
        if ticket_id:
            flash(f'Spot Check ticket #{ticket_id} created.', 'success')
        else:
            flash('No assets available for spot check.', 'info')
    except Exception as e:
        flash(f'Error running spot check: {str(e)}', 'danger')
    return redirect(url_for('admin.index'))


def run_asset_spot_check(app):
    """Create a spot check ticket with selected assets to verify."""
    with app.app_context():
        from ...models import Setting as _Setting, Asset as _Asset, Ticket as _Ticket, TicketTask as _TicketTask, User as _User
        from ... import db as _db
        from datetime import datetime as _dt
        import random
        
        # Check if enabled
        enabled = (_Setting.get('ASSET_SPOT_CHECK_ENABLED', '0') or '0') in ('1', 'true', 'on', 'yes')
        if not enabled:
            return None
        
        # Get settings
        mode = _Setting.get('ASSET_SPOT_CHECK_MODE', 'count') or 'count'
        try:
            count = int(_Setting.get('ASSET_SPOT_CHECK_COUNT', '10') or '10')
        except Exception:
            count = 10
        try:
            percent = int(_Setting.get('ASSET_SPOT_CHECK_PERCENT', '5') or '5')
        except Exception:
            percent = 5
        try:
            assignee_id = int(_Setting.get('ASSET_SPOT_CHECK_ASSIGNEE_ID', '') or '0') or None
        except Exception:
            assignee_id = None
        
        # Get eligible assets (deployed or available, not archived/retired)
        eligible_statuses = ['deployed', 'available', 'Active (deployed)', 'Active (deployable)']
        assets = _Asset.query.filter(
            _Asset.status.in_(eligible_statuses),
            _Asset.deleted_flag != True
        ).all()
        
        if not assets:
            return None
        
        # Calculate how many to select
        if mode == 'percent':
            num_to_select = max(1, int(len(assets) * percent / 100))
        else:
            num_to_select = min(count, len(assets))
        
        # Prioritize assets that haven't been spot checked or were checked longest ago
        assets_sorted = sorted(assets, key=lambda a: (a.last_spot_check or _dt.min))
        selected_assets = assets_sorted[:num_to_select]
        
        if not selected_assets:
            return None
        
        # Build ticket body
        body_lines = [
            "## Asset Spot Check Verification",
            "",
            "Please verify the following assets are present and accurate:",
            "",
        ]
        for asset in selected_assets:
            status = asset.status or 'unknown'
            assigned_to = asset.assigned_contact.name if asset.assigned_contact else 'Unassigned'
            location = asset.location or 'No location'
            body_lines.append(f"- **{asset.name}** (Tag: {asset.asset_tag or 'N/A'}, Serial: {asset.serial_number or 'N/A'})")
            body_lines.append(f"  - Status: {status} | Location: {location} | Assigned: {assigned_to}")
            body_lines.append("")
        
        body_lines.extend([
            "---",
            "**Instructions:**",
            "1. Physically locate each asset listed above",
            "2. Verify the asset tag and serial number match",
            "3. Confirm the location and assignment are accurate",
            "4. Check off each task below once verified",
            "5. Add notes for any discrepancies found",
        ])
        
        # Create ticket
        ticket = _Ticket(
            subject=f"Asset Spot Check - {_dt.now().strftime('%Y-%m-%d')}",
            body="\n".join(body_lines),
            status='new',
            priority='medium',
            assignee_id=assignee_id,
            source='system'
        )
        _db.session.add(ticket)
        _db.session.flush()
        
        # Add tasks for each asset
        for idx, asset in enumerate(selected_assets):
            task_label = f"Verify: {asset.name} (Tag: {asset.asset_tag or 'N/A'})"
            task = _TicketTask(
                ticket_id=ticket.id,
                label=task_label,
                list_name='Asset Verification',
                position=idx,
                asset_id=asset.id  # Link task to asset for spot check tracking
            )
            _db.session.add(task)
        
        _db.session.commit()
        return ticket.id  # Return ID instead of object to avoid session binding issues
