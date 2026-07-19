from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file, current_app, jsonify, make_response, session
from flask_login import login_required, current_user
from ...forms import MSGraphForm, TechForm, ProcessTemplateForm, ProcessTemplateItemForm, AllowedDomainForm, DenyFilterForm, ClientApiForm
from ...models import Setting, User, Role, ProcessTemplate, ProcessTemplateItem, AllowedDomain, DenyFilter, Vendor, PurchaseOrder, Company, ShippingLocation, DocumentCategory, AssetAudit, Asset, AssetCategory, AssetManufacturer, AssetCondition, AssetLocation, ScheduledTicket, Ticket, TicketTask, TicketStatus, Tag, Report, ReportRun, ApiToken, TicketNote, TicketAttachment, Contact, ApprovalRequest, Project, OrderItem, Document, EmailCheck, EmailCheckEntry, OutgoingEmail, EmailOutbox
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
import sys
import requests
import ftplib

from . import admin_bp, admin_required, _bump_schedule_version  # noqa: F401


def _resolve_backup_dir():
    """Mirror run_auto_backup's directory resolution (app/__init__.py)."""
    if getattr(sys, 'frozen', False):
        try:
            default_dir = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'backups')
        except Exception:
            default_dir = os.path.join(current_app.instance_path, 'backups')
    else:
        default_dir = os.path.join(current_app.instance_path, 'backups')
    return (Setting.get('AUTO_BACKUP_DIR', default_dir) or default_dir).strip() or default_dir


def _dir_stats(path):
    """Recursive file count + total bytes for a directory. Never raises."""
    stats = {'path': path or '', 'exists': False, 'file_count': 0, 'total_bytes': 0, 'error': None}
    try:
        if not path or not os.path.isdir(path):
            return stats
        stats['exists'] = True
        for root, _dirs, files in os.walk(path):
            for f in files:
                stats['file_count'] += 1
                try:
                    stats['total_bytes'] += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
    except Exception as e:
        stats['error'] = str(e)
    return stats


def _backup_dir_info():
    """Backup directory health summary shared by the Data pages."""
    info = {
        'dir': _resolve_backup_dir(), 'exists': False, 'error': None,
        'file_count': 0, 'total_bytes': 0,
        'auto_count': 0, 'latest_auto': None,
        'has_secret_key': False,
        'auto_enabled': (Setting.get('AUTO_BACKUP_ENABLED', '0') or '0') in ('1', 'true', 'on', 'yes'),
        'status': 'disabled',
        'latest_age_hours': None,
    }
    try:
        if os.path.isdir(info['dir']):
            info['exists'] = True
            latest = None
            for name in os.listdir(info['dir']):
                full = os.path.join(info['dir'], name)
                if not os.path.isfile(full):
                    continue
                info['file_count'] += 1
                try:
                    info['total_bytes'] += os.path.getsize(full)
                except OSError:
                    pass
                if name == 'secret_key':
                    info['has_secret_key'] = True
                if name.startswith('helpdesk-autobackup-') and name.endswith('.db'):
                    info['auto_count'] += 1
                    try:
                        mtime = os.path.getmtime(full)
                        if latest is None or mtime > latest[1]:
                            latest = (name, mtime, os.path.getsize(full))
                    except OSError:
                        pass
            if latest:
                info['latest_auto'] = {'name': latest[0], 'mtime': datetime.fromtimestamp(latest[1]), 'size': latest[2]}
    except Exception as e:
        info['error'] = str(e)
    if info['error']:
        info['status'] = 'error'
    elif not info['auto_enabled']:
        info['status'] = 'disabled'
    elif not info['latest_auto']:
        info['status'] = 'never'
    else:
        # File mtime and now are both local time; 26h tolerates daily-schedule drift.
        age = datetime.now() - info['latest_auto']['mtime']
        info['latest_age_hours'] = round(age.total_seconds() / 3600, 1)
        info['status'] = 'ok' if age.total_seconds() < 26 * 3600 else 'stale'
    return info


