from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file, current_app, jsonify, make_response, session
from flask_login import login_required, current_user
from ...forms import MSGraphForm, TechForm, ProcessTemplateForm, ProcessTemplateItemForm, AllowedDomainForm, DenyFilterForm, ClientApiForm
from ...models import Setting, User, Role, ProcessTemplate, ProcessTemplateItem, AllowedDomain, DenyFilter, Vendor, PurchaseOrder, Company, ShippingLocation, DocumentCategory, AssetAudit, Asset, AssetCategory, AssetManufacturer, AssetCondition, AssetLocation, ScheduledTicket, Ticket, TicketTask, TicketStatus, Tag, Report, ReportRun, ApiToken
from ... import db
from ...permissions import (
    MODULES, LEVEL_CHOICES, VIEW, EDIT,
    has_permission, is_administrator,
)
from ...utils.security import hash_password
from ...services.email_poll import poll_ms_graph
from ...services.ms_graph import get_msal_app, get_access_token
import sqlite3
import io
import tempfile
import shutil
import zipfile
from datetime import datetime
import os
import requests
import ftplib

from . import admin_bp, admin_required, _bump_schedule_version  # noqa: F401
import json as _json
import re as _re


_REPORT_TYPE_CHOICES = [('executive', 'Executive Report')]


_REPORT_FREQ_CHOICES = [('daily', 'Daily'), ('weekly', 'Weekly'), ('monthly', 'Monthly')]


_DAY_OF_WEEK_CHOICES = [
    (0, 'Monday'), (1, 'Tuesday'), (2, 'Wednesday'), (3, 'Thursday'),
    (4, 'Friday'), (5, 'Saturday'), (6, 'Sunday'),
]


_EMAIL_RE = _re.compile(r'^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$')


