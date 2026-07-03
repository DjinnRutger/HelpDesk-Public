"""Central permission registry and enforcement helpers.

Permission model: each Role stores one cumulative access level per module.
Levels: NONE(0) < VIEW(1) < CREATE(2) < EDIT(3) < DELETE(4) — a higher level
implies all lower ones (Edit includes View and Create, etc.).

When adding a NEW feature/module to the app:
  1. Add an entry to MODULES below — it automatically appears in the
     role editor UI (Admin -> Roles & Permissions).
  2. Gate its blueprint with protect_blueprint(bp, '<key>') and mutating
     routes with @require_permission('<key>', CREATE/EDIT/DELETE).
  3. Gate templates and nav links with {% if can('<key>', 'view') %}.
Existing custom roles default to NONE for new modules (fail closed); the
built-in Administrator role bypasses all checks and sees them immediately.
"""
from functools import wraps

from flask import flash, jsonify, redirect, request, url_for
from flask_login import current_user

# Cumulative access levels
NONE, VIEW, CREATE, EDIT, DELETE = 0, 1, 2, 3, 4

LEVEL_NAMES = {
    'none': NONE,
    'view': VIEW,
    'create': CREATE,
    'edit': EDIT,
    'delete': DELETE,
}

LEVEL_CHOICES = [
    (NONE, 'No Access'),
    (VIEW, 'View Only'),
    (CREATE, 'View + Create'),
    (EDIT, 'View / Create / Edit'),
    (DELETE, 'Full Access (Delete)'),
]

# Central module registry. Order drives the role editor UI.
# Dashboard, auth, and profile pages are always accessible and are not modules.
# setup/client_api are bootstrap/machine endpoints and are excluded.
MODULES = [
    {'key': 'tickets',   'label': 'Tickets',          'description': 'Tickets and pipeline'},
    {'key': 'projects',  'label': 'Projects',         'description': 'Project grouping of tickets'},
    {'key': 'documents', 'label': 'Documents',        'description': 'Document library'},
    {'key': 'assets',    'label': 'Assets',           'description': 'Asset / device inventory'},
    {'key': 'orders',    'label': 'Orders',           'description': 'Purchase orders'},
    {'key': 'contacts',  'label': 'Users (Contacts)', 'description': 'Contact / end-user directory'},
    {'key': 'admin',     'label': 'Admin / System',   'description': 'System settings (role and technician management always requires the Administrator role)'},
]

MODULE_KEYS = {m['key'] for m in MODULES}


def get_level(user, module_key):
    """Return the user's access level for a module. 0 for anonymous/inactive."""
    if not user or not getattr(user, 'is_authenticated', False):
        return NONE
    if not getattr(user, 'is_active', False):
        return NONE
    role = getattr(user, 'role_obj', None)
    if role is not None:
        return role.level(module_key)
    # Half-migrated fallback: legacy string role before backfill has run
    return DELETE if getattr(user, 'role', None) == 'admin' else NONE


def has_permission(user, module_key, level):
    return get_level(user, module_key) >= level


def is_administrator(user):
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    role = getattr(user, 'role_obj', None)
    if role is not None:
        return role.builtin_key == 'administrator'
    return getattr(user, 'role', None) == 'admin'


def _wants_json():
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    accept = request.accept_mimetypes
    return accept['application/json'] >= accept['text/html'] and accept['application/json'] > 0


def _deny(message='You do not have permission to do that.'):
    if not current_user.is_authenticated:
        return redirect(url_for('auth.login'))
    if _wants_json():
        return jsonify({'success': False, 'error': message}), 403
    flash(message, 'danger')
    return redirect(url_for('dashboard.index'))


def require_permission(module_key, level):
    """Route decorator enforcing a minimum access level on a module."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not has_permission(current_user, module_key, level):
                return _deny()
            return view(*args, **kwargs)
        return wrapped
    return decorator


def require_administrator(view):
    """Route decorator restricting a view to the built-in Administrator role."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_administrator(current_user):
            return _deny('Administrator access required.')
        return view(*args, **kwargs)
    return wrapped


def protect_blueprint(bp, module_key):
    """Require login + VIEW level on every route of a blueprint.

    Per-route @require_permission decorators add CREATE/EDIT/DELETE on top.
    """
    @bp.before_request
    def _enforce_view():
        if not current_user.is_authenticated:
            return redirect(url_for('auth.login'))
        if not has_permission(current_user, module_key, VIEW):
            return _deny()
        return None


def register_permission_helpers(app):
    """Expose can()/perm helpers to Jinja templates."""
    @app.context_processor
    def inject_permissions():
        def can(module_key, level='view'):
            lvl = LEVEL_NAMES.get(level, level) if isinstance(level, str) else level
            return has_permission(current_user, module_key, lvl)
        return {
            'can': can,
            'perm_modules': MODULES,
            'perm_level_choices': LEVEL_CHOICES,
        }
