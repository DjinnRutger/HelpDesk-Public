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


@admin_bp.route('/app-logs')
@login_required
def app_logs():
    """Show application logs with filtering and pagination."""
    import os
    from collections import deque
    
    # Get the log file path from config
    log_file = current_app.config.get('LOG_FILE_PATH', '')
    
    # Filters
    level_filter = (request.args.get('level') or '').strip().upper()
    search_query = (request.args.get('q') or '').strip()
    
    # Pagination
    try:
        lines_count = int(request.args.get('lines') or 500)
    except Exception:
        lines_count = 500
    if lines_count < 100:
        lines_count = 100
    if lines_count > 5000:
        lines_count = 5000
    
    logs = []
    log_exists = False
    error_message = None
    
    if log_file and os.path.exists(log_file):
        log_exists = True
        try:
            # Read last N lines efficiently
            with open(log_file, 'r', encoding='utf-8', errors='replace') as f:
                # Use deque to keep only last N lines
                all_lines = deque(f, maxlen=lines_count)
            
            for line in all_lines:
                line = line.strip()
                if not line:
                    continue
                
                # Parse log line: "2024-12-24 10:30:00 - INFO - message"
                entry = {'raw': line, 'level': 'INFO', 'timestamp': '', 'message': line}
                try:
                    parts = line.split(' - ', 2)
                    if len(parts) >= 3:
                        entry['timestamp'] = parts[0]
                        entry['level'] = parts[1].strip()
                        entry['message'] = parts[2]
                    elif len(parts) == 2:
                        entry['timestamp'] = parts[0]
                        entry['message'] = parts[1]
                except Exception:
                    pass
                
                # Apply level filter
                if level_filter and entry['level'] != level_filter:
                    continue
                
                # Apply search filter
                if search_query and search_query.lower() not in line.lower():
                    continue
                
                logs.append(entry)
            
            # Reverse to show newest first
            logs.reverse()
            
        except Exception as e:
            error_message = f'Error reading log file: {str(e)}'
    elif log_file:
        error_message = f'Log file not found: {log_file}'
    else:
        error_message = 'Log file path not configured'
    
    return render_template(
        'admin/app_logs.html',
        logs=logs,
        log_exists=log_exists,
        log_file=log_file,
        error_message=error_message,
        level_filter=level_filter,
        search_query=search_query,
        lines_count=lines_count,
    )


@admin_bp.route('/app-logs/clear', methods=['POST'])
@login_required
def clear_app_logs():
    """Clear the application log file."""
    import os
    
    log_file = current_app.config.get('LOG_FILE_PATH', '')
    
    if log_file and os.path.exists(log_file):
        try:
            # Truncate the log file
            with open(log_file, 'w', encoding='utf-8') as f:
                f.write('')
            current_app.logger.info('Log file cleared by admin')
            flash('Application logs cleared successfully.', 'success')
        except Exception as e:
            flash(f'Error clearing logs: {str(e)}', 'danger')
    else:
        flash('Log file not found.', 'warning')
    
    return redirect(url_for('admin.app_logs'))


@admin_bp.route('/app-logs/download')
@login_required
def download_app_logs():
    """Download the application log file."""
    import os
    
    log_file = current_app.config.get('LOG_FILE_PATH', '')
    
    if log_file and os.path.exists(log_file):
        return send_file(
            log_file,
            mimetype='text/plain',
            as_attachment=True,
            download_name=f'helpdesk-logs-{datetime.now().strftime("%Y%m%d-%H%M%S")}.log'
        )
    
    flash('Log file not found.', 'warning')
    return redirect(url_for('admin.app_logs'))


