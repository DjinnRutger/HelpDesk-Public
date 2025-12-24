from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file, current_app, jsonify
from flask_login import login_required, current_user
from ..forms import MSGraphForm, TechForm, ProcessTemplateForm, ProcessTemplateItemForm, AllowedDomainForm, DenyFilterForm
from ..models import Setting, User, ProcessTemplate, ProcessTemplateItem, AllowedDomain, DenyFilter, Vendor, PurchaseOrder, Company, ShippingLocation, DocumentCategory, AssetAudit, Asset, AssetCategory, AssetManufacturer, AssetCondition, AssetLocation, ScheduledTicket, Ticket, TicketTask
from .. import db, scheduler, run_auto_backup
from ..utils.security import hash_password
from ..services.email_poll import poll_ms_graph
from ..services.ms_graph import get_msal_app, get_access_token
import sqlite3
import io
import tempfile
import shutil
import zipfile
from datetime import datetime
import os
import requests
import ftplib


admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def admin_required():
    return current_user.is_authenticated and current_user.role == 'admin'


@admin_bp.before_request
def restrict_to_admin():
    # If not logged in, go to login; if logged but not admin, go to dashboard
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    if current_user.role != 'admin':
        return redirect(url_for('dashboard.index'))


@admin_bp.route('/')
@login_required
def index():
    # Show settings and list of techs
    settings = {
        'client_id': Setting.get('MS_CLIENT_ID', ''),
        'tenant_id': Setting.get('MS_TENANT_ID', ''),
        'user_email': Setting.get('MS_USER_EMAIL', ''),
        'ms_enabled': (Setting.get('MS_ENABLED', '1') or '1') in ('1','true','on','yes'),
        # FTP (HDWish) settings surfaced in Ticket Import modal
        'ftp_enabled': (Setting.get('FTP_ENABLED', '0') or '0') in ('1','true','on','yes'),
        'ftp_host': Setting.get('FTP_HOST', ''),
        'ftp_port': Setting.get('FTP_PORT', '21'),
        'ftp_user': Setting.get('FTP_USER', ''),
        'ftp_base': Setting.get('FTP_BASE_DIR', ''),
        'ftp_subdir': Setting.get('FTP_SUBDIR', 'HDWish Data'),
        # Active Directory settings
        'ad_enabled': (Setting.get('AD_ENABLED', '0') or '0') in ('1','true','on','yes'),
        'ad_server': Setting.get('AD_SERVER', ''),
        'ad_port': Setting.get('AD_PORT', '389'),
        'ad_use_ssl': (Setting.get('AD_USE_SSL', '0') or '0') in ('1','true','on','yes'),
        'ad_start_tls': (Setting.get('AD_START_TLS', '0') or '0') in ('1','true','on','yes'),
        'ad_base_dn': Setting.get('AD_BASE_DN', ''),
        'ad_bind_dn': Setting.get('AD_BIND_DN', ''),
        # AD Password Check settings
        'ad_pwd_check_enabled': (Setting.get('AD_PWD_CHECK_ENABLED', '0') or '0') in ('1','true','on','yes'),
        'ad_pwd_check_time': Setting.get('AD_PWD_CHECK_TIME', '07:00'),
        'ad_pwd_warning_days': Setting.get('AD_PWD_WARNING_DAYS', '14'),
    }
    techs = User.query.order_by(User.created_at.desc()).all()
    templates = ProcessTemplate.query.order_by(ProcessTemplate.name.asc()).all()
    doc_cats = DocumentCategory.query.order_by(DocumentCategory.name.asc()).all()
    recent_audits = AssetAudit.query.order_by(AssetAudit.created_at.desc()).limit(5).all()
    # Read app version from version.txt within the app package
    version = None
    try:
        version_file = os.path.join(current_app.root_path, 'version.txt')
        with open(version_file, 'r', encoding='utf-8') as f:
            version = (f.read() or '').strip()
    except Exception:
        version = None
    # Attachments directory (relative to static/ or instance based on setting)
    try:
        attachments_subdir = (Setting.get('ATTACHMENTS_DIR_REL', 'attachments') or 'attachments').strip()
        attachments_subdir = attachments_subdir.replace('\\','/').lstrip('/') or 'attachments'
    except Exception:
        attachments_subdir = 'attachments'
    try:
        attachments_base = (Setting.get('ATTACHMENTS_BASE', 'instance') or 'instance').strip().lower()
    except Exception:
        attachments_base = 'instance'
    base_root = current_app.instance_path if attachments_base == 'instance' else (current_app.static_folder or os.path.join(current_app.root_path, 'static'))
    attachments_abs = os.path.join(base_root, attachments_subdir)
    # Fetch latest version from public GitHub repo (raw view preferred, but fallback works by parsing text)
    latest_version = None
    try:
        # Use raw.githubusercontent.com for direct file, but the user provided the blob URL; support both.
        url = 'https://raw.githubusercontent.com/DjinnRutger/HelpDesk-Public/refs/heads/main/version.txt'
        resp = requests.get(url, timeout=2.5)
        if resp.ok and resp.text:
            latest_version = resp.text.strip()
        else:
            # Fallback: try the provided blob URL and extract visible text
            blob_url = 'https://github.com/DjinnRutger/HelpDesk-Public/blob/main/version.txt'
            r2 = requests.get(blob_url, timeout=2.5)
            if r2.ok and r2.text:
                import re
                # GitHub HTML renders file content inside <table class="highlight"> or <td class="blob-code">
                m = re.search(r'class="blob-code[^>]*">([^<]+)</td>', r2.text)
                if m:
                    latest_version = m.group(1).strip()
    except Exception:
        latest_version = None
    # Picklist counts for quick links
    cat_count = AssetCategory.query.count()
    mfg_count = AssetManufacturer.query.count()
    cond_count = AssetCondition.query.count()
    loc_count = AssetLocation.query.count()
    # Auto-backup settings
    auto_enabled = (Setting.get('AUTO_BACKUP_ENABLED', '0') or '0') in ('1','true','on','yes')
    auto_time = (Setting.get('AUTO_BACKUP_TIME', '23:00') or '23:00')
    auto_dir = (Setting.get('AUTO_BACKUP_DIR', '') or '')
    try:
        auto_keep = int(Setting.get('AUTO_BACKUP_RETENTION', '7') or '7')
    except Exception:
        auto_keep = 7
    demo_mode = (Setting.get('DEMO_MODE', '0') or '0') in ('1','true','on','yes') or (Setting.get('DEMO_DATA_LOADED','0') in ('1','true','on','yes'))
    return render_template(
        'admin/index.html',
        settings=settings,
        techs=techs,
        templates=templates,
        doc_cats=doc_cats,
        recent_audits=recent_audits,
        version=version,
        attachments_subdir=attachments_subdir,
        attachments_abs=attachments_abs,
        attachments_base=attachments_base,
        latest_version=latest_version,
        cat_count=cat_count,
        mfg_count=mfg_count,
        cond_count=cond_count,
        loc_count=loc_count,
        auto_enabled=auto_enabled,
        auto_time=auto_time,
        auto_dir=auto_dir,
        auto_keep=auto_keep,
        demo_mode=demo_mode,
    )


