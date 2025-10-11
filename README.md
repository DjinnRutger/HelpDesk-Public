# HelpfulDjinn# HelpfulD- **Purchasing/Orders**:

	- Planned Items list with Dept/Cost Code, bulk d## Microsoft Graph

A Flask-based HelpDesk and lightweight purchasing app using SQLite + SQLAlchemy, Flask-Login, and Bootstrap. It ingests emails from Microsoft Graph to create tickets and includes asset management, purchase order workflows, project tracking, and scheduled tickets.- Configure in Admin > Microsoft Graph. The poller runs every `POLL_INTERVAL_SECONDS` ## Troubleshooting

- **Python path issues**: If a VS Code task points at a non-existent Python path, launch with the system launcher:

## Features	```powershell

- **Tickets**: Email ingestion via Microsoft Graph, dashboard, notes, attachments, processes, tasks, scheduled recurring tickets	py -3 run.py

- **Assets**: Complete asset lifecycle management with categories, manufacturers, conditions, locations, checkout/checkin, audit trail, CSV import/export	```

- **Users**: Login, profile (theme + ticket view prefs), admin management of techs	Or activate your venv and run:

- **Projects**: Group related tickets, track project status, toggle between open/all ticket views	```powershell

- **Documents**: Organize documents by category with URLs	.\.venv\Scripts\Activate.ps1

- **Purchasing/Orders**:	python run.py

	- Planned Items list with Dept/Cost Code, bulk delete, and quick add	```

	- Vendors, Companies, and Shipping Locations (with per-location tax rate)- **Login problems**: Confirm `ADMIN_EMAIL`/`ADMIN_PASSWORD` are set in `.env` and restart; watch console for DB ensure messages.

	- Create draft POs from selected items; edit items in draft, then finalize- **Email ingestion issues**: Verify Microsoft Graph credentials in Admin, try "Test" and "Check Now", and inspect server logs.

	- PO totals include Subtotal + Tax (from selected Ship To) + Shipping cost; Grand Total in UI/PDF- **Missing images/favicon**: Ensure `app/images/` folder exists with required image files; the `/images/<filename>` route serves them.

	- PO list pagination (5 default, toggle to 20), with search + pagination preserved- **Asset tag uniqueness errors**: The system validates unique asset tags on creation; check existing assets before importing.

	- PO PDF generation and email on finalize- **Database errors after upgrade**: The app automatically runs lightweight migrations on startup; check console for ensure messages. Use Admin > Backup before major changes.). You can "Test" the connection and "Check Now".

- **Admin**: - Ensure your Azure AD app has the necessary permissions (e.g., Mail.ReadWrite), and admin consent is granted.

	- Configure Microsoft Graph, allowed email domains/deny filters- Email replies to closed tickets automatically reopen them to "in_progress" status.

	- Manage asset picklists (Categories, Manufacturers, Conditions, Locations)

	- Backup/restore database## Assets Management

	- Configure attachment storage location- Complete asset tracking with categories, manufacturers, conditions, and locations managed from Admin panel

	- Version tracking with online latest version check- Checkout/checkin workflow with assignment to users

	- Asset audit log- Audit trail tracking all changes (edits, assignments, status updates)

	- Scheduled ticket management- CSV import/export for bulk operations

- **Theming**: Built-in themes (light, dark, ocean, fallout) with consistent UI across all pages- Asset detail pages show related tickets and full history

- **SQLite by default**: Lightweight "ensure" migrations on startup for zero-config upgrades- Create new assets with validated unique asset tags

- Search and filter by availability, status, category, etc.

## Requirements

- Python 3.10+ (tested with 3.13)## Scheduled Tickets

- Windows, macOS, or Linux- Create recurring tickets (daily, weekly, monthly) from Admin panel

- Configurable schedule time and associated tasks

## Quick start (Windows PowerShell)- Automatic ticket creation based on schedule

- Link to process templates for consistent workflowste, and quick add

1) Create virtual environment and install dependencies	- Vendors, Companies, and Shipping Locations (with per-location tax rate)

```powershell	- Create draft POs from selected items; edit items in draft, then finalize

py -3 -m venv .venv; .\.venv\Scripts\Activate.ps1	- PO totals include Subtotal + Tax (from selected Ship To) + Shipping cost; Grand Total in UI/PDF

pip install -r requirements.txt	- PO list pagination (5 default, toggle to 20), with search + pagination preserved

```	- PO PDF generation and email on finalize

- **Admin**: 

2) Configure environment	- Configure Microsoft Graph, allowed email domains/deny filters

```powershell	- Manage asset picklists (Categories, Manufacturers, Conditions, Locations)

