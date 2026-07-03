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


@admin_bp.route('/vendors')
@login_required
def vendors():
    q = request.args.get('q', '').strip()
    query = Vendor.query
    if q:
        like = f"%{q}%"
        query = query.filter(
            (Vendor.company_name.ilike(like)) |
            (Vendor.contact_name.ilike(like)) |
            (Vendor.email.ilike(like))
        )
    vendors = query.order_by(Vendor.company_name.asc()).all()
    return render_template('admin/vendors.html', vendors=vendors, q=q)


@admin_bp.route('/vendors/new', methods=['GET', 'POST'])
@login_required
def vendor_new():
    if request.method == 'POST':
        # Check if AJAX request
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
        
        company = (request.form.get('company_name') or '').strip()
        if not company:
            if is_ajax:
                return jsonify({'success': False, 'error': 'Company name is required'}), 400
            flash('Company name is required', 'danger')
            return redirect(url_for('admin.vendor_new'))
        
        try:
            v = Vendor(
                company_name=company,
                contact_name=request.form.get('contact_name') or None,
                email=request.form.get('email') or None,
                address=request.form.get('address') or None,
                phone=request.form.get('phone') or None,
            )
            db.session.add(v)
            db.session.commit()
            
            if is_ajax:
                return jsonify({'success': True, 'vendor_id': v.id})
            flash('Vendor created', 'success')
            return redirect(url_for('admin.vendors'))
        except Exception as e:
            db.session.rollback()
            if is_ajax:
                return jsonify({'success': False, 'error': str(e)}), 500
            flash(f'Error: {str(e)}', 'danger')
            return redirect(url_for('admin.vendor_new'))
    
    return render_template('admin/vendor_form.html', action='New', vendor=None)


@admin_bp.route('/vendors/<int:vendor_id>/edit', methods=['GET', 'POST'])
@login_required
def vendor_edit(vendor_id):
    v = Vendor.query.get_or_404(vendor_id)
    if request.method == 'POST':
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
        
        company = (request.form.get('company_name') or '').strip()
        if not company:
            if is_ajax:
                return jsonify({'success': False, 'error': 'Company name is required'}), 400
            flash('Company name is required', 'danger')
            return redirect(url_for('admin.vendor_edit', vendor_id=v.id))
        
        try:
            v.company_name = company
            v.contact_name = request.form.get('contact_name') or None
            v.email = request.form.get('email') or None
            v.address = request.form.get('address') or None
            v.phone = request.form.get('phone') or None
            db.session.commit()
            
            if is_ajax:
                return jsonify({'success': True, 'vendor_id': v.id})
            flash('Vendor updated', 'success')
            return redirect(url_for('admin.vendors'))
        except Exception as e:
            db.session.rollback()
            if is_ajax:
                return jsonify({'success': False, 'error': str(e)}), 500
            flash(f'Error: {str(e)}', 'danger')
            return redirect(url_for('admin.vendor_edit', vendor_id=v.id))
    
    return render_template('admin/vendor_form.html', action='Edit', vendor=v)