@admin_bp.route('/data')
@login_required
def data():
    """Data health overview: database, attachments, backups, record counts."""
    dialect = db.engine.dialect.name
    db_info = {'dialect': dialect, 'is_sqlite': dialect == 'sqlite', 'path': None, 'size': None,
               'wal_size': 0, 'stray_count': 0, 'stray_bytes': 0}
    if db_info['is_sqlite']:
        db_path = db.engine.url.database
        db_info['path'] = db_path
        try:
            if db_path and os.path.exists(db_path):
                db_info['size'] = os.path.getsize(db_path)
                wal = db_path + '-wal'
                if os.path.exists(wal):
                    db_info['wal_size'] = os.path.getsize(wal)
                db_dir = os.path.dirname(db_path) or '.'
                prefix = os.path.basename(db_path) + '.pre-'
                for name in os.listdir(db_dir):
                    if name.startswith(prefix):
                        db_info['stray_count'] += 1
                        try:
                            db_info['stray_bytes'] += os.path.getsize(os.path.join(db_dir, name))
                        except OSError:
                            pass
        except Exception:
            pass

    attachments_subdir = (Setting.get('ATTACHMENTS_DIR_REL', 'attachments') or 'attachments').strip()
    attachments_subdir = attachments_subdir.replace('\\', '/').lstrip('/') or 'attachments'
    attachments_base = (Setting.get('ATTACHMENTS_BASE', 'instance') or 'instance').strip().lower()
    base_root = current_app.instance_path if attachments_base == 'instance' else (current_app.static_folder or os.path.join(current_app.root_path, 'static'))
    attachments_abs = os.path.join(base_root, attachments_subdir)
    att_info = _dir_stats(attachments_abs)
    att_info['folder_count'] = 0
    if att_info['exists']:
        try:
            att_info['folder_count'] = len([d for d in os.listdir(attachments_abs) if os.path.isdir(os.path.join(attachments_abs, d))])
        except Exception:
            pass
    att_info['db_count'] = TicketAttachment.query.count()
    att_info['db_bytes'] = db.session.query(db.func.coalesce(db.func.sum(TicketAttachment.size_bytes), 0)).scalar() or 0

    backup_info = _backup_dir_info()

    log_file = current_app.config.get('LOG_FILE_PATH', '')
    log_info = _dir_stats(os.path.dirname(log_file)) if log_file else _dir_stats('')

    record_groups = [
        ('Tickets', [
            ('Tickets', Ticket.query.count()),
            ('Ticket notes', TicketNote.query.count()),
            ('Attachments', att_info['db_count']),
            ('Tasks', TicketTask.query.count()),
            ('Scheduled tickets', ScheduledTicket.query.count()),
        ]),
        ('People & Projects', [
            ('Technicians', User.query.count()),
            ('Contacts', Contact.query.count()),
            ('Approval requests', ApprovalRequest.query.count()),
            ('Projects', Project.query.count()),
        ]),
        ('Purchasing', [
            ('Vendors', Vendor.query.count()),
            ('Purchase orders', PurchaseOrder.query.count()),
            ('Order items', OrderItem.query.count()),
        ]),
        ('Documents', [
            ('Documents', Document.query.count()),
            ('Categories', DocumentCategory.query.count()),
        ]),
        ('Assets', [
            ('Assets (active)', Asset.query.filter(Asset.deleted_flag.isnot(True)).count()),
            ('Assets (deleted)', Asset.query.filter(Asset.deleted_flag.is_(True)).count()),
            ('Asset log entries', AssetAudit.query.count()),
        ]),
        ('Email', [
            ('Poll runs', EmailCheck.query.count()),
            ('Messages processed', EmailCheckEntry.query.count()),
            ('Sent emails', OutgoingEmail.query.count()),
            ('Outbox', EmailOutbox.query.count()),
        ]),
        ('System', [
            ('Settings', Setting.query.count()),
            ('Roles', Role.query.count()),
            ('API tokens', ApiToken.query.count()),
        ]),
    ]
    total_records = sum(count for _label, rows in record_groups for _name, count in rows)
    outbox_pending = EmailOutbox.query.filter_by(status='pending').count()
    outbox_dead = EmailOutbox.query.filter_by(status='dead').count()
    return render_template('admin/data.html', db_info=db_info, att_info=att_info,
                           backup_info=backup_info, log_info=log_info,
                           record_groups=record_groups, total_records=total_records,
                           outbox_pending=outbox_pending, outbox_dead=outbox_dead)


