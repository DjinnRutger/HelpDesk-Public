"""Automated report generation and email delivery.

Polled every minute by the scheduler. Each active Report row whose schedule matches
the current minute is generated (per report_type) and emailed to its recipients.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional

from flask import render_template
from sqlalchemy import func

from .. import db
from ..models import (
    Report, ReportRun, Ticket, Asset, User, TicketStatus, Contact,
)
from .ms_graph import send_mail


# ---------------------------------------------------------------------------
# Chart palette (used for stacked-bar and per-section bar charts)
# Color-blind friendly, OK-contrast on white. Keep "other" as muted gray.
# ---------------------------------------------------------------------------
CHART_PALETTE: List[str] = [
    '#0d6efd',  # blue
    '#198754',  # green
    '#6f42c1',  # purple
    '#fd7e14',  # orange
    '#20c997',  # teal
    '#d63384',  # pink
    '#6c757d',  # muted gray (also used for "other")
]
PRIORITY_COLORS = {
    'high':   '#dc3545',
    'medium': '#fd7e14',
    'low':    '#0d6efd',
}
AGING_BUCKETS = [
    ('0_7',   '0–7 days',   '#198754'),  # green
    ('8_14',  '8–14 days',  '#fd7e14'),  # orange
    ('15_30', '15–30 days', '#d63384'),  # pink
    ('30p',   '30+ days',   '#dc3545'),  # red
]


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

# Section visibility modes — each section is one of these strings. 'both' is the
# default; 'off' suppresses the block entirely. The template branches on these.
SECTION_MODES = ('off', 'data', 'chart', 'both')
TREND_MODES = ('data', 'chart', 'both')  # trend is core — 'off' is intentionally not offered
ALLOWED_TREND_PERIODS = (4, 8, 13, 26, 52)
DEFAULT_TREND_PERIODS = 4
CHART_TYPES = ('bar', 'pie')   # only applies when section mode is 'chart' or 'both'
MODE_SECTION_KEYS = (
    'source_breakdown', 'user_vs_tech', 'inventory_status',
    'password_expirations', 'sla_resolution', 'backlog_aging', 'tech_workload',
)
# Default chart type per section. Share-style sections default to 'pie' (a
# 100%-stacked horizontal bar with legend), value-style sections default to
# 'bar' (one horizontal bar per item scaled to its count or metric).
DEFAULT_CHART_TYPE: Dict[str, str] = {
    'source_breakdown':     'pie',
    'user_vs_tech':         'pie',
    'inventory_status':     'pie',
    'password_expirations': 'pie',
    'backlog_aging':        'bar',
    'sla_resolution':       'bar',
    'tech_workload':        'bar',
}

DEFAULT_SECTIONS: Dict[str, object] = {
    'source_breakdown':                'both',
    'user_vs_tech':                    'both',
    'inventory_status':                'both',
    'password_expirations':            'both',
    'password_expirations_show_users': True,   # independent boolean sub-option
    'sla_resolution':                  'both',
    'backlog_aging':                   'both',
    'tech_workload':                   'both',
    'trend_mode':                      'both',
    'trend_periods':                   DEFAULT_TREND_PERIODS,
}
# Add the per-section chart-type defaults.
for _k, _v in DEFAULT_CHART_TYPE.items():
    DEFAULT_SECTIONS[_k + '_chart_type'] = _v


def _coerce_mode(raw, default='both', valid=SECTION_MODES):
    """Normalize a raw sections value into one of the valid modes.

    Accepts legacy booleans (True → 'both', False → 'off') so reports created
    in Phase 1/Phase 2 continue to render correctly without a data backfill.
    """
    if isinstance(raw, bool):
        return 'both' if raw else 'off'
    if isinstance(raw, str):
        v = raw.strip().lower()
        if v in valid:
            return v
    return default


def _coerce_trend_periods(raw, default=DEFAULT_TREND_PERIODS):
    try:
        n = int(raw)
    except Exception:
        return default
    return n if n in ALLOWED_TREND_PERIODS else default


def render_pie_png(segments: List[Dict], size: int = 240, attr: str = 'percent') -> Optional[bytes]:
    """Render a pie chart as PNG bytes using Pillow.

    Returns None when Pillow is unavailable or the segments contain no data.
    The image is rendered at `size` pixels (2x the display width for HiDPI).
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    active = [s for s in segments if s.get('count', 0) > 0 and (s.get(attr, 0) or 0) > 0]
    if not active:
        return None
    total = sum(float(s.get(attr, 0) or 0) for s in active) or 1.0
    img = Image.new('RGBA', (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(img)
    bbox = (1, 1, size - 2, size - 2)
    start_angle = -90.0  # 12 o'clock; PIL angles are clockwise from 3 o'clock
    last_end = start_angle
    for s in active:
        pct = float(s.get(attr, 0) or 0)
        sweep = (pct / total) * 360.0
        end_angle = last_end + sweep
        color_hex = (s.get('color') or '#cccccc').lstrip('#')
        try:
            rgb = tuple(int(color_hex[i:i + 2], 16) for i in (0, 2, 4))
        except Exception:
            rgb = (204, 204, 204)
        draw.pieslice(bbox, last_end, end_angle, fill=rgb)
        last_end = end_angle
    import io
    buf = io.BytesIO()
    img.save(buf, format='PNG', optimize=True)
    return buf.getvalue()


def _should_render_pie(sections: Dict, key: str) -> bool:
    """True iff the section key is in chart-or-both mode and configured for pie."""
    return (
        sections.get(key) in ('chart', 'both')
        and sections.get(key + '_chart_type') == 'pie'
    )


def _build_pies(sections: Dict, data: Dict) -> Dict[str, bytes]:
    """Generate PNG bytes for each pie that should render in this report.

    Returns a dict mapping cid keys (e.g. 'pie_source_breakdown') to PNG bytes.
    Used by the email pipeline as inline attachments and by the preview route
    after base64-encoding for browser display.
    """
    pies: Dict[str, bytes] = {}
    if _should_render_pie(sections, 'source_breakdown'):
        png = render_pie_png(data.get('source_breakdown') or [], 240, 'width_pct')
        if png:
            pies['pie_source_breakdown'] = png
    if _should_render_pie(sections, 'user_vs_tech'):
        png = render_pie_png((data.get('user_vs_tech') or {}).get('segments') or [], 240, 'width_pct')
        if png:
            pies['pie_user_vs_tech'] = png
    if _should_render_pie(sections, 'inventory_status'):
        png = render_pie_png((data.get('inventory') or {}).get('statuses') or [], 240, 'width_pct')
        if png:
            pies['pie_inventory_status'] = png
    if _should_render_pie(sections, 'backlog_aging'):
        png = render_pie_png((data.get('backlog') or {}).get('buckets') or [], 240, 'percent')
        if png:
            pies['pie_backlog_buckets'] = png
        png = render_pie_png((data.get('backlog') or {}).get('priorities') or [], 240, 'percent')
        if png:
            pies['pie_backlog_priorities'] = png
    if _should_render_pie(sections, 'sla_resolution'):
        png = render_pie_png(data.get('sla_resolution') or [], 240, 'percent')
        if png:
            pies['pie_sla_resolution'] = png
    if _should_render_pie(sections, 'tech_workload'):
        # Re-shape tech_workload rows into pie segments.
        tw_segs = [
            {'label': t.get('name'), 'count': t.get('open_count', 0),
             'percent': t.get('percent', 0), 'color': t.get('color')}
            for t in (data.get('tech_workload') or [])
        ]
        png = render_pie_png(tw_segs, 240, 'percent')
        if png:
            pies['pie_tech_workload'] = png
    if _should_render_pie(sections, 'password_expirations'):
        segs = (data.get('password_expirations') or {}).get('buckets_segments') or []
        png = render_pie_png(segs, 240, 'width_pct')
        if png:
            pies['pie_password_expirations'] = png
    return pies


def svg_pie(segments: List[Dict], size: int = 120, attr: str = 'percent') -> str:
    """Generate inline SVG pie chart markup from segments.

    Each segment needs: 'count', the share attr (default 'percent'), and 'color'.
    Returns an empty string when there's no data.
    """
    try:
        cx = cy = size / 2
        # Leave 1px room so the stroke isn't clipped.
        r = size / 2 - 1
        total = 0.0
        for s in segments:
            if s.get('count', 0) > 0:
                total += float(s.get(attr, 0) or 0)
        if total <= 0:
            return ''

        paths: List[str] = []
        start = -math.pi / 2  # 12 o'clock
        active = [s for s in segments if s.get('count', 0) > 0 and (s.get(attr, 0) or 0) > 0]

        # Single-slice pie can't use a single arc (start==end). Render as full
        # circle composed of two arcs.
        if len(active) == 1:
            s = active[0]
            d = (
                f"M {cx} {cy - r} "
                f"A {r} {r} 0 1 1 {cx - 0.01:.2f} {cy - r:.2f} Z"
            )
            return (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}"'
                f' viewBox="0 0 {size} {size}" role="img" aria-label="pie chart">'
                f'<path d="{d}" fill="{s["color"]}"/>'
                f'</svg>'
            )

        for s in active:
            pct = float(s.get(attr, 0) or 0)
            sweep = (pct / total) * 2 * math.pi
            end = start + sweep
            x1 = cx + r * math.cos(start)
            y1 = cy + r * math.sin(start)
            x2 = cx + r * math.cos(end)
            y2 = cy + r * math.sin(end)
            large_arc = 1 if sweep > math.pi else 0
            d = (
                f"M {cx} {cy} L {x1:.2f} {y1:.2f} "
                f"A {r} {r} 0 {large_arc} 1 {x2:.2f} {y2:.2f} Z"
            )
            paths.append(f'<path d="{d}" fill="{s["color"]}"/>')
            start = end

        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}"'
            f' viewBox="0 0 {size} {size}" role="img" aria-label="pie chart">'
            + ''.join(paths)
            + '</svg>'
        )
    except Exception:
        return ''


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