@admin_bp.route('/email-logs')
@login_required
def email_logs():
    """Show email polling logs for the last N days with per-message actions and filters."""
    from datetime import timedelta
    from ..models import EmailCheck, EmailCheckEntry, OutgoingEmail
    from sqlalchemy import or_, func

    # Direction: incoming or outgoing
    direction = (request.args.get('direction') or '').strip().lower()
    
    # ========== INCOMING EMAILS ==========
    # Filters
    q = (request.args.get('q') or '').strip()
    action = (request.args.get('action') or '').strip().lower()
    # Optional toggle to hide "No new messages" rows (action == 'none')
    # Default: ON (hide entries with action == 'none' unless explicitly disabled)
    hide_none_raw = request.args.get('hide_none')
    if hide_none_raw is None:
        hide_none = True
    else:
        val = (str(hide_none_raw) or '').strip().lower()
        hide_none = val in ('1', 'true', 'yes', 'on') or hide_none_raw == ''
    try:
        days = int(request.args.get('days') or 7)
    except Exception:
        days = 7
    if days < 1:
        days = 1
    if days > 7:
        days = 7
    
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
    if days_out > 7:
        days_out = 7
    
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
        # Direction
        direction=direction,
    )


# --- Scheduled Tickets Management ---
@admin_bp.route('/scheduled')
@login_required
def scheduled_list():
    rows = ScheduledTicket.query.order_by(ScheduledTicket.created_at.desc()).all()
    return render_template('admin/scheduled_list.html', rows=rows)


@admin_bp.route('/scheduled/new', methods=['GET','POST'])
@login_required
def scheduled_new():
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        subject = (request.form.get('subject') or '').strip()
        if not name or not subject:
            if is_ajax:
                return jsonify({'success': False, 'error': 'Name and Subject are required.'}), 400
            flash('Name and Subject are required.', 'danger')
            return redirect(url_for('admin.scheduled_new'))
        # Default schedule_time to midnight if not provided
        sched_time = (request.form.get('schedule_time') or '').strip() or '00:00'
        row = ScheduledTicket(
            name=name,
            subject=subject,
            body=(request.form.get('body') or None),
            status=(request.form.get('status') or 'open'),
            priority=(request.form.get('priority') or 'medium'),
            assignee_id=(int(request.form.get('assignee_id')) if (request.form.get('assignee_id') or '').isdigit() else None),
            tasks_text=(request.form.get('tasks_text') or None),
            schedule_type=(request.form.get('schedule_type') or 'daily'),
            day_of_week=(int(request.form.get('day_of_week')) if (request.form.get('day_of_week') or '').isdigit() else None),
            day_of_month=(int(request.form.get('day_of_month')) if (request.form.get('day_of_month') or '').isdigit() else None),
            schedule_time=sched_time,
            active=bool(request.form.get('active')),
        )
        try:
            db.session.add(row)
            db.session.commit()
            if is_ajax:
                return jsonify({'success': True, 'id': row.id})
            flash('Scheduled ticket created.', 'success')
            return redirect(url_for('admin.scheduled_list'))
        except Exception as e:
            db.session.rollback()
            if is_ajax:
                return jsonify({'success': False, 'error': str(e)}), 500
            flash(f'Error creating scheduled ticket: {str(e)}', 'danger')
            return redirect(url_for('admin.scheduled_new'))
    techs = User.query.order_by(User.name.asc()).all()
    return render_template('admin/scheduled_form.html', action='New', row=None, techs=techs)


