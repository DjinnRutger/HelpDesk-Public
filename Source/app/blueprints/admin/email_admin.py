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


@admin_bp.route('/email', methods=['GET', 'POST'])
@login_required
def email_settings():
    form = AllowedDomainForm()
    deny_form = DenyFilterForm()
    # Add allowed domain
    if form.validate_on_submit() and 'domain' in request.form:
        domain = form.domain.data.strip().lower()
        if not domain:
            flash('Domain is required', 'danger')
            return redirect(url_for('admin.email_settings'))
        if not AllowedDomain.query.filter_by(domain=domain).first():
            db.session.add(AllowedDomain(domain=domain))
            db.session.commit()
            flash('Domain added', 'success')
        else:
            flash('Domain already exists', 'info')
        return redirect(url_for('admin.email_settings'))
    # Add deny filter phrase
    if deny_form.validate_on_submit() and 'phrase' in request.form:
        phrase = deny_form.phrase.data.strip()
        if not phrase:
            flash('Phrase is required', 'danger')
            return redirect(url_for('admin.email_settings'))
        if not DenyFilter.query.filter_by(phrase=phrase).first():
            db.session.add(DenyFilter(phrase=phrase))
            db.session.commit()
            flash('Deny filter added', 'success')
        else:
            flash('Phrase already exists', 'info')
        return redirect(url_for('admin.email_settings'))
    domains = AllowedDomain.query.order_by(AllowedDomain.domain.asc()).all()
    denies = DenyFilter.query.order_by(DenyFilter.phrase.asc()).all()
    # Email log retention settings
    email_log_retention_enabled = (Setting.get('EMAIL_LOG_RETENTION_ENABLED', '0') or '0') in ('1', 'true', 'on', 'yes')
    email_log_retention_days = int(Setting.get('EMAIL_LOG_RETENTION_DAYS', '90') or '90')
    email_log_no_new_messages = (Setting.get('EMAIL_LOG_NO_NEW_MESSAGES', '1') or '1') in ('1', 'true', 'on', 'yes')
    return render_template(
        'admin/email_settings.html',
        form=form,
        domains=domains,
        deny_form=deny_form,
        denies=denies,
        email_log_retention_enabled=email_log_retention_enabled,
        email_log_retention_days=email_log_retention_days,
        email_log_no_new_messages=email_log_no_new_messages
    )


@admin_bp.route('/email/domains/<int:domain_id>/delete', methods=['POST'])
@login_required
def email_delete_domain(domain_id):
    d = AllowedDomain.query.get_or_404(domain_id)
    db.session.delete(d)
    db.session.commit()
    flash('Domain deleted', 'success')
    return redirect(url_for('admin.email_settings'))


@admin_bp.route('/email/denies/<int:deny_id>/delete', methods=['POST'])
@login_required
def email_delete_deny(deny_id):
    d = DenyFilter.query.get_or_404(deny_id)
    db.session.delete(d)
    db.session.commit()
    flash('Deny filter deleted', 'success')
    return redirect(url_for('admin.email_settings'))


@admin_bp.route('/email_templates_list')
@login_required
def email_templates_list():
    """Return list of all email templates as JSON."""
    from ...models import EmailTemplate
    templates = EmailTemplate.query.order_by(EmailTemplate.name).all()
    return jsonify({
        'templates': [
            {
                'id': t.id,
                'name': t.name,
                'subject': t.subject,
                'created_at': t.created_at.strftime('%Y-%m-%d %H:%M') if t.created_at else None,
                'updated_at': t.updated_at.strftime('%Y-%m-%d %H:%M') if t.updated_at else None
            }
            for t in templates
        ]
    })


@admin_bp.route('/email_template_get')
@login_required
def email_template_get():
    """Get a single email template by ID."""
    from ...models import EmailTemplate
    template_id = request.args.get('id', type=int)
    if not template_id:
        return jsonify({'error': 'Template ID required'}), 400
    
    template = EmailTemplate.query.get(template_id)
    if not template:
        return jsonify({'error': 'Template not found'}), 404
    
    return jsonify({
        'template': {
            'id': template.id,
            'name': template.name,
            'subject': template.subject,
            'body': template.body,
            'created_at': template.created_at.strftime('%Y-%m-%d %H:%M') if template.created_at else None,
            'updated_at': template.updated_at.strftime('%Y-%m-%d %H:%M') if template.updated_at else None
        }
    })


