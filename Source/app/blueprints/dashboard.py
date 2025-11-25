from flask import Blueprint, render_template, url_for, request
from flask_login import login_required, current_user
from ..models import Ticket, Project, User
from .. import db
from datetime import datetime, timedelta
from sqlalchemy import func


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
    
    # Calculate total open tickets (exclude project tickets and optionally snoozed)
    total_open = Ticket.query.filter((Ticket.status != 'closed') & (Ticket.project_id.is_(None)) & ((Ticket.snoozed_until.is_(None)) | (Ticket.snoozed_until <= datetime.utcnow()))).count() if not show_snoozed else Ticket.query.filter((Ticket.status != 'closed') & (Ticket.project_id.is_(None))).count()
    
    # Calculate Health Score
    # Health = 100 - (Overdue tickets × 10) - (Unassigned > 24h × 5) - (Open > 7 days × 2) - (Open > 14 days × 4) + (Closed today × 3)
    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)
    
    # Overdue tickets (assuming priority high or past certain age)
    overdue_count = Ticket.query.filter(
        (Ticket.status != 'closed') & 
        (Ticket.project_id.is_(None)) &
        ((Ticket.snoozed_until.is_(None)) | (Ticket.snoozed_until <= datetime.utcnow())) &
        (Ticket.priority == 'high') &
        (Ticket.created_at < now - timedelta(days=1))
    ).count()
    
    # Unassigned tickets older than 24 hours
    unassigned_24h = Ticket.query.filter(
        (Ticket.status != 'closed') &
        (Ticket.project_id.is_(None)) &
        ((Ticket.snoozed_until.is_(None)) | (Ticket.snoozed_until <= datetime.utcnow())) &
        (Ticket.assignee_id.is_(None)) &
        (Ticket.created_at < now - timedelta(hours=24))
    ).count()
    
    # Open tickets older than 7 days
    open_7days = Ticket.query.filter(
        (Ticket.status != 'closed') &
        (Ticket.project_id.is_(None)) &
        ((Ticket.snoozed_until.is_(None)) | (Ticket.snoozed_until <= datetime.utcnow())) &
        (Ticket.created_at < now - timedelta(days=7))
    ).count()
    
    # Open tickets older than 14 days
    open_14days = Ticket.query.filter(
        (Ticket.status != 'closed') &
        (Ticket.project_id.is_(None)) &
        ((Ticket.snoozed_until.is_(None)) | (Ticket.snoozed_until <= datetime.utcnow())) &
        (Ticket.created_at < now - timedelta(days=14))
    ).count()
    
    # Closed tickets today
    closed_today = Ticket.query.filter(
        (Ticket.status == 'closed') &
        (Ticket.project_id.is_(None)) &
        (Ticket.closed_at >= today_start)
    ).count()
    
    # Calculate health score
    health = 100
    health -= (overdue_count * 10)
    health -= (unassigned_24h * 5)
    health -= (open_7days * 2)
    health -= (open_14days * 4)
    health += (closed_today * 3)
    health = max(0, min(100, health))  # Clamp between 0 and 100
    
    # Determine health color
    if health >= 90:
        health_color = 'darkgreen'
        health_class = 'success'
    elif health >= 70:
        health_color = 'lightgreen'
        health_class = 'success'
    elif health >= 50:
        health_color = 'yellow'
        health_class = 'warning'
    else:
        health_color = 'red'
        health_class = 'danger'
    
    # Get leaders - techs with most closed tickets this week (reset on Sunday)
    # Compute start of current week as Sunday 00:00
    # Python weekday(): Monday=0 .. Sunday=6, so days since Sunday is (weekday+1) % 7
    days_since_sunday = (now.weekday() + 1) % 7
    week_start = now - timedelta(days=days_since_sunday)
    week_start = datetime(week_start.year, week_start.month, week_start.day)
    
    leaders = db.session.query(
        User.id,
        User.name,
        func.count(Ticket.id).label('closed_count')
    ).join(
        Ticket, Ticket.assignee_id == User.id
    ).filter(
        (Ticket.status == 'closed') &
        (Ticket.closed_at >= week_start) &
        (Ticket.project_id.is_(None))
    ).group_by(User.id, User.name).order_by(func.count(Ticket.id).desc()).limit(2).all()
    
    # Count active projects
    active_projects = Project.query.filter(Project.status != 'closed').count()
    
    # Count snoozed tickets
    snoozed_count = Ticket.query.filter((Ticket.status != 'closed') & (Ticket.snoozed_until.isnot(None)) & (Ticket.snoozed_until > datetime.utcnow())).count()
    
    stats = {
        'open_total': total_open,
        'active_projects': active_projects,
        'snoozed_count': snoozed_count,
        'health': health,
        'health_color': health_color,
        'health_class': health_class,
        'leaders': leaders,
    }
    return render_template('dashboard/index.html', tickets=tickets, stats=stats, show_snoozed=show_snoozed)
