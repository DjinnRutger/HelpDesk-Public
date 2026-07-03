# HelpfulDjinn HelpDesk

Flask helpdesk application ("HelpfulDjinn") with tickets, assets, purchase orders, projects, documents, and a contact directory.

## Architecture

- **Framework:** Flask + Flask-SQLAlchemy + Flask-Login + Flask-WTF (CSRF), Jinja2 templates with Bootstrap 5.
- **App factory:** `Source/app/__init__.py` (`create_app()`) — registers blueprints, runs DB migrations/seeders, sets up context processors and the APScheduler wiring.
- **Models:** `Source/app/models.py` (single file, all tables).
- **Blueprints:** `Source/app/blueprints/` — `tickets` (/tickets), `projects` (/projects), `documents` (/documents), `assets` (/assets), `orders` (/orders), `users` (/users — contact directory), `admin` (/admin), `dashboard` (/), `auth`, `setup`, `client_api` (/api — token-authenticated machine intake; NOT session-based).
- **Templates:** `Source/app/templates/<blueprint>/`.
- **Database:** SQLite at `Source/instance/helpdesk.db` (override with `DATABASE_URL`). Schema via `db.create_all()` + **manual migrations** — there is NO Alembic.
- **Scheduler:** runs as a separate process (`HELPFULDJINN_ROLE=scheduler`, `scheduler_run.py`) so gunicorn web workers don't duplicate jobs. Web workers bump `SCHEDULE_VERSION` (Setting) to signal job rebuilds.

## Permission system (MANDATORY for all new features)

Roles and per-module permissions live in `Source/app/permissions.py` (registry + helpers) and the `Role` model in `models.py`. Levels are **cumulative**: `NONE(0) < VIEW(1) < CREATE(2) < EDIT(3) < DELETE(4)` — each level includes everything below it.

Built-in roles (seeded at startup, undeletable):
- **Administrator** (`builtin_key='administrator'`) — bypasses all checks, always full access. Not editable.
- **Technician** (`builtin_key='technician'`) — default role; permissions editable in the UI.

Custom roles are managed at **Admin → Roles & Permissions** (`/admin/roles`).

### When adding ANY new feature or module, you MUST:

1. **Register it** in `MODULES` in `Source/app/permissions.py` (key, label, description). It then automatically appears in the role editor UI; existing custom roles default to No Access (fail closed), Administrators see it immediately.
2. **Gate the blueprint**: call `protect_blueprint(<bp>, '<module_key>')` at the bottom of the blueprint file (enforces login + View on every route).
3. **Gate mutating routes** with `@require_permission('<module_key>', CREATE|EDIT|DELETE)` placed under `@login_required`. Mapping convention: create buttons/forms → CREATE; modifying existing records (including imports, check-in/out, tag changes) → EDIT; destructive/moderation actions (delete, purge) → DELETE.
4. **Gate templates and nav** with the `can()` helper: `{% if can('<module_key>', 'view'|'create'|'edit'|'delete') %}`. Add the nav link in `base.html` wrapped in `can('<key>', 'view')`.
5. **Never check `current_user.role` strings.** The `user.role` column ('admin'/'tech') is legacy/derived — kept in sync by `User.set_role()` for backward compatibility only. Use `can()` / `current_user.can(key, LEVEL)` / `current_user.is_administrator` instead.
6. **Role & technician management stays Administrator-only.** Those endpoints are listed in `ADMINISTRATOR_ONLY_ENDPOINTS` in `Source/app/blueprints/admin.py`; if you add management routes that could grant privileges, add them to that set.
7. `dashboard`, `auth`, and profile pages are always accessible (not modules). `setup` and `client_api` are excluded from permission gating (no session user).

Lockout guards (keep intact when touching admin.py): the last active Administrator cannot be demoted, deactivated, or deleted; users cannot delete their own account; system roles and roles with assigned users cannot be deleted.

## Database migration conventions

- Add an idempotent `ensure_<thing>(engine)` function to `Source/app/utils/db_migrate.py` (use `PRAGMA table_info` / `sqlite_master` checks + raw `ALTER TABLE`/`CREATE TABLE`).
- Call it from `create_app()` in `Source/app/__init__.py` alongside the other `ensure_*` calls.
- Data seeding uses idempotent `seed_*(db)` functions (e.g. `seed_builtin_roles`) called later in `create_app()` inside the app context.

## Running / verifying

- Dev run: `python Source/run.py` (serves on port 5000). Requirements: `Source/requirements.txt`.
- First run with an empty DB redirects to `/setup` to create the first admin user (gets the Administrator role).
- Bootstrap admin can also come from `ADMIN_EMAIL`/`ADMIN_PASSWORD` env vars.
- Scheduler process: `python Source/scheduler_run.py` with `HELPFULDJINN_ROLE=scheduler`.
- The machine client API (`/api/...`) authenticates with `ApiToken` bearer tokens, configured at Admin → Client API — it must keep working without a session user.