@admin_bp.route('/email_template_save', methods=['POST'])
@login_required
def email_template_save():
    """Create or update an email template."""
    from ...models import EmailTemplate
    
    template_id = request.form.get('template_id', type=int)
    name = request.form.get('name', '').strip()
    subject = request.form.get('subject', '').strip()
    body = request.form.get('body', '').strip()
    
    if not name:
        return jsonify({'error': 'Template name is required'}), 400
    if not subject:
        return jsonify({'error': 'Email subject is required'}), 400
    if not body:
        return jsonify({'error': 'Email body is required'}), 400
    
    # Check for duplicate name (excluding current template if editing)
    existing = EmailTemplate.query.filter(EmailTemplate.name == name).first()
    if existing and (not template_id or existing.id != template_id):
        return jsonify({'error': f'A template with the name "{name}" already exists'}), 400
    
    if template_id:
        # Update existing
        template = EmailTemplate.query.get(template_id)
        if not template:
            return jsonify({'error': 'Template not found'}), 404
        template.name = name
        template.subject = subject
        template.body = body
    else:
        # Create new
        template = EmailTemplate(
            name=name,
            subject=subject,
            body=body,
            created_at=datetime.utcnow()
        )
        db.session.add(template)
    
    db.session.commit()
    return jsonify({'success': True, 'id': template.id})


@admin_bp.route('/email_template_delete', methods=['POST'])
@login_required
def email_template_delete():
    """Delete an email template."""
    from ...models import EmailTemplate
    
    template_id = request.form.get('id', type=int)
    if not template_id:
        return jsonify({'error': 'Template ID required'}), 400
    
    template = EmailTemplate.query.get(template_id)
    if not template:
        return jsonify({'error': 'Template not found'}), 404
    
    # Check if template is in use by any password expiry notifications
    if template.notifications:
        return jsonify({'error': 'Cannot delete template - it is used by password expiry notifications'}), 400
    
    db.session.delete(template)
    db.session.commit()
    return jsonify({'success': True})


@admin_bp.route('/password_notifications_list')
@login_required
def password_notifications_list():
    """Return list of all password expiry notification rules as JSON."""
    from ...models import PasswordExpiryNotification, EmailTemplate
    
    notifications = PasswordExpiryNotification.query.order_by(PasswordExpiryNotification.days_before.desc()).all()
    templates = EmailTemplate.query.order_by(EmailTemplate.name).all()
    
    return jsonify({
        'notifications': [
            {
                'id': n.id,
                'days_before': n.days_before,
                'template_id': n.template_id,
                'template_name': n.template.name if n.template else None,
                'enabled': n.enabled
            }
            for n in notifications
        ],
        'templates': [
            {'id': t.id, 'name': t.name}
            for t in templates
        ]
    })


@admin_bp.route('/password_notification_save', methods=['POST'])
@login_required
def password_notification_save():
    """Create or update a password expiry notification rule."""
    from ...models import PasswordExpiryNotification, EmailTemplate
    
    notification_id = request.form.get('notification_id', type=int)
    days_before = request.form.get('days_before', type=int)
    template_id = request.form.get('template_id', type=int)
    enabled = request.form.get('enabled') in ('1', 'true', 'on', 'yes')
    
    if days_before is None or days_before < 1:
        return jsonify({'error': 'Days before expiry must be at least 1'}), 400
    if not template_id:
        return jsonify({'error': 'Email template is required'}), 400
    
    # Verify template exists
    template = EmailTemplate.query.get(template_id)
    if not template:
        return jsonify({'error': 'Selected email template not found'}), 404
    
    # Check for duplicate days_before (excluding current notification if editing)
    existing = PasswordExpiryNotification.query.filter(PasswordExpiryNotification.days_before == days_before).first()
    if existing and (not notification_id or existing.id != notification_id):
        return jsonify({'error': f'A notification for {days_before} days before already exists'}), 400
    
    if notification_id:
        # Update existing
        notification = PasswordExpiryNotification.query.get(notification_id)
        if not notification:
            return jsonify({'error': 'Notification not found'}), 404
        notification.days_before = days_before
        notification.template_id = template_id
        notification.enabled = enabled
    else:
        # Create new
        notification = PasswordExpiryNotification(
            days_before=days_before,
            template_id=template_id,
            enabled=enabled,
            created_at=datetime.utcnow()
        )
        db.session.add(notification)
    
    db.session.commit()
    return jsonify({'success': True, 'id': notification.id})


@admin_bp.route('/password_notification_delete', methods=['POST'])
@login_required
def password_notification_delete():
    """Delete a password expiry notification rule."""
    from ...models import PasswordExpiryNotification
    
    notification_id = request.form.get('id', type=int)
    if not notification_id:
        return jsonify({'error': 'Notification ID required'}), 400
    
    notification = PasswordExpiryNotification.query.get(notification_id)
    if not notification:
        return jsonify({'error': 'Notification not found'}), 404
    
    db.session.delete(notification)
    db.session.commit()
    return jsonify({'success': True})
