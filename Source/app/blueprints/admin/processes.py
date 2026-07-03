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


@admin_bp.route('/processes-data')
@login_required
def processes_data():
    """Return process templates as JSON for AJAX loading"""
    processes = ProcessTemplate.query.order_by(ProcessTemplate.name.asc()).all()
    return jsonify([{
        'id': p.id,
        'name': p.name,
        'items_count': len(p.items) if p.items else 0
    } for p in processes])


@admin_bp.route('/processes/<int:template_id>/items-data')
@login_required
def process_items_data(template_id):
    """Return checklist items for a specific process template"""
    pt = ProcessTemplate.query.get_or_404(template_id)
    return jsonify([{
        'id': it.id,
        'type': it.type,
        'label': it.label,
        'assigned_tech': it.assigned_tech.name if hasattr(it, 'assigned_tech') and it.assigned_tech else None,
        'position': it.position
    } for it in pt.items])


@admin_bp.route('/processes/<int:template_id>/items/new', methods=['POST'])
@login_required
def process_item_new_ajax(template_id):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    if not is_ajax:
        return redirect(url_for('admin.process_edit', template_id=template_id))
    pt = ProcessTemplate.query.get_or_404(template_id)
    try:
        item_type = (request.form.get('type') or 'checkbox').strip()
        label = (request.form.get('label') or '').strip()
        assigned_tech_id = request.form.get('assigned_tech_id')
        position = request.form.get('position')
        if not label:
            return jsonify({'success': False, 'error': 'Label is required'}), 400
        try:
            assigned_tech_id = int(assigned_tech_id) if assigned_tech_id not in (None, '', '0') else None
        except Exception:
            assigned_tech_id = None
        try:
            position = int(position) if position not in (None, '') else (len(pt.items) + 1)
        except Exception:
            position = len(pt.items) + 1
        it = ProcessTemplateItem(template_id=pt.id, type=item_type, label=label, assigned_tech_id=assigned_tech_id, position=position)
        db.session.add(it)
        db.session.commit()
        return jsonify({'success': True, 'id': it.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/processes/<int:template_id>/items/<int:item_id>/update-ajax', methods=['POST'])
@login_required
def process_item_update_ajax(template_id, item_id):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    if not is_ajax:
        return redirect(url_for('admin.process_edit', template_id=template_id))
    it = ProcessTemplateItem.query.get_or_404(item_id)
    if it.template_id != template_id:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    try:
        item_type = (request.form.get('type') or it.type).strip()
        label = (request.form.get('label') or it.label).strip()
        assigned_tech_id = request.form.get('assigned_tech_id')
        if not label:
            return jsonify({'success': False, 'error': 'Label is required'}), 400
        try:
            assigned_tech_id = int(assigned_tech_id) if assigned_tech_id not in (None, '', '0') else None
        except Exception:
            assigned_tech_id = None
        it.type = item_type
        it.label = label
        it.assigned_tech_id = assigned_tech_id
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/processes/<int:template_id>/items/<int:item_id>/delete-ajax', methods=['POST'])
@login_required
def process_item_delete_ajax(template_id, item_id):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    if not is_ajax:
        return redirect(url_for('admin.process_edit', template_id=template_id))
    it = ProcessTemplateItem.query.get_or_404(item_id)
    if it.template_id != template_id:
        return jsonify({'success': False, 'error': 'Not found'}), 404
    try:
        db.session.delete(it)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@admin_bp.route('/processes/<int:template_id>/items/reorder-ajax', methods=['POST'])
@login_required
def process_items_reorder_ajax(template_id):
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    if not is_ajax:
        return redirect(url_for('admin.process_edit', template_id=template_id))
    pt = ProcessTemplate.query.get_or_404(template_id)
    data = request.get_json(silent=True) or {}
    order = data if isinstance(data, list) else data.get('order')
    if not isinstance(order, list) or not order:
        return jsonify({'success': False, 'error': 'invalid order'}), 400
    items = {it.id: it for it in ProcessTemplateItem.query.filter_by(template_id=pt.id).all()}
    pos = 1
    for item_id in order:
        try:
            iid = int(item_id)
        except Exception:
            continue
        it = items.get(iid)
        if it:
            it.position = pos
            pos += 1
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/processes')
@login_required
def processes():
    templates = ProcessTemplate.query.order_by(ProcessTemplate.name.asc()).all()
    return render_template('admin/processes.html', templates=templates)


@admin_bp.route('/processes/new', methods=['GET', 'POST'])
@login_required
def process_new():
    form = ProcessTemplateForm()
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    
    if form.validate_on_submit():
        try:
            pt = ProcessTemplate(name=form.name.data)
            db.session.add(pt)
            db.session.commit()
            
            if is_ajax:
                return jsonify({'success': True, 'id': pt.id})
            
            flash('Process created', 'success')
            return redirect(url_for('admin.process_edit', template_id=pt.id))
        except Exception as e:
            db.session.rollback()
            if is_ajax:
                return jsonify({'success': False, 'error': str(e)})
            flash(f'Error creating process: {str(e)}', 'danger')
    
    if is_ajax and request.method == 'POST' and not form.validate():
        return jsonify({'success': False, 'error': 'Validation failed', 'errors': form.errors})
    
    return render_template('admin/process_form.html', form=form, action='New')


@admin_bp.route('/processes/<int:template_id>/edit', methods=['GET', 'POST'])
@login_required
def process_edit(template_id):
    pt = ProcessTemplate.query.get_or_404(template_id)
    form = ProcessTemplateForm(obj=pt)
    item_form = ProcessTemplateItemForm()
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    
    # Populate tech choices
    techs = User.query.order_by(User.name.asc()).all()
    item_form.assigned_tech_id.choices = [(0, '— None —')] + [(u.id, u.name) for u in techs]

    if form.validate_on_submit():
        try:
            pt.name = form.name.data
            db.session.commit()
            
            if is_ajax:
                return jsonify({'success': True})
            
            flash('Process updated', 'success')
            return redirect(url_for('admin.process_edit', template_id=pt.id))
        except Exception as e:
            db.session.rollback()
            if is_ajax:
                return jsonify({'success': False, 'error': str(e)})
            flash(f'Error updating process: {str(e)}', 'danger')
    
    if is_ajax and request.method == 'POST' and not form.validate():
        return jsonify({'success': False, 'error': 'Validation failed'})

    return render_template('admin/process_form.html', form=form, action='Edit', template=pt, item_form=item_form)


@admin_bp.route('/processes/<int:template_id>/items', methods=['POST'])
@login_required
def process_add_item(template_id):
    pt = ProcessTemplate.query.get_or_404(template_id)
    form = ProcessTemplateItemForm()
    techs = User.query.order_by(User.name.asc()).all()
    form.assigned_tech_id.choices = [(0, '— None —')] + [(u.id, u.name) for u in techs]
    if form.validate_on_submit():
        position = form.position.data if form.position.data is not None else (len(pt.items) + 1)
        item = ProcessTemplateItem(
            template_id=pt.id,
            type=form.type.data,
            label=form.label.data,
            assigned_tech_id=None if form.assigned_tech_id.data == 0 else form.assigned_tech_id.data,
            position=position,
        )
        db.session.add(item)
        db.session.commit()
        flash('Item added', 'success')
    else:
        flash('Invalid item', 'danger')
    return redirect(url_for('admin.process_edit', template_id=pt.id))


@admin_bp.route('/processes/<int:template_id>/items/<int:item_id>/delete', methods=['POST'])
@login_required
def process_delete_item(template_id, item_id):
    item = ProcessTemplateItem.query.get_or_404(item_id)
    db.session.delete(item)
    db.session.commit()
    flash('Item deleted', 'success')
    return redirect(url_for('admin.process_edit', template_id=template_id))


@admin_bp.route('/processes/<int:template_id>/delete', methods=['POST'])
@login_required
def process_delete(template_id):
    pt = ProcessTemplate.query.get_or_404(template_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    
    try:
        db.session.delete(pt)
        db.session.commit()
        
        if is_ajax:
            return jsonify({'success': True})
        
        flash('Process deleted', 'success')
        return redirect(url_for('admin.processes'))
    except Exception as e:
        db.session.rollback()
        if is_ajax:
            return jsonify({'success': False, 'error': str(e)})
        flash(f'Error deleting process: {str(e)}', 'danger')
        return redirect(url_for('admin.processes'))


@admin_bp.route('/processes/<int:template_id>/items/<int:item_id>/update', methods=['POST'])
@login_required
def process_update_item(template_id, item_id):
    item = ProcessTemplateItem.query.get_or_404(item_id)
    if item.template_id != template_id:
        flash('Not found', 'danger')
        return redirect(url_for('admin.process_edit', template_id=template_id))
    form = ProcessTemplateItemForm()
    techs = User.query.order_by(User.name.asc()).all()
    form.assigned_tech_id.choices = [(0, '— None —')] + [(u.id, u.name) for u in techs]
    if form.validate_on_submit():
        item.type = form.type.data
        item.label = form.label.data
        item.assigned_tech_id = None if (form.assigned_tech_id.data or 0) == 0 else form.assigned_tech_id.data
        db.session.commit()
        flash('Item updated', 'success')
    else:
        flash('Invalid item update', 'danger')
    return redirect(url_for('admin.process_edit', template_id=template_id))


@admin_bp.route('/processes/<int:template_id>/items/reorder', methods=['POST'])
@login_required
def process_reorder_items(template_id):
    pt = ProcessTemplate.query.get_or_404(template_id)
    data = request.get_json(silent=True) or {}
    order = data if isinstance(data, list) else data.get('order')
    if not isinstance(order, list) or not order:
        return ({'error': 'invalid order'}, 400)
    # Fetch items for this template into a map
    items = {it.id: it for it in ProcessTemplateItem.query.filter_by(template_id=pt.id).all()}
    pos = 1
    for item_id in order:
        try:
            iid = int(item_id)
        except Exception:
            continue
        it = items.get(iid)
        if it:
            it.position = pos
            pos += 1
    db.session.commit()
    return ('', 204)