def _attach_chart_props(rows: List[Dict], palette: List[str] = CHART_PALETTE) -> List[Dict]:
    """Attach 'color' and 'width_pct' to each row for the stacked_bar/h_bar macros.

    Rows that resolve to 'other' (or are at the end) get the muted gray slot.
    """
    palette_main = palette[:-1] if len(palette) > 1 else palette
    muted = palette[-1] if palette else '#6c757d'
    for i, r in enumerate(rows):
        if r.get('key') == 'other':
            r['color'] = muted
        else:
            r['color'] = palette_main[i % len(palette_main)]
        r['width_pct'] = r.get('percent', 0.0)
    return rows


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
    _attach_chart_props(out)
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
    user_pct = round(100.0 * user_created / total, 1)
    tech_pct = round(100.0 * tech_created / total, 1)
    return {
        'user_count': user_created,
        'user_percent': user_pct,
        'tech_count': tech_created,
        'tech_percent': tech_pct,
        'total': user_created + tech_created,
        # Segments for the stacked-bar chart
        'segments': [
            {'key': 'user', 'label': 'User emailed in', 'count': user_created,
             'percent': user_pct, 'width_pct': user_pct, 'color': '#0d6efd'},
            {'key': 'tech', 'label': 'Tech created', 'count': tech_created,
             'percent': tech_pct, 'width_pct': tech_pct, 'color': '#198754'},
        ],
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
    _attach_chart_props(statuses)

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


def _password_expirations_snapshot(show_users: bool) -> Dict:
    """Summarize AD password expirations from the Contact model.

    Special values per Contact.password_expires_days:
      - None: never checked yet
      - -1:   never expires
      - -999: not found in AD
      - any other negative: already expired (e.g. -3 = 3 days overdue)
      - 0 or positive: days until expiry
    """
    active_filter = (Contact.archived.is_(False)) | (Contact.archived.is_(None))

    expired = Contact.query.filter(
        Contact.password_expires_days.isnot(None),
        Contact.password_expires_days < 0,
        Contact.password_expires_days != -1,
        Contact.password_expires_days != -999,
        active_filter,
    ).count()
    expiring_0_3 = Contact.query.filter(
        Contact.password_expires_days >= 0,
        Contact.password_expires_days <= 3,
        active_filter,
    ).count()
    expiring_4_14 = Contact.query.filter(
        Contact.password_expires_days >= 4,
        Contact.password_expires_days <= 14,
        active_filter,
    ).count()
    never_expires = Contact.query.filter(
        Contact.password_expires_days == -1,
        active_filter,
    ).count()
    not_in_ad = Contact.query.filter(
        Contact.password_expires_days == -999,
        active_filter,
    ).count()

    last_check_row = Contact.query.filter(
        Contact.password_checked_at.isnot(None),
    ).order_by(Contact.password_checked_at.desc()).first()
    last_checked_at = last_check_row.password_checked_at if last_check_row else None

    at_risk_users: List[Dict] = []
    if show_users:
        rows = Contact.query.filter(
            Contact.password_expires_days.isnot(None),
            Contact.password_expires_days <= 14,
            Contact.password_expires_days != -1,
            Contact.password_expires_days != -999,
            active_filter,
            (Contact.ad_disabled.is_(False)) | (Contact.ad_disabled.is_(None)),
        ).order_by(Contact.password_expires_days.asc()).limit(15).all()
        for c in rows:
            days = c.password_expires_days
            if days < 0:
                badge = f"Expired ({-days}d)"
                color = '#dc3545'
            elif days <= 3:
                badge = f"{days}d"
                color = '#dc3545'
            elif days <= 7:
                badge = f"{days}d"
                color = '#fd7e14'
            else:
                badge = f"{days}d"
                color = '#ffc107'
            at_risk_users.append({
                'name': c.name or c.email,
                'email': c.email,
                'days': days,
                'badge': badge,
                'color': color,
            })

    bucket_defs = [
        ('expired',       'Expired',     expired,       '#dc3545'),
        ('expiring_0_3',  '0–3 days',    expiring_0_3,  '#fd7e14'),
        ('expiring_4_14', '4–14 days',   expiring_4_14, '#ffc107'),
        ('never_expires', 'Never',       never_expires, '#6c757d'),
    ]
    total_buckets = sum(c for _, _, c, _ in bucket_defs) or 1
    buckets_segments = []
    for key, label, count, color in bucket_defs:
        pct = round(100.0 * count / total_buckets, 1) if total_buckets else 0.0
        buckets_segments.append({
            'key': key, 'label': label, 'count': count,
            'percent': pct, 'width_pct': pct, 'color': color,
        })

    return {
        'expired': expired,
        'expiring_0_3': expiring_0_3,
        'expiring_4_14': expiring_4_14,
        'never_expires': never_expires,
        'not_in_ad': not_in_ad,
        'at_risk_users': at_risk_users,
        'show_users': show_users,
        'last_checked_at': last_checked_at,
        'configured': last_checked_at is not None,
        'buckets_segments': buckets_segments,
    }


def _format_duration(seconds: float) -> str:
    if seconds is None or seconds <= 0:
        return '—'
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    if days >= 1:
        return f"{days}d {hours}h"
    if hours >= 1:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _sla_resolution_by_priority(start: datetime, end: datetime, closed_statuses: List[str]) -> List[Dict]:
    """For tickets closed in [start,end], avg (closed_at - created_at) grouped by priority."""
    rows = Ticket.query.filter(
        Ticket.status.in_(closed_statuses),
        Ticket.closed_at.isnot(None),
        Ticket.closed_at >= start,
        Ticket.closed_at <= end,
        Ticket.project_id.is_(None),
    ).all()
    buckets: Dict[str, List[float]] = {'high': [], 'medium': [], 'low': []}
    for t in rows:
        try:
            seconds = (t.closed_at - t.created_at).total_seconds()
        except Exception:
            continue
        if seconds < 0:
            continue
        pri = (t.priority or 'medium').lower()
        if pri not in buckets:
            buckets[pri] = []
        buckets[pri].append(seconds)
    out: List[Dict] = []
    total_count = sum(len(v) for v in buckets.values()) or 1
    for key in ('high', 'medium', 'low'):
        secs = buckets.get(key, [])
        avg_s = sum(secs) / len(secs) if secs else 0
        pct = round(100.0 * len(secs) / total_count, 1) if total_count else 0.0
        out.append({
            'key': key,
            'label': key.capitalize(),
            'count': len(secs),
            'avg_seconds': avg_s,
            'avg_display': _format_duration(avg_s) if secs else '—',
            'percent': pct,   # share of closed tickets for pie variant
            'color': PRIORITY_COLORS.get(key, '#6c757d'),
        })
    return out


def _backlog_and_priority_snapshot(closed_statuses: List[str]) -> Dict:
    """Open-ticket aging + open-by-priority + oldest-open callout.

    "Open" mirrors dashboard.py:37-41: not closed, project_id is null, not currently snoozed.
    """
    now = datetime.utcnow()
    base_filter = [
        ~Ticket.status.in_(closed_statuses),
        Ticket.project_id.is_(None),
        ((Ticket.snoozed_until.is_(None)) | (Ticket.snoozed_until <= now)),
    ]

    # Cumulative counts (avoids gap/overlap bugs at bucket boundaries).
    def _count_at_most(days: int) -> int:
        return Ticket.query.filter(
            *base_filter,
            Ticket.created_at >= now - timedelta(days=days),
        ).count()

    total_open = Ticket.query.filter(*base_filter).count()
    le_7 = _count_at_most(7)
    le_14 = _count_at_most(14)
    le_30 = _count_at_most(30)
    bucket_counts = {
        '0_7':   le_7,
        '8_14':  max(0, le_14 - le_7),
        '15_30': max(0, le_30 - le_14),
        '30p':   max(0, total_open - le_30),
    }
    max_val = max(bucket_counts.values()) if bucket_counts else 0
    bucket_total = total_open or 1
    buckets_out = []
    for key, label, color in AGING_BUCKETS:
        v = bucket_counts.get(key, 0)
        pct = round(100.0 * v / bucket_total, 1) if total_open else 0.0
        buckets_out.append({
            'key': key,
            'label': label,
            'count': v,
            'width_pct': round(100.0 * v / max_val, 1) if max_val else 0.0,
            'percent': pct,   # share-of-total for pie variant
            'color': color,
        })

    # Open-by-priority
    priority_rows = db.session.query(
        Ticket.priority,
        func.count(Ticket.id).label('cnt'),
    ).filter(*base_filter).group_by(Ticket.priority).all()
    pri_counts = {(p or 'medium'): int(c) for p, c in priority_rows}
    pri_max = max(pri_counts.values()) if pri_counts else 0
    pri_total = sum(pri_counts.values()) or 1
    priorities = []
    for key in ('high', 'medium', 'low'):
        v = pri_counts.get(key, 0)
        pct = round(100.0 * v / pri_total, 1) if pri_total else 0.0
        priorities.append({
            'key': key,
            'label': key.capitalize(),
            'count': v,
            'width_pct': round(100.0 * v / pri_max, 1) if pri_max else 0.0,
            'percent': pct,   # share-of-total for pie variant
            'color': PRIORITY_COLORS.get(key, '#6c757d'),
        })

    oldest = Ticket.query.filter(*base_filter).order_by(Ticket.created_at.asc()).first()
    oldest_info = None
    if oldest:
        age_days = max(0, int((now - oldest.created_at).total_seconds() / 86400))
        oldest_info = {
            'id': oldest.id,
            'subject': oldest.subject,
            'created_at': oldest.created_at,
            'age_days': age_days,
        }

    return {
        'buckets': buckets_out,
        'total_open': total_open,
        'priorities': priorities,
        'oldest': oldest_info,
    }


def _tech_workload(period_start: datetime, period_end: datetime, closed_statuses: List[str]) -> List[Dict]:
    """Per-tech: currently-open count + tickets closed in period."""
    now = datetime.utcnow()
    techs = User.query.filter(
        User.role.in_(['admin', 'tech']),
        User.is_active.is_(True),
    ).all()
    rows: List[Dict] = []
    for u in techs:
        open_count = Ticket.query.filter(
            ((Ticket.assignee_id == u.id) | (Ticket.co_assignee_id == u.id)),
            ~Ticket.status.in_(closed_statuses),
            Ticket.project_id.is_(None),
            ((Ticket.snoozed_until.is_(None)) | (Ticket.snoozed_until <= now)),
        ).count()
        closed_count = Ticket.query.filter(
            ((Ticket.assignee_id == u.id) | (Ticket.co_assignee_id == u.id)),
            Ticket.status.in_(closed_statuses),
            Ticket.closed_at.isnot(None),
            Ticket.closed_at >= period_start,
            Ticket.closed_at <= period_end,
            Ticket.project_id.is_(None),
        ).count()
        if open_count == 0 and closed_count == 0:
            continue
        rows.append({
            'id': u.id,
            'name': u.name or u.email,
            'open_count': open_count,
            'closed_count': closed_count,
        })
    rows.sort(key=lambda r: (-r['open_count'], -r['closed_count'], r['name']))
    rows = rows[:10]
    # Bar variant: scale bars to the max across both metrics so the two bars
    # per row are comparable across rows.
    max_val = max((max(r['open_count'], r['closed_count']) for r in rows), default=0)
    # Pie variant: share-of-open across the top techs (capped to a palette).
    total_open = sum(r['open_count'] for r in rows) or 1
    palette = CHART_PALETTE
    for i, r in enumerate(rows):
        r['open_pct'] = round(100.0 * r['open_count'] / max_val, 1) if max_val else 0.0
        r['closed_pct'] = round(100.0 * r['closed_count'] / max_val, 1) if max_val else 0.0
        # Share of open tickets across the listed techs (sum may be <100 if
        # other inactive techs exist, but that's fine for pie purposes).
        r['percent'] = round(100.0 * r['open_count'] / total_open, 1) if total_open else 0.0
        r['color'] = palette[i % len(palette)]
    return rows


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

    # Multi-period trend (window length configurable via sections.trend_periods)
    trend_periods = _coerce_trend_periods(sections.get('trend_periods', DEFAULT_TREND_PERIODS))
    trend = []
    for (b_start, b_end, b_label) in _trend_buckets(freq, period_end, count=trend_periods):
        trend.append({
            'label': b_label,
            'created': _count_created(b_start, b_end),
            'closed': _count_closed(b_start, b_end, closed_statuses),
        })
    trend_max = max((max(t['created'], t['closed']) for t in trend), default=0)
    for t in trend:
        t['created_pct'] = round(100.0 * t['created'] / trend_max, 1) if trend_max else 0.0
        t['closed_pct'] = round(100.0 * t['closed'] / trend_max, 1) if trend_max else 0.0

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
        'trend_periods': trend_periods,
        'sections': sections,
        'generated_at': datetime.utcnow(),
    }

    if sections.get('source_breakdown') != 'off':
        data['source_breakdown'] = _source_breakdown(period_start, period_end)
    if sections.get('user_vs_tech') != 'off':
        data['user_vs_tech'] = _user_vs_tech_split(period_start, period_end)
    if sections.get('inventory_status') != 'off':
        data['inventory'] = _inventory_snapshot()
    if sections.get('password_expirations') != 'off':
        data['password_expirations'] = _password_expirations_snapshot(
            show_users=bool(sections.get('password_expirations_show_users', True))
        )
    if sections.get('sla_resolution') != 'off':
        data['sla_resolution'] = _sla_resolution_by_priority(period_start, period_end, closed_statuses)
    if sections.get('backlog_aging') != 'off':
        data['backlog'] = _backlog_and_priority_snapshot(closed_statuses)
    if sections.get('tech_workload') != 'off':
        data['tech_workload'] = _tech_workload(period_start, period_end, closed_statuses)

    return data


