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
        # Bundle the DB with the encryption key: encrypted settings (MS Graph
        # secret, AD password) are unreadable on a fresh install without it.
        ts = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
        key_path = os.path.join(current_app.instance_path, 'secret_key')
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('helpdesk.db', data)
            if os.path.exists(key_path):
                with open(key_path, 'r', encoding='utf-8') as kf:
                    zf.writestr('secret_key', kf.read())
                key_note = ('secret_key   - the encryption/session key this database pairs with.\n'
                            '               Without it, encrypted settings (MS Graph secret, AD\n'
                            '               password) cannot be decrypted after a restore.')
            else:
                key_note = ('(no secret_key file: this install takes its key from the\n'
                            ' FLASK_SECRET_KEY environment variable - keep that value safe.)')
            zf.writestr(
                'BACKUP-README.txt',
                f'HelpfulDjinn HelpDesk backup - {ts} UTC\n\n'
                'Contents:\n'
                'helpdesk.db  - SQLite database snapshot.\n'
                f'{key_note}\n\n'
                'Restore via Admin -> Backup -> Restore from File (upload this zip),\n'
                'or manually place both files in Source/instance/ and restart.\n'
                'This archive contains credentials in recoverable form - store it securely.\n'
            )
        zip_buffer.seek(0)
        filename = f"helpdesk-backup-{ts}.zip"
        return send_file(zip_buffer, as_attachment=True, download_name=filename, mimetype='application/zip')
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
    # Zip backups (current download format) bundle helpdesk.db + secret_key;
    # bare .db uploads (legacy backups) are still accepted.
    restored_key = None
    if zipfile.is_zipfile(tmp_path):
        try:
            with zipfile.ZipFile(tmp_path) as zf:
                names = zf.namelist()
                db_member = 'helpdesk.db' if 'helpdesk.db' in names else next(
                    (n for n in names if n.lower().endswith('.db')), None)
                if not db_member:
                    raise ValueError('zip contains no .db file')
                db_bytes = zf.read(db_member)
                if 'secret_key' in names:
                    restored_key = zf.read('secret_key').decode('utf-8').strip() or None
            with open(tmp_path, 'wb') as f:
                f.write(db_bytes)
        except Exception:
            try:
                shutil.os.remove(tmp_path)  # type: ignore
            except Exception:
                pass
            flash('Uploaded zip is not a valid HelpDesk backup (expected helpdesk.db inside).', 'danger')
            return redirect(url_for('admin.index'))
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
        # Restore the paired encryption key if the backup carried one
        if restored_key:
            key_path = os.path.join(current_app.instance_path, 'secret_key')
            try:
                current = None
                if os.path.exists(key_path):
                    with open(key_path, 'r', encoding='utf-8') as kf:
                        current = kf.read().strip()
                if current != restored_key:
                    if current:
                        shutil.copyfile(key_path, f"{key_path}.pre-restore-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}")
                    with open(key_path, 'w', encoding='utf-8') as kf:
                        kf.write(restored_key)
                    flash('Encryption key restored from backup. Restart the app (web and scheduler) to apply it; all users will need to log in again.', 'warning')
            except Exception as e:
                current_app.logger.exception('Failed to restore secret_key from backup: %s', e)
                flash('Database restored, but the encryption key from the backup could not be written. Encrypted settings may be unreadable.', 'danger')
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
    _bump_schedule_version()
    flash('Auto-backup settings updated.', 'success')
    return redirect(url_for('admin.index'))