@admin_bp.route('/scheduled/<int:row_id>/edit', methods=['GET','POST'])
@login_required
def scheduled_edit(row_id):
    row = ScheduledTicket.query.get_or_404(row_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        subject = (request.form.get('subject') or '').strip()
        if not name or not subject:
            if is_ajax:
                return jsonify({'success': False, 'error': 'Name and Subject are required.'}), 400
            flash('Name and Subject are required.', 'danger')
            return redirect(url_for('admin.scheduled_edit', row_id=row.id))
        row.name = name
        row.subject = subject
        row.body = (request.form.get('body') or None)
        row.status = (request.form.get('status') or 'open')
        row.priority = (request.form.get('priority') or 'medium')
        row.assignee_id = (int(request.form.get('assignee_id')) if (request.form.get('assignee_id') or '').isdigit() else None)
        row.tasks_text = (request.form.get('tasks_text') or None)
        row.schedule_type = (request.form.get('schedule_type') or 'daily')
        row.day_of_week = (int(request.form.get('day_of_week')) if (request.form.get('day_of_week') or '').isdigit() else None)
        row.day_of_month = (int(request.form.get('day_of_month')) if (request.form.get('day_of_month') or '').isdigit() else None)
        row.schedule_time = (request.form.get('schedule_time') or None)
        row.active = bool(request.form.get('active'))
        try:
            db.session.commit()
            if is_ajax:
                return jsonify({'success': True})
            flash('Scheduled ticket updated.', 'success')
            return redirect(url_for('admin.scheduled_list'))
        except Exception as e:
            db.session.rollback()
            if is_ajax:
                return jsonify({'success': False, 'error': str(e)}), 500
            flash(f'Error updating scheduled ticket: {str(e)}', 'danger')
            return redirect(url_for('admin.scheduled_edit', row_id=row.id))
    techs = User.query.order_by(User.name.asc()).all()
    return render_template('admin/scheduled_form.html', action='Edit', row=row, techs=techs)


@admin_bp.route('/scheduled/<int:row_id>/delete', methods=['POST'])
@login_required
def scheduled_delete(row_id):
    row = ScheduledTicket.query.get_or_404(row_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    
    try:
        db.session.delete(row)
        db.session.commit()
        
        if is_ajax:
            return jsonify({'success': True})
        
        flash('Scheduled ticket deleted.', 'success')
        return redirect(url_for('admin.scheduled_list'))
    except Exception as e:
        db.session.rollback()
        if is_ajax:
            return jsonify({'success': False, 'error': str(e)})
        flash(f'Error deleting scheduled ticket: {str(e)}', 'danger')
        return redirect(url_for('admin.scheduled_list'))


def _create_ticket_from_schedule(row: ScheduledTicket):
    t = Ticket(
        subject=row.subject,
        body=row.body,
        status=row.status or 'open',
        priority=row.priority or 'medium',
        assignee_id=row.assignee_id,
        source='scheduled'
    )
    db.session.add(t)
    db.session.flush()
    # Add tasks
    if row.tasks_text:
        for line in [ln.strip() for ln in row.tasks_text.splitlines() if ln.strip()]:
            db.session.add(TicketTask(ticket_id=t.id, label=line))
    db.session.commit()
    return t


@admin_bp.route('/scheduled/<int:row_id>/run_now', methods=['POST'])
@login_required
def scheduled_run_now(row_id):
    row = ScheduledTicket.query.get_or_404(row_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    
    try:
        t = _create_ticket_from_schedule(row)
        row.last_run_at = datetime.utcnow()
        db.session.commit()
        
        if is_ajax:
            return jsonify({'success': True, 'ticket_id': t.id})
        
        flash(f"Ticket #{t.id} created from schedule.", 'success')
        return redirect(url_for('admin.scheduled_list'))
    except Exception as e:
        db.session.rollback()
        if is_ajax:
            return jsonify({'success': False, 'error': str(e)})
        flash(f'Error running scheduled ticket: {str(e)}', 'danger')
        return redirect(url_for('admin.scheduled_list'))


@admin_bp.route('/attachments', methods=['POST'])
@login_required
def attachments_settings():
    # Save the relative subfolder and base (instance/static) for attachments
    subdir = (request.form.get('attachments_subdir') or '').strip()
    base = (request.form.get('attachments_base') or 'instance').strip().lower()
    if not subdir:
        flash('Folder name is required.', 'danger')
        return redirect(url_for('admin.index'))
    # Sanitize: normalize separators, strip leading slashes, block traversal
    cleaned = subdir.replace('\\','/').strip()
    while cleaned.startswith('/'):
        cleaned = cleaned[1:]
    if '..' in cleaned or cleaned == '':
        flash('Invalid folder name.', 'danger')
        return redirect(url_for('admin.index'))
    try:
        Setting.set('ATTACHMENTS_DIR_REL', cleaned)
        if base not in ('instance', 'static'):
            base = 'instance'
        Setting.set('ATTACHMENTS_BASE', base)
        # Ensure directory exists under chosen base
        root = current_app.instance_path if base == 'instance' else (current_app.static_folder or os.path.join(current_app.root_path, 'static'))
        target_dir = os.path.join(root, cleaned)
        os.makedirs(target_dir, exist_ok=True)
        flash('Attachments folder updated.', 'success')
    except Exception:
        flash('Failed to update attachments folder.', 'danger')
    return redirect(url_for('admin.index'))


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


# --- Purchasing: Vendors, Companies, Shipping ---
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


# JSON API endpoints for modal management
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


# AJAX: create item
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


# AJAX: update item
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


# AJAX: delete item
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


# AJAX: reorder items
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


@admin_bp.route('/techs-data')
@login_required
def techs_data():
    techs = User.query.order_by(User.name.asc()).all()
    return jsonify([{'id': u.id, 'name': u.name} for u in techs])


@admin_bp.route('/doccategories-data')
@login_required
def doccategories_data():
    """Return document categories as JSON for AJAX loading"""
    categories = DocumentCategory.query.order_by(DocumentCategory.name.asc()).all()
    # documents relationship is lazy='dynamic', so use .count() instead of len()
    return jsonify([{
        'id': c.id,
        'name': c.name,
        'documents_count': c.documents.count()
    } for c in categories])


@admin_bp.route('/scheduled-data')
@login_required
def scheduled_data():
    """Return scheduled tickets as JSON for AJAX loading"""
    scheduled = ScheduledTicket.query.order_by(ScheduledTicket.name.asc()).all()
    return jsonify([{
        'id': s.id,
        'name': s.name,
        'subject': s.subject,
        'body': s.body,
        'status': s.status,
        'priority': s.priority,
        'assignee_id': s.assignee_id,
        'assignee_name': s.assignee.name if s.assignee else None,
        'tasks_text': s.tasks_text,
        'schedule_type': s.schedule_type,
        'day_of_week': s.day_of_week,
        'day_of_month': s.day_of_month,
        'schedule_time': s.schedule_time,
        'active': s.active,
        'is_active': s.active,
        'last_run': s.last_run_at.strftime('%Y-%m-%d %H:%M:%S') if s.last_run_at else None
    } for s in scheduled])


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


@admin_bp.route('/msgraph', methods=['GET', 'POST'])
@login_required
def msgraph():
    form = MSGraphForm()
    if request.method == 'GET':
        form.client_id.data = Setting.get('MS_CLIENT_ID', '')
        form.client_secret.data = Setting.get('MS_CLIENT_SECRET', '')
        form.tenant_id.data = Setting.get('MS_TENANT_ID', '')
        form.user_email.data = Setting.get('MS_USER_EMAIL', '')
        try:
            form.poll_interval.data = int(Setting.get('POLL_INTERVAL_SECONDS', '60'))
        except Exception:
            form.poll_interval.data = 60
    # Save settings
    if form.validate_on_submit() and 'submit' in request.form:
        Setting.set('MS_CLIENT_ID', form.client_id.data)
        Setting.set('MS_CLIENT_SECRET', form.client_secret.data)
        Setting.set('MS_TENANT_ID', form.tenant_id.data)
        Setting.set('MS_USER_EMAIL', form.user_email.data)
        Setting.set('POLL_INTERVAL_SECONDS', str(form.poll_interval.data))
        # Update scheduler job interval
        try:
            if scheduler.get_job('email_poll'):
                scheduler.reschedule_job('email_poll', trigger='interval', seconds=form.poll_interval.data)
        except Exception:
            pass
        flash('Saved Microsoft Graph settings', 'success')
        return redirect(url_for('admin.index'))
    # Force a poll now
    if request.method == 'POST' and request.form.get('action') == 'check_now':
        poll_ms_graph()
        flash('Mailbox checked for new unread messages.', 'success')
        return redirect(url_for('admin.msgraph'))
    # Test connection
    if request.method == 'POST' and request.form.get('action') == 'test':
        app = get_msal_app()
        if not app:
            flash('Missing or invalid Graph credentials. Save valid settings first.', 'danger')
            return redirect(url_for('admin.msgraph'))
        token = get_access_token(app)
        if token:
            flash('Connection test succeeded: token acquired.', 'success')
        else:
            flash('Connection test failed: could not acquire token.', 'danger')
        return redirect(url_for('admin.msgraph'))
    return render_template('admin/msgraph.html', form=form)


@admin_bp.route('/ftp_settings', methods=['POST'])
@login_required
def ftp_settings():
    """Save Ticket Import settings.

    This endpoint serves two small forms in the Ticket Import modal:
    - MS Graph enable toggle (form_section=ms)
    - FTP enable + connection settings (form_section=ftp)

    Each form should only update its own settings so MS and FTP can be enabled/disabled independently.
    """
    section = (request.form.get('form_section') or '').strip().lower()
    try:
        if section == 'ms':
            # Checkbox sends when checked; absence means off
            raw = (request.form.get('ms_enabled') or '').strip().lower()
            ms_enabled = raw in ('1', 'true', 'on', 'yes')
            Setting.set('MS_ENABLED', '1' if ms_enabled else '0')
            flash('Saved MS Graph import setting.', 'success')
        elif section == 'ftp':
            # Read FTP fields; only update FTP-related settings
            raw_enabled = (request.form.get('ftp_enabled') or '').strip().lower()
            enabled = raw_enabled in ('1', 'true', 'on', 'yes')
            host = (request.form.get('ftp_host') or '').strip()
            port_raw = (request.form.get('ftp_port') or '').strip() or '21'
            user = (request.form.get('ftp_user') or '').strip()
            pwd = (request.form.get('ftp_pass') or '').strip()
            base = (request.form.get('ftp_base') or '').strip()
            subdir = (request.form.get('ftp_subdir') or '').strip() or 'HDWish Data'
            try:
                port = int(port_raw)
                if port <= 0:
                    port = 21
            except Exception:
                port = 21
            Setting.set('FTP_ENABLED', '1' if enabled else '0')
            Setting.set('FTP_HOST', host)
            Setting.set('FTP_PORT', str(port))
            Setting.set('FTP_USER', user)
            if pwd:
                Setting.set('FTP_PASS', pwd)
            Setting.set('FTP_BASE_DIR', base)
            Setting.set('FTP_SUBDIR', subdir)
            flash('Saved FTP import settings.', 'success')
        else:
            # Fallback: be conservative and update only keys that are present
            if 'ms_enabled' in request.form:
                raw = (request.form.get('ms_enabled') or '').strip().lower()
                ms_enabled = raw in ('1', 'true', 'on', 'yes')
                Setting.set('MS_ENABLED', '1' if ms_enabled else '0')
            if any(k in request.form for k in ('ftp_enabled','ftp_host','ftp_port','ftp_user','ftp_pass','ftp_base','ftp_subdir')):
                raw_enabled = (request.form.get('ftp_enabled') or '').strip().lower()
                enabled = raw_enabled in ('1', 'true', 'on', 'yes') if 'ftp_enabled' in request.form else ((Setting.get('FTP_ENABLED','0') or '0') in ('1','true','on','yes'))
                host = (request.form.get('ftp_host') or Setting.get('FTP_HOST','')).strip()
                port_raw = (request.form.get('ftp_port') or Setting.get('FTP_PORT','21')).strip() or '21'
                user = (request.form.get('ftp_user') or Setting.get('FTP_USER','')).strip()
                pwd = (request.form.get('ftp_pass') or '').strip()
                base = (request.form.get('ftp_base') or Setting.get('FTP_BASE_DIR','')).strip()
                subdir = (request.form.get('ftp_subdir') or Setting.get('FTP_SUBDIR','HDWish Data')).strip() or 'HDWish Data'
                try:
                    port = int(port_raw)
                    if port <= 0:
                        port = 21
                except Exception:
                    port = 21
                Setting.set('FTP_ENABLED', '1' if enabled else '0')
                Setting.set('FTP_HOST', host)
                Setting.set('FTP_PORT', str(port))
                Setting.set('FTP_USER', user)
                if pwd:
                    Setting.set('FTP_PASS', pwd)
                Setting.set('FTP_BASE_DIR', base)
                Setting.set('FTP_SUBDIR', subdir)
            flash('Ticket Import settings saved.', 'success')
    except Exception:
        flash('Failed to save settings.', 'danger')
    return redirect(url_for('admin.index'))


@admin_bp.route('/ftp_test', methods=['POST'])
@login_required
def ftp_test():
    """Test the FTP connection using saved settings and attempt to list the HDWish folder."""
    try:
        host = Setting.get('FTP_HOST', '')
        port = int(Setting.get('FTP_PORT', '21') or '21')
        user = Setting.get('FTP_USER', '')
        pwd = Setting.get('FTP_PASS', '')
        base = (Setting.get('FTP_BASE_DIR', '') or '').strip()
        subdir = (Setting.get('FTP_SUBDIR', 'HDWish Data') or 'HDWish Data').strip()
        if not host:
            flash('FTP host is required. Save settings first.', 'danger')
            return redirect(url_for('admin.index'))
        ftp = ftplib.FTP()
        ftp.connect(host, port, timeout=10)
        ftp.login(user=user or 'anonymous', passwd=pwd or '')
        # Navigate to base/subdir if provided
        if base:
            ftp.cwd(base)
        if subdir:
            ftp.cwd(subdir)
        # Try listing entries
        entries = ftp.nlst()
        ftp.quit()
        flash(f'FTP test succeeded. Found {len(entries)} item(s) under {base}/{subdir}.', 'success')
    except Exception as e:
        try:
            ftp.quit()
        except Exception:
            pass
        flash(f'FTP test failed: {e}', 'danger')
    return redirect(url_for('admin.index'))


# --- Active Directory Settings ---
@admin_bp.route('/ad_settings', methods=['POST'])
@login_required
def ad_settings():
    """Save Active Directory connection settings."""
    ad_enabled = request.form.get('ad_enabled') in ('1', 'on', 'true', 'yes')
    ad_server = (request.form.get('ad_server') or '').strip()
    ad_port = (request.form.get('ad_port') or '389').strip()
    ad_use_ssl = request.form.get('ad_use_ssl') in ('1', 'on', 'true', 'yes')
    ad_start_tls = request.form.get('ad_start_tls') in ('1', 'on', 'true', 'yes')
    ad_base_dn = (request.form.get('ad_base_dn') or '').strip()
    ad_bind_dn = (request.form.get('ad_bind_dn') or '').strip()
    ad_bind_password = request.form.get('ad_bind_password') or ''
    
    Setting.set('AD_ENABLED', '1' if ad_enabled else '0')
    Setting.set('AD_SERVER', ad_server)
    Setting.set('AD_PORT', ad_port)
    Setting.set('AD_USE_SSL', '1' if ad_use_ssl else '0')
    Setting.set('AD_START_TLS', '1' if ad_start_tls else '0')
    Setting.set('AD_BASE_DN', ad_base_dn)
    Setting.set('AD_BIND_DN', ad_bind_dn)
    # Only update password if a new one is provided
    if ad_bind_password:
        Setting.set('AD_BIND_PASSWORD', ad_bind_password)
    
    flash('Active Directory settings saved.', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/ad_test', methods=['POST'])
@login_required
def ad_test():
    """Test the Active Directory connection using provided or saved settings."""
    try:
        from ldap3 import Server, Connection, ALL, SUBTREE, Tls
        import ssl
    except ImportError:
        return jsonify({'success': False, 'message': 'ldap3 package not installed. Please run: pip install ldap3'})
    
    # Get settings from form (for testing before save) or fall back to saved settings
    ad_server = (request.form.get('ad_server') or Setting.get('AD_SERVER', '')).strip()
    ad_port = int((request.form.get('ad_port') or Setting.get('AD_PORT', '389') or '389').strip())
    ad_use_ssl = request.form.get('ad_use_ssl') in ('1', 'on', 'true', 'yes') if 'ad_use_ssl' in request.form else (Setting.get('AD_USE_SSL', '0') in ('1', 'true', 'on', 'yes'))
    ad_start_tls = request.form.get('ad_start_tls') in ('1', 'on', 'true', 'yes') if 'ad_start_tls' in request.form else (Setting.get('AD_START_TLS', '0') in ('1', 'true', 'on', 'yes'))
    ad_base_dn = (request.form.get('ad_base_dn') or Setting.get('AD_BASE_DN', '')).strip()
    ad_bind_dn = (request.form.get('ad_bind_dn') or Setting.get('AD_BIND_DN', '')).strip()
    ad_bind_password = request.form.get('ad_bind_password') or ''
    
    # If password is empty, use saved password
    if not ad_bind_password:
        ad_bind_password = Setting.get('AD_BIND_PASSWORD', '')
    
    if not ad_server:
        return jsonify({'success': False, 'message': 'AD server is required.'})
    
    if not ad_bind_dn:
        return jsonify({'success': False, 'message': 'Bind DN/Username is required.'})
    
    try:
        # Configure TLS if needed
        tls_config = None
        if ad_use_ssl or ad_start_tls:
            tls_config = Tls(validate=ssl.CERT_NONE)  # For self-signed certs; in production, consider CERT_REQUIRED
        
        # Create server object
        server = Server(
            ad_server,
            port=ad_port,
            use_ssl=ad_use_ssl,
            tls=tls_config,
            get_info=ALL,
            connect_timeout=10
        )
        
        # Create connection and bind
        conn = Connection(
            server,
            user=ad_bind_dn,
            password=ad_bind_password,
            auto_bind=False,
            raise_exceptions=True
        )
        
        # Start TLS if configured (and not already using SSL)
        if ad_start_tls and not ad_use_ssl:
            conn.open()
            conn.start_tls()
        
        # Bind to the server
        if not conn.bind():
            return jsonify({'success': False, 'message': f'Bind failed: {conn.result}'})
        
        # Test search if base DN is provided
        search_info = ''
        if ad_base_dn:
            conn.search(
                search_base=ad_base_dn,
                search_filter='(objectClass=user)',
                search_scope=SUBTREE,
                attributes=['cn'],
                size_limit=5
            )
            user_count = len(conn.entries)
            search_info = f' Found {user_count} user(s) in sample search.'
        
        # Get server info
        server_info = ''
        if server.info:
            naming_contexts = getattr(server.info, 'naming_contexts', None)
            if naming_contexts:
                server_info = f' Naming contexts: {", ".join(naming_contexts[:2])}...'
        
        conn.unbind()
        
        return jsonify({
            'success': True,
            'message': f'Successfully connected and authenticated to {ad_server}:{ad_port}.{search_info}{server_info}'
        })
        
    except Exception as e:
        error_msg = str(e)
        # Provide more helpful error messages for common issues
        if 'invalidCredentials' in error_msg or '49' in error_msg:
            error_msg = 'Invalid credentials. Check your Bind DN and password.'
        elif 'LDAP_SERVER_DOWN' in error_msg or 'socket' in error_msg.lower():
            error_msg = f'Cannot connect to {ad_server}:{ad_port}. Check server address and port.'
        elif 'timeout' in error_msg.lower():
            error_msg = f'Connection timed out. Server {ad_server}:{ad_port} may be unreachable.'
        
        return jsonify({'success': False, 'message': f'Connection failed: {error_msg}'})


# --- AD Password Check Settings ---
@admin_bp.route('/ad_password_settings', methods=['POST'])
@login_required
def ad_password_settings():
    """Save AD password check schedule settings."""
    ad_pwd_check_enabled = request.form.get('ad_pwd_check_enabled') in ('1', 'on', 'true', 'yes')
    ad_pwd_check_time = (request.form.get('ad_pwd_check_time') or '07:00').strip()
    ad_pwd_warning_days = (request.form.get('ad_pwd_warning_days') or '14').strip()
    
    Setting.set('AD_PWD_CHECK_ENABLED', '1' if ad_pwd_check_enabled else '0')
    Setting.set('AD_PWD_CHECK_TIME', ad_pwd_check_time)
    Setting.set('AD_PWD_WARNING_DAYS', ad_pwd_warning_days)
    
    # Update scheduler job for daily AD password check
    try:
        from .. import scheduler
        from ..services.ad_password_check import run_ad_password_check
        
        if ad_pwd_check_enabled:
            hh, mm = 7, 0
            try:
                parts = ad_pwd_check_time.split(':')
                hh = int(parts[0] or 7)
                mm = int(parts[1] or 0)
            except Exception:
                hh, mm = 7, 0
            
            app_obj = current_app._get_current_object()
            scheduler.add_job(
                func=lambda: run_ad_password_check(app_obj),
                trigger='cron',
                hour=hh,
                minute=mm,
                id='ad_password_check',
                replace_existing=True
            )
        else:
            try:
                scheduler.remove_job('ad_password_check')
            except Exception:
                pass
    except Exception as e:
        current_app.logger.warning(f'Failed to update AD password check scheduler: {e}')
    
    flash('AD password check settings saved.', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/ad_password_check_now', methods=['POST'])
@login_required
def ad_password_check_now():
    """Check AD password expiry for all contacts (users) in the system."""
    from ..models import Contact
    
    # Check if debug mode is enabled
    debug_mode = request.form.get('debug') in ('1', 'true', 'on', 'yes')
    debug_info = {} if debug_mode else None
    
    # Check if AD is configured
    ad_enabled = (Setting.get('AD_ENABLED', '0') or '0') in ('1', 'true', 'on', 'yes')
    if not ad_enabled:
        return jsonify({'error': 'Active Directory is not enabled. Configure it in AD Connect first.'})
    
    ad_server = Setting.get('AD_SERVER', '')
    if not ad_server:
        return jsonify({'error': 'AD server is not configured.'})
    
    try:
        from ldap3 import Server, Connection, ALL, SUBTREE, Tls
        import ssl
    except ImportError:
        return jsonify({'error': 'ldap3 package not installed. Please run: pip install ldap3'})
    
    # Get AD settings
    ad_port = int(Setting.get('AD_PORT', '389') or '389')
    ad_use_ssl = (Setting.get('AD_USE_SSL', '0') or '0') in ('1', 'true', 'on', 'yes')
    ad_start_tls = (Setting.get('AD_START_TLS', '0') or '0') in ('1', 'true', 'on', 'yes')
    ad_base_dn = Setting.get('AD_BASE_DN', '')
    ad_bind_dn = Setting.get('AD_BIND_DN', '')
    ad_bind_password = Setting.get('AD_BIND_PASSWORD', '')
    warning_days = int(Setting.get('AD_PWD_WARNING_DAYS', '14') or '14')
    
    if debug_mode:
        debug_info['settings'] = {
            'ad_server': ad_server,
            'ad_port': ad_port,
            'ad_use_ssl': ad_use_ssl,
            'ad_start_tls': ad_start_tls,
            'ad_base_dn': ad_base_dn,
            'ad_bind_dn': ad_bind_dn,
            'warning_days': warning_days
        }
    
    if not ad_base_dn:
        return jsonify({'error': 'AD Base DN is not configured.'})
    
    try:
        # Configure TLS if needed
        tls_config = None
        if ad_use_ssl or ad_start_tls:
            tls_config = Tls(validate=ssl.CERT_NONE)
        
        # Create server and connection
        server = Server(
            ad_server,
            port=ad_port,
            use_ssl=ad_use_ssl,
            tls=tls_config,
            get_info=ALL,
            connect_timeout=10
        )
        
        conn = Connection(
            server,
            user=ad_bind_dn,
            password=ad_bind_password,
            auto_bind=False,
            raise_exceptions=True
        )
        
        if ad_start_tls and not ad_use_ssl:
            conn.open()
            conn.start_tls()
        
        if not conn.bind():
            return jsonify({'error': f'Failed to bind to AD: {conn.result}'})
        
        # Get all contacts with email addresses (non-archived)
        # Include contacts where archived is False OR NULL (for older records without this field set)
        from sqlalchemy import or_
        contacts = Contact.query.filter(
            Contact.email.isnot(None),
            Contact.email != '',
            or_(Contact.archived == False, Contact.archived.is_(None))
        ).all()
        
        results = []
        found_count = 0
        expiring_soon_count = 0
        expired_count = 0
        
        from datetime import datetime, timedelta
        
        # Get domain max password age from AD (default to 90 days if not found)
        max_pwd_age_days = 90
        try:
            # Query the domain policy for maxPwdAge
            conn.search(
                search_base=ad_base_dn,
                search_filter='(objectClass=domain)',
                search_scope=SUBTREE,
                attributes=['maxPwdAge']
            )
            if conn.entries:
                max_pwd_age = conn.entries[0].maxPwdAge.value
                if max_pwd_age:
                    # maxPwdAge is in 100-nanosecond intervals (negative value)
                    # Convert to days
                    max_pwd_age_days = abs(int(max_pwd_age)) / (10000000 * 60 * 60 * 24)
        except Exception:
            pass  # Use default if we can't get the policy
        
        if debug_mode:
            debug_info['searches'] = []
        
        for contact in contacts:
            email = contact.email.strip().lower()
            user_result = {
                'name': contact.name,
                'email': email,
                'found_in_ad': False,
                'ad_username': None,
                'password_expiry': None,
                'days_until_expiry': None,
                'is_expiring_soon': False,
                'is_expired': False,
                'never_expires': False
            }
            
            # Escape special LDAP characters in email
            from ldap3.utils.conv import escape_filter_chars
            escaped_email = escape_filter_chars(email)
            
            # Search for user by multiple email-related attributes
            # mail: primary email, userPrincipalName: UPN (often email format), proxyAddresses: all email aliases
            search_filter = f'(|(mail={escaped_email})(userPrincipalName={escaped_email})(proxyAddresses=smtp:{escaped_email})(proxyAddresses=SMTP:{escaped_email}))'
            
            if debug_mode:
                search_debug = {
                    'contact_email': email,
                    'escaped_email': escaped_email,
                    'search_filter': search_filter,
                    'search_base': ad_base_dn,
                }
            
            conn.search(
                search_base=ad_base_dn,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=['sAMAccountName', 'userPrincipalName', 'pwdLastSet', 'userAccountControl', 'mail', 'cn', 'proxyAddresses']
            )
            
            if debug_mode:
                search_debug['entries_found'] = len(conn.entries)
                search_debug['result'] = str(conn.result)
                if conn.entries:
                    # Show first entry details
                    entry = conn.entries[0]
                    search_debug['first_entry'] = {
                        'dn': str(entry.entry_dn),
                        'cn': str(entry.cn) if hasattr(entry, 'cn') else None,
                        'mail': str(entry.mail) if hasattr(entry, 'mail') and entry.mail else None,
                        'userPrincipalName': str(entry.userPrincipalName) if hasattr(entry, 'userPrincipalName') and entry.userPrincipalName else None,
                        'sAMAccountName': str(entry.sAMAccountName) if hasattr(entry, 'sAMAccountName') and entry.sAMAccountName else None,
                    }
                debug_info['searches'].append(search_debug)
            
            if conn.entries:
                entry = conn.entries[0]
                user_result['found_in_ad'] = True
                found_count += 1
                
                # Get username
                user_result['ad_username'] = str(entry.sAMAccountName) if hasattr(entry, 'sAMAccountName') and entry.sAMAccountName else None
                
                # Check if password never expires (bit 65536 in userAccountControl)
                uac = int(entry.userAccountControl.value) if hasattr(entry, 'userAccountControl') and entry.userAccountControl.value else 0
                if uac & 0x10000:  # DONT_EXPIRE_PASSWORD flag
                    user_result['never_expires'] = True
                else:
                    # Calculate password expiry
                    pwd_last_set = entry.pwdLastSet.value if hasattr(entry, 'pwdLastSet') and entry.pwdLastSet.value else None
                    
                    if pwd_last_set:
                        # pwdLastSet is a datetime in ldap3
                        if isinstance(pwd_last_set, datetime):
                            pwd_set_date = pwd_last_set
                        else:
                            # If it's a Windows FILETIME (100-nanosecond intervals since 1601)
                            try:
                                pwd_set_date = datetime(1601, 1, 1) + timedelta(microseconds=int(pwd_last_set) / 10)
                            except Exception:
                                pwd_set_date = None
                        
                        if pwd_set_date:
                            expiry_date = pwd_set_date + timedelta(days=max_pwd_age_days)
                            user_result['password_expiry'] = expiry_date.strftime('%Y-%m-%d %H:%M')
                            
                            # Make both datetimes timezone-naive for comparison
                            now = datetime.utcnow()
                            # If expiry_date is timezone-aware, convert to naive UTC
                            if expiry_date.tzinfo is not None:
                                expiry_date_naive = expiry_date.replace(tzinfo=None)
                            else:
                                expiry_date_naive = expiry_date
                            days_until = (expiry_date_naive - now).days
                            user_result['days_until_expiry'] = days_until
                            
                            if days_until < 0:
                                user_result['is_expired'] = True
                                expired_count += 1
                            elif days_until <= warning_days:
                                user_result['is_expiring_soon'] = True
                                expiring_soon_count += 1
            
            # Save password expiry data to Contact record
            now = datetime.utcnow()
            if not user_result['found_in_ad']:
                contact.password_expires_days = -999  # Not found in AD
            elif user_result['never_expires']:
                contact.password_expires_days = -1  # Never expires
            elif user_result['days_until_expiry'] is not None:
                contact.password_expires_days = user_result['days_until_expiry']
            else:
                contact.password_expires_days = None  # Could not determine
            contact.password_checked_at = now
            
            results.append(user_result)
        
        # Commit all Contact updates
        db.session.commit()
        
        conn.unbind()
        
        # Sort results: expired first, then expiring soon, then not found, then OK
        def sort_key(r):
            if r['is_expired']:
                return (0, r.get('days_until_expiry') or 0)
            if r['is_expiring_soon']:
                return (1, r.get('days_until_expiry') or 0)
            if not r['found_in_ad']:
                return (3, 0)
            return (2, r.get('days_until_expiry') or 999)
        
        results.sort(key=sort_key)
        
        return jsonify({
            'results': results,
            'summary': {
                'total_users': len(contacts),
                'found_in_ad': found_count,
                'expiring_soon': expiring_soon_count,
                'expired': expired_count,
                'max_pwd_age_days': int(max_pwd_age_days)
            },
            'debug': debug_info
        })
        
    except Exception as e:
        import traceback
        error_details = str(e)
        if debug_mode:
            error_details += '\n\nTraceback:\n' + traceback.format_exc()
        return jsonify({'error': f'Error checking AD: {error_details}', 'debug': debug_info})


@admin_bp.route('/import_check_now', methods=['POST'])
@login_required
def import_check_now():
    """Run an immediate Ticket Import for enabled services (MS Graph and/or FTP)."""
    # We reuse poll_ms_graph, which respects MS_ENABLED and FTP_ENABLED and will skip
    # MS processing if disabled/misconfigured while still running FTP if enabled.
    poll_ms_graph()
    flash('Ticket import run completed.', 'success')
    return redirect(url_for('admin.index'))


# --- Documents: Categories management ---
@admin_bp.route('/documents', methods=['GET', 'POST'])
@admin_bp.route('/documents/categories', methods=['POST'])
@login_required
def documents_categories():
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        if not name:
            if is_ajax:
                return jsonify({'success': False, 'error': 'Category name is required'})
            flash('Category name is required', 'danger')
            return redirect(url_for('admin.documents_categories'))
        
        exists = DocumentCategory.query.filter(DocumentCategory.name.ilike(name)).first()
        if exists:
            if is_ajax:
                return jsonify({'success': False, 'error': 'A category with that name already exists'})
            flash('A category with that name already exists.', 'warning')
            return redirect(url_for('admin.documents_categories'))
        
        try:
            c = DocumentCategory(name=name)
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
    
    cats = DocumentCategory.query.order_by(DocumentCategory.name.asc()).all()
    return render_template('admin/documents.html', categories=cats)

# --- Assets: Picklists management ---
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


@admin_bp.route('/documents/<int:category_id>/delete', methods=['POST'])
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


@admin_bp.route('/techs/new', methods=['GET', 'POST'])
@login_required
def tech_new():
    form = TechForm()
    if form.validate_on_submit():
        user = User(
            name=form.name.data,
            email=form.email.data.lower(),
            role=form.role.data,
            is_active=form.is_active.data,
        )
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
    if form.validate_on_submit():
        user.name = form.name.data
        user.email = form.email.data.lower()
        user.role = form.role.data
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
    db.session.delete(user)
    db.session.commit()
    flash('Tech deleted', 'success')
    return redirect(url_for('admin.index'))


# --- Process Templates ---
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


# --- Email Settings ---
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
    return render_template('admin/email_settings.html', form=form, domains=domains, deny_form=deny_form, denies=denies)


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


# --- Backup / Restore ---
@admin_bp.route('/backup/attachments', methods=['POST'])
@login_required
def backup_attachments():
    """Download a zip file of all attachment folders."""
    if not admin_required():
        return redirect(url_for('dashboard.index'))
    try:
        # Determine the attachments directory
        attachments_subdir = (Setting.get('ATTACHMENTS_DIR_REL', 'attachments') or 'attachments').strip()
        attachments_subdir = attachments_subdir.replace('\\', '/').lstrip('/') or 'attachments'
        attachments_base = (Setting.get('ATTACHMENTS_BASE', 'instance') or 'instance').strip().lower()
        base_root = current_app.instance_path if attachments_base == 'instance' else (current_app.static_folder or os.path.join(current_app.root_path, 'static'))
        attachments_abs = os.path.join(base_root, attachments_subdir)
        
        if not os.path.exists(attachments_abs):
            flash('Attachments directory does not exist.', 'warning')
            return redirect(url_for('admin.index'))
        
        # Create zip file in memory
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
            for root, dirs, files in os.walk(attachments_abs):
                for file in files:
                    file_path = os.path.join(root, file)
                    # Calculate the archive name relative to the attachments directory
                    # This preserves the folder structure (e.g., attachments/10/file.pdf)
                    arcname = os.path.join(attachments_subdir, os.path.relpath(file_path, attachments_abs))
                    zip_file.write(file_path, arcname)
        
        zip_buffer.seek(0)
        filename = f"helpdesk-attachments-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.zip"
        try:
            current_app.logger.info('Attachments backup created: %s bytes', zip_buffer.getbuffer().nbytes)
        except Exception:
            pass
        return send_file(zip_buffer, as_attachment=True, download_name=filename, mimetype='application/zip')
    except Exception as e:
        try:
            current_app.logger.exception('Attachments backup failed: %s', e)
        except Exception:
            pass
        flash('Attachments backup failed. See logs for details.', 'danger')
        return redirect(url_for('admin.index'))


@admin_bp.route('/backup', methods=['POST'])
@login_required
def backup_db():
    if not admin_required():
        return redirect(url_for('dashboard.index'))
    # Only support SQLite backups for now
    try:
        if db.engine.dialect.name != 'sqlite':
            flash('Backup is only supported for SQLite databases in this version.', 'warning')
            return redirect(url_for('admin.index'))
        db_path = db.engine.url.database
        if not db_path:
            flash('Could not determine database file path.', 'danger')
            return redirect(url_for('admin.index'))
        # Create a temporary file and use sqlite backup to ensure consistency
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        tmp_path = tmp.name
        tmp.close()
        with sqlite3.connect(db_path) as src, sqlite3.connect(tmp_path) as dst:
            src.backup(dst)
        # Read into memory for sending and remove temp file
        with open(tmp_path, 'rb') as f:
            data = f.read()
        try:
            current_app.logger.info('Database backup created: %s bytes', len(data))
        except Exception:
            pass
        try:
            unlink_ok = True
            os_remove = shutil.os.remove  # type: ignore
            os_remove(tmp_path)
        except Exception:
            unlink_ok = False
        filename = f"helpdesk-backup-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.db"
        return send_file(io.BytesIO(data), as_attachment=True, download_name=filename, mimetype='application/octet-stream')
    except Exception as e:
        try:
            current_app.logger.exception('Backup failed: %s', e)
        except Exception:
            pass
        flash('Backup failed. See logs for details.', 'danger')
        return redirect(url_for('admin.index'))


@admin_bp.route('/restore', methods=['POST'])
@login_required
def restore_db():
    if not admin_required():
        return redirect(url_for('dashboard.index'))
    if 'backup_file' not in request.files:
        flash('No file uploaded.', 'danger')
        return redirect(url_for('admin.index'))
    file = request.files['backup_file']
    if not file or file.filename == '':
        flash('Please select a backup file to upload.', 'warning')
        return redirect(url_for('admin.index'))
    if db.engine.dialect.name != 'sqlite':
        flash('Restore is only supported for SQLite databases in this version.', 'warning')
        return redirect(url_for('admin.index'))
    # Save upload to a temp file
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
    tmp_path = tmp.name
    tmp.close()
    file.save(tmp_path)
    # Validate it is a readable SQLite database
    try:
        with sqlite3.connect(tmp_path) as test:
            test.execute('PRAGMA schema_version;').fetchone()
    except Exception:
        try:
            shutil.os.remove(tmp_path)  # type: ignore
        except Exception:
            pass
        flash('Uploaded file is not a valid SQLite database.', 'danger')
        return redirect(url_for('admin.index'))
    # Replace the live DB safely
    try:
        db_path = db.engine.url.database
        if not db_path:
            flash('Could not determine database file path.', 'danger')
            return redirect(url_for('admin.index'))
        # Dispose connections
        db.session.remove()
        db.engine.dispose()
        # Backup current DB
        backup_path = f"{db_path}.pre-restore-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
        try:
            shutil.copyfile(db_path, backup_path)
        except Exception:
            pass
        # Replace with uploaded
        shutil.copyfile(tmp_path, db_path)
        # Re-run lightweight migrations to ensure required columns exist
        try:
            from ..utils.db_migrate import (
                ensure_ticket_columns,
                ensure_user_columns,
                ensure_ticket_process_item_columns,
                ensure_ticket_note_columns,
                ensure_order_tables,
                ensure_company_shipping_tables,
                ensure_documents_tables,
                ensure_assets_table,
                ensure_asset_picklists,
            )
            ensure_ticket_columns(db.engine)
            ensure_user_columns(db.engine)
            ensure_ticket_process_item_columns(db.engine)
            ensure_ticket_note_columns(db.engine)
            ensure_order_tables(db.engine)
            ensure_company_shipping_tables(db.engine)
            ensure_documents_tables(db.engine)
            ensure_assets_table(db.engine)
            ensure_asset_picklists(db.engine)
        except Exception:
            pass
        flash('Database restored successfully.', 'success')
    except Exception as e:
        try:
            current_app.logger.exception('Restore failed: %s', e)
        except Exception:
            pass
        flash('Restore failed. See logs for details.', 'danger')
    finally:
        try:
            shutil.os.remove(tmp_path)
        except Exception:
            pass
    return redirect(url_for('admin.index'))


@admin_bp.route('/backup/settings', methods=['POST'])
@login_required
def backup_settings():
    if not admin_required():
        return redirect(url_for('dashboard.index'))
    # Read form values
    enabled = bool(request.form.get('auto_enabled'))
    time_str = (request.form.get('auto_time') or '23:00').strip()
    backup_dir = (request.form.get('auto_dir') or '').strip()
    keep_raw = (request.form.get('auto_keep') or '7').strip()
    try:
        keep = max(1, int(keep_raw))
    except Exception:
        keep = 7
    # Normalize time HH:MM
    hh, mm = 23, 0
    try:
        parts = time_str.split(':')
        hh = int(parts[0] or 23)
        mm = int(parts[1] or 0)
        if hh < 0 or hh > 23 or mm < 0 or mm > 59:
            raise ValueError('invalid time')
    except Exception:
        time_str = '23:00'
        hh, mm = 23, 0
    # Ensure directory exists (if provided)
    if backup_dir:
        try:
            os.makedirs(backup_dir, exist_ok=True)
        except Exception:
            flash('Could not create backup directory. Using default instance/backups.', 'warning')
            backup_dir = ''
    # Persist settings
    Setting.set('AUTO_BACKUP_ENABLED', '1' if enabled else '0')
    Setting.set('AUTO_BACKUP_TIME', time_str)
    if backup_dir:
        Setting.set('AUTO_BACKUP_DIR', backup_dir)
    Setting.set('AUTO_BACKUP_RETENTION', str(keep))
    # Reschedule job immediately
    try:
        # Capture real app object for later job execution
        app_obj = current_app._get_current_object()
        if enabled:
            scheduler.add_job(func=lambda: run_auto_backup(app_obj), trigger='cron', hour=hh, minute=mm, id='auto_backup', replace_existing=True)
        else:
            try:
                scheduler.remove_job('auto_backup')
            except Exception:
                pass
        flash('Auto-backup settings updated.', 'success')
    except Exception:
        flash('Failed to update auto-backup scheduler.', 'danger')
    return redirect(url_for('admin.index'))


# --- Demo Mode: Disable and reset database ---
@admin_bp.route('/demo/disable', methods=['POST'])
@login_required
def demo_disable():
    if not admin_required():
        return redirect(url_for('dashboard.index'))
    # Only for SQLite in this simple implementation
    try:
        if db.engine.dialect.name != 'sqlite':
            flash('Demo reset is only supported for SQLite in this version.', 'warning')
            return redirect(url_for('admin.index'))
        db_path = db.engine.url.database
        if not db_path:
            flash('Could not determine database file path.', 'danger')
            return redirect(url_for('admin.index'))
        # Dispose connections before file operations
        db.session.remove()
        db.engine.dispose()
        # Backup current DB
        try:
            backup_path = f"{db_path}.pre-demo-reset-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"
            shutil.copyfile(db_path, backup_path)
        except Exception:
            pass
        # Delete and recreate empty DB
        try:
            if os.path.exists(db_path):
                os.remove(db_path)
        except Exception:
            # If delete fails, try truncating
            open(db_path, 'wb').close()
        # Create empty SQLite file
        with sqlite3.connect(db_path) as conn:
            conn.execute('PRAGMA journal_mode=WAL;')
        # Recreate schema and ensure required columns/tables
        from .. import db as _db
        from ..models import User as _User  # import to register models
        try:
            _db.create_all()
            from ..utils.db_migrate import (
                ensure_ticket_columns,
                ensure_user_columns,
                ensure_ticket_process_item_columns,
                ensure_ticket_note_columns,
                ensure_order_tables,
                ensure_company_shipping_tables,
                ensure_documents_tables,
                ensure_assets_table,
                ensure_asset_picklists,
                ensure_scheduled_tickets_table,
            )
            ensure_ticket_columns(_db.engine)
            ensure_user_columns(_db.engine)
            ensure_ticket_process_item_columns(_db.engine)
            ensure_ticket_note_columns(_db.engine)
            ensure_order_tables(_db.engine)
            ensure_company_shipping_tables(_db.engine)
            ensure_documents_tables(_db.engine)
            ensure_assets_table(_db.engine)
            ensure_asset_picklists(_db.engine)
            ensure_scheduled_tickets_table(_db.engine)
        except Exception:
            pass
        flash('Demo Mode disabled. Database has been reset. Please complete setup again.', 'success')
        return redirect(url_for('setup.index'))
    except Exception as e:
        try:
            current_app.logger.exception('Demo reset failed: %s', e)
        except Exception:
            pass
        flash('Demo reset failed. See logs for details.', 'danger')
        return redirect(url_for('admin.index'))


# --- Email Templates Routes ---

@admin_bp.route('/email_templates_list')
@login_required
def email_templates_list():
    """Return list of all email templates as JSON."""
    from ..models import EmailTemplate
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
    from ..models import EmailTemplate
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
    from ..models import EmailTemplate
    
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
    from ..models import EmailTemplate
    
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


# --- Password Expiry Notification Routes ---

@admin_bp.route('/password_notifications_list')
@login_required
def password_notifications_list():
    """Return list of all password expiry notification rules as JSON."""
    from ..models import PasswordExpiryNotification, EmailTemplate
    
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
    from ..models import PasswordExpiryNotification, EmailTemplate
    
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
    from ..models import PasswordExpiryNotification
    
    notification_id = request.form.get('id', type=int)
    if not notification_id:
        return jsonify({'error': 'Notification ID required'}), 400
    
    notification = PasswordExpiryNotification.query.get(notification_id)
    if not notification:
        return jsonify({'error': 'Notification not found'}), 404
    
    db.session.delete(notification)
    db.session.commit()
    return jsonify({'success': True})