Copy-Item .env.example .env	- Backup/restore database

# Edit .env and set at minimum:	- Configure attachment storage location

#   FLASK_SECRET_KEY=your-random-secret	- Version tracking with online latest version check

#   ADMIN_EMAIL=admin@example.com	- Asset audit log

#   ADMIN_PASSWORD=Strong#Password1	- Scheduled ticket management

# Optional:- **Theming**: Built-in themes (light, dark, ocean, fallout) with consistent UI across all pages

#   DATABASE_URL=sqlite:///helpdesk.db- **SQLite by default**: Lightweight "ensure" migrations on startup for zero-config upgradesFlask-based HelpDesk and lightweight purchasing app using SQLite + SQLAlchemy, Flask-Login, and Bootstrap. It ingests emails from Microsoft Graph to create tickets and includes asset management, purchase order workflows, project tracking, and scheduled tickets.

#   POLL_INTERVAL_SECONDS=60

#   MS_CLIENT_ID=...## Features

#   MS_CLIENT_SECRET=...- **Tickets**: Email ingestion via Microsoft Graph, dashboard, notes, attachments, processes, tasks, scheduled recurring tickets

#   MS_TENANT_ID=...- **Assets**: Complete asset lifecycle management with categories, manufacturers, conditions, locations, checkout/checkin, audit trail, CSV import/export

#   MS_USER_EMAIL=...- **Users**: Login, profile (theme + ticket view prefs), admin management of techs

```- **Projects**: Group related tickets, track project status, toggle between open/all ticket views

- **Documents**: Organize documents by category with URLs

3) Run the app- **Purchasing/Orders**:

```powershell	- Planned Items list with Dept/Cost Code, bulk delete, and quick add

python run.py	- Vendors, Companies, and Shipping Locations (with per-location tax rate)

```	- Create draft POs from selected items; edit items in draft, then finalize

Open http://localhost:5000 and log in.	- PO totals include Subtotal + Tax (from selected Ship To) + Shipping cost; Grand Total in UI/PDF

	- PO list pagination (5 default, toggle to 20), with search + pagination preserved

## Version	- PO PDF generation and email on finalize

- The Admin page shows the application version read from `app/version.txt`. Update this file to change the displayed version.- Admin: configure Microsoft Graph and allowed email domains/deny filters

- The Admin page also fetches and displays the latest available version from the GitHub repository (with timeout/fallback handling).- Theming: built-in themes (light, dark, ocean, fallout)

- SQLite by default; lightweight “ensure” migrations on startup

## Default admin account

- On first run, if `ADMIN_EMAIL` and `ADMIN_PASSWORD` are set in `.env`, the app bootstraps an admin account with those credentials.## Requirements

- There is no hard-coded default login. If you don't set these, create an admin later by adding the env vars and restarting.- Python 3.10+ (tested with 3.13)

- Windows, macOS, or Linux

## Purchasing/PO workflow

1. Go to Orders & Purchasing to add Planned Items (optionally with Dept/Cost Code).## Quick start (Windows PowerShell)

2. Select items and create a draft PO. While a PO is in draft:

	 - Edit or delete line items; add new items directly on the PO page.1) Create virtual environment and install dependencies

	 - Choose Company and Ship To (Shipping Location). Shipping Location tax is applied to item subtotal.```powershell

	 - Enter a Shipping Cost (defaults to $0). Totals update to Subtotal + Tax + Shipping = Grand Total.py -3 -m venv .venv; .\.venv\Scripts\Activate.ps1

3. Finalize the PO to assign a PO number, generate a PDF, and email it to the logged-in user. You can also download the PDF from the PO page.pip install -r requirements.txt

```

## Microsoft Graph

- Configure in Admin > Microsoft Graph. The poller runs every `POLL_INTERVAL_SECONDS` (default 60s). You can "Test" the connection and "Check Now".2) Configure environment

- Ensure your Azure AD app has the necessary permissions (e.g., Mail.ReadWrite), and admin consent is granted.```powershell

- Email replies to closed tickets automatically reopen them to "in_progress" status.Copy-Item .env.example .env

# Edit .env and set at minimum:

## Assets Management#   FLASK_SECRET_KEY=your-random-secret

- Complete asset tracking with categories, manufacturers, conditions, and locations managed from Admin panel#   ADMIN_EMAIL=admin@example.com

- Checkout/checkin workflow with assignment to users#   ADMIN_PASSWORD=Strong#Password1

