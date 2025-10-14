# HelpfulDjinn - Simple Helpdesk

A Flask-based HelpDesk and lightweight purchasing app using SQLite + SQLAlchemy, Flask-Login, and Bootstrap.

## Features	

- **Tickets**: Email ingestion via Microsoft Graph, dashboard, notes, attachments, processes, tasks, scheduled recurring tickets

- **Assets**: Complete asset lifecycle management with categories, manufacturers, conditions, locations, checkout/checkin, audit trail, CSV import/export

- **Users**: Login, profile (theme + ticket view prefs), admin management of techs	Or activate your venv and run:

- **Projects**: Group related tickets, track project status, toggle between open/all ticket views

- **Documents**: Organize documents by category with URLs

- **Purchasing/Orders**:

	- Planned Items list with Dept/Cost Code, bulk delete, and quick add

	- Vendors, Companies, and Shipping Locations (with per-location tax rate)

	- Create draft POs from selected items; edit items in draft, then finalize

	- PO PDF generation and email on finalize- 

- **Admin**: - Ensure your Azure AD app has the necessary permissions (e.g., Mail.ReadWrite), and admin consent is granted.

	- Configure Microsoft Graph, allowed email domains/deny filters- Email replies to closed tickets automatically reopen them to "in_progress" status.

	- Manage asset picklists (Categories, Manufacturers, Conditions, Locations)

	- Backup/restore database## Assets Management

	- Configure attachment storage location- Complete asset tracking with categories, manufacturers, conditions, and locations managed from Admin panel

	- Version tracking with online latest version check- Checkout/checkin workflow with assignment to users

	- Asset audit log- Audit trail tracking all changes (edits, assignments, status updates)

	- Scheduled ticket management- CSV import/export for bulk operations

- **Theming**: Built-in themes (light, dark, ocean, fallout) with consistent UI across all pages- Asset detail pages show related tickets and full history

- **SQLite by default**: Lightweight "ensure" migrations on startup for zero-config upgrades

## Requirements

- Python 3.10+ (tested with 3.13)## Scheduled Tickets

- Windows, macOS, or Linux- Create recurring tickets (daily, weekly, monthly) from Admin panel

- Configurable schedule time and associated tasks

## Quick start (Windows PowerShell)

pip install -r requirements.txt	

python run.py

Open http://localhost:5000 and log in.

On first run, you’ll see the Setup screen:
- Create your admin account.
- Optional: check “Load demo data” to pre-populate 20 tickets, 20 users, 20 assets, and 20 purchase orders with sensible relationships (assets assigned to users; tickets linked to users/assets; POs that ordered some assets). You can skip this if you plan to restore a backup instead.

## Attachments directory

## Required files (directory/file list)- Incoming email attachments are saved under a configurable base and subfolder. Default base is `instance`, default subfolder is `attachments`.

These are the essential files and folders needed to run the application. Items marked optional are created or populated at runtime.	- Default full path: `instance/attachments/<ticket_id>/<filename>`

	- Alternate base: `static` (then files are under `static/<subdir>/<ticket_id>`)

- Project root- Configure in Admin > Attachments. Settings used: `ATTACHMENTS_BASE` (`instance` or `static`) and `ATTACHMENTS_DIR_REL` (relative folder).

	- requirements.txt- When using the default instance base, files are served via a protected download route, not directly from static.

	- run.py- Existing files are not moved automatically if you change the base or folder.

	- README.md

	- instance/ (optional) – may hold database and backups- On startup, the app ensures required tables/columns exist for tickets, users, processes, purchasing, documents, and assets.

	- helpdesk.db (optional; auto-created on first run if missing)- Admin > Backup/Restore supports SQLite backups and restores; after restore a light ensure revalidates required columns.

