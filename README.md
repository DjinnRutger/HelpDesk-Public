# HelpfulDjinn - Simple Helpdesk

A Flask-based HelpDesk and lightweight purchasing app using SQLite + SQLAlchemy, Flask-Login, and Bootstrap all in one executible file.

If running from executible jump to end of readme

## Security & Safety
This app is compiled into a single executable for easy deployment. We've scanned the latest release (1.2.2) with VirusTotal (72 engines) and it came back 3/72 detections.

- [View Full VirusTotal Report] https://www.virustotal.com/gui/file/6cc3f74972f6e2b9a6ae0f6a7553fa9c313d292e80ea5b5ece4132a614ba1d9f?nocache=1
- SHA-256 Hash: `6cc3f74972f6e2b9a6ae0f6a7553fa9c313d292e80ea5b5ece4132a614ba1d9f` (for verification)

We recommend users verify downloads themselves via VT for peace of mind.


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
<<<<<<< HEAD

## Snoozed Tickets Wake-up Notifications

When a ticket is snoozed, it is hidden until the selected date/time. A background job runs every minute and:
- Detects tickets whose snooze time has arrived
- Clears the snooze so they appear again
- Sends an email to the assigned tech (if any) letting them know the ticket is active again

Notes:
- Email delivery uses Microsoft Graph. Configure MS_CLIENT_ID, MS_CLIENT_SECRET, MS_TENANT_ID, and MS_USER_EMAIL in Admin > Email Settings (or environment variables).
- If no tech is assigned, the ticket is simply unsnoozed with a private system note.
=======
>>>>>>> 5348acca801cc1836eb2afc774a4503303920f3e