def _parse_report_form():
    """Parse and validate the report form. Returns (data, error_str). data is None if error."""
    name = (request.form.get('name') or '').strip()
    if not name:
        return None, 'Report name is required'

    report_type = (request.form.get('report_type') or 'executive').strip()
    if report_type not in {k for k, _ in _REPORT_TYPE_CHOICES}:
        return None, 'Invalid report type'

    frequency = (request.form.get('schedule_frequency') or 'weekly').strip()
    if frequency not in {k for k, _ in _REPORT_FREQ_CHOICES}:
        return None, 'Invalid schedule frequency'

    schedule_time = (request.form.get('schedule_time') or '').strip()
    if not _re.match(r'^\d{1,2}:\d{2}$', schedule_time):
        return None, 'Time must be in HH:MM format'
    hh, mm = schedule_time.split(':')
    try:
        hh_i = int(hh); mm_i = int(mm)
        if not (0 <= hh_i <= 23 and 0 <= mm_i <= 59):
            raise ValueError
    except Exception:
        return None, 'Time must be a valid HH:MM value'
    schedule_time = f'{hh_i:02d}:{mm_i:02d}'

    day_of_week = None
    day_of_month = None
    if frequency == 'weekly':
        dow_raw = request.form.get('schedule_day_of_week', '')
        if dow_raw == '':
            return None, 'Day of week is required for weekly reports'
        try:
            day_of_week = int(dow_raw)
            if not (0 <= day_of_week <= 6):
                raise ValueError
        except Exception:
            return None, 'Invalid day of week'
    elif frequency == 'monthly':
        dom_raw = request.form.get('schedule_day_of_month', '')
        if dom_raw == '':
            return None, 'Day of month is required for monthly reports'
        try:
            day_of_month = int(dom_raw)
            if not (1 <= day_of_month <= 28):
                raise ValueError
        except Exception:
            return None, 'Day of month must be 1–28'

    # Recipients
    user_ids = request.form.getlist('recipient_user_ids')
    try:
        user_ids = [int(x) for x in user_ids if str(x).strip()]
    except Exception:
        user_ids = []

    emails_raw = (request.form.get('recipient_emails') or '').strip()
    cleaned_emails = []
    if emails_raw:
        for piece in emails_raw.replace('\n', ',').replace(';', ',').split(','):
            addr = piece.strip()
            if not addr:
                continue
            if not _EMAIL_RE.match(addr):
                return None, f'Invalid email address: {addr}'
            cleaned_emails.append(addr)

    if not user_ids and not cleaned_emails:
        return None, 'At least one recipient (user or email) is required'

    # Sections (executive-specific)
    _VALID_MODES = {'off', 'data', 'chart', 'both'}
    _VALID_TREND_MODES = {'data', 'chart', 'both'}
    _ALLOWED_TREND_PERIODS = {4, 8, 13, 26, 52}
    _VALID_CHART_TYPES = {'bar', 'pie'}
    # Default chart type per section (must match generator's DEFAULT_CHART_TYPE).
    _DEFAULT_CHART_TYPE = {
        'source_breakdown':     'pie',
        'user_vs_tech':         'pie',
        'inventory_status':     'pie',
        'password_expirations': 'pie',
        'backlog_aging':        'bar',
        'sla_resolution':       'bar',
        'tech_workload':        'bar',
    }
    def _mode(field):
        v = (request.form.get(field) or 'both').strip().lower()
        return v if v in _VALID_MODES else 'both'
    def _trend_mode(field):
        v = (request.form.get(field) or 'both').strip().lower()
        return v if v in _VALID_TREND_MODES else 'both'
    def _trend_periods(field):
        try:
            n = int(request.form.get(field) or 4)
        except Exception:
            n = 4
        return n if n in _ALLOWED_TREND_PERIODS else 4
    def _chart_type(key):
        field = f'section_{key}_chart_type'
        default = _DEFAULT_CHART_TYPE.get(key, 'bar')
        v = (request.form.get(field) or default).strip().lower()
        return v if v in _VALID_CHART_TYPES else default
    sections = {
        'source_breakdown':                _mode('section_source_breakdown_mode'),
        'user_vs_tech':                    _mode('section_user_vs_tech_mode'),
        'inventory_status':                _mode('section_inventory_status_mode'),
        'password_expirations':            _mode('section_password_expirations_mode'),
        'password_expirations_show_users': request.form.get('section_password_expirations_show_users') == 'on',
        'sla_resolution':                  _mode('section_sla_resolution_mode'),
        'backlog_aging':                   _mode('section_backlog_aging_mode'),
        'tech_workload':                   _mode('section_tech_workload_mode'),
        'trend_mode':                      _trend_mode('trend_mode'),
        'trend_periods':                   _trend_periods('trend_periods'),
    }
    # Chart type per section (only meaningful when the section's mode includes a chart).
    for _k in _DEFAULT_CHART_TYPE.keys():
        sections[_k + '_chart_type'] = _chart_type(_k)

    return {
        'name': name,
        'description': (request.form.get('description') or '').strip() or None,
        'report_type': report_type,
        'is_active': request.form.get('is_active') == 'on',
        'schedule_frequency': frequency,
        'schedule_time': schedule_time,
        'schedule_day_of_week': day_of_week,
        'schedule_day_of_month': day_of_month,
        'recipient_user_ids': _json.dumps(user_ids),
        'recipient_emails': ', '.join(cleaned_emails) if cleaned_emails else None,
        'sections': _json.dumps(sections),
    }, None


def _all_techs():
    """Users that can receive internal report emails."""
    return User.query.filter(User.is_active.is_(True)).order_by(User.name.asc()).all()


@admin_bp.route('/reports')
@login_required
def reports():
    reports = Report.query.order_by(Report.name.asc()).all()
    return render_template('admin/reports.html', reports=reports,
                           freq_labels={k: v for k, v in _REPORT_FREQ_CHOICES},
                           dow_labels={k: v for k, v in _DAY_OF_WEEK_CHOICES})


@admin_bp.route('/reports/new', methods=['GET', 'POST'])
@login_required
def report_new():
    if request.method == 'POST':
        data, err = _parse_report_form()
        if err:
            flash(err, 'danger')
            return render_template('admin/report_form.html', action='New', report=None,
                                   users=_all_techs(),
                                   type_choices=_REPORT_TYPE_CHOICES,
                                   freq_choices=_REPORT_FREQ_CHOICES,
                                   dow_choices=_DAY_OF_WEEK_CHOICES,
                                   form=request.form)
        try:
            r = Report(**data)
            db.session.add(r)
            db.session.commit()
            flash('Report created', 'success')
            return redirect(url_for('admin.reports'))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {e}', 'danger')
            return redirect(url_for('admin.report_new'))

    return render_template('admin/report_form.html', action='New', report=None,
                           users=_all_techs(),
                           type_choices=_REPORT_TYPE_CHOICES,
                           freq_choices=_REPORT_FREQ_CHOICES,
                           dow_choices=_DAY_OF_WEEK_CHOICES,
                           form=None)