- app/

	- __init__.py (Flask app factory; config, DB init, scheduler, blueprints, image serving route)- Application branded as "HelpfulDjinn" (navbar, page titles, login page)

	- models.py (SQLAlchemy models including Asset, AssetCategory, AssetManufacturer, AssetCondition, AssetLocation, ScheduledTicket, etc.)- Custom logo displayed on login page (`HelpfulDjinnMd.png`)

	- forms.py (WTForms for all UI forms)- Favicon configured for all pages (`HelpfulDjinnFav32.png`)

	- version.txt (application version displayed on Admin page)- Images served from `app/images/` via custom route `/images/<filename>`

	- blueprints/

		- admin.py (admin dashboard, settings, picklist management, scheduled tickets, backup/restore)## Theming

		- assets.py (asset management, import/export, checkout/checkin)- Themes: light (default), dark, ocean, fallout.

		- auth.py (login, profile, logout)- Users can pick a theme on their profile; templates and components are standardized for visual consistency.

		- dashboard.py (main dashboard with ticket summary)- Theme applies to all pages including login, dashboard, tickets, assets, orders, and admin sections.

		- documents.py (document management by category)

		- orders.py (purchase orders, planned items, PO workflow)## Required files (directory/file list)

		- projects.py (project tracking, grouped tickets)These are the essential files and folders needed to run the application. Items marked optional are created or populated at runtime.

		- tickets.py (ticket list, detail, notes, processes, tasks, attachments)

		- users.py (contact/user directory)- Project root

	- services/

		- email_poll.py (MS Graph polling + message processing, ticket auto-reopen on reply)	- run.py

		- ms_graph.py (Graph auth helpers, send/receive email)	- .env.example (copy to .env and configure)

		- po_pdf.py (PO PDF generation with ReportLab)	- .env (your configuration) – required at runtime for secrets and admin bootstrap

	- utils/

		- db_migrate.py (ensure/compat migrations for all tables including assets and picklists)	- instance/ (optional) – not used by default SQLite path; may hold alternative configs

		- security.py (password hashing with werkzeug)	- helpdesk.db (optional; auto-created on first run if missing)

	- images/ (logo and favicon assets)

		- HelpfulDjinn.png- app/

		- HelpfulDjinnMd.png (displayed on login page)	- __init__.py (Flask app factory; config, DB init, scheduler, blueprints, image serving route)

		- HelpfulDjinnFav32.png (favicon)	- models.py (SQLAlchemy models including Asset, AssetCategory, AssetManufacturer, AssetCondition, AssetLocation, ScheduledTicket, etc.)

	- templates/

		- base.html (main template with navbar, themes, favicon)	- version.txt (application version displayed on Admin page)

		- admin/ ... (all admin templates including asset_picklist.html)	- blueprints/

		- assets/ ... (index, detail with edit/new forms)		- admin.py (admin dashboard, settings, picklist management, scheduled tickets, backup/restore)

		- auth/ ... (login with logo, profile)		- assets.py (asset management, import/export, checkout/checkin)

		- dashboard/ ...		- auth.py (login, profile, logout)

		- documents/ ...		- dashboard.py (main dashboard with ticket summary)

		- orders/ ...		- documents.py (document management by category)

		- projects/ ...		- orders.py (purchase orders, planned items, PO workflow)

		- tickets/ ...		- projects.py (project tracking, grouped tickets)

		- users/ ...		- tickets.py (ticket list, detail, notes, processes, tasks, attachments)

	- static/		- users.py (contact/user directory)

		- themes/	- services/

			- dark.css		- email_poll.py (MS Graph polling + message processing, ticket auto-reopen on reply)

			- ocean.css		- ms_graph.py (Graph auth helpers, send/receive email)

			- fallout.css		- po_pdf.py (PO PDF generation with ReportLab)

		- attachments/ (optional; runtime storage for email attachments when using static base)	- utils/

		- db_migrate.py (ensure/compat migrations for all tables including assets and picklists)

- scripts/ (optional, developer utilities)		- security.py (password hashing with werkzeug)

	- images/ (logo and favicon assets)


## Troubleshooting
- If a VS Code task points at a non-existent Python path, launch with the system launcher:
	```powershell
	py -3 run.py
	```
## Production notes
- Use a production WSGI server (e.g., Waitress or Gunicorn) and run the email poller job continuously (the built-in APScheduler can run in-process; for scale, consider an external worker).
- Set a strong `FLASK_SECRET_KEY` and a persistent database (e.g., PostgreSQL) for multi-user scenarios.
