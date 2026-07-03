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


admin_bp = Blueprint('admin', __name__, url_prefix='/admin')


def _bump_schedule_version():
    """Signal the scheduler process to re-read settings and rebuild dynamic jobs.

    Web workers don't run the scheduler (it's a separate systemd service), so
    they can't add/remove jobs in-process. Instead they bump SCHEDULE_VERSION;
    the scheduler polls it every 30s and reapplies dynamic jobs on change.
    """
    try:
        Setting.set('SCHEDULE_VERSION', str(int(datetime.utcnow().timestamp())))
    except Exception as e:
        current_app.logger.error(f'Failed to bump SCHEDULE_VERSION: {e}')


def admin_required():
    # Settings-changing routes need Edit on the Admin/System module
    return has_permission(current_user, 'admin', EDIT)


# Role and technician management is reserved for the built-in Administrator
# role even when a custom role has Admin/System access (lockout / privilege
# escalation prevention).
ADMINISTRATOR_ONLY_ENDPOINTS = frozenset({
    'admin.tech_new', 'admin.tech_edit', 'admin.tech_delete',
    'admin.roles', 'admin.role_new', 'admin.role_edit', 'admin.role_delete',
})


@admin_bp.before_request
def restrict_to_admin():
    # Not logged in -> login; no Admin/System access -> dashboard
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    if not has_permission(current_user, 'admin', VIEW):
        return redirect(url_for('dashboard.index'))
    if request.endpoint in ADMINISTRATOR_ONLY_ENDPOINTS and not is_administrator(current_user):
        flash('Administrator access required.', 'danger')
        return redirect(url_for('admin.index'))


# Route modules register themselves on admin_bp at import time.
# Keep these imports at the bottom (submodules import names defined above).
from . import (  # noqa: E402,F401
    home,
    logs,
    scheduled_tickets,
    purchasing,
    ticket_config,
    processes,
    documents_admin,
    assets_admin,
    users_roles,
    integrations,
    email_admin,
    backup,
    reports_admin,
)

# Re-exports used by app/__init__.py scheduler wiring (_apply_dynamic_jobs)
from .logs import cleanup_old_email_logs  # noqa: E402,F401
from .assets_admin import run_asset_spot_check  # noqa: E402,F401
