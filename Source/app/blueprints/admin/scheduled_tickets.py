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


@admin_bp.route('/scheduled')
@login_required
def scheduled_list():
    rows = ScheduledTicket.query.order_by(ScheduledTicket.created_at.desc()).all()
    return render_template('admin/scheduled_list.html', rows=rows)


@admin_bp.route('/scheduled/new', methods=['GET','POST'])
@login_required
def scheduled_new():
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        subject = (request.form.get('subject') or '').strip()
        if not name or not subject:
            if is_ajax:
                return jsonify({'success': False, 'error': 'Name and Subject are required.'}), 400
            flash('Name and Subject are required.', 'danger')
            return redirect(url_for('admin.scheduled_new'))
        # Default schedule_time to midnight if not provided
        sched_time = (request.form.get('schedule_time') or '').strip() or '00:00'
        row = ScheduledTicket(
            name=name,
            subject=subject,
            body=(request.form.get('body') or None),
            status=(request.form.get('status') or 'open'),
            priority=(request.form.get('priority') or 'medium'),
            assignee_id=(int(request.form.get('assignee_id')) if (request.form.get('assignee_id') or '').isdigit() else None),
            tasks_text=(request.form.get('tasks_text') or None),
            schedule_type=(request.form.get('schedule_type') or 'daily'),
            day_of_week=(int(request.form.get('day_of_week')) if (request.form.get('day_of_week') or '').isdigit() else None),
            day_of_month=(int(request.form.get('day_of_month')) if (request.form.get('day_of_month') or '').isdigit() else None),
            schedule_time=sched_time,
            active=bool(request.form.get('active')),
        )
        try:
            db.session.add(row)
            db.session.commit()
            if is_ajax:
                return jsonify({'success': True, 'id': row.id})
            flash('Scheduled ticket created.', 'success')
            return redirect(url_for('admin.scheduled_list'))
        except Exception as e:
            db.session.rollback()
            if is_ajax:
                return jsonify({'success': False, 'error': str(e)}), 500
            flash(f'Error creating scheduled ticket: {str(e)}', 'danger')
            return redirect(url_for('admin.scheduled_new'))
    techs = User.query.order_by(User.name.asc()).all()
    ticket_statuses = TicketStatus.query.order_by(TicketStatus.position).all()
    return render_template('admin/scheduled_form.html', action='New', row=None, techs=techs, ticket_statuses=ticket_statuses)


@admin_bp.route('/scheduled/<int:row_id>/edit', methods=['GET','POST'])
@login_required
def scheduled_edit(row_id):
    row = ScheduledTicket.query.get_or_404(row_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()
        subject = (request.form.get('subject') or '').strip()
        if not name or not subject:
            if is_ajax:
                return jsonify({'success': False, 'error': 'Name and Subject are required.'}), 400
            flash('Name and Subject are required.', 'danger')
            return redirect(url_for('admin.scheduled_edit', row_id=row.id))
        row.name = name
        row.subject = subject
        row.body = (request.form.get('body') or None)
        row.status = (request.form.get('status') or 'open')
        row.priority = (request.form.get('priority') or 'medium')
        row.assignee_id = (int(request.form.get('assignee_id')) if (request.form.get('assignee_id') or '').isdigit() else None)
        row.tasks_text = (request.form.get('tasks_text') or None)
        row.schedule_type = (request.form.get('schedule_type') or 'daily')
        row.day_of_week = (int(request.form.get('day_of_week')) if (request.form.get('day_of_week') or '').isdigit() else None)
        row.day_of_month = (int(request.form.get('day_of_month')) if (request.form.get('day_of_month') or '').isdigit() else None)
        row.schedule_time = (request.form.get('schedule_time') or None)
        row.active = bool(request.form.get('active'))
        try:
            db.session.commit()
            if is_ajax:
                return jsonify({'success': True})
            flash('Scheduled ticket updated.', 'success')
            return redirect(url_for('admin.scheduled_list'))
        except Exception as e:
            db.session.rollback()
            if is_ajax:
                return jsonify({'success': False, 'error': str(e)}), 500
            flash(f'Error updating scheduled ticket: {str(e)}', 'danger')
            return redirect(url_for('admin.scheduled_edit', row_id=row.id))
    techs = User.query.order_by(User.name.asc()).all()
    ticket_statuses = TicketStatus.query.order_by(TicketStatus.position).all()
    return render_template('admin/scheduled_form.html', action='Edit', row=row, techs=techs, ticket_statuses=ticket_statuses)


@admin_bp.route('/scheduled/<int:row_id>/delete', methods=['POST'])
@login_required
def scheduled_delete(row_id):
    row = ScheduledTicket.query.get_or_404(row_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    
    try:
        db.session.delete(row)
        db.session.commit()
        
        if is_ajax:
            return jsonify({'success': True})
        
        flash('Scheduled ticket deleted.', 'success')
        return redirect(url_for('admin.scheduled_list'))
    except Exception as e:
        db.session.rollback()
        if is_ajax:
            return jsonify({'success': False, 'error': str(e)})
        flash(f'Error deleting scheduled ticket: {str(e)}', 'danger')
        return redirect(url_for('admin.scheduled_list'))


def _create_ticket_from_schedule(row: ScheduledTicket):
    t = Ticket(
        subject=row.subject,
        body=row.body,
        status=row.status or 'new',
        priority=row.priority or 'medium',
        assignee_id=row.assignee_id,
        source='scheduled'
    )
    db.session.add(t)
    db.session.flush()
    # Add tasks
    if row.tasks_text:
        for line in [ln.strip() for ln in row.tasks_text.splitlines() if ln.strip()]:
            db.session.add(TicketTask(ticket_id=t.id, label=line))
    db.session.commit()
    return t


@admin_bp.route('/scheduled/<int:row_id>/run_now', methods=['POST'])
@login_required
def scheduled_run_now(row_id):
    row = ScheduledTicket.query.get_or_404(row_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes.accept_json
    
    try:
        t = _create_ticket_from_schedule(row)
        row.last_run_at = datetime.utcnow()
        db.session.commit()
        
        if is_ajax:
            return jsonify({'success': True, 'ticket_id': t.id})
        
        flash(f"Ticket #{t.id} created from schedule.", 'success')
        return redirect(url_for('admin.scheduled_list'))
    except Exception as e:
        db.session.rollback()
        if is_ajax:
            return jsonify({'success': False, 'error': str(e)})
        flash(f'Error running scheduled ticket: {str(e)}', 'danger')
        return redirect(url_for('admin.scheduled_list'))


@admin_bp.route('/scheduled-data')
@login_required
def scheduled_data():
    """Return scheduled tickets as JSON for AJAX loading"""
    scheduled = ScheduledTicket.query.order_by(ScheduledTicket.name.asc()).all()
    return jsonify([{
        'id': s.id,
        'name': s.name,
        'subject': s.subject,
        'body': s.body,
        'status': s.status,
        'priority': s.priority,
        'assignee_id': s.assignee_id,
        'assignee_name': s.assignee.name if s.assignee else None,
        'tasks_text': s.tasks_text,
        'schedule_type': s.schedule_type,
        'day_of_week': s.day_of_week,
        'day_of_month': s.day_of_month,
        'schedule_time': s.schedule_time,
        'active': s.active,
        'is_active': s.active,
        'last_run': s.last_run_at.strftime('%Y-%m-%d %H:%M:%S') if s.last_run_at else None
    } for s in scheduled])