@admin_bp.route('/reports/<int:report_id>/edit', methods=['GET', 'POST'])
@login_required
def report_edit(report_id):
    r = Report.query.get_or_404(report_id)
    if request.method == 'POST':
        data, err = _parse_report_form()
        if err:
            flash(err, 'danger')
            return render_template('admin/report_form.html', action='Edit', report=r,
                                   users=_all_techs(),
                                   type_choices=_REPORT_TYPE_CHOICES,
                                   freq_choices=_REPORT_FREQ_CHOICES,
                                   dow_choices=_DAY_OF_WEEK_CHOICES,
                                   form=request.form)
        try:
            for k, v in data.items():
                setattr(r, k, v)
            db.session.commit()
            # Refresh so the redirected GET sees the values we just wrote.
            db.session.refresh(r)
            flash('Report saved', 'success')
            # Stay on edit and pass ?saved=1 so the template can render an
            # inline confirmation that does not depend on Flask's flash
            # cookies (which can be silently dropped behind some proxies).
            return redirect(url_for('admin.report_edit', report_id=r.id, saved=1))
        except Exception as e:
            db.session.rollback()
            flash(f'Error: {e}', 'danger')
            return redirect(url_for('admin.report_edit', report_id=r.id))

    resp = make_response(render_template('admin/report_form.html', action='Edit', report=r,
                                         users=_all_techs(),
                                         type_choices=_REPORT_TYPE_CHOICES,
                                         freq_choices=_REPORT_FREQ_CHOICES,
                                         dow_choices=_DAY_OF_WEEK_CHOICES,
                                         form=None))
    # Edit form must always reflect the latest saved state, never a cached copy.
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@admin_bp.route('/reports/<int:report_id>/delete', methods=['POST'])
@login_required
def report_delete(report_id):
    r = Report.query.get_or_404(report_id)
    try:
        # Manually clear children — SQLite FK ON DELETE CASCADE isn't enforced
        # unless PRAGMA foreign_keys=ON is set per-connection. The ORM cascade
        # on Report.runs handles this when we go through db.session.delete().
        db.session.delete(r)
        db.session.commit()
        flash('Report deleted', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'danger')
    return redirect(url_for('admin.reports'))


@admin_bp.route('/reports/<int:report_id>/toggle', methods=['POST'])
@login_required
def report_toggle(report_id):
    r = Report.query.get_or_404(report_id)
    try:
        r.is_active = not bool(r.is_active)
        db.session.commit()
        flash(f"Report '{r.name}' {'enabled' if r.is_active else 'disabled'}", 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error: {e}', 'danger')
    return redirect(url_for('admin.reports'))


@admin_bp.route('/reports/<int:report_id>/run-now', methods=['POST'])
@login_required
def report_run_now(report_id):
    r = Report.query.get_or_404(report_id)
    from ...services.report_generator import generate_and_send
    ok, sent, err = generate_and_send(r, current_app, manual=True)
    if ok:
        flash(f"Report sent to {sent} recipient(s)", 'success')
    else:
        flash(f"Report send failed: {err or 'see logs'}", 'danger')
    return redirect(url_for('admin.reports'))


@admin_bp.route('/reports/<int:report_id>/preview')
@login_required
def report_preview(report_id):
    """Render the report HTML in the browser without sending.

    The email pipeline embeds pie charts as inline (cid:) attachments. The
    browser preview swaps each cid: reference for a data: URI so the PNGs
    render directly without any attachment plumbing.
    """
    r = Report.query.get_or_404(report_id)
    from ...services.report_generator import _build_executive_html
    import base64 as _b64
    html, _subject, pies = _build_executive_html(r, datetime.now())
    for cid, png in (pies or {}).items():
        b64 = _b64.b64encode(png).decode('ascii')
        data_uri = f'data:image/png;base64,{b64}'
        html = html.replace(f'src="cid:{cid}"', f'src="{data_uri}"')
    return html