- Audit trail tracking all changes (edits, assignments, status updates)# Optional:

- CSV import/export for bulk operations#   DATABASE_URL=sqlite:///helpdesk.db

- Asset detail pages show related tickets and full history#   POLL_INTERVAL_SECONDS=60

- Create new assets with validated unique asset tags#   MS_CLIENT_ID=...

- Search and filter by availability, status, category, etc.#   MS_CLIENT_SECRET=...

#   MS_TENANT_ID=...

## Scheduled Tickets#   MS_USER_EMAIL=...

- Create recurring tickets (daily, weekly, monthly) from Admin panel```

- Configurable schedule time and associated tasks

- Automatic ticket creation based on schedule3) Run the app

- Link to process templates for consistent workflows```powershell

python run.py

## Attachments directory```

- Incoming email attachments are saved under a configurable base and subfolder. Default base is `instance`, default subfolder is `attachments`.Open http://localhost:5000 and log in.

	- Default full path: `instance/attachments/<ticket_id>/<filename>`

	- Alternate base: `static` (then files are under `static/<subdir>/<ticket_id>`)## Version

- Configure in Admin > Attachments. Settings used: `ATTACHMENTS_BASE` (`instance` or `static`) and `ATTACHMENTS_DIR_REL` (relative folder).- The Admin page shows the application version read from `app/version.txt`. Update this file to change the displayed version.

- When using the default instance base, files are served via a protected download route, not directly from static.- The Admin page also fetches and displays the latest available version from the GitHub repository (with timeout/fallback handling).

- Existing files are not moved automatically if you change the base or folder.

## Default admin account

## Data & migrations- On first run, if `ADMIN_EMAIL` and `ADMIN_PASSWORD` are set in `.env`, the app bootstraps an admin account with those credentials.

- SQLite DB by default at `helpdesk.db` under the project root (unless `DATABASE_URL` overrides it).- There is no hard-coded default login. If you don’t set these, create an admin later by adding the env vars and restarting.

- On startup, the app ensures required tables/columns exist for tickets, users, processes, purchasing, documents, assets, and picklists.

- Admin > Backup/Restore supports SQLite backups and restores; after restore a light ensure revalidates required columns.## Purchasing/PO workflow

1. Go to Orders & Purchasing to add Planned Items (optionally with Dept/Cost Code).

## Branding2. Select items and create a draft PO. While a PO is in draft:

- Application branded as "HelpfulDjinn" (navbar, page titles, login page)	 - Edit or delete line items; add new items directly on the PO page.

- Custom logo displayed on login page (`HelpfulDjinnMd.png`)	 - Choose Company and Ship To (Shipping Location). Shipping Location tax is applied to item subtotal.

- Favicon configured for all pages (`HelpfulDjinnFav32.png`)	 - Enter a Shipping Cost (defaults to $0). Totals update to Subtotal + Tax + Shipping = Grand Total.

- Images served from `app/images/` via custom route `/images/<filename>`3. Finalize the PO to assign a PO number, generate a PDF, and email it to the logged-in user. You can also download the PDF from the PO page.



## Theming## Microsoft Graph

- Themes: light (default), dark, ocean, fallout.- Configure in Admin > Microsoft Graph. The poller runs every `POLL_INTERVAL_SECONDS` (default 60s). You can “Test” the connection and “Check Now”.

- Users can pick a theme on their profile; templates and components are standardized for visual consistency.- Ensure your Azure AD app has the necessary permissions (e.g., Mail.ReadWrite), and admin consent is granted.

- Theme applies to all pages including login, dashboard, tickets, assets, orders, and admin sections.

## Attachments directory

## Required files (directory/file list)- Incoming email attachments are saved under a configurable base and subfolder. Default base is `instance`, default subfolder is `attachments`.

These are the essential files and folders needed to run the application. Items marked optional are created or populated at runtime.	- Default full path: `instance/attachments/<ticket_id>/<filename>`

	- Alternate base: `static` (then files are under `static/<subdir>/<ticket_id>`)

- Project root- Configure in Admin > Attachments. Settings used: `ATTACHMENTS_BASE` (`instance` or `static`) and `ATTACHMENTS_DIR_REL` (relative folder).

	- requirements.txt- When using the default instance base, files are served via a protected download route, not directly from static.

	- run.py- Existing files are not moved automatically if you change the base or folder.

	- .env.example (copy to .env and configure)

	- .env (your configuration) – required at runtime for secrets and admin bootstrap## Data & migrations

	- README.md- SQLite DB by default at `helpdesk.db` under the project root (unless `DATABASE_URL` overrides it).

	- instance/ (optional) – may hold database and backups- On startup, the app ensures required tables/columns exist for tickets, users, processes, purchasing, documents, and assets.

	- helpdesk.db (optional; auto-created on first run if missing)- Admin > Backup/Restore supports SQLite backups and restores; after restore a light ensure revalidates required columns.



