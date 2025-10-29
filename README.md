# HelpfulDjinn - Simple Helpdesk

A Flask-based HelpDesk and lightweight purchasing app using SQLite + SQLAlchemy, Flask-Login, and Bootstrap all in one executible file.

If running from executible jump to end of readme

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

## Requirements - Run from Source Code

- Python 3.10+ (tested with 3.13)## Scheduled Tickets


## Quick start (Windows PowerShell from Source)

pip install -r requirements.txt	

python run.py

Open http://localhost:5000 and log in.

On first run, you’ll see the Setup screen:
- Create your admin account.
- Optional: check “Load demo data” to pre-populate 20 tickets, 20 users, 20 assets, and 20 purchase orders with sensible relationships (assets assigned to users; tickets linked to users/assets; POs that ordered some assets). You can skip this if you plan to restore a backup instead.


## Run from Executible

On first run the following directories are created if not presant:
- Attachments - For Ticket Attachments
- Backups - For auto backups
- Databases - Main Database folder

to upgrade to newest version, backup your database then remove HelpfulDjinn.exe. copy new HelpfulDjinn.exe and run. System will update database (if applicable) and start.