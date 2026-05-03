from flask import Blueprint, render_template, url_for, request, jsonify
from flask_login import login_required, current_user
from ..models import Ticket, Project, User, TicketStatus, Tag, ticket_tags, Contact
from .. import db
from datetime import datetime, timedelta
from sqlalchemy import func


dashboard_bp = Blueprint('dashboard', __name__)


def get_closed_status_names():
    """Get list of status names that are marked as closed."""
    return [s.name for s in TicketStatus.query.filter_by(is_closed=True).all()] or ['closed']


def _week_start(dt):
    """Return the most recent Sunday 00:00 UTC on or before dt."""
    days_since_sunday = (dt.weekday() + 1) % 7
    ws = dt - timedelta(days=days_since_sunday)
    return datetime(ws.year, ws.month, ws.day)


# Bootstrap color name → Chart.js hex
_COLOR_MAP = {
    'primary': '#0d6efd', 'success': '#198754', 'danger': '#dc3545',
    'warning': '#ffc107', 'info': '#0dcaf0', 'secondary': '#6c757d',
}


@dashboard_bp.route('/')
@login_required
def index():
    closed_statuses = get_closed_status_names()

    # Total open tickets (exclude project tickets and currently-snoozed)
    total_open = Ticket.query.filter(
        ~Ticket.status.in_(closed_statuses) &
        (Ticket.project_id.is_(None)) &
        ((Ticket.snoozed_until.is_(None)) | (Ticket.snoozed_until <= datetime.utcnow()))
    ).count()

    # ── Health Score ──────────────────────────────────────────────────────────
    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)

    overdue_count = Ticket.query.filter(
        ~Ticket.status.in_(closed_statuses) &
        (Ticket.project_id.is_(None)) &
        ((Ticket.snoozed_until.is_(None)) | (Ticket.snoozed_until <= datetime.utcnow())) &
        (Ticket.priority == 'high') &
        (Ticket.created_at < now - timedelta(days=1))
    ).count()

    unassigned_24h = Ticket.query.filter(
        ~Ticket.status.in_(closed_statuses) &
        (Ticket.project_id.is_(None)) &
        ((Ticket.snoozed_until.is_(None)) | (Ticket.snoozed_until <= datetime.utcnow())) &
        (Ticket.assignee_id.is_(None)) &
        (Ticket.created_at < now - timedelta(hours=24))
    ).count()

    open_7days = Ticket.query.filter(
        ~Ticket.status.in_(closed_statuses) &
        (Ticket.project_id.is_(None)) &
        ((Ticket.snoozed_until.is_(None)) | (Ticket.snoozed_until <= datetime.utcnow())) &
        (Ticket.created_at < now - timedelta(days=7))
    ).count()

    open_14days = Ticket.query.filter(
        ~Ticket.status.in_(closed_statuses) &
        (Ticket.project_id.is_(None)) &
        ((Ticket.snoozed_until.is_(None)) | (Ticket.snoozed_until <= datetime.utcnow())) &
        (Ticket.created_at < now - timedelta(days=14))
    ).count()

    closed_today = Ticket.query.filter(
        Ticket.status.in_(closed_statuses) &
        (Ticket.project_id.is_(None)) &
        (Ticket.closed_at >= today_start)
    ).count()

    health = 100
    health -= (overdue_count * 10)
    health -= (unassigned_24h * 5)
    health -= (open_7days * 2)
    health -= (open_14days * 4)
    health += (closed_today * 3)
    health = max(0, min(100, health))

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

    # ── Leaders (reset each Sunday) ───────────────────────────────────────────
    week_start = _week_start(now)

    leaders = db.session.query(
        User.id,
        User.name,
        func.count(Ticket.id).label('closed_count')
    ).join(
        Ticket, Ticket.assignee_id == User.id
    ).filter(
        Ticket.status.in_(closed_statuses) &
        (Ticket.closed_at >= week_start) &
        (Ticket.project_id.is_(None))
    ).group_by(User.id, User.name).order_by(func.count(Ticket.id).desc()).limit(2).all()

    active_projects = Project.query.filter(Project.status != 'closed').count()

    snoozed_count = Ticket.query.filter(
        ~Ticket.status.in_(closed_statuses) &
        (Ticket.snoozed_until.isnot(None)) &
        (Ticket.snoozed_until > datetime.utcnow())
    ).count()

    # ── Extra Metrics ─────────────────────────────────────────────────────────
    thirty_days_ago = now - timedelta(days=30)

    # Average resolution time (tickets closed in last 30 days)
    resolved = db.session.query(Ticket.created_at, Ticket.closed_at).filter(
        Ticket.status.in_(closed_statuses),
        Ticket.project_id.is_(None),
        Ticket.closed_at.isnot(None),
        Ticket.closed_at >= thirty_days_ago,
    ).all()
    avg_resolution = '—'
    if resolved:
        deltas = [
            (t.closed_at - t.created_at).total_seconds()
            for t in resolved
            if t.closed_at and t.created_at and t.closed_at > t.created_at
        ]
        if deltas:
            avg_s = sum(deltas) / len(deltas)
            avg_days = int(avg_s // 86400)
            avg_hours = int((avg_s % 86400) // 3600)
            avg_resolution = f"{avg_days}d {avg_hours}h" if avg_days > 0 else f"{avg_hours}h"

    # Unassigned open tickets
    unassigned_open = Ticket.query.filter(
        ~Ticket.status.in_(closed_statuses),
        Ticket.project_id.is_(None),
        Ticket.assignee_id.is_(None),
        (Ticket.snoozed_until.is_(None)) | (Ticket.snoozed_until <= now),
    ).count()

    # Priority breakdown of open (non-project) tickets
    priority_rows = db.session.query(
        Ticket.priority, func.count(Ticket.id)
    ).filter(
        ~Ticket.status.in_(closed_statuses),
        Ticket.project_id.is_(None),
    ).group_by(Ticket.priority).all()
    priority_breakdown = {'high': 0, 'medium': 0, 'low': 0}
    for priority, cnt in priority_rows:
        if priority in priority_breakdown:
            priority_breakdown[priority] = cnt

    # Opened this week
    opened_this_week = Ticket.query.filter(
        Ticket.created_at >= week_start,
        Ticket.project_id.is_(None),
    ).count()

    # Closed this week
    closed_this_week = Ticket.query.filter(
        Ticket.status.in_(closed_statuses),
        Ticket.closed_at.isnot(None),
        Ticket.closed_at >= week_start,
        Ticket.project_id.is_(None),
    ).count()

    # Top requesters last 30 days — resolve name and contact_id via Contact table
    top_req_rows = db.session.query(
        Ticket.requester_email,
        func.count(Ticket.id).label('cnt')
    ).filter(
        Ticket.created_at >= thirty_days_ago,
        Ticket.project_id.is_(None),
        Ticket.requester_email.isnot(None),
        Ticket.requester_email != '',
    ).group_by(Ticket.requester_email).order_by(func.count(Ticket.id).desc()).limit(5).all()

    top_emails = [r.requester_email for r in top_req_rows]
    contact_map = {}
    if top_emails:
        for c in Contact.query.filter(Contact.email.in_(top_emails)).all():
            contact_map[c.email.lower()] = c

    top_requesters = []
    for row in top_req_rows:
        email_lower = (row.requester_email or '').lower()
        contact = contact_map.get(email_lower)
        top_requesters.append({
            'email': row.requester_email,
            'name': (contact.name if contact and contact.name else row.requester_email),
            'contact_id': contact.id if contact else None,
            'cnt': row.cnt,
        })

    stats = {
        'open_total': total_open,
        'active_projects': active_projects,
        'snoozed_count': snoozed_count,
        'health': health,
        'health_color': health_color,
        'health_class': health_class,
        'leaders': leaders,
        'health_breakdown': {
            'overdue_count': overdue_count,
            'overdue_penalty': overdue_count * 10,
            'unassigned_24h': unassigned_24h,
            'unassigned_penalty': unassigned_24h * 5,
            'open_7days': open_7days,
            'open_7days_penalty': open_7days * 2,
            'open_14days': open_14days,
            'open_14days_penalty': open_14days * 4,
            'closed_today': closed_today,
            'closed_today_bonus': closed_today * 3,
        },
        # Extra metrics
        'avg_resolution': avg_resolution,
        'unassigned_open': unassigned_open,
        'priority_breakdown': priority_breakdown,
        'opened_this_week': opened_this_week,
        'closed_this_week': closed_this_week,
        'top_requesters': top_requesters,
    }
    return render_template('dashboard/index.html', stats=stats)


@dashboard_bp.route('/top-tags')
@login_required
def top_tags():
    """Return top tag categories and individual leaf tags as JSON (two buckets)."""
    days = request.args.get('days', 30, type=int)
    if days not in (7, 30, 60, 90):
        days = 30
    cutoff = datetime.utcnow() - timedelta(days=days)

    tag_map = {t.id: t for t in Tag.query.all()}

    def get_hex(tag_id):
        t = tag_map.get(tag_id)
        if not t:
            return '#6c757d'
        return _COLOR_MAP.get(t.effective_color, '#6c757d')

    # ── Categories: roll each child up to its parent via COALESCE ─────────────
    # A ticket tagged "Hardware › Laptop" counts once toward "Hardware".
    # A top-level tag with no parent counts toward itself.
    cat_rows = (
        db.session.query(
            func.coalesce(Tag.parent_id, Tag.id).label('cat_id'),
            func.count(ticket_tags.c.ticket_id).label('cnt')
        )
        .join(ticket_tags, Tag.id == ticket_tags.c.tag_id)
        .join(Ticket, Ticket.id == ticket_tags.c.ticket_id)
        .filter(Ticket.created_at >= cutoff)
        .group_by(func.coalesce(Tag.parent_id, Tag.id))
        .order_by(func.count(ticket_tags.c.ticket_id).desc())
        .limit(10)
        .all()
    )
    cat_labels, cat_counts, cat_colors = [], [], []
    for row in cat_rows:
        t = tag_map.get(row.cat_id)
        if t:
            cat_labels.append(t.name)
            cat_counts.append(row.cnt)
            cat_colors.append(get_hex(t.id))

    # ── Individual tags: only leaf tags (those that have a parent) ────────────
    leaf_rows = (
        db.session.query(
            Tag.id, Tag.name, Tag.parent_id,
            func.count(ticket_tags.c.ticket_id).label('cnt')
        )
        .join(ticket_tags, Tag.id == ticket_tags.c.tag_id)
        .join(Ticket, Ticket.id == ticket_tags.c.ticket_id)
        .filter(Ticket.created_at >= cutoff, Tag.parent_id.isnot(None))
        .group_by(Tag.id, Tag.name, Tag.parent_id)
        .order_by(func.count(ticket_tags.c.ticket_id).desc())
        .limit(10)
        .all()
    )
    tag_labels = [r.name for r in leaf_rows]
    tag_counts = [r.cnt for r in leaf_rows]
    tag_colors = [get_hex(r.id) for r in leaf_rows]

    return jsonify(
        days=days,
        categories=dict(labels=cat_labels, counts=cat_counts, colors=cat_colors),
        tags=dict(labels=tag_labels, counts=tag_counts, colors=tag_colors),
    )


@dashboard_bp.route('/ticket-sources')
@login_required
def ticket_sources():
    """Return ticket-source breakdown as JSON for the pie chart."""
    days = request.args.get('days', 30, type=int)
    if days not in (7, 30, 60, 90):
        days = 30
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.session.query(Ticket.source, func.count(Ticket.id))
        .filter(Ticket.created_at >= cutoff)
        .group_by(Ticket.source)
        .all()
    )
    labels = [r[0] or 'Unknown' for r in rows]
    counts = [r[1] for r in rows]
    return jsonify(labels=labels, counts=counts, days=days)


@dashboard_bp.route('/tickets-per-week')
@login_required
def tickets_per_week():
    """Return opened/closed ticket counts bucketed by week."""
    range_param = request.args.get('range', '4w')
    if range_param not in ('4w', '2m', '3m', '6m', '1y'):
        range_param = '4w'

    weeks_map = {'4w': 4, '2m': 8, '3m': 13, '6m': 26, '1y': 52}
    num_weeks = weeks_map[range_param]

    now = datetime.utcnow()
    current_ws = _week_start(now)

    # Build bucket list oldest → newest
    buckets = []
    for i in range(num_weeks - 1, -1, -1):
        start = current_ws - timedelta(weeks=i)
        end = start + timedelta(weeks=1)
        buckets.append((start, end))

    earliest = buckets[0][0]
    closed_statuses = get_closed_status_names()

    opened_rows = db.session.query(Ticket.created_at).filter(
        Ticket.created_at >= earliest,
        Ticket.project_id.is_(None),
    ).all()

    closed_rows = db.session.query(Ticket.closed_at).filter(
        Ticket.closed_at.isnot(None),
        Ticket.closed_at >= earliest,
        Ticket.project_id.is_(None),
        Ticket.status.in_(closed_statuses),
    ).all()

    opened_counts = [0] * num_weeks
    closed_counts = [0] * num_weeks

    for (created_at,) in opened_rows:
        for i, (start, end) in enumerate(buckets):
            if start <= created_at < end:
                opened_counts[i] += 1
                break

    for (closed_at,) in closed_rows:
        for i, (start, end) in enumerate(buckets):
            if start <= closed_at < end:
                closed_counts[i] += 1
                break

    labels = [start.strftime('%b') + ' ' + str(start.day) for start, _ in buckets]

    return jsonify(range=range_param, labels=labels, opened=opened_counts, closed=closed_counts)