@admin_bp.route('/data/cleanup-strays', methods=['POST'])
@login_required
def data_cleanup_strays():
    """Delete old .pre-* restore/migration snapshot copies next to the SQLite DB."""
    if not admin_required():
        return redirect(url_for('dashboard.index'))
    if db.engine.dialect.name != 'sqlite':
        flash('Cleanup is only available for SQLite databases.', 'warning')
        return redirect(url_for('admin.data'))
    db_path = db.engine.url.database
    if not db_path or not os.path.exists(db_path):
        flash('Could not determine database file path.', 'danger')
        return redirect(url_for('admin.data'))
    db_dir = os.path.dirname(db_path) or '.'
    prefix = os.path.basename(db_path) + '.pre-'
    removed, freed, failed = 0, 0, 0
    try:
        for name in os.listdir(db_dir):
            if not name.startswith(prefix):
                continue
            full = os.path.join(db_dir, name)
            if not os.path.isfile(full):
                continue
            try:
                size = os.path.getsize(full)
                os.remove(full)
                removed += 1
                freed += size
            except OSError:
                failed += 1
    except Exception as e:
        current_app.logger.exception('Stray DB copy cleanup failed: %s', e)
        flash('Cleanup failed. See logs for details.', 'danger')
        return redirect(url_for('admin.data'))
    if removed:
        current_app.logger.info('Stray DB copy cleanup by user %s: removed %d files, %d bytes',
                                getattr(current_user, 'id', None), removed, freed)
        flash(f'Removed {removed} old database cop{"ies" if removed != 1 else "y"} ({freed / 1048576:.1f} MB freed).', 'success')
    else:
        flash('No old database copies found.', 'info')
    if failed:
        flash(f'{failed} file(s) could not be deleted (in use or permission denied).', 'warning')
    return redirect(url_for('admin.data'))


@admin_bp.route('/data/backup')
@login_required
def data_backup():
    """Backup & Restore page (second tab of the Data section)."""
    backup_info = _backup_dir_info()
    auto_time = (Setting.get('AUTO_BACKUP_TIME', '23:00') or '23:00')
    auto_dir = (Setting.get('AUTO_BACKUP_DIR', '') or '')
    try:
        auto_keep = int(Setting.get('AUTO_BACKUP_RETENTION', '7') or '7')
    except Exception:
        auto_keep = 7
    return render_template('admin/data_backup.html', backup_info=backup_info,
                           is_sqlite=(db.engine.dialect.name == 'sqlite'),
                           auto_enabled=backup_info['auto_enabled'],
                           auto_time=auto_time, auto_dir=auto_dir, auto_keep=auto_keep)


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
            return redirect(url_for('admin.data_backup'))
        
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
        return redirect(url_for('admin.data_backup'))


@admin_bp.route('/backup', methods=['POST'])
@login_required
def backup_db():
    if not admin_required():
        return redirect(url_for('dashboard.index'))
    # Only support SQLite backups for now
    try:
        if db.engine.dialect.name != 'sqlite':
            flash('Backup is only supported for SQLite databases in this version.', 'warning')
            return redirect(url_for('admin.data_backup'))
        db_path = db.engine.url.database
        if not db_path:
            flash('Could not determine database file path.', 'danger')
            return redirect(url_for('admin.data_backup'))
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
                'Restore via Admin -> Data -> Backup & Restore -> Restore from File (upload this zip),\n'
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
        return redirect(url_for('admin.data_backup'))


@admin_bp.route('/restore', methods=['POST'])
@login_required
def restore_db():
    if not admin_required():
        return redirect(url_for('dashboard.index'))
    if 'backup_file' not in request.files:
        flash('No file uploaded.', 'danger')
        return redirect(url_for('admin.data_backup'))
    file = request.files['backup_file']
    if not file or file.filename == '':
        flash('Please select a backup file to upload.', 'warning')
        return redirect(url_for('admin.data_backup'))
    if db.engine.dialect.name != 'sqlite':
        flash('Restore is only supported for SQLite databases in this version.', 'warning')
        return redirect(url_for('admin.data_backup'))
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
            return redirect(url_for('admin.data_backup'))
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
        return redirect(url_for('admin.data_backup'))
    # Replace the live DB safely
    try:
        db_path = db.engine.url.database
        if not db_path:
            flash('Could not determine database file path.', 'danger')
            return redirect(url_for('admin.data_backup'))
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
    return redirect(url_for('admin.data_backup'))


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
    # Blank reverts to the default directory (run_auto_backup falls back to it)
    Setting.set('AUTO_BACKUP_DIR', backup_dir)
    Setting.set('AUTO_BACKUP_RETENTION', str(keep))
    _bump_schedule_version()
    flash('Auto-backup settings updated.', 'success')
    return redirect(url_for('admin.data_backup'))
