# HelpfulDjinn HelpDesk

Flask helpdesk application ("HelpfulDjinn") with tickets, assets, purchase orders, projects, documents, and a contact directory. Companion desktop client "DjinnWish" submits tickets and machine check-ins through the token-authenticated client API.

## Architecture

- **Framework:** Flask + Flask-SQLAlchemy + Flask-Login + Flask-WTF (CSRF), Jinja2 templates with Bootstrap 5.
- **App factory:** `Source/app/__init__.py` (`create_app()`) — registers blueprints, runs DB migrations/seeders, sets up context processors and the APScheduler wiring. There is NO `config.py`; all config is inline in `create_app()` (env vars + the DB-backed `Setting` table).
- **Models:** `Source/app/models.py` (single file, all tables). **Forms:** `Source/app/forms.py` (Flask-WTF classes; select choices are populated in each form's `__init__`).
- **Blueprints:** `Source/app/blueprints/` — `tickets` (/tickets), `projects` (/projects), `documents` (/documents), `assets` (/assets), `orders` (/orders), `users` (/users — contact directory), `admin` (/admin), `dashboard` (/), `auth`, `setup`, `client_api` (/api — token-authenticated machine intake; NOT session-based).
- **Admin blueprint is a package** (`Source/app/blueprints/admin/`): `__init__.py` holds `admin_bp`, shared helpers (`admin_required`, `_bump_schedule_version`), `ADMINISTRATOR_ONLY_ENDPOINTS` + the `before_request` guard, and imports the route submodules (home, logs, scheduled_tickets, purchasing, ticket_config, processes, documents_admin, assets_admin, users_roles, integrations, email_admin, backup, reports_admin, ai_admin) at the bottom. New admin routes go in the matching submodule; endpoint names stay `admin.<function_name>`. `cleanup_old_email_logs` and `run_asset_spot_check` must stay re-exported from the package (`app/__init__.py` lazy-imports them for scheduler jobs).
- **Templates:** `Source/app/templates/<blueprint>/`.
- **Database:** SQLite at `Source/instance/helpdesk.db` (override with `DATABASE_URL`). Schema via `db.create_all()` + **manual migrations** — there is NO Alembic.
- **SECRET_KEY:** resolved by `utils/security.load_or_create_secret_key` — `FLASK_SECRET_KEY` env var wins, else a generated key persisted at `Source/instance/secret_key` (gitignored, shared by web + scheduler). There is no constant fallback. The Fernet key encrypting sensitive Settings (MS Graph secret, AD password) derives from it, so the key must travel with DB backups: the Admin backup download is a zip bundling `helpdesk.db` + `secret_key`, restore accepts that zip (rotating the key file, old one backed up as `secret_key.pre-restore-*`) or a legacy bare `.db`, and `run_auto_backup` mirrors `secret_key` into the backup directory. A startup migration re-encrypts values still stored under the legacy `'dev'` key.
- **Scheduler:** runs as a separate process (`HELPFULDJINN_ROLE=scheduler`, `scheduler_run.py`) so gunicorn web workers don't duplicate jobs. Web workers bump `SCHEDULE_VERSION` (Setting) to signal job rebuilds. In single-process dev (no `HELPFULDJINN_ROLE`), background work runs as one-shot daemon threads instead (`mailer` drain, `ai.kick_*`).
- **Outbound email:** web routes must NOT call `ms_graph.send_mail` directly (it blocks on the Graph API). Call `services/mailer.enqueue_mail` (same signature) — rows land in `EmailOutbox` and the scheduler's `email_outbox` job drains them every 20s with retry/backoff (5 attempts → `dead`; visible under Admin → Email Logs → Queue). Direct `send_mail` is fine inside scheduler-process services.
- **HTML sanitization:** all user-supplied HTML must go through `Source/app/utils/html_sanitize.py` before storage — `sanitize_rich_text` (notes), `sanitize_document_html` (documents), `sanitize_ticket_body` (web ticket bodies; passes plain text through untouched), `sanitize_email_html` (inbound email, wider allowlist + CSS sanitizer). Never render user HTML with `|safe` unless it was sanitized on write.

## Feature map (what lives where)

- **tickets** (`/tickets`, module `tickets`): ticket list with status/search/tag filters (auto-reloads every 30s) + Kanban `pipeline`; ticket detail is the largest page — public replies & private notes (contenteditable editor), status/priority, assignee + co-assignee, checklist tasks, process instances, project move, attachments; snooze/unsnooze; watchers (`notify_ticket_watchers`); note forward/edit; manager **approval requests** for order items (email-driven, replies parsed by the poller); asset assignment; **merge-to-ticket** (see Gotchas); tags; AI endpoints (similar tickets, suggested reply generate/dismiss).
- **orders** (`/orders`, module `orders`): order line items (planned → ordered → backordered → received/canceled), PO creation from items, PO detail (meta, notes, finalize assigns `po_number` + emails), PDF download (`services/po_pdf.py`, ReportLab), receiving flow that can create one or many Assets from a received item.
- **assets** (`/assets`, module `assets`): device inventory CRUD, checkout/checkin to Contacts (+ bulk checkin per contact), mark-audited (writes `AssetAudit`), CSV import/export, soft delete (`deleted_flag`) + purge, tags. Category/manufacturer/condition/location are **denormalized strings** validated against picklist tables, not FKs.
- **documents** (`/documents`, module `documents`): hierarchical categories, rich-HTML documents, per-user favorites, search (page + JSON), `ai_excluded` flag keeps a doc out of AI embedding/suggestions.
- **projects** (`/projects`, module `projects`): group tickets into projects; project tickets are hidden from normal ticket lists/counts; drag-reorder of tickets within a project.
- **users** (`/users`, module **`contacts`**): contact/end-user directory; manager links (used by approvals); archive; AD password-expiry status fields; DjinnWish machine check-in info (last computer/IP/version); per-contact asset log.
- **dashboard** (`/`): KPI page + JSON chart endpoints (top-tags, ticket-sources, tickets-per-week). Login-only, always accessible — not a permission module.
- **auth / setup**: `/login`, `/logout`, `/profile` (theme + password); `/setup` first-run wizard (create first admin or restore a backup) driven by `needs_setup()`.
- **client_api** (`/api`, CSRF-exempt, no session): `POST /api/tickets` — DjinnWish intake, multipart `payload` JSON + `screenshot` PNG, creates a ticket (`source='DjinnWish'`, archives raw systemInfo into `Ticket.system_info_json`), returns `HD-<id>`; `POST /api/checkin` — stamps `Contact.last_checkin_*`. Auth scheme from `CLIENTAPI_AUTH_SCHEME` (None / Bearer / ApiKeyHeader); tokens verified via `ApiToken.verify()` (only SHA-256 hash stored; plaintext shown once at Admin → Client API). Must keep working without a session user.

**Admin submodules** (`Source/app/blueprints/admin/`):

| Submodule | Provides |
|---|---|
| `home` | Admin landing page: settings summaries, version + GitHub update check, attachments dir config, demo-mode toggle |
| `logs` | App log view/clear/download; email poll logs; outbox retry; log retention settings |
| `scheduled_tickets` | Recurring ticket templates (daily/weekly/monthly + time, task list) |
| `purchasing` | Vendors, Companies, Shipping Locations (reference data for POs) |
| `ticket_config` | Configurable ticket statuses (label/color/closed-flag/order) and the tag taxonomy |
| `processes` | Process (checklist) templates + items, applied to tickets |
| `documents_admin` | Document category create/rename/delete/reorder |
| `assets_admin` | Asset audit log, picklists, spot-check schedule + run-now (exports `run_asset_spot_check`) |
| `users_roles` | Technician + Role management — these ARE the `ADMINISTRATOR_ONLY_ENDPOINTS` |
| `integrations` | MS Graph config, Client API tokens (generate/revoke), FTP import, AD bind + password-check settings/tests |
| `email_admin` | Allowed domains, deny filters, email templates, password-expiry notification tiers |
| `backup` | **Data section**: `/admin/data` (health: DB size/WAL/stray snapshots, attachments on-disk vs DB, backup dir status, record counts), `/admin/data/backup` (manual backup zip, attachments zip, restore, auto-backup settings), `/admin/data/cleanup-strays`. Legacy `/admin/backup*` routes redirect here |
| `reports_admin` | Scheduled "Executive Report" emails (sections, charts, recipients, preview/run-now) |
| `ai_admin` | Ollama settings (host/port/models), connection test, force reindex |

## Data model overview (`Source/app/models.py`)

- **Config/auth:** `Setting` (KV store — see Settings section), `Role` (per-module levels in `permissions_json`, fail-closed for unknown keys), `User` (technicians; `theme`, `tickets_view_pref`, `signature`; legacy `role` string — never check it), `ApiToken`.
- **Tickets:** `Ticket` (see Gotchas for `external_id`, merge, snooze), `TicketNote` (`is_private`; author NULL + public = inbound email), `TicketAttachment`, `TicketTask` (checklist), `TicketWatcher`, `TicketStatus` (DB-configurable statuses; `is_closed` flag; `ensure_defaults` seeds new/open/in_progress/closed), `ScheduledTicket`, `Tag` + `ticket_tags`/`asset_tags` M2M tables, `ProcessTemplate(Item)` → instantiated as `TicketProcess(Item)`.
- **AI:** `TicketEmbedding` / `DocumentEmbedding` (packed little-endian float32 vectors, L2-normalized so cosine = dot product; `content_hash` for change detection), `TicketAISuggestion` (status: pending/generating/ready/failed/dismissed; `sources_json` lists docs used).
- **People:** `Contact` (end users; self-ref `manager_id` for approvals; `archived`; AD fields — `password_expires_days` special values: `None` unchecked, `-1` never expires, `-999` not in AD, other negative = expired N days ago; DjinnWish `last_checkin_*` fields), `ApprovalRequest`, `Project`.
- **Purchasing:** `Vendor`, `Company`, `ShippingLocation` (holds `tax_rate`), `PurchaseOrder` (snapshots vendor/company/shipping text at creation; computed totals properties; `po_number` assigned at finalize), `OrderItem`, `PoNote`.
- **Documents:** `DocumentCategory` (hierarchical `parent_id`), `Document` (`ai_excluded`), `DocumentFavorite`.
- **Assets:** `Asset` (soft delete via `deleted_flag`; `checkout()`/`checkin()`; warranty/EOL/depreciation; optional links to source PO/order item), `AssetAudit` (field-level change log), picklists `AssetCategory/Manufacturer/Condition/Location`.
- **Email:** `AllowedDomain` (new-ticket intake allowlist), `DenyFilter` (subject denylist), `EmailCheck(Entry)` (poll logs, pruned after 7 days), `OutgoingEmail` (sent audit), `EmailOutbox` (queue: pending/sending/sent/failed/dead), `EmailTemplate` + `PasswordExpiryNotification`.
- **Reports:** `Report` / `ReportRun` (scheduled executive reports).

## Services (`Source/app/services/`)

- **`ai.py`** — all AI is local **Ollama** (default `127.0.0.1:11434`; chat `qwen2.5:14b`, embeddings `nomic-embed-text`). Two features: embedding index for similar-ticket/relevant-doc search (`run_ai_index`, scheduler) and AI-suggested replies (`run_ai_auto_suggest` scheduler job + web-triggered `generate_suggestion`; raw-SQL atomic claim prevents double generation; rows stuck `generating` >30 min are recovered). `find_similar`/`find_relevant_documents` (min score 0.45, top 3) work offline from stored vectors; numpy optional. The suggestion system prompt forbids leaking private notes/credentials — keep that intact.
- **`ms_graph.py`** — low-level Microsoft Graph client (MSAL client-credentials): read inbox, download attachments, `send_mail` (HTML + inline `cid:` images; logs to `OutgoingEmail`). Scheduler-side use only; web routes go through the mailer.
- **`mailer.py`** — `enqueue_mail(...)` (drop-in for `send_mail`, inserts `EmailOutbox` row) + `drain_outbox` (claims atomically, exponential backoff 1m→6h, `dead` after 5 attempts).
- **`email_poll.py`** — inbound intake pipeline (`poll_ms_graph`): deny-filter → approval-request replies (approve/deny keywords) → ticket replies ("Ticket #N" in subject: append note, reopen closed → in_progress, save attachments, refresh AI suggestion, notify assignees) → new tickets (domain allowlist, dedupe by `external_id`, upsert Contact). Also FTP (HDWish) folder import and `email_poll_watchdog` (clears the DB-settings poll lock if stale).
- **`report_generator.py`** — `run_due_reports` fires scheduled executive report emails; sections configurable per-report (data/chart/both); pie charts via Pillow PNG (inline cid) and the `svg_pie` Jinja global; template `templates/emails/report_executive.html`.
- **`ad_password_check.py`** — daily ldap3 job: reads domain `maxPwdAge` + per-contact `pwdLastSet`/`userAccountControl`, updates `Contact` password fields, sends tiered `PasswordExpiryNotification` emails (rendered from `EmailTemplate`), files a summary ticket.
- **`snooze_wakeup.py`** — wakes tickets whose `snoozed_until` passed: system note, clear snooze, email assignee.
- **`po_pdf.py`** — ReportLab PO PDF renderer (`render_po_pdf(po)` → bytes).

## Scheduler jobs (scheduler process only; TZ America/Chicago)

Static (registered in `create_app`): `email_poll` (every `POLL_INTERVAL_SECONDS`, default 60s), `email_poll_watchdog` (5m), `snooze_wakeup` (1m), `email_outbox` (20s), `scheduled_tickets` (1m), `scheduled_reports` (1m), `schedule_version_watch` (30s — polls `SCHEDULE_VERSION` and re-applies dynamic jobs on change).

Dynamic (`_apply_dynamic_jobs`, driven by Settings): `ad_password_check` (daily `AD_PWD_CHECK_TIME`), `auto_backup` (daily `AUTO_BACKUP_TIME`; retention prune; files a `[SYSTEM]` ticket on failure), `email_log_cleanup` (03:00), `asset_spot_check` (weekly/monthly), `ai_index` (every `AI_INDEX_INTERVAL_MINUTES`), `ai_auto_suggest` (2m).

Web workers cannot touch the scheduler — after changing schedule-related settings, call `_bump_schedule_version()` (admin package) so the scheduler rebuilds jobs within ~30s.

## Settings & configuration

- `Setting.get(key, default)` / `Setting.set(key, value)` — DB-backed KV store, the app's primary config surface (Admin UI writes it). Keys in `SENSITIVE_SETTING_KEYS` (`MS_CLIENT_SECRET`, `AD_BIND_PASSWORD` — defined in `utils/security.py`) are transparently Fernet-encrypted with an `ENC:` prefix; add any new secret-valued key to that set.
- Boolean convention: values stored as strings, read via `(Setting.get('X', '0') or '0') in ('1', 'true', 'on', 'yes')`.
- Key families: `MS_*` (Graph email), `POLL_INTERVAL_SECONDS`, `FTP_*` (HDWish import), `AI_*` (Ollama), `AD_*` (Active Directory), `CLIENTAPI_*` (machine API), `AUTO_BACKUP_*`, `ASSET_SPOT_CHECK_*`, `ATTACHMENTS_BASE`/`ATTACHMENTS_DIR_REL`, `EMAIL_POLL_*` (runtime poll lock/metrics), `SCHEDULE_VERSION`.
- Env vars: `FLASK_SECRET_KEY`, `DATABASE_URL`, `ADMIN_EMAIL`/`ADMIN_PASSWORD` (bootstrap admin), `HELPFULDJINN_ROLE` (`web`/`scheduler`), `DISABLE_SCHEDULER`. `.env` is loaded via `load_dotenv()` and gitignored.
- Attachments are stored at `<instance or static>/<ATTACHMENTS_DIR_REL>/<ticket_id>/<filename>` — default `Source/instance/attachments/<ticket_id>/` — and served by a tickets route, not Flask static. App log: `Source/instance/logs/helpdesk.log` (rotating).

## Frontend conventions

- Single layout `templates/base.html`: blocks `title` / `head` / `content` / `extra_modals` / `scripts`; nav links wrapped in `can('<module>', 'view')`; flash categories map straight to Bootstrap alert classes (`success`/`danger`/`warning`/`info`); a global delegated handler makes `tr.clickable-row[data-href]` rows navigate; `<meta name="csrf-token">` is emitted for JS.
- **Theming is server-side per user**: `User.theme` (light/dark/ocean/fallout, chosen on /profile) → `active_theme` context var → `data-bs-theme` attribute + optional `static/themes/<theme>.css` overriding Bootstrap `--bs-*` variables. There is no client-side toggle. New UI must look right in dark and fallout themes (use `--bs-*` vars, avoid hardcoded colors).
- Libraries come from **jsDelivr CDN**: Bootstrap 5.3.3 + Icons 1.11.3 (global), Chart.js 4.4.7 (dashboard only), SortableJS 1.15.3 (admin reordering). There is no local JS/CSS bundle — page-specific JS/CSS lives inline in each template.
- Rich text is a **custom `contenteditable` div + `execCommand` toolbar** (tickets, PO notes, documents) — no third-party editor, no select2/typeahead libs (typeaheads are hand-rolled `fetch` + dropdown). HTML is sanitized server-side on save.
- Forms: Flask-WTF with `{{ form.hidden_tag() }}`, or plain HTML forms with `<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">`. CSRFProtect is app-wide.
- AJAX: send `X-Requested-With: XMLHttpRequest` on fragment/JSON GETs (the server — e.g. `permissions._deny` — uses it to return JSON 403 instead of a redirect); mutating JSON POSTs send an `X-CSRFToken` header (read from the meta tag). Form-based AJAX posts `FormData` (CSRF already inside) and expects `204` + a JS refresh.
- Pagination: either SQLAlchemy `query.paginate(...)` (orders, users, logs) or manual offset/limit with a whitelisted `per_page` (assets). Filters are GET forms whose state round-trips through the query string; pagination links must preserve those args.
- Jinja helpers available everywhere: `can()`, `cst_datetime`, `status_color`, `status_label`, `from_json`, `svg_pie`, `active_theme`, `demo_mode`.

## Entry points, build & deployment

- Dev web: `python Source/run.py` (0.0.0.0:5000). Scheduler: `python Source/scheduler_run.py`. Requirements: `Source/requirements.txt` (msal, apscheduler, reportlab, Pillow, bleach+tinycss2, cryptography, ldap3, numpy; no gunicorn/pytest/alembic).
- **Linux prod**: `Source/restart.sh` rsyncs to `/opt/helpfuldjinn` and restarts systemd units `helpfuldjinn` (web) + `helpfuldjinn-scheduler`. No Docker.
- **Windows single-exe**: PyInstaller onefile via `Source/build.py` + `Source/HelpfulDjinn.spec`. When frozen, DB/attachments/backups/logs live in folders **next to the exe** (handled in `create_app()`); templates/static come from `sys._MEIPASS`.
- **No automated tests.** Verify changes by running the app (`Source/scripts/smoke.py` just builds the app and lists tables). Version strings: root `version.txt` and bundled `Source/app/version.txt` (admin home compares against GitHub latest).

## Permission system (MANDATORY for all new features)

Roles and per-module permissions live in `Source/app/permissions.py` (registry + helpers) and the `Role` model in `models.py`. Levels are **cumulative**: `NONE(0) < VIEW(1) < CREATE(2) < EDIT(3) < DELETE(4)` — each level includes everything below it.

Registered modules: `tickets`, `projects`, `documents`, `assets`, `orders`, `contacts` ("Users (Contacts)"), `ai` ("AI Assistant"), `admin` ("Admin / System").

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
6. **Role & technician management stays Administrator-only.** Those endpoints are listed in `ADMINISTRATOR_ONLY_ENDPOINTS` in `Source/app/blueprints/admin/__init__.py`; if you add management routes that could grant privileges, add them to that set.
7. `dashboard`, `auth`, and profile pages are always accessible (not modules). `setup` and `client_api` are excluded from permission gating (no session user).

Lockout guards (keep intact when touching the admin package): the last active Administrator cannot be demoted, deactivated, or deleted; users cannot delete their own account; system roles and roles with assigned users cannot be deleted.

## Database migration conventions

- Add an idempotent `ensure_<thing>(engine)` function to `Source/app/utils/db_migrate.py` (use `PRAGMA table_info` / `sqlite_master` checks + raw `ALTER TABLE`/`CREATE TABLE`).
- Call it from `create_app()` in `Source/app/__init__.py` alongside the other `ensure_*` calls.
- Data seeding uses idempotent `seed_*(db)` functions (e.g. `seed_builtin_roles`) called later in `create_app()` inside the app context.
- The restore flow (`admin/backup.py`) re-runs a subset of `ensure_*` on the uploaded DB — if your migration is needed for restored backups, add it there too.

## Gotchas

- **`Ticket.external_id` decides the sanitizer**: NULL = web-form ticket (`sanitize_ticket_body`, plain text passes through unbleached and renders escaped); set = email/FTP intake (`sanitize_email_html`, wider allowlist). Don't break this split.
- **Merged tickets**: `Ticket.merged_into_id`/`merged_at` mark a ticket merged into a parent (one level; children are *released*, never cascaded, on delete/unmerge). Every ticket list/count/aggregate query must exclude them — add `Ticket.merged_into_id.is_(None)` alongside the existing `project_id` exclusion (dashboard, reports, snooze wakeup, AI auto-suggest all do this).
- **Raw-SQL atomic claims**: `EmailOutbox` draining and `TicketAISuggestion` generation both claim rows with `UPDATE ... WHERE status IN (...)` so scheduler + dev threads never double-process. Preserve this pattern when editing those paths.
- **Legacy shims**: `User.role` string (use `can()`), `Ticket.requester` (use `requester_name`/`requester_email`), `TicketStatus.is_status_closed` falls back to the literal `'closed'`, `TicketNote.is_private` NULL treated as private.
- `Ticket.bump_new_to_open()` auto-transitions `new` → `open` when a tech acts on a ticket; call it in new tech-action routes.
- Ticket statuses are **DB rows, not an enum** — never hardcode a status string except via `TicketStatus` helpers.
- AI similarity search works with Ollama **down** (stored vectors); only indexing/generation needs the server. numpy is optional (pure-Python fallback).
- Backups are useless without `instance/secret_key` (encrypted settings) — any new backup/restore path must bundle it.
- Correlated subqueries need `.scalar_subquery()` (SQLAlchemy 2.x) — `.subquery()` in a scalar context emits warnings.

## Running / verifying

- Dev run: `python Source/run.py` (serves on port 5000). Requirements: `Source/requirements.txt`.
- First run with an empty DB redirects to `/setup` to create the first admin user (gets the Administrator role).
- Bootstrap admin can also come from `ADMIN_EMAIL`/`ADMIN_PASSWORD` env vars.
- Scheduler process: `python Source/scheduler_run.py` with `HELPFULDJINN_ROLE=scheduler`.
- The machine client API (`/api/...`) authenticates with `ApiToken` bearer tokens, configured at Admin → Client API — it must keep working without a session user.

## Maintaining this file (do this with every change)

This file is loaded into every AI session — it is the substitute for re-exploring the codebase, so keeping it accurate is part of every change, not an afterthought.

- **New feature or module** → update the **Feature map** (and Architecture if a new blueprint/admin submodule was added), add new models to the **Data model overview**, new setting keys to **Settings & configuration**, new background jobs to **Scheduler jobs**, and follow the Permission-system checklist.
- **New service, library, or UI pattern** → add it to **Services** or **Frontend conventions**.
- **New invariant or trap discovered while debugging** → add a line to **Gotchas**.
- **Removed/renamed code** → delete or fix the stale reference here in the same commit.
- Keep entries **feature-level**, not route-by-route; no line numbers or file sizes (they go stale). Accuracy beats completeness — a wrong line here is worse than a missing one.