- app/## Branding

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

	- services/	- requirements.txt

		- email_poll.py (MS Graph polling + message processing, ticket auto-reopen on reply)	- run.py

		- ms_graph.py (Graph auth helpers, send/receive email)	- .env.example (copy to .env and configure)

		- po_pdf.py (PO PDF generation with ReportLab)	- .env (your configuration) – required at runtime for secrets and admin bootstrap

	- utils/	- README.md

		- db_migrate.py (ensure/compat migrations for all tables including assets and picklists)	- instance/ (optional) – not used by default SQLite path; may hold alternative configs

		- security.py (password hashing with werkzeug)	- helpdesk.db (optional; auto-created on first run if missing)

	- images/ (logo and favicon assets)

		- HelpfulDjinn.png- app/

		- HelpfulDjinnMd.png (displayed on login page)	- __init__.py (Flask app factory; config, DB init, scheduler, blueprints, image serving route)

		- HelpfulDjinnFav32.png (favicon)	- models.py (SQLAlchemy models including Asset, AssetCategory, AssetManufacturer, AssetCondition, AssetLocation, ScheduledTicket, etc.)

	- templates/	- forms.py (WTForms for all UI forms)

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

If you deploy subsets of features, remove corresponding routes/templates carefully; otherwise keep all templates and theme files present to avoid missing-template errors.		- HelpfulDjinn.png

		- HelpfulDjinnMd.png (displayed on login page)

## Troubleshooting		- HelpfulDjinnFav32.png (favicon)

- **Python path issues**: If a VS Code task points at a non-existent Python path, launch with the system launcher:	- templates/

	```powershell		- base.html (main template with navbar, themes, favicon)

	py -3 run.py		- admin/ ... (all admin templates including asset_picklist.html)

	```		- assets/ ... (index, detail with edit/new forms)

	Or activate your venv and run:		- auth/ ... (login with logo, profile)

	```powershell		- dashboard/ ...

	.\.venv\Scripts\Activate.ps1		- documents/ ...

	python run.py		- orders/ ...

	```		- projects/ ...

- **Login problems**: Confirm `ADMIN_EMAIL`/`ADMIN_PASSWORD` are set in `.env` and restart; watch console for DB ensure messages.		- tickets/ ...

- **Email ingestion issues**: Verify Microsoft Graph credentials in Admin, try "Test" and "Check Now", and inspect server logs.		- users/ ...

- **Missing images/favicon**: Ensure `app/images/` folder exists with required image files; the `/images/<filename>` route serves them.	- static/

- **Asset tag uniqueness errors**: The system validates unique asset tags on creation; check existing assets before importing.		- themes/

- **Database errors after upgrade**: The app automatically runs lightweight migrations on startup; check console for ensure messages. Use Admin > Backup before major changes.			- dark.css

			- ocean.css

## Production notes			- fallout.css

- Use a production WSGI server (e.g., Waitress or Gunicorn) and run the email poller job continuously (the built-in APScheduler can run in-process; for scale, consider an external worker).		- attachments/ (optional; runtime storage for email attachments when using static base)

- Set a strong `FLASK_SECRET_KEY` and a persistent database (e.g., PostgreSQL) for multi-user scenarios.

- scripts/ (optional, developer utilities)

If you deploy subsets of features, remove corresponding routes/templates carefully; otherwise keep all templates and theme files present to avoid missing-template errors.

## Troubleshooting
- If a VS Code task points at a non-existent Python path, launch with the system launcher:
	```powershell
	py -3 run.py
	```
	Or activate your venv and run:
	```powershell
	.\.venv\Scripts\Activate.ps1
	python run.py
	```
- If you can’t log in: confirm `ADMIN_EMAIL`/`ADMIN_PASSWORD` are set in `.env` and restart; watch console for DB ensure messages.
- If emails aren’t ingesting: verify Microsoft Graph credentials in Admin, try “Test” and “Check Now”, and inspect server logs.

## Production notes
- Use a production WSGI server (e.g., Waitress or Gunicorn) and run the email poller job continuously (the built-in APScheduler can run in-process; for scale, consider an external worker).
- Set a strong `FLASK_SECRET_KEY` and a persistent database (e.g., PostgreSQL) for multi-user scenarios.
