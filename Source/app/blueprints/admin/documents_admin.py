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


@admin_bp.route('/doccategories-data')
@login_required
def doccategories_data():
    """Return document categories as hierarchical JSON for AJAX loading"""
    root_cats = DocumentCategory.query.filter_by(parent_id=None).order_by(DocumentCategory.position.asc(), DocumentCategory.name.asc()).all()
    result = []
    for c in root_cats:
        subcats = sorted([{
            'id': sub.id,
            'name': sub.name,
            'parent_id': sub.parent_id,
            'documents_count': sub.documents.count()
        } for sub in c.subcategories], key=lambda x: x['name'])
        result.append({
            'id': c.id,
            'name': c.name,
            'parent_id': None,
            'documents_count': c.documents.count(),
            'subcategories': subcats
        })
    return jsonify(result)


@admin_bp.route('/documents', methods=['GET', 'POST'])
@admin_bp.route('/documents/categories', methods=['POST'])
@login_required
def documents_categories():
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        parent_id_raw = (request.form.get('parent_id') or '').strip()
        parent_id = None

        if not name:
            if is_ajax:
                return jsonify({'success': False, 'error': 'Category name is required'})
            flash('Category name is required', 'danger')
            return redirect(url_for('admin.documents_categories'))

        if parent_id_raw:
            try:
                parent_id = int(parent_id_raw)
                parent_cat = DocumentCategory.query.get(parent_id)
                if not parent_cat:
                    if is_ajax:
                        return jsonify({'success': False, 'error': 'Invalid parent category'})
                    flash('Invalid parent category', 'danger')
                    return redirect(url_for('admin.documents_categories'))
                if parent_cat.parent_id is not None:
                    if is_ajax:
                        return jsonify({'success': False, 'error': 'Sub-categories cannot have sub-categories'})
                    flash('Sub-categories cannot have sub-categories', 'danger')
                    return redirect(url_for('admin.documents_categories'))
            except ValueError:
                if is_ajax:
                    return jsonify({'success': False, 'error': 'Invalid parent category'})
                flash('Invalid parent category', 'danger')
                return redirect(url_for('admin.documents_categories'))

        exists = DocumentCategory.query.filter(
            DocumentCategory.name.ilike(name),
            DocumentCategory.parent_id == parent_id
        ).first()
        if exists:
            if is_ajax:
                return jsonify({'success': False, 'error': 'A category with that name already exists at this level'})
            flash('A category with that name already exists at this level.', 'warning')
            return redirect(url_for('admin.documents_categories'))

        try:
            c = DocumentCategory(name=name, parent_id=parent_id)
            db.session.add(c)
            db.session.commit()

            if is_ajax:
                return jsonify({'success': True, 'id': c.id})

            flash('Category created', 'success')
            return redirect(url_for('admin.documents_categories'))
        except Exception as e:
            db.session.rollback()
            if is_ajax:
                return jsonify({'success': False, 'error': str(e)})
            flash(f'Error creating category: {str(e)}', 'danger')
            return redirect(url_for('admin.documents_categories'))

    cats = DocumentCategory.query.filter_by(parent_id=None).order_by(DocumentCategory.position.asc(), DocumentCategory.name.asc()).all()
    return render_template('admin/documents.html', categories=cats)


@admin_bp.route('/documents/<int:category_id>/delete', methods=['POST'])
@admin_bp.route('/documents/categories/<int:category_id>/rename', methods=['POST'])
@login_required
def documents_category_rename(category_id):
    c = DocumentCategory.query.get_or_404(category_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    new_name = (request.form.get('name') or '').strip()
    if not new_name:
        if is_ajax:
            return jsonify({'success': False, 'error': 'Category name is required'})
        flash('Category name is required', 'danger')
        return redirect(url_for('admin.documents_categories'))
    # Check uniqueness within the same parent, excluding self
    exists = DocumentCategory.query.filter(
        DocumentCategory.id != c.id,
        DocumentCategory.parent_id == c.parent_id,
        DocumentCategory.name.ilike(new_name)
    ).first()
    if exists:
        if is_ajax:
            return jsonify({'success': False, 'error': 'A category with that name already exists at this level'})
        flash('A category with that name already exists at this level.', 'warning')
        return redirect(url_for('admin.documents_categories'))
    try:
        c.name = new_name
        db.session.commit()
        if is_ajax:
            return jsonify({'success': True, 'id': c.id, 'name': c.name})
        flash('Category renamed', 'success')
        return redirect(url_for('admin.documents_categories'))
    except Exception as e:
        db.session.rollback()
        if is_ajax:
            return jsonify({'success': False, 'error': str(e)})
        flash(f'Error renaming category: {str(e)}', 'danger')
        return redirect(url_for('admin.documents_categories'))


@admin_bp.route('/documents/categories/<int:category_id>/delete', methods=['POST'])
@login_required
def documents_category_delete(category_id):
    c = DocumentCategory.query.get_or_404(category_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    
    try:
        db.session.delete(c)
        db.session.commit()
        
        if is_ajax:
            return jsonify({'success': True})
        
        flash('Category deleted', 'success')
        return redirect(url_for('admin.documents_categories'))
    except Exception as e:
        db.session.rollback()
        if is_ajax:
            return jsonify({'success': False, 'error': str(e)})
        flash(f'Error deleting category: {str(e)}', 'danger')
        return redirect(url_for('admin.documents_categories'))


@admin_bp.route('/documents/categories/reorder', methods=['POST'])
@login_required
def documents_categories_reorder():
    data = request.get_json()
    if not data or 'categories' not in data:
        return jsonify({'success': False, 'error': 'Invalid data'}), 400
    try:
        for item in data['categories']:
            cat = DocumentCategory.query.get(item['id'])
            if not cat:
                continue
            new_parent_id = item.get('parent_id')
            if new_parent_id is not None:
                parent = DocumentCategory.query.get(new_parent_id)
                if not parent or parent.parent_id is not None:
                    return jsonify({'success': False, 'error': 'Cannot nest more than one level deep'}), 400
            cat.parent_id = new_parent_id
            cat.position = item.get('position', 0)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
