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


@admin_bp.route('/ticket-statuses-data')
@login_required
def ticket_statuses_data():
    """Return ticket statuses as JSON for AJAX loading"""
    # Ensure default statuses exist
    TicketStatus.ensure_defaults()
    statuses = TicketStatus.query.order_by(TicketStatus.position).all()
    return jsonify({
        'statuses': [{
            'id': s.id,
            'name': s.name,
            'label': s.label,
            'color': s.color,
            'is_closed': s.is_closed,
            'position': s.position
        } for s in statuses]
    })


@admin_bp.route('/ticket-statuses/new', methods=['POST'])
@login_required
def ticket_status_new():
    """Create a new ticket status"""
    try:
        name = (request.form.get('name') or '').strip().lower().replace(' ', '_')
        label = (request.form.get('label') or '').strip()
        color = (request.form.get('color') or 'secondary').strip()
        is_closed = request.form.get('is_closed') in ('1', 'true', 'on', 'yes')
        
        if not name or not label:
            return jsonify({'success': False, 'error': 'Name and label are required'}), 400
        
        # Check for duplicate name
        existing = TicketStatus.query.filter_by(name=name).first()
        if existing:
            return jsonify({'success': False, 'error': f'Status "{name}" already exists'}), 400
        
        # Get next position
        max_pos = db.session.query(db.func.max(TicketStatus.position)).scalar() or 0
        
        status = TicketStatus(
            name=name,
            label=label,
            color=color,
            is_closed=is_closed,
            position=max_pos + 1
        )
        db.session.add(status)
        db.session.commit()
        return jsonify({'success': True, 'id': status.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/ticket-statuses/<int:status_id>/edit', methods=['POST'])
@login_required
def ticket_status_edit(status_id):
    """Update an existing ticket status"""
    status = TicketStatus.query.get_or_404(status_id)
    try:
        name = (request.form.get('name') or '').strip().lower().replace(' ', '_')
        label = (request.form.get('label') or '').strip()
        color = (request.form.get('color') or 'secondary').strip()
        is_closed = request.form.get('is_closed') in ('1', 'true', 'on', 'yes')
        
        if not name or not label:
            return jsonify({'success': False, 'error': 'Name and label are required'}), 400
        
        # Check for duplicate name (excluding self)
        existing = TicketStatus.query.filter(TicketStatus.name == name, TicketStatus.id != status_id).first()
        if existing:
            return jsonify({'success': False, 'error': f'Status "{name}" already exists'}), 400
        
        # If name changed, update all tickets using the old status name
        if status.name != name:
            Ticket.query.filter_by(status=status.name).update({'status': name})
        
        status.name = name
        status.label = label
        status.color = color
        status.is_closed = is_closed
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/ticket-statuses/<int:status_id>/delete', methods=['POST'])
@login_required
def ticket_status_delete(status_id):
    """Delete a ticket status"""
    status = TicketStatus.query.get_or_404(status_id)
    try:
        # Don't allow deletion if it's the only status
        if TicketStatus.query.count() <= 1:
            return jsonify({'success': False, 'error': 'Cannot delete the only remaining status'}), 400
        
        # Check if any tickets use this status
        ticket_count = Ticket.query.filter_by(status=status.name).count()
        if ticket_count > 0:
            return jsonify({'success': False, 'error': f'Cannot delete: {ticket_count} ticket(s) use this status'}), 400
        
        db.session.delete(status)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/ticket-statuses/reorder', methods=['POST'])
@login_required
def ticket_statuses_reorder():
    """Reorder ticket statuses"""
    try:
        order = request.json.get('order', [])
        for idx, status_id in enumerate(order):
            status = TicketStatus.query.get(status_id)
            if status:
                status.position = idx
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/tags', methods=['GET', 'POST'])
@login_required
def tags():
    """List and create tags."""
    if request.method == 'POST':
        name     = request.form.get('name', '').strip()
        color    = request.form.get('color', '').strip() or None
        parent_id = request.form.get('parent_id', type=int) or None
        keywords = request.form.get('keywords', '').strip() or None
        if not name:
            flash('Tag name is required.', 'danger')
            return redirect(url_for('admin.tags'))
        tag = Tag(
            name=name,
            color=color,
            parent_id=parent_id,
            position=Tag.query.filter_by(parent_id=parent_id).count(),
            keywords=keywords,
        )
        db.session.add(tag)
        db.session.commit()
        flash(f'Tag "{name}" created.', 'success')
        return redirect(url_for('admin.tags'))

    root_tags = Tag.query.filter_by(parent_id=None).order_by(Tag.position).all()
    all_tags  = Tag.query.order_by(Tag.parent_id.asc(), Tag.position).all()
    return render_template('admin/tags.html', root_tags=root_tags, all_tags=all_tags)


@admin_bp.route('/tags/<int:tag_id>/edit', methods=['POST'])
@login_required
def tags_edit(tag_id):
    """Edit a tag's name, color, and parent."""
    tag = Tag.query.get_or_404(tag_id)
    name      = request.form.get('name', '').strip()
    color     = request.form.get('color', '').strip() or None
    parent_id = request.form.get('parent_id', type=int) or None
    keywords  = request.form.get('keywords', '').strip() or None

    if not name:
        flash('Tag name is required.', 'danger')
        return redirect(url_for('admin.tags'))

    # Prevent assigning a tag as its own parent or creating a cycle
    if parent_id == tag.id:
        flash('A tag cannot be its own parent.', 'danger')
        return redirect(url_for('admin.tags'))

    tag.name      = name
    tag.color     = color
    tag.parent_id = parent_id
    tag.keywords  = keywords
    db.session.commit()
    flash(f'Tag "{name}" updated.', 'success')
    return redirect(url_for('admin.tags'))


@admin_bp.route('/tags/<int:tag_id>/delete', methods=['POST'])
@login_required
def tags_delete(tag_id):
    """Delete a tag. Blocked if tickets or assets use it."""
    tag = Tag.query.get_or_404(tag_id)
    if tag.tickets:
        flash(f'Cannot delete "{tag.name}": it is used by {len(tag.tickets)} ticket(s).', 'danger')
        return redirect(url_for('admin.tags'))
    if tag.assets:
        flash(f'Cannot delete "{tag.name}": it is used by {len(tag.assets)} asset(s).', 'danger')
        return redirect(url_for('admin.tags'))
    db.session.delete(tag)
    db.session.commit()
    flash(f'Tag "{tag.name}" deleted.', 'success')
    return redirect(url_for('admin.tags'))


@admin_bp.route('/tags/reorder', methods=['POST'])
@login_required
def tags_reorder():
    """Drag-and-drop reorder. Expects JSON: {"tags": [{id, parent_id, position}]}"""
    data = request.get_json()
    if not data or 'tags' not in data:
        return jsonify({'success': False, 'error': 'Invalid data'}), 400
    try:
        for item in data['tags']:
            tag = Tag.query.get(item['id'])
            if not tag:
                continue
            new_parent_id = item.get('parent_id')
            # Prevent a tag from being its own parent (safety net for drag-and-drop)
            if new_parent_id == tag.id:
                new_parent_id = None
            tag.parent_id = new_parent_id
            tag.position  = item.get('position', 0)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


_DEFAULT_TAGS = [
    {'name': 'Hardware', 'color': 'primary', 'children': [
        'Laptop', 'Desktop', 'Monitor', 'Printer', 'Phone / Mobile', 'Peripheral',
    ]},
    {'name': 'Software', 'color': 'info', 'children': [
        'Office / M365', 'Operating System', 'Email', 'Browser', 'Application',
    ]},
    {'name': 'Network', 'color': 'success', 'children': [
        'WiFi', 'VPN', 'Internet', 'Shared Drive',
    ]},
    {'name': 'Account', 'color': 'warning', 'children': [
        'Password Reset', 'Access Request', 'New User Setup', 'Offboarding',
    ]},
    {'name': 'Other', 'color': 'secondary', 'children': []},
]


@admin_bp.route('/tags/reset', methods=['POST'])
@login_required
def tags_reset():
    """Delete all unused tags and restore the default set."""
    all_tags = Tag.query.all()
    skipped = []
    for tag in all_tags:
        in_use = bool(tag.tickets) or bool(tag.assets)
        if in_use:
            skipped.append(tag.name)
        else:
            db.session.delete(tag)
    db.session.commit()

    # Re-create defaults (skip any whose name already exists after the above purge)
    existing_names = {t.name for t in Tag.query.all()}
    pos = Tag.query.filter_by(parent_id=None).count()
    for group in _DEFAULT_TAGS:
        if group['name'] not in existing_names:
            parent = Tag(name=group['name'], color=group['color'], parent_id=None, position=pos)
            db.session.add(parent)
            db.session.flush()  # get parent.id
            existing_names.add(group['name'])
            pos += 1
        else:
            parent = Tag.query.filter_by(name=group['name'], parent_id=None).first()

        if parent:
            child_pos = Tag.query.filter_by(parent_id=parent.id).count()
            for child_name in group['children']:
                if child_name not in existing_names:
                    db.session.add(Tag(name=child_name, color=None, parent_id=parent.id, position=child_pos))
                    existing_names.add(child_name)
                    child_pos += 1

    db.session.commit()

    if skipped:
        flash(f'Reset complete. Default tags restored. Some tags still in use could not be removed: {", ".join(skipped)}.', 'warning')
    else:
        flash('All tags reset to defaults.', 'success')
    return redirect(url_for('admin.tags'))