def _parse_sections(report: Report) -> Dict[str, object]:
    """Return a normalized sections dict.

    - Each MODE_SECTION_KEY is coerced to one of SECTION_MODES (defaults to 'both').
    - Legacy booleans are accepted (True→'both', False→'off') so Phase 1/Phase 2
      reports continue to render correctly without DB migration.
    - 'password_expirations_show_users' stays a bool.
    - Unknown keys (including obsolete 'show_charts') are dropped silently.
    """
    out: Dict[str, object] = dict(DEFAULT_SECTIONS)
    raw_obj = None
    try:
        raw_obj = json.loads(report.sections or '{}')
    except Exception:
        raw_obj = None
    if not isinstance(raw_obj, dict):
        return out

    for key in MODE_SECTION_KEYS:
        if key in raw_obj:
            out[key] = _coerce_mode(raw_obj[key], default=out[key])
        # chart type sub-option
        ct_key = key + '_chart_type'
        if ct_key in raw_obj:
            raw_ct = raw_obj[ct_key]
            if isinstance(raw_ct, str) and raw_ct.strip().lower() in CHART_TYPES:
                out[ct_key] = raw_ct.strip().lower()

    if 'password_expirations_show_users' in raw_obj:
        out['password_expirations_show_users'] = bool(raw_obj['password_expirations_show_users'])

    if 'trend_mode' in raw_obj:
        out['trend_mode'] = _coerce_mode(raw_obj['trend_mode'], default='both', valid=TREND_MODES)
    if 'trend_periods' in raw_obj:
        out['trend_periods'] = _coerce_trend_periods(raw_obj['trend_periods'])

    return out


