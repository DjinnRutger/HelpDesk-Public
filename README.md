# HelpDesk

A Flask-based HelpDesk and lightweight purchasing app using SQLite + SQLAlchemy, Flask-Login, and Bootstrap. It ingests emails from Microsoft Graph to create tickets and includes a simple Purchase Order (PO) workflow with PDF export.

## Features
- Tickets: email ingestion via Microsoft Graph, dashboard, notes, attachments, processes, tasks
- Users: login, profile (theme + ticket view prefs), admin management of techs
- Purchasing/Orders:
	- Planned Items list with Dept/Cost Code, bulk delete, and quick add
	- Vendors, Companies, and Shipping Locations (with per-location tax rate)
	- Create draft POs from selected items; edit items in draft, then finalize
	- PO totals include Subtotal + Tax (from selected Ship To) + Shipping cost; Grand Total in UI/PDF
	- PO list pagination (5 default, toggle to 20), with search + pagination preserved
	- PO PDF generation and email on finalize
- Admin: configure Microsoft Graph and allowed email domains/deny filters
- Theming: built-in themes (light, dark, ocean, fallout)
- SQLite by default; lightweight “ensure” migrations on startup

## Requirements
- Python 3.10+ (tested with 3.13)
- Windows, macOS, or Linux

## Quick start (Windows PowerShell)

1) Create virtual environment and install dependencies
```powershell
py -3 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2) Configure environment
```powershell
Copy-Item .env.example .env
# Edit .env and set at minimum:
#   FLASK_SECRET_KEY=your-random-secret
#   ADMIN_EMAIL=admin@example.com
#   ADMIN_PASSWORD=Strong#Password1
# Optional:
#   DATABASE_URL=sqlite:///helpdesk.db
#   POLL_INTERVAL_SECONDS=60
#   MS_CLIENT_ID=...
#   MS_CLIENT_SECRET=...
#   MS_TENANT_ID=...
#   MS_USER_EMAIL=...
```

3) Run the app
```powershell
python run.py
```
Open http://localhost:5000 and log in.

## Version
- The Admin page shows the application version read from `version.txt` at the repository root. Update this file to change the displayed version.

## Default admin account
- On first run, if `ADMIN_EMAIL` and `ADMIN_PASSWORD` are set in `.env`, the app bootstraps an admin account with those credentials.
- There is no hard-coded default login. If you don’t set these, create an admin later by adding the env vars and restarting.

## Purchasing/PO workflow
1. Go to Orders & Purchasing to add Planned Items (optionally with Dept/Cost Code).
2. Select items and create a draft PO. While a PO is in draft:
	 - Edit or delete line items; add new items directly on the PO page.
	 - Choose Company and Ship To (Shipping Location). Shipping Location tax is applied to item subtotal.
	 - Enter a Shipping Cost (defaults to $0). Totals update to Subtotal + Tax + Shipping = Grand Total.
3. Finalize the PO to assign a PO number, generate a PDF, and email it to the logged-in user. You can also download the PDF from the PO page.

## Microsoft Graph
- Configure in Admin > Microsoft Graph. The poller runs every `POLL_INTERVAL_SECONDS` (default 60s). You can “Test” the connection and “Check Now”.
- Ensure your Azure AD app has the necessary permissions (e.g., Mail.ReadWrite), and admin consent is granted.

## Data & migrations
- SQLite DB by default at `helpdesk.db` under the project root (unless `DATABASE_URL` overrides it).
- On startup, the app ensures required tables/columns exist for tickets, users, processes, purchasing, documents, and assets.
- Admin > Backup/Restore supports SQLite backups and restores; after restore a light ensure revalidates required columns.

## Theming
- Themes: light (default), dark, ocean, fallout.
- Users can pick a theme on their profile; templates and components are standardized for visual consistency.

## Required files (directory/file list)
These are the essential files and folders needed to run the application. Items marked optional are created or populated at runtime.

- Project root
	- requirements.txt
	- run.py
	- .env.example (copy to .env and configure)
	- .env (your configuration) – required at runtime for secrets and admin bootstrap
	- version.txt
	- README.md
	- instance/ (optional) – not used by default SQLite path; may hold alternative configs
	- helpdesk.db (optional; auto-created on first run if missing)

- app/
	- __init__.py (Flask app factory; config, DB init, scheduler, blueprints)
	- models.py (SQLAlchemy models)
	- forms.py (WTForms)
	- blueprints/
		- admin.py
		- assets.py
		- auth.py
		- dashboard.py
		- documents.py
		- orders.py
		- projects.py
		- tickets.py
		- users.py
	- services/
		- email_poll.py (MS Graph polling + message processing)
		- ms_graph.py (Graph auth helpers)
		- po_pdf.py (PO PDF generation)
	- utils/
		- db_migrate.py (ensure/compat migrations)
		- security.py (password hashing utilities)
	- templates/
		- base.html
		- admin/ ... (all templates under this folder)
		- assets/ ...
		- auth/ ...
		- dashboard/ ...
		- documents/ ...
		- orders/ ...
		- projects/ ...
		- tickets/ ...
		- users/ ...
	- static/
		- themes/
			- dark.css
			- ocean.css
			- fallout.css
		- attachments/ (optional; runtime storage for uploaded email/notes attachments)

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