@admin_bp.route('/email-logs')
@login_required
def email_logs():
    """Show email polling logs for the last N days with per-message actions and filters."""
    from datetime import timedelta
    from ...models import EmailCheck, EmailCheckEntry, OutgoingEmail
    from sqlalchemy import or_, func

    # Direction: incoming or outgoing
    direction = (request.args.get('direction') or '').strip().lower()
    
    # ========== INCOMING EMAILS ==========
    # Filters
    q = (request.args.get('q') or '').strip()
    action = (request.args.get('action') or '').strip().lower()
    # Optional toggle to hide "No new messages" rows (action == 'none')
    # Default: ON on initial load, but respect unchecked state when form is submitted
    hide_none_raw = request.args.get('hide_none')
    # Check if form was submitted (direction param present means form submitted)
    form_submitted = request.args.get('direction') is not None or request.args.get('q') is not None or request.args.get('action') is not None or request.args.get('days') is not None
    if form_submitted:
        # Form was submitted - checkbox unchecked means hide_none should be False
        hide_none = hide_none_raw in ('1', 'true', 'yes', 'on')
    else:
        # Initial page load - default to True
        hide_none = True
    try:
        days = int(request.args.get('days') or 7)
    except Exception:
        days = 7
    if days < 1:
        days = 1
    if days > 90:
        days = 90
    
    # Pagination
    try:
        page = int(request.args.get('page') or 1)
    except Exception:
        page = 1
    if page < 1:
        page = 1
    
    try:
        per_page = int(request.args.get('per_page') or 20)
    except Exception:
        per_page = 20
    if per_page not in (20, 100):
        per_page = 20

    cutoff = datetime.utcnow() - timedelta(days=days)
    
    # Build query for incoming entries (joined with check for timestamp filtering)
    query = (
        EmailCheckEntry.query
        .join(EmailCheck, EmailCheckEntry.check_id == EmailCheck.id)
        .filter(EmailCheck.checked_at >= cutoff)
    )
    
    # Apply filters
    ql = q.lower()
    if action:
        query = query.filter(EmailCheckEntry.action.ilike(action))
    # If explicitly filtering to action "none", do not hide them regardless of toggle
    if action == 'none':
        hide_none = False
    # Apply hide-none filter
    if hide_none:
        query = query.filter(
            or_(EmailCheckEntry.action.is_(None), func.lower(EmailCheckEntry.action) != 'none')
        )
    if ql:
        query = query.filter(
            (EmailCheckEntry.sender.ilike(f'%{ql}%')) |
            (EmailCheckEntry.subject.ilike(f'%{ql}%'))
        )
    
    # Order and paginate
    query = query.order_by(EmailCheck.checked_at.desc(), EmailCheckEntry.id.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    # Count for tab badge
    incoming_count = EmailCheckEntry.query.join(EmailCheck).filter(EmailCheck.checked_at >= cutoff).count()
    
    # ========== OUTGOING EMAILS ==========
    # Outgoing filters
    q_out = (request.args.get('q_out') or '').strip()
    category = (request.args.get('category') or '').strip().lower()
    failed_only_raw = request.args.get('failed_only')
    failed_only = failed_only_raw in ('1', 'true', 'yes', 'on')
    
    try:
        days_out = int(request.args.get('days_out') or 7)
    except Exception:
        days_out = 7
    if days_out < 1:
        days_out = 1
    if days_out > 90:
        days_out = 90
    
    # Outgoing pagination
    try:
        page_out = int(request.args.get('page_out') or 1)
    except Exception:
        page_out = 1
    if page_out < 1:
        page_out = 1
    
    try:
        per_page_out = int(request.args.get('per_page_out') or 20)
    except Exception:
        per_page_out = 20
    if per_page_out not in (20, 100):
        per_page_out = 20
    
    cutoff_out = datetime.utcnow() - timedelta(days=days_out)
    
    # Build query for outgoing emails
    out_query = OutgoingEmail.query.filter(OutgoingEmail.created_at >= cutoff_out)
    
    # Apply outgoing filters
    if category:
        out_query = out_query.filter(OutgoingEmail.category.ilike(category))
    if failed_only:
        out_query = out_query.filter(OutgoingEmail.success == False)
    if q_out:
        ql_out = q_out.lower()
        out_query = out_query.filter(
            (OutgoingEmail.to_address.ilike(f'%{ql_out}%')) |
            (OutgoingEmail.to_name.ilike(f'%{ql_out}%')) |
            (OutgoingEmail.subject.ilike(f'%{ql_out}%'))
        )
    
    # Order and paginate outgoing
    out_query = out_query.order_by(OutgoingEmail.created_at.desc())
    outgoing_pagination = out_query.paginate(page=page_out, per_page=per_page_out, error_out=False)
    
    # Count for tab badge
    outgoing_count = OutgoingEmail.query.filter(OutgoingEmail.created_at >= cutoff_out).count()

    # ========== QUEUED EMAILS (outbox) ==========
    from ...models import EmailOutbox
    queue_query = EmailOutbox.query.filter(EmailOutbox.status != 'sent').order_by(EmailOutbox.id.desc())
    queue_count = queue_query.count()
    queue_entries = queue_query.limit(200).all()

    return render_template(
        'admin/email_logs.html',
        # Incoming
        entries=pagination.items,
        pagination=pagination,
        q=q,
        action=action,
        days=days,
        per_page=per_page,
        hide_none=hide_none,
        incoming_count=incoming_count,
        # Outgoing
        outgoing_entries=outgoing_pagination.items,
        outgoing_pagination=outgoing_pagination,
        q_out=q_out,
        category=category,
        days_out=days_out,
        per_page_out=per_page_out,
        failed_only=failed_only,
        outgoing_count=outgoing_count,
        # Queue (outbox)
        queue_entries=queue_entries,
        queue_count=queue_count,
        # Direction
        direction=direction,
    )


@admin_bp.route('/email-outbox/<int:outbox_id>/retry', methods=['POST'])
@login_required
def email_outbox_retry(outbox_id):
    """Re-queue a failed/dead outbox email for delivery."""
    from ...models import EmailOutbox
    row = EmailOutbox.query.get_or_404(outbox_id)
    if row.status not in ('failed', 'dead'):
        flash('Only failed or dead emails can be retried.', 'warning')
    else:
        row.status = 'pending'
        row.next_attempt_at = datetime.utcnow()
        row.last_error = None
        db.session.commit()
        flash('Email re-queued for delivery.', 'success')
    return redirect(url_for('admin.email_logs', direction='queue'))


@admin_bp.route('/email/log-retention', methods=['POST'])
@login_required
def email_log_retention_settings():
    """Save email log retention settings."""
    if not admin_required():
        return redirect(url_for('dashboard.index'))
    enabled = request.form.get('email_log_retention_enabled') in ('1', 'on', 'true', 'yes')
    try:
        days = int(request.form.get('email_log_retention_days', 90))
        if days < 1:
            days = 1
        if days > 365:
            days = 365
    except (ValueError, TypeError):
        days = 90
    Setting.set('EMAIL_LOG_RETENTION_ENABLED', '1' if enabled else '0')
    Setting.set('EMAIL_LOG_RETENTION_DAYS', str(days))
    _bump_schedule_version()
    flash(f'Email log retention settings saved. Auto-delete is {"enabled" if enabled else "disabled"}.', 'success')
    return redirect(url_for('admin.email_settings'))


@admin_bp.route('/email/log-no-new-messages', methods=['POST'])
@login_required
def email_log_no_new_messages_settings():
    """Toggle whether 'No New Messages' rows are written on each poll."""
    if not admin_required():
        return redirect(url_for('dashboard.index'))
    enabled = request.form.get('email_log_no_new_messages') in ('1', 'on', 'true', 'yes')
    Setting.set('EMAIL_LOG_NO_NEW_MESSAGES', '1' if enabled else '0')
    flash(f'"No New Messages" logging {"enabled" if enabled else "disabled"}.', 'success')
    return redirect(url_for('admin.email_settings'))


def cleanup_old_email_logs(app):
    """Delete email logs older than the configured retention period."""
    with app.app_context():
        from datetime import timedelta
        from ...models import EmailCheck, EmailCheckEntry, OutgoingEmail, Setting as _Setting
        enabled = (_Setting.get('EMAIL_LOG_RETENTION_ENABLED', '0') or '0') in ('1', 'true', 'on', 'yes')
        if not enabled:
            return
        try:
            days = int(_Setting.get('EMAIL_LOG_RETENTION_DAYS', '90') or '90')
        except (ValueError, TypeError):
            days = 90
        cutoff = datetime.utcnow() - timedelta(days=days)
        # Delete old outgoing emails
        OutgoingEmail.query.filter(OutgoingEmail.created_at < cutoff).delete(synchronize_session=False)
        # Delete old email check entries (cascade will handle entries via EmailCheck)
        old_checks = EmailCheck.query.filter(EmailCheck.checked_at < cutoff).all()
        for check in old_checks:
            db.session.delete(check)
        db.session.commit()
