from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
import os
import sys
from pathlib import Path
from datetime import timezone
from zoneinfo import ZoneInfo

# Initialize extensions
csrf = CSRFProtect()
db = SQLAlchemy()
login_manager = LoginManager()
scheduler = BackgroundScheduler()


def run_auto_backup(app: Flask) -> None:
    """Create a local SQLite backup into configured directory and enforce retention.

    When running frozen (PyInstaller onefile), place default backups in
    ./backups next to the executable for persistence.
    """
    with app.app_context():
        try:
            from .models import Setting as _Setting  # type: ignore
            # Only support SQLite backups
            if db.engine.dialect.name != 'sqlite':
                return
            # Resolve backup directory
            if getattr(sys, 'frozen', False):
                try:
                    exe_dir = Path(sys.executable).resolve().parent
                    default_dir = str(exe_dir / 'backups')
                except Exception:
                    default_dir = os.path.join(app.instance_path, 'backups')
            else:
                default_dir = os.path.join(app.instance_path, 'backups')
            backup_dir = (_Setting.get('AUTO_BACKUP_DIR', default_dir) or default_dir).strip()
            try:
                os.makedirs(backup_dir, exist_ok=True)
            except Exception:
                return
            # Create backup using sqlite backup for consistency
            from datetime import datetime as __dt
            db_path = db.engine.url.database
            if not db_path:
                return
            ts = __dt.utcnow().strftime('%Y%m%d-%H%M%S')
            filename = f"helpdesk-autobackup-{ts}.db"
            dest_path = os.path.join(backup_dir, filename)
            import sqlite3 as _sqlite3
            try:
                with _sqlite3.connect(db_path) as src, _sqlite3.connect(dest_path) as dst:
                    src.backup(dst)
            except Exception:
                # If direct backup fails, try copy as fallback
                try:
                    import shutil as _sh
                    _sh.copyfile(db_path, dest_path)
                except Exception:
                    return
            # Enforce retention
            try:
                keep = int(_Setting.get('AUTO_BACKUP_RETENTION', '7') or '7')
            except Exception:
                keep = 7
            try:
                entries = sorted(
                    [f for f in os.listdir(backup_dir) if f.lower().endswith('.db') and 'autobackup' in f.lower()],
                    reverse=True
                )
                for f in entries[keep:]:
                    try:
                        os.remove(os.path.join(backup_dir, f))
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception:
            # If settings or engine not ready, skip
            pass


