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
    # Asset Spot Check settings
    spot_check_enabled = (Setting.get('ASSET_SPOT_CHECK_ENABLED', '0') or '0') in ('1','true','on','yes')
    spot_check_frequency = Setting.get('ASSET_SPOT_CHECK_FREQUENCY', 'weekly') or 'weekly'  # 'weekly' or 'monthly'
    spot_check_day_of_week = Setting.get('ASSET_SPOT_CHECK_DAY_OF_WEEK', '1') or '1'  # 0=Mon, 6=Sun
    spot_check_day_of_month = Setting.get('ASSET_SPOT_CHECK_DAY_OF_MONTH', '1') or '1'  # 1-31
    spot_check_time = Setting.get('ASSET_SPOT_CHECK_TIME', '09:00') or '09:00'
    spot_check_mode = Setting.get('ASSET_SPOT_CHECK_MODE', 'count') or 'count'  # 'count' or 'percent'
    spot_check_count = Setting.get('ASSET_SPOT_CHECK_COUNT', '10') or '10'
    spot_check_percent = Setting.get('ASSET_SPOT_CHECK_PERCENT', '5') or '5'
    try:
        spot_check_assignee_id = int(Setting.get('ASSET_SPOT_CHECK_ASSIGNEE_ID', '') or '0') or None
    except Exception:
        spot_check_assignee_id = None
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
        spot_check_enabled=spot_check_enabled,
        spot_check_frequency=spot_check_frequency,
        spot_check_day_of_week=spot_check_day_of_week,
        spot_check_day_of_month=spot_check_day_of_month,
        spot_check_time=spot_check_time,
        spot_check_mode=spot_check_mode,
        spot_check_count=spot_check_count,
        spot_check_percent=spot_check_percent,
        spot_check_assignee_id=spot_check_assignee_id,
    )


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
        from ... import db as _db
        from ...models import User as _User  # import to register models
        try:
            _db.create_all()
            from ...utils.db_migrate import (
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
