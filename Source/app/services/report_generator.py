"""Automated report generation and email delivery.

Polled every minute by the scheduler. Each active Report row whose schedule matches
the current minute is generated (per report_type) and emailed to its recipients.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional

from flask import render_template
from sqlalchemy import func

from .. import db
from ..models import (
    Report, ReportRun, Ticket, Asset, User, TicketStatus,
)
from .ms_graph import send_mail


# ---------------------------------------------------------------------------
# Source dropdown values mirror Source/app/forms.py:34
# ---------------------------------------------------------------------------
SOURCE_CHOICES: List[Tuple[str, str]] = [
    ('email', 'Email'),
    ('zoom', 'Zoom'),
    ('walk_in', 'Walk In'),
    ('phone', 'Phone'),
]

ASSET_STATUS_CHOICES: List[Tuple[str, str]] = [
    ('available', 'Available'),
    ('deployed', 'Deployed'),
    ('maintenance', 'Maintenance'),
    ('retired', 'Retired'),
    ('lost', 'Lost'),
    ('archived', 'Archived'),
]

DEFAULT_SECTIONS: Dict[str, bool] = {
    'source_breakdown': True,
    'user_vs_tech': True,
    'inventory_status': True,
}


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------

def _previous_period(frequency: str, now: datetime) -> Tuple[datetime, datetime, str]:
    """Return (start, end, label) for the *previous* full period before now.

    daily   -> yesterday 00:00 .. 23:59:59
    weekly  -> previous Mon 00:00 .. previous Sun 23:59:59
    monthly -> first day of last month 00:00 .. last day of last month 23:59:59
    """
    if frequency == 'daily':
        end_day = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        start = end_day
        end = end_day + timedelta(days=1) - timedelta(seconds=1)
        label = start.strftime('%A, %B %d, %Y')
        return start, end, label

    if frequency == 'monthly':
        first_of_this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_of_prev_month = first_of_this_month - timedelta(seconds=1)
        first_of_prev_month = last_of_prev_month.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        label = first_of_prev_month.strftime('%B %Y')
        return first_of_prev_month, last_of_prev_month, label

    # weekly (default) — previous Mon-Sun
    # weekday(): Mon=0 .. Sun=6. Find this week's Monday, then go back 7 days.
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    this_monday = today_midnight - timedelta(days=now.weekday())
    last_monday = this_monday - timedelta(days=7)
    last_sunday_end = this_monday - timedelta(seconds=1)
    label = f"{last_monday.strftime('%b %d')} – {last_sunday_end.strftime('%b %d, %Y')}"
    return last_monday, last_sunday_end, label


def _prior_period(frequency: str, period_start: datetime, period_end: datetime) -> Tuple[datetime, datetime]:
    """Return the (start, end) of the period immediately before the given period.

    Used for week-over-week / period-over-period delta calculations.
    """
    if frequency == 'daily':
        start = period_start - timedelta(days=1)
        end = period_end - timedelta(days=1)
        return start, end
    if frequency == 'monthly':
        # First of prior month .. last second of prior month
        prior_end = period_start - timedelta(seconds=1)
        prior_start = prior_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return prior_start, prior_end
    # weekly
    start = period_start - timedelta(days=7)
    end = period_end - timedelta(days=7)
    return start, end


def _trend_buckets(frequency: str, period_end: datetime, count: int = 4) -> List[Tuple[datetime, datetime, str]]:
    """Return [count] most-recent completed buckets ending at period_end."""
    buckets: List[Tuple[datetime, datetime, str]] = []
    end = period_end
    for _ in range(count):
        if frequency == 'daily':
            start = end.replace(hour=0, minute=0, second=0, microsecond=0)
            label = start.strftime('%a %b %d')
        elif frequency == 'monthly':
            start = end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            label = start.strftime('%b %Y')
        else:
            # Weekly: end is a Sunday end-of-day; start is the prior Monday
            start = (end - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0)
            label = f"{start.strftime('%b %d')}–{end.strftime('%b %d')}"
        buckets.append((start, end, label))
        # Step back one period
        if frequency == 'daily':
            end = start - timedelta(seconds=1)
        elif frequency == 'monthly':
            end = start - timedelta(seconds=1)
        else:
            end = start - timedelta(seconds=1)
    buckets.reverse()
    return buckets


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def _closed_status_names() -> List[str]:
    return [s.name for s in TicketStatus.query.filter_by(is_closed=True).all()] or ['closed']


def _count_created(start: datetime, end: datetime) -> int:
    return Ticket.query.filter(
        Ticket.created_at >= start,
        Ticket.created_at <= end,
        Ticket.project_id.is_(None),
    ).count()


def _count_closed(start: datetime, end: datetime, closed_statuses: List[str]) -> int:
    return Ticket.query.filter(
        Ticket.status.in_(closed_statuses),
        Ticket.closed_at.isnot(None),
        Ticket.closed_at >= start,
        Ticket.closed_at <= end,
        Ticket.project_id.is_(None),
    ).count()


def _source_breakdown(start: datetime, end: datetime) -> List[Dict]:
    rows = db.session.query(
        Ticket.source,
        func.count(Ticket.id).label('cnt'),
    ).filter(
        Ticket.created_at >= start,
        Ticket.created_at <= end,
        Ticket.project_id.is_(None),
    ).group_by(Ticket.source).all()
    counts = {(src or 'email'): int(cnt) for src, cnt in rows}
    total = sum(counts.values()) or 1
    out = []
    seen = set()
    for key, label in SOURCE_CHOICES:
        c = counts.get(key, 0)
        out.append({
            'key': key,
            'label': label,
            'count': c,
            'percent': round(100.0 * c / total, 1) if total else 0.0,
        })
        seen.add(key)
    # Any unexpected source values (e.g., 'system', 'ftp') get an "Other" row
    other = sum(v for k, v in counts.items() if k not in seen)
    if other:
        out.append({
            'key': 'other',
            'label': 'Other',
            'count': other,
            'percent': round(100.0 * other / total, 1),
        })
    return out


def _user_vs_tech_split(start: datetime, end: datetime) -> Dict:
    user_created = Ticket.query.filter(
        Ticket.created_at >= start,
        Ticket.created_at <= end,
        Ticket.created_by_user_id.is_(None),
        Ticket.project_id.is_(None),
    ).count()
    tech_created = Ticket.query.filter(
        Ticket.created_at >= start,
        Ticket.created_at <= end,
        Ticket.created_by_user_id.isnot(None),
        Ticket.project_id.is_(None),
    ).count()
    total = (user_created + tech_created) or 1
    return {
        'user_count': user_created,
        'user_percent': round(100.0 * user_created / total, 1),
        'tech_count': tech_created,
        'tech_percent': round(100.0 * tech_created / total, 1),
        'total': user_created + tech_created,
    }


def _inventory_snapshot() -> Dict:
    rows = db.session.query(
        Asset.status,
        func.count(Asset.id).label('cnt'),
    ).filter(
        (Asset.deleted_flag.is_(False)) | (Asset.deleted_flag.is_(None)),
    ).group_by(Asset.status).all()
    counts = {(s or 'available'): int(c) for s, c in rows}
    total = sum(counts.values()) or 1
    statuses = []
    seen = set()
    for key, label in ASSET_STATUS_CHOICES:
        c = counts.get(key, 0)
        statuses.append({
            'key': key,
            'label': label,
            'count': c,
            'percent': round(100.0 * c / total, 1) if total else 0.0,
        })
        seen.add(key)
    other = sum(v for k, v in counts.items() if k not in seen)
    if other:
        statuses.append({'key': 'other', 'label': 'Other', 'count': other,
                         'percent': round(100.0 * other / total, 1)})

    now = datetime.utcnow()
    soon = now + timedelta(days=30)
    warranty_soon = Asset.query.filter(
        Asset.warranty_expires.isnot(None),
        Asset.warranty_expires >= now,
        Asset.warranty_expires <= soon,
        (Asset.deleted_flag.is_(False)) | (Asset.deleted_flag.is_(None)),
    ).count()
    past_eol = Asset.query.filter(
        Asset.eol_date.isnot(None),
        Asset.eol_date < now,
        (Asset.deleted_flag.is_(False)) | (Asset.deleted_flag.is_(None)),
    ).count()
    total_assets = sum(counts.values())
    return {
        'statuses': statuses,
        'total': total_assets,
        'warranty_soon': warranty_soon,
        'past_eol': past_eol,
    }


def _pct_change(current: int, prior: int) -> Optional[float]:
    if prior == 0:
        return None  # undefined — show as "—" or "new"
    return round(100.0 * (current - prior) / prior, 1)


# ---------------------------------------------------------------------------
# Recipient resolution
# ---------------------------------------------------------------------------

def _resolve_recipients(report: Report) -> List[Tuple[str, Optional[str]]]:
    """Return list of (email, name) tuples, deduplicated by lowercase email."""
    out: Dict[str, Optional[str]] = {}

    # User-id list
    try:
        ids = json.loads(report.recipient_user_ids or '[]')
    except Exception:
        ids = []
    if ids:
        for u in User.query.filter(User.id.in_(ids)).all():
            if u.email:
                key = u.email.strip().lower()
                if key and key not in out:
                    out[key] = getattr(u, 'name', None)

    # Free-text emails (comma- or newline-separated)
    raw = (report.recipient_emails or '').replace('\n', ',').replace(';', ',')
    for piece in raw.split(','):
        addr = piece.strip()
        if not addr:
            continue
        key = addr.lower()
        if key and key not in out:
            out[key] = None

    return [(email, name) for email, name in out.items()]


# ---------------------------------------------------------------------------
# Executive report builder
# ---------------------------------------------------------------------------

def _build_executive_data(report: Report, now: datetime) -> Dict:
    sections = _parse_sections(report)
    freq = report.schedule_frequency or 'weekly'
    period_start, period_end, period_label = _previous_period(freq, now)
    prior_start, prior_end = _prior_period(freq, period_start, period_end)
    closed_statuses = _closed_status_names()

    created_curr = _count_created(period_start, period_end)
    created_prev = _count_created(prior_start, prior_end)
    closed_curr = _count_closed(period_start, period_end, closed_statuses)
    closed_prev = _count_closed(prior_start, prior_end, closed_statuses)

    # 4-period trend table (always shown — small)
    trend = []
    for (b_start, b_end, b_label) in _trend_buckets(freq, period_end, count=4):
        trend.append({
            'label': b_label,
            'created': _count_created(b_start, b_end),
            'closed': _count_closed(b_start, b_end, closed_statuses),
        })

    data = {
        'report': report,
        'period_label': period_label,
        'period_start': period_start,
        'period_end': period_end,
        'frequency': freq,
        'frequency_label': {'daily': 'Day', 'weekly': 'Week', 'monthly': 'Month'}.get(freq, 'Period'),
        'created_curr': created_curr,
        'created_prev': created_prev,
        'created_delta_pct': _pct_change(created_curr, created_prev),
        'closed_curr': closed_curr,
        'closed_prev': closed_prev,
        'closed_delta_pct': _pct_change(closed_curr, closed_prev),
        'trend': trend,
        'sections': sections,
        'generated_at': datetime.utcnow(),
    }

    if sections.get('source_breakdown'):
        data['source_breakdown'] = _source_breakdown(period_start, period_end)
    if sections.get('user_vs_tech'):
        data['user_vs_tech'] = _user_vs_tech_split(period_start, period_end)
    if sections.get('inventory_status'):
        data['inventory'] = _inventory_snapshot()

    return data


def _parse_sections(report: Report) -> Dict[str, bool]:
    try:
        raw = json.loads(report.sections or '{}')
        if isinstance(raw, dict):
            merged = dict(DEFAULT_SECTIONS)
            for k, v in raw.items():
                merged[k] = bool(v)
            return merged
    except Exception:
        pass
    return dict(DEFAULT_SECTIONS)


def _build_executive_html(report: Report, now: datetime) -> Tuple[str, str]:
    """Render the executive report HTML + subject line."""
    data = _build_executive_data(report, now)
    html = render_template('emails/report_executive.html', **data)
    subject = f"{report.name} — {data['period_label']}"
    return html, subject


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

def generate_and_send(report: Report, app, manual: bool = False) -> Tuple[bool, int, Optional[str]]:
    """Generate and send a report. Returns (success, recipients_sent, error)."""
    try:
        now = datetime.now()
        if (report.report_type or 'executive') == 'executive':
            html, subject = _build_executive_html(report, now)
        else:
            return False, 0, f"Unknown report_type: {report.report_type}"

        recipients = _resolve_recipients(report)
        if not recipients:
            err = "No recipients configured"
            _record_run(report, manual, 0, False, err)
            return False, 0, err

        sent = 0
        first_err: Optional[str] = None
        for email, name in recipients:
            ok = send_mail(
                to_address=email,
                subject=subject,
                html_body=html,
                to_name=name,
                category='report',
                ticket_id=None,
            )
            if ok:
                sent += 1
            elif first_err is None:
                first_err = f"send_mail failed for {email}"

        success = sent == len(recipients) and sent > 0
        partial = sent > 0 and not success
        status = 'success' if success else ('partial' if partial else 'error')
        report.last_run_at = datetime.utcnow()
        report.last_run_status = status
        db.session.commit()
        _record_run(report, manual, sent, success or partial, first_err)
        return (success or partial), sent, first_err
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        try:
            app.logger.exception("Report generation failed for id=%s: %s", report.id, e)
        except Exception:
            pass
        _record_run(report, manual, 0, False, str(e)[:500])
        return False, 0, str(e)


def _record_run(report: Report, manual: bool, recipients_count: int, success: bool, error: Optional[str]) -> None:
    try:
        rr = ReportRun(
            report_id=report.id,
            run_at=datetime.utcnow(),
            triggered_by='manual' if manual else 'schedule',
            recipients_count=recipients_count,
            success=success,
            error=error,
        )
        db.session.add(rr)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Schedule matching + polling entry point
# ---------------------------------------------------------------------------

def _matches_schedule(report: Report, now: datetime) -> bool:
    """Does the report's schedule fire at the current minute?"""
    if not report.is_active:
        return False
    hhmm = (report.schedule_time or '').strip()
    if not hhmm or ':' not in hhmm:
        return False
    try:
        hh, mm = hhmm.split(':')
        target_h = int(hh)
        target_m = int(mm)
    except Exception:
        return False
    if now.hour != target_h or now.minute != target_m:
        return False

    freq = report.schedule_frequency or 'weekly'
    if freq == 'daily':
        return True
    if freq == 'weekly':
        # 0=Mon .. 6=Sun (python weekday)
        dow = report.schedule_day_of_week
        if dow is None:
            return False
        return now.weekday() == int(dow)
    if freq == 'monthly':
        dom = report.schedule_day_of_month
        if dom is None:
            return False
        return now.day == int(dom)
    return False


def _ran_this_minute(report: Report, now: datetime) -> bool:
    if not report.last_run_at:
        return False
    try:
        return (now - report.last_run_at).total_seconds() < 60
    except Exception:
        return False


def run_due_reports(app) -> None:
    """Polling job — fire any active reports whose schedule matches the current minute."""
    try:
        with app.app_context():
            now = datetime.now()
            reports = Report.query.filter_by(is_active=True).all()
            for r in reports:
                if _matches_schedule(r, now) and not _ran_this_minute(r, now):
                    try:
                        generate_and_send(r, app, manual=False)
                    except Exception as e:
                        try:
                            app.logger.exception("run_due_reports: failed report id=%s: %s", r.id, e)
                        except Exception:
                            pass
    except Exception as e:
        try:
            app.logger.exception("run_due_reports outer failure: %s", e)
        except Exception:
            pass