def create_app():
    """Application factory.

    When running under a PyInstaller --onefile executable (detected via
    sys.frozen), persist mutable data (database, attachments, backups) in
    subfolders alongside the executable instead of inside the temporary
    extraction directory that is discarded on exit.

    Layout (next to HelpfulDjinn.exe):
      ./database/helpdesk.db   (SQLite file)
      ./attachments/           (ticket & email attachments)
      ./backups/               (auto/manual backups)
    """
    load_dotenv()

    frozen = getattr(sys, 'frozen', False)
    exe_dir: Path | None = None
    instance_path_arg = None
    if frozen:
        try:
            exe_dir = Path(sys.executable).resolve().parent
            instance_path_arg = str(exe_dir)  # so instance = exe folder
        except Exception:
            exe_dir = None
            instance_path_arg = None

    # Create Flask app, overriding instance_path when frozen so that
    # app.instance_path points at the stable executable directory.
    if instance_path_arg:
        app = Flask(__name__, static_folder="static", template_folder="templates", instance_path=instance_path_arg)
    else:
        app = Flask(__name__, static_folder="static", template_folder="templates")

    app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "dev")

    if exe_dir:
        # Ensure persistent subfolders exist
        db_dir = exe_dir / 'database'
        attach_dir = exe_dir / 'attachments'
        backup_dir = exe_dir / 'backups'
        for d in (db_dir, attach_dir, backup_dir):
            try:
                d.mkdir(exist_ok=True)
            except Exception:
                pass
        # Absolute DB path (avoid relative to working dir ambiguity)
        db_path = db_dir / 'helpdesk.db'
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}".replace('\\', '/')
        # Hint defaults for attachments/base if settings not yet defined
        # (Settings table may override later; code computing attachments_abs uses instance_path)
    else:
        app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///helpdesk.db")

    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Init extensions
    csrf.init_app(app)
    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"

    # Import models so SQLAlchemy registers them before create_all.
    from .models import (  # noqa: F401
    User, Ticket, Setting, ProcessTemplate, ProcessTemplateItem, TicketProcess,
    TicketProcessItem, AllowedDomain, TicketAttachment, Contact, DenyFilter,
    TicketTask, OrderItem, PurchaseOrder, Vendor, Company, ShippingLocation,
    DocumentCategory, Document, Asset, AssetCategory, AssetManufacturer, AssetCondition, AssetLocation,
    EmailCheck, EmailCheckEntry
    )

    with app.app_context():
        db.create_all()
        # Ensure DB has required ticket columns (for existing SQLite DBs)
        try:
            from .utils.db_migrate import (
                ensure_ticket_columns,
                ensure_user_columns,
                ensure_ticket_process_item_columns,
                ensure_ticket_note_columns,
                ensure_project_table,
                ensure_ticket_task_table,
                ensure_order_tables,
                ensure_vendor_table,
                ensure_company_shipping_tables,
                ensure_documents_tables,
                ensure_assets_table,
                ensure_asset_picklists,
                ensure_scheduled_tickets_table,
            )
            ensure_ticket_columns(db.engine)
            ensure_user_columns(db.engine)
            ensure_ticket_process_item_columns(db.engine)
            ensure_ticket_note_columns(db.engine)
            ensure_project_table(db.engine)
            ensure_ticket_task_table(db.engine)
            ensure_order_tables(db.engine)
            ensure_vendor_table(db.engine)
            ensure_company_shipping_tables(db.engine)
            ensure_documents_tables(db.engine)
            ensure_assets_table(db.engine)
            ensure_asset_picklists(db.engine)
            ensure_scheduled_tickets_table(db.engine)
            # Ensure AssetAudit table (runtime lightweight migration with pre-backup for SQLite)
            from sqlalchemy import inspect
            insp = inspect(db.engine)
            existing = {t.lower() for t in insp.get_table_names()}
            if 'assetaudit' not in existing:
                uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
                if uri.startswith('sqlite:///'):
                    db_path = uri.replace('sqlite:///','')
                    if os.path.exists(db_path):
                        try:
                            import shutil, datetime as _dt
                            ts = _dt.datetime.utcnow().strftime('%Y%m%d-%H%M%S')
                            shutil.copy2(db_path, f"{db_path}.pre-assetaudit-{ts}.bak")
                        except Exception:
                            pass
                try:
                    from .models import AssetAudit  # noqa: F401
                    db.create_all()  # will create missing AssetAudit
                except Exception:
                    pass
            # Ensure TicketTask table exists (simple create_all should create it, but keep in try/except)
        except Exception:
            pass

        # One-time backfill for legacy notes missing is_private
        try:
            from .models import TicketNote as _TicketNote
            missing = _TicketNote.query.filter(_TicketNote.is_private.is_(None)).all()
            changed = 0
            for n in missing:
                # If no author, it's received from email -> public (False)
                n.is_private = True if n.author_id else False
                changed += 1
            if changed:
                db.session.commit()
        except Exception:
            pass
        # Bootstrap admin user if not exists (only if NOT in setup mode)
        from .utils.security import hash_password
        admin_email = os.getenv("ADMIN_EMAIL")
        admin_password = os.getenv("ADMIN_PASSWORD")
        # Only create bootstrap admin if: 1) env vars set, 2) user doesn't exist, 3) we're not in setup mode
        if admin_email and admin_password and not User.query.filter_by(email=admin_email).first():
            # Check if any users exist - if not, we should use setup flow instead
            if User.query.count() > 0:
                admin = User(email=admin_email, name="Administrator", role="admin", is_active=True)
                admin.password_hash = hash_password(admin_password)
                db.session.add(admin)
                db.session.commit()

    # Register blueprints
    from .blueprints.setup import setup_bp
    from .blueprints.auth import auth_bp
    from .blueprints.dashboard import dashboard_bp
    from .blueprints.admin import admin_bp
    from .blueprints.tickets import tickets_bp
    from .blueprints.projects import projects_bp
    from .blueprints.users import users_bp
    from .blueprints.orders import orders_bp
    from .blueprints.documents import documents_bp
    from .blueprints.assets import assets_bp

    app.register_blueprint(setup_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(tickets_bp)
    app.register_blueprint(projects_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(orders_bp)
    app.register_blueprint(documents_bp)
    app.register_blueprint(assets_bp)

    # Middleware to redirect to setup if no users exist
    @app.before_request
    def check_setup():
        from flask import request as req, redirect, url_for
        from .blueprints.setup import needs_setup
        
        # Skip setup check for setup routes, static files, and images
        if req.endpoint and (req.endpoint.startswith('setup.') or req.endpoint.startswith('static') or req.endpoint == 'serve_image'):
            return None
        
        # Redirect to setup if needed
        if needs_setup():
            return redirect(url_for('setup.index'))
        
        return None

    # Route to serve images from the images folder
    @app.route('/images/<path:filename>')
    def serve_image(filename):
        from flask import send_from_directory
        images_path = os.path.join(app.root_path, 'images')
        return send_from_directory(images_path, filename)

    # Theming context
    @app.context_processor
    def inject_theme():
        try:
            from flask_login import current_user
            theme = getattr(current_user, 'theme', 'light') if current_user and current_user.is_authenticated else 'light'
        except Exception:
            theme = 'light'
        # Demo mode flag (visible across all pages)
        try:
            from .models import Setting as _Setting  # local import to avoid circulars at import time
            demo_mode = (_Setting.get('DEMO_MODE', '0') or '0') in ('1','true','on','yes') or (_Setting.get('DEMO_DATA_LOADED','0') in ('1','true','on','yes'))
        except Exception:
            demo_mode = False
        # MS Graph configured but no valid domains warning
        graph_needs_domains = False
        try:
            from .models import Setting as _Setting, AllowedDomain as _AllowedDomain
            # Check if MS Graph is configured (has client_id, tenant_id, and user_email)
            client_id = _Setting.get('MS_CLIENT_ID', '')
            tenant_id = _Setting.get('MS_TENANT_ID', '')
            user_email = _Setting.get('MS_USER_EMAIL', '')
            graph_configured = bool(client_id and tenant_id and user_email)
            # Check if any valid domains exist
            has_domains = _AllowedDomain.query.count() > 0
            graph_needs_domains = graph_configured and not has_domains
        except Exception:
            graph_needs_domains = False
        return {'active_theme': theme, 'demo_mode': demo_mode, 'graph_needs_domains': graph_needs_domains}

    # Jinja filters
    def cst_datetime(value, fmt='%m-%d-%Y %I:%M %p'):
        if not value:
            return ''
        try:
            dt = value
            if getattr(dt, 'tzinfo', None) is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local = dt.astimezone(ZoneInfo('America/Chicago'))
            return local.strftime(fmt)
        except Exception:
            return str(value)

    app.add_template_filter(cst_datetime, name='cst_datetime')

    # Schedule email polling job (can be disabled for tests by setting DISABLE_SCHEDULER=1)
    if os.getenv("DISABLE_SCHEDULER") != "1":
        from .services.email_poll import poll_ms_graph, email_poll_watchdog
        from .models import ScheduledTicket, Ticket, TicketTask
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _Z
        def _should_run(row: ScheduledTicket, now_local):
            if not row.active:
                return False
            # Parse schedule time HH:MM
            hh, mm = 0, 0
            try:
                if row.schedule_time and len(row.schedule_time) >= 4:
                    parts = row.schedule_time.split(':')
                    hh = int(parts[0] or 0)
                    mm = int(parts[1] or 0)
            except Exception:
                hh, mm = 0, 0
            # Only run if current local hour/minute matches
            if not (now_local.hour == hh and now_local.minute == mm):
                return False
            # Check schedule type
            if row.schedule_type == 'daily':
                return True
            if row.schedule_type == 'weekly':
                return (row.day_of_week is None) or (now_local.weekday() == int(row.day_of_week))
            if row.schedule_type == 'monthly':
                return (row.day_of_month is None) or (now_local.day == int(row.day_of_month))
            return False
        def run_scheduled_tickets():
            # Ensure we are within the Flask application context when running in APScheduler
            with app.app_context():
                try:
                    now_utc = _dt.utcnow().replace(tzinfo=_Z('UTC'))
                    now_local = now_utc.astimezone(_Z('America/Chicago'))
                except Exception:
                    now_local = _dt.now()
                from . import db as _db
                rows = ScheduledTicket.query.filter_by(active=True).all()
                for r in rows:
                    if _should_run(r, now_local):
                        # Avoid duplicate runs in same minute
                        try:
                            last = r.last_run_at
                            if last is not None:
                                # Handle potential tz-aware vs naive stored timestamps defensively
                                ref = now_local.replace(tzinfo=None)
                                if getattr(last, 'tzinfo', None) is not None:
                                    last = last.replace(tzinfo=None)
                                if abs((ref - last).total_seconds()) < 60:
                                    continue
                        except Exception:
                            # If comparison fails for any reason, proceed to create and update last_run_at
                            pass
                        # Create ticket
                        t = Ticket(
                            subject=r.subject,
                            body=r.body,
                            status=r.status or 'open',
                            priority=r.priority or 'medium',
                            assignee_id=r.assignee_id,
                            source='scheduled'
                        )
                        _db.session.add(t)
                        _db.session.flush()
                        if r.tasks_text:
                            for line in [ln.strip() for ln in r.tasks_text.splitlines() if ln.strip()]:
                                _db.session.add(TicketTask(ticket_id=t.id, label=line))
                        r.last_run_at = now_local.replace(tzinfo=None)
                _db.session.commit()
        try:
            scheduler.add_job(func=run_scheduled_tickets, trigger="interval", minutes=1, id="scheduled_tickets", replace_existing=True)
        except Exception:
            pass
        # Prefer DB setting if present, fallback to env, then default
        try:
            from .models import Setting as _Setting
            interval = int(_Setting.get("POLL_INTERVAL_SECONDS", os.getenv("POLL_INTERVAL_SECONDS", "60")))
        except Exception:
            interval = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
        if not scheduler.running:
            scheduler.start()
        # Pass the app so the job can create an app context
        scheduler.add_job(func=lambda: poll_ms_graph(app), trigger="interval", seconds=interval, id="email_poll", replace_existing=True)
        # Watchdog runs every 5 minutes to clear stale locks
        try:
            scheduler.add_job(func=lambda: email_poll_watchdog(app), trigger="interval", minutes=5, id="email_poll_watchdog", replace_existing=True)
        except Exception:
            pass

        # Schedule or remove the auto-backup job based on settings
        try:
            from .models import Setting as _Setting  # type: ignore
            enabled = (_Setting.get('AUTO_BACKUP_ENABLED', '0') or '0') in ('1', 'true', 'on', 'yes')
            time_str = (_Setting.get('AUTO_BACKUP_TIME', '23:00') or '23:00')
            hh, mm = 23, 0
            try:
                parts = time_str.split(':')
                hh = int(parts[0] or 23)
                mm = int(parts[1] or 0)
            except Exception:
                hh, mm = 23, 0
            if enabled:
                scheduler.add_job(func=lambda: run_auto_backup(app), trigger='cron', hour=hh, minute=mm, id='auto_backup', replace_existing=True)
            else:
                try:
                    scheduler.remove_job('auto_backup')
                except Exception:
                    pass
        except Exception:
            # If settings model not ready, skip; admin can enable later
            pass

    return app