@admin_bp.route('/vendors/<int:vendor_id>/delete', methods=['POST'])
@login_required
def vendor_delete(vendor_id):
    v = Vendor.query.get_or_404(vendor_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    
    # Prevent delete if used by POs
    used = PurchaseOrder.query.filter_by(vendor_id=v.id).count()
    if used:
        if is_ajax:
            return jsonify({'success': False, 'error': 'Cannot delete: used by existing purchase orders'}), 400
        flash('Cannot delete vendor: it is used by existing purchase orders.', 'warning')
        return redirect(url_for('admin.vendors'))
    
    try:
        db.session.delete(v)
        db.session.commit()
        if is_ajax:
            return jsonify({'success': True})
        flash('Vendor deleted', 'success')
        return redirect(url_for('admin.vendors'))
    except Exception as e:
        db.session.rollback()
        if is_ajax:
            return jsonify({'success': False, 'error': str(e)}), 500
        flash(f'Error: {str(e)}', 'danger')
        return redirect(url_for('admin.vendors'))


@admin_bp.route('/vendors/<int:vendor_id>')
@login_required
def vendor_detail(vendor_id):
    v = Vendor.query.get_or_404(vendor_id)
    # List purchase orders that pointed to this vendor (by vendor_id or name match fallback)
    pos = PurchaseOrder.query.filter(
        (PurchaseOrder.vendor_id == v.id) | (PurchaseOrder.vendor_name == v.company_name)
    ).order_by(PurchaseOrder.created_at.desc()).all()
    return render_template('admin/vendor_detail.html', vendor=v, pos=pos)


@admin_bp.route('/vendors-data')
@login_required
def vendors_data():
    """Return vendors as JSON for AJAX loading"""
    vendors = Vendor.query.order_by(Vendor.company_name.asc()).all()
    return jsonify({
        'vendors': [{
            'id': v.id,
            'company_name': v.company_name,
            'contact_name': v.contact_name,
            'email': v.email,
            'phone': v.phone,
            'address': v.address
        } for v in vendors]
    })


@admin_bp.route('/companies-data')
@login_required
def companies_data():
    """Return companies as JSON for AJAX loading"""
    companies = Company.query.order_by(Company.name.asc()).all()
    return jsonify({
        'companies': [{
            'id': c.id,
            'name': c.name,
            'address': c.address,
            'city': c.city,
            'state': c.state,
            'zip_code': c.zip_code
        } for c in companies]
    })


@admin_bp.route('/shipping-data')
@login_required
def shipping_data():
    """Return shipping locations as JSON for AJAX loading"""
    locations = ShippingLocation.query.order_by(ShippingLocation.name.asc()).all()
    return jsonify({
        'locations': [{
            'id': s.id,
            'name': s.name,
            'address': s.address,
            'city': s.city,
            'state': s.state,
            'zip_code': s.zip_code,
            'tax_rate': s.tax_rate or 0.0
        } for s in locations]
    })


@admin_bp.route('/companies')
@login_required
def companies():
    q = request.args.get('q','').strip()
    query = Company.query
    if q:
        like = f"%{q}%"
        query = query.filter(Company.name.ilike(like))
    companies = query.order_by(Company.name.asc()).all()
    return render_template('admin/companies.html', companies=companies, q=q)


@admin_bp.route('/companies/new', methods=['GET','POST'])
@login_required
def company_new():
    if request.method == 'POST':
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
        
        name = (request.form.get('name') or '').strip()
        if not name:
            if is_ajax:
                return jsonify({'success': False, 'error': 'Company name is required'}), 400
            flash('Company name is required', 'danger')
            return redirect(url_for('admin.company_new'))
        
        try:
            c = Company(
                name=name,
                address=request.form.get('address') or None,
                city=request.form.get('city') or None,
                state=request.form.get('state') or None,
                zip_code=request.form.get('zip_code') or None,
            )
            db.session.add(c)
            db.session.commit()
            
            if is_ajax:
                return jsonify({'success': True, 'company_id': c.id})
            flash('Company created', 'success')
            return redirect(url_for('admin.companies'))
        except Exception as e:
            db.session.rollback()
            if is_ajax:
                return jsonify({'success': False, 'error': str(e)}), 500
            flash(f'Error: {str(e)}', 'danger')
            return redirect(url_for('admin.company_new'))
    
    return render_template('admin/company_form.html', action='New', company=None)


@admin_bp.route('/companies/<int:company_id>/edit', methods=['GET','POST'])
@login_required
def company_edit(company_id):
    c = Company.query.get_or_404(company_id)
    if request.method == 'POST':
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
        
        name = (request.form.get('name') or '').strip()
        if not name:
            if is_ajax:
                return jsonify({'success': False, 'error': 'Company name is required'}), 400
            flash('Company name is required', 'danger')
            return redirect(url_for('admin.company_edit', company_id=c.id))
        
        try:
            c.name = name
            c.address = request.form.get('address') or None
            c.city = request.form.get('city') or None
            c.state = request.form.get('state') or None
            c.zip_code = request.form.get('zip_code') or None
            db.session.commit()
            
            if is_ajax:
                return jsonify({'success': True, 'company_id': c.id})
            flash('Company updated', 'success')
            return redirect(url_for('admin.companies'))
        except Exception as e:
            db.session.rollback()
            if is_ajax:
                return jsonify({'success': False, 'error': str(e)}), 500
            flash(f'Error: {str(e)}', 'danger')
            return redirect(url_for('admin.company_edit', company_id=c.id))
    
    return render_template('admin/company_form.html', action='Edit', company=c)


@admin_bp.route('/companies/<int:company_id>/delete', methods=['POST'])
@login_required
def company_delete(company_id):
    c = Company.query.get_or_404(company_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    
    try:
        db.session.delete(c)
        db.session.commit()
        if is_ajax:
            return jsonify({'success': True})
        flash('Company deleted', 'success')
        return redirect(url_for('admin.companies'))
    except Exception as e:
        db.session.rollback()
        if is_ajax:
            return jsonify({'success': False, 'error': str(e)}), 500
        flash(f'Error: {str(e)}', 'danger')
        return redirect(url_for('admin.companies'))


@admin_bp.route('/shipping')
@login_required
def shipping_locations():
    locs = ShippingLocation.query.order_by(ShippingLocation.name.asc()).all()
    return render_template('admin/shipping.html', locations=locs)


@admin_bp.route('/shipping/new', methods=['GET','POST'])
@login_required
def shipping_new():
    if request.method == 'POST':
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
        
        name = (request.form.get('name') or '').strip()
        if not name:
            if is_ajax:
                return jsonify({'success': False, 'error': 'Name is required'}), 400
            flash('Name is required', 'danger')
            return redirect(url_for('admin.shipping_new'))
        
        try:
            # Parse tax_rate as percent to decimal
            tax_rate_raw = (request.form.get('tax_rate') or '').strip()
            tax_rate = 0.0
            if tax_rate_raw:
                try:
                    tax_rate = max(0.0, min(100.0, float(tax_rate_raw))) / 100.0
                except ValueError:
                    tax_rate = 0.0
            
            s = ShippingLocation(
                name=name,
                address=request.form.get('address') or None,
                city=request.form.get('city') or None,
                state=request.form.get('state') or None,
                zip_code=request.form.get('zip_code') or None,
                tax_rate=tax_rate,
            )
            db.session.add(s)
            db.session.commit()
            
            if is_ajax:
                return jsonify({'success': True, 'location_id': s.id})
            flash('Shipping location created', 'success')
            return redirect(url_for('admin.shipping_locations'))
        except Exception as e:
            db.session.rollback()
            if is_ajax:
                return jsonify({'success': False, 'error': str(e)}), 500
            flash(f'Error: {str(e)}', 'danger')
            return redirect(url_for('admin.shipping_new'))
    
    return render_template('admin/shipping_form.html', action='New', location=None)


@admin_bp.route('/shipping/<int:loc_id>/edit', methods=['GET','POST'])
@login_required
def shipping_edit(loc_id):
    s = ShippingLocation.query.get_or_404(loc_id)
    if request.method == 'POST':
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
        
        name = (request.form.get('name') or '').strip()
        if not name:
            if is_ajax:
                return jsonify({'success': False, 'error': 'Name is required'}), 400
            flash('Name is required', 'danger')
            return redirect(url_for('admin.shipping_edit', loc_id=s.id))
        
        try:
            s.name = name
            s.address = request.form.get('address') or None
            s.city = request.form.get('city') or None
            s.state = request.form.get('state') or None
            s.zip_code = request.form.get('zip_code') or None
            
            # Parse tax_rate as percent to decimal
            tax_rate_raw = (request.form.get('tax_rate') or '').strip()
            if tax_rate_raw == '':
                s.tax_rate = 0.0
            else:
                try:
                    s.tax_rate = max(0.0, min(100.0, float(tax_rate_raw))) / 100.0
                except ValueError:
                    pass
            
            db.session.commit()
            
            if is_ajax:
                return jsonify({'success': True, 'location_id': s.id})
            flash('Shipping location updated', 'success')
            return redirect(url_for('admin.shipping_locations'))
        except Exception as e:
            db.session.rollback()
            if is_ajax:
                return jsonify({'success': False, 'error': str(e)}), 500
            flash(f'Error: {str(e)}', 'danger')
            return redirect(url_for('admin.shipping_edit', loc_id=s.id))
    
    return render_template('admin/shipping_form.html', action='Edit', location=s)


@admin_bp.route('/shipping/<int:loc_id>/delete', methods=['POST'])
@login_required
def shipping_delete(loc_id):
    s = ShippingLocation.query.get_or_404(loc_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    
    try:
        db.session.delete(s)
        db.session.commit()
        if is_ajax:
            return jsonify({'success': True})
        flash('Shipping location deleted', 'success')
        return redirect(url_for('admin.shipping_locations'))
    except Exception as e:
        db.session.rollback()
        if is_ajax:
            return jsonify({'success': False, 'error': str(e)}), 500
        flash(f'Error: {str(e)}', 'danger')
        return redirect(url_for('admin.shipping_locations'))
