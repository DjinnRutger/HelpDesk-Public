from flask import Blueprint, render_template, url_for, request
from flask_login import login_required, current_user
from ..models import Ticket, Project
from datetime import datetime


dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
@login_required
def index():
    show_snoozed = request.args.get('show_snoozed', '0') == '1'
    # List all open tickets for the table (exclude project tickets)
    base = Ticket.query.filter((Ticket.status != 'closed') & (Ticket.project_id.is_(None)))
    if not show_snoozed:
        base = base.filter((Ticket.snoozed_until.is_(None)) | (Ticket.snoozed_until <= datetime.utcnow()))
    tickets = base.order_by(Ticket.created_at.desc()).all()
    # Top widgets (exclude project tickets in counts)
    total_open = Ticket.query.filter((Ticket.status != 'closed') & (Ticket.project_id.is_(None)) & ((Ticket.snoozed_until.is_(None)) | (Ticket.snoozed_until <= datetime.utcnow()))).count() if not show_snoozed else Ticket.query.filter((Ticket.status != 'closed') & (Ticket.project_id.is_(None))).count()
    my_open = Ticket.query.filter((Ticket.status != 'closed') & (Ticket.assignee_id == current_user.id) & (Ticket.project_id.is_(None)) & ((Ticket.snoozed_until.is_(None)) | (Ticket.snoozed_until <= datetime.utcnow()))).count() if not show_snoozed else Ticket.query.filter((Ticket.status != 'closed') & (Ticket.assignee_id == current_user.id) & (Ticket.project_id.is_(None))).count()
    unassigned = Ticket.query.filter((Ticket.status != 'closed') & (Ticket.assignee_id.is_(None)) & (Ticket.project_id.is_(None)) & ((Ticket.snoozed_until.is_(None)) | (Ticket.snoozed_until <= datetime.utcnow()))).count() if not show_snoozed else Ticket.query.filter((Ticket.status != 'closed') & (Ticket.assignee_id.is_(None)) & (Ticket.project_id.is_(None))).count()
    active_projects = Project.query.filter(Project.status != 'closed').count()
    snoozed_count = Ticket.query.filter((Ticket.status != 'closed') & (Ticket.snoozed_until.isnot(None)) & (Ticket.snoozed_until > datetime.utcnow())).count()
    stats = {
        'open_total': total_open,
        'my_open': my_open,
        'unassigned': unassigned,
        'active_projects': active_projects,
        'snoozed_count': snoozed_count,
    }
    return render_template('dashboard/index.html', tickets=tickets, stats=stats, show_snoozed=show_snoozed)