def _build_executive_html(report: Report, now: datetime) -> Tuple[str, str, Dict[str, bytes]]:
    """Render the executive report HTML + subject line + pie PNGs.

    Returns (html, subject, pies). `pies` is a dict mapping CID keys to PNG
    bytes for inline (cid:) attachments. Email senders attach them as inline
    parts; the preview route base64-encodes them into data: URIs.
    """
    data = _build_executive_data(report, now)
    sections = data.get('sections') or {}
    pies = _build_pies(sections, data)
    data['pies'] = pies
    html = render_template('emails/report_executive.html', **data)
    subject = f"{report.name} — {data['period_label']}"
    return html, subject, pies


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------

def generate_and_send(report: Report, app, manual: bool = False) -> Tuple[bool, int, Optional[str]]:
    """Generate and send a report. Returns (success, recipients_sent, error)."""
    try:
        now = datetime.now()
        if (report.report_type or 'executive') == 'executive':
            html, subject, pies = _build_executive_html(report, now)
        else:
            return False, 0, f"Unknown report_type: {report.report_type}"

        recipients = _resolve_recipients(report)
        if not recipients:
            err = "No recipients configured"
            _record_run(report, manual, 0, False, err)
            return False, 0, err

        # Build inline (CID) attachments for the pie PNGs.
        import base64 as _base64
        inline_attachments = [
            {
                'name': f'{cid}.png',
                'contentType': 'image/png',
                'contentBytes': _base64.b64encode(png).decode('ascii'),
                'contentId': cid,
                'isInline': True,
            }
            for cid, png in (pies or {}).items()
        ]

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
                attachments=inline_attachments or None,
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
