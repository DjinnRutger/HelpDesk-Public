from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file, abort, current_app
from flask_login import login_required
from ..models import Ticket, ProcessTemplate, TicketProcess, TicketProcessItem, Project, TicketTask, OrderItem, ApprovalRequest
from .. import db
from ..forms import TicketForm, NoteForm, TicketUpdateForm, ProcessAssignForm, TaskAssignForm
from ..models import User, Contact, TicketNote
from ..models import Asset
from flask_login import current_user
from datetime import datetime, timedelta
from sqlalchemy.orm import joinedload
import bleach
from sqlalchemy import or_


tickets_bp = Blueprint('tickets', __name__, url_prefix='/tickets')


@tickets_bp.route('/')
@login_required
def list_tickets():
    # Filters: status=open|all, assigned=any, q=search
    status = request.args.get('status', 'open')
    show_snoozed = request.args.get('show_snoozed', '0') == '1'
    # If user provided the 'assigned' parameter (even empty), respect it; otherwise use profile default
    if 'assigned' in request.args:
        assigned = request.args.get('assigned', '')
    else:
        try:
            assigned = getattr(current_user, 'tickets_view_pref', 'any')
        except Exception:
            assigned = 'any'
    q = request.args.get('q')

    me_id = getattr(current_user, 'id', -1)
    base = Ticket.query.options(joinedload(Ticket.assignee), joinedload(Ticket.project))
    # If searching (q provided) include ALL tickets (project + non-project) so project tickets are discoverable.
    # Otherwise (no search) keep existing behavior: hide project tickets unless assigned to current user.
    if q:
        query = base
    else:
        query = base.filter((Ticket.project_id.is_(None)) | ((Ticket.project_id.isnot(None)) & (Ticket.assignee_id == me_id)))

    if status == 'open':
        query = query.filter(Ticket.status != 'closed')
    # Hide snoozed by default unless toggled
    if not show_snoozed:
        query = query.filter((Ticket.snoozed_until.is_(None)) | (Ticket.snoozed_until <= datetime.utcnow()))
    if assigned == 'me':
        query = query.filter(Ticket.assignee_id == me_id)
    elif assigned == 'me_or_unassigned':
        query = query.filter((Ticket.assignee_id == me_id) | (Ticket.assignee_id.is_(None)))
    elif assigned == 'any':
        # Any = include all tickets regardless of assignment (assigned to any tech OR unassigned)
        pass
    if q:
        like = f"%{q}%"
        # Include legacy requester field (may contain email) in search
        query = query.filter(
            (Ticket.subject.ilike(like)) |
            (Ticket.body.ilike(like)) |
            (Ticket.requester_name.ilike(like)) |
            (Ticket.requester_email.ilike(like)) |
            (Ticket.requester.ilike(like))
        )
    tickets = query.order_by(Ticket.created_at.desc()).limit(200).all()
    # Snoozed count for toggle badge
    snoozed_count = Ticket.query.filter(
        (Ticket.status != 'closed') & (Ticket.snoozed_until.isnot(None)) & (Ticket.snoozed_until > datetime.utcnow())
    ).count()

    # Items: only tickets
    items = [{'kind': 'ticket', 'obj': t, 'created_at': t.created_at} for t in tickets]
    items.sort(key=lambda x: x['created_at'] or 0, reverse=True)
    return render_template('tickets/list.html', items=items, status=status, assigned=assigned, q=q, show_snoozed=show_snoozed, snoozed_count=snoozed_count)


@tickets_bp.route('/<int:ticket_id>/snooze', methods=['POST'])
@login_required
def snooze_ticket(ticket_id):
    t = Ticket.query.get_or_404(ticket_id)
    # Expect date as YYYY-MM-DD
    date_str = (request.form.get('snooze_until') or '').strip()
    if not date_str:
        flash('Please select a snooze date.', 'warning')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        # Snooze until end of selected day for clarity
        dt = dt + timedelta(hours=23, minutes=59, seconds=59)
    except ValueError:
        flash('Invalid snooze date.', 'danger')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    t.snoozed_until = dt
    db.session.commit()
    flash('Ticket snoozed.', 'success')
    return redirect(url_for('tickets.show_ticket', ticket_id=t.id))


@tickets_bp.route('/<int:ticket_id>/unsnooze', methods=['POST'])
@login_required
def unsnooze_ticket(ticket_id):
    t = Ticket.query.get_or_404(ticket_id)
    t.snoozed_until = None
    db.session.commit()
    flash('Ticket unsnoozed.', 'success')
    return redirect(url_for('tickets.show_ticket', ticket_id=t.id))


@tickets_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_ticket():
    form = TicketForm()
    # Prefill requester (email) if provided via query string on initial GET
    if request.method == 'GET':
        pre_req = (request.args.get('requester') or '').strip().lower()
        contact_id_raw = request.args.get('contact_id')
        if not pre_req and contact_id_raw:
            try:
                cid = int(contact_id_raw)
            except Exception:
                cid = None
            if cid:
                from ..models import Contact
                c = Contact.query.get(cid)
                if c and c.email:
                    pre_req = c.email.lower()
        if pre_req and not form.requester.data:
            form.requester.data = pre_req
    # Populate asset choices dynamically each request (after potential prefill)
    from ..models import Asset, Contact
    contact_for_assets = None
    # Determine contact via explicit contact_id param first
    contact_id_raw = request.args.get('contact_id') if request.method == 'GET' else None
    if contact_id_raw:
        try:
            cid = int(contact_id_raw)
            contact_for_assets = Contact.query.get(cid)
        except Exception:
            contact_for_assets = None
    # If not from param, infer from requester email already filled on form
    if not contact_for_assets and form.requester.data:
        contact_for_assets = Contact.query.filter_by(email=form.requester.data.strip().lower()).first()
    assets = []
    if contact_for_assets:
        assets = Asset.query.filter_by(assigned_contact_id=contact_for_assets.id).order_by(Asset.name.asc()).all()
    # Build choices (0 = none)
    form.asset_id.choices = [(0, '— None —')] + [(a.id, f"{a.name}#{a.asset_tag or ''}".rstrip('#')) for a in assets]

    if form.validate_on_submit():
        # Upsert contact if requester provided
        c = None
        if form.requester.data:
            email = form.requester.data.strip().lower()
            c = Contact.query.filter_by(email=email).first()
            if not c:
                c = Contact(email=email)
                db.session.add(c)
            # We don't have a separate requester name on manual form; use Contact name if present
        t = Ticket(
            subject=form.subject.data,
            requester=form.requester.data,
            requester_email=form.requester.data,
            requester_name=(c.name if c and c.name else None),
            body=form.body.data,
            status=form.status.data,
            priority=form.priority.data or 'medium',
            source=form.source.data or 'manual',
            created_by_user_id=getattr(current_user, 'id', None),
            asset_id=(form.asset_id.data if form.asset_id.data else None) if form.asset_id.data != 0 else None
        )
        db.session.add(t)
        db.session.commit()
        flash('Ticket created', 'success')
        return redirect(url_for('tickets.list_tickets'))
    elif request.method == 'POST':
        # Form did not validate; surface errors so user can see issue.
        err_text = '; '.join([f"{field}: {', '.join(errs)}" for field, errs in form.errors.items()]) or 'Unknown validation error'
        flash(f'Ticket not saved: {err_text}', 'danger')
    # Provide contacts for requester autocomplete
    contacts = Contact.query.order_by(Contact.name.asc()).limit(500).all()
    return render_template('tickets/new.html', form=form, contacts=contacts)


@tickets_bp.route('/assets_for_requester')
@login_required
def assets_for_requester():
    """Return JSON list of assets assigned to a contact (by requester email)."""
    email = (request.args.get('email') or '').strip().lower()
    if not email:
        return jsonify({'assets': []})
    from ..models import Contact, Asset
    c = Contact.query.filter_by(email=email).first()
    if not c:
        return jsonify({'assets': []})
    assets = Asset.query.filter_by(assigned_contact_id=c.id).order_by(Asset.name.asc()).all()
    data = [
        {
            'id': a.id,
            'label': f"{a.name}{('#' + a.asset_tag) if a.asset_tag else ''}"
        } for a in assets
    ]
    return jsonify({'assets': data})


@tickets_bp.route('/<int:ticket_id>', methods=['GET', 'POST'])
@login_required
def show_ticket(ticket_id):
    t = Ticket.query.get_or_404(ticket_id)
    # Find associated contact by email (preferred)
    contact = None
    try:
        if t.requester_email:
            contact = Contact.query.filter_by(email=t.requester_email.lower()).first()
        if not contact and t.requester_name:
            contact = Contact.query.filter_by(name=t.requester_name).first()
    except Exception:
        contact = None
    note_form = NoteForm()
    update_form = TicketUpdateForm()
    assign_form = ProcessAssignForm()
    task_form = TaskAssignForm()
    # NEW: Merge to Project form
    from ..forms import MergeToProjectForm
    merge_form = MergeToProjectForm()
    # Populate assignee choices
    techs = User.query.order_by(User.name.asc()).all()
    update_form.assignee_id.choices = [(0, 'Unassigned')] + [(u.id, u.name) for u in techs]
    task_form.assigned_tech_id.choices = [(0, 'Unassigned')] + [(u.id, u.name) for u in techs]
    # Populate project choices (open projects only)
    open_projects = Project.query.filter(Project.status != 'closed').order_by(Project.created_at.desc()).all()
    merge_form.project_id.choices = [(p.id, p.name) for p in open_projects]

    # Handle quick requester change (from modal)
    if request.method == 'POST' and request.form.get('quick_change_requester') == '1':
        new_req = (request.form.get('requester_email') or '').strip().lower()
        if new_req:
            # Upsert contact
            c = Contact.query.filter_by(email=new_req).first()
            if not c:
                c = Contact(email=new_req)
                db.session.add(c)
                db.session.flush()
            # Update ticket snapshot fields so UI reflects the change consistently
            t.requester_email = new_req
            # Prefer the contact's name if available; otherwise clear to fall back to email
            t.requester_name = (c.name or None)
            # Keep legacy requester in sync with email for backwards-compatibility
            t.requester = new_req
            db.session.commit()
            flash('Requester updated', 'success')
        else:
            flash('Requester email required', 'warning')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))

    # Handle updates
    # Enforce that ticket cannot be closed unless all checkbox items on attached processes are checked
    if update_form.submit_update.data and update_form.validate_on_submit():
        prev_assignee_id = t.assignee_id
        new_status = update_form.status.data
        if new_status == 'closed':
            # any unchecked checkbox?
            incomplete = False
            for tp in t.processes:
                for it in tp.items:
                    if it.type == 'checkbox' and not it.checked:
                        incomplete = True
                        break
                if incomplete:
                    break
            # any unchecked ticket tasks?
            if not incomplete:
                for task in t.tasks or []:
                    if not task.checked:
                        incomplete = True
                        break
            if incomplete:
                flash('Cannot close ticket. All process and task checklist items must be completed.', 'warning')
                return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
        t.status = new_status
        # Maintain closed_at timestamp when status changes
        if new_status == 'closed':
            t.closed_at = datetime.utcnow()
        else:
            t.closed_at = None
        t.priority = update_form.priority.data
        t.source = update_form.source.data or t.source
        t.assignee_id = None if update_form.assignee_id.data == 0 else update_form.assignee_id.data
        db.session.commit()
        # Notify assigned tech if assignment changed and the current user is not the assignee
        try:
            if t.assignee_id and t.assignee_id != prev_assignee_id and (not current_user or t.assignee_id != getattr(current_user, 'id', None)):
                new_assignee = User.query.get(t.assignee_id)
                if new_assignee and new_assignee.email:
                    from ..services.ms_graph import send_mail
                    subject = f"New Ticket Assigned: #{t.id}"
                    link = url_for('tickets.show_ticket', ticket_id=t.id, _external=True)
                    requester = t.requester_name or t.requester_email or t.requester or 'Unknown'
                    html = f"""
                        <p>Hi {new_assignee.name},</p>
                        <p>A ticket has been assigned to you.</p>
                        <p><strong>Ticket #{t.id}</strong>: {t.subject}</p>
                        <p><strong>From:</strong> {requester}</p>
                        <p><a href="{link}">View ticket</a></p>
                    """
                    send_mail(new_assignee.email, subject, html, to_name=new_assignee.name)
        except Exception:
            pass
        flash('Ticket updated', 'success')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))

    # Handle notes
    if note_form.validate_on_submit():
        close_after = bool(request.form.get('add_note_close'))
        # Determine privacy flag from form (default True)
        try:
            is_private_flag = bool(note_form.private.data)
        except Exception:
            is_private_flag = True
        # Sanitize note content to allow safe rich text
        raw_content = note_form.content.data or ''
        allowed_tags = [
            'p', 'br', 'div', 'span', 'b', 'strong', 'i', 'em', 'u', 'ul', 'ol', 'li',
            'h3', 'h4', 'h5', 'h6', 'a', 'table', 'thead', 'tbody', 'tr', 'th', 'td'
        ]
        allowed_attrs = {
            'a': ['href', 'title', 'target', 'rel'],
            'td': ['colspan', 'rowspan'],
            'th': ['colspan', 'rowspan']
        }
        sanitized_html = bleach.clean(
            raw_content,
            tags=allowed_tags,
            attributes=allowed_attrs,
            protocols=['http', 'https', 'mailto'],
            strip=True
        )
        # Ensure links open in a new tab with rel safety
        def _set_target_rel(attrs, new=False):
            href = attrs.get('href')
            if href:
                attrs['target'] = '_blank'
                rel = attrs.get('rel', '') or ''
                rel_vals = set(rel.split()) if rel else set()
                rel_vals.update(['noopener', 'noreferrer'])
                attrs['rel'] = ' '.join(sorted(rel_vals))
            return attrs
        sanitized_html = bleach.linkify(sanitized_html, callbacks=[_set_target_rel])

        note = TicketNote(
            ticket_id=t.id,
            author_id=getattr(current_user, 'id', None),
            content=sanitized_html,
            is_private=is_private_flag,
        )
        db.session.add(note)
        db.session.commit()
        # If note is public (Private unchecked), email the requester
        if not is_private_flag:
            try:
                to_email = (t.requester_email or t.requester)
                if to_email:
                    from ..services.ms_graph import send_mail
                    subject = f"Ticket#{t.id} - {t.subject}"
                    body_html = note.content or ''
                    send_mail(to_email, subject, body_html)
            except Exception:
                pass
        # Close ticket if requested (and allowed)
        if close_after and t.status != 'closed':
            # Re-use existing close validation: ensure all checklist items/tasks complete
            incomplete = False
            for tp in t.processes:
                for it in tp.items:
                    if it.type == 'checkbox' and not it.checked:
                        incomplete = True
                        break
                if incomplete:
                    break
            if not incomplete:
                for task in t.tasks or []:
                    if not task.checked:
                        incomplete = True
                        break
            if incomplete:
                flash('Note added, but ticket not closed (open checklist items/tasks remain).', 'warning')
                return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
            t.status = 'closed'
            t.closed_at = datetime.utcnow()
            db.session.commit()
            flash('Note added and ticket closed', 'success')
            return redirect(url_for('dashboard.index'))
        flash('Note added', 'success')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))

    # Handle merge to project
    if getattr(merge_form, 'submit_merge', None) and merge_form.submit_merge.data and merge_form.validate_on_submit():
        # if already in a project, just redirect there
        if t.project_id:
            flash('Ticket is already part of a project.', 'info')
            return redirect(url_for('projects.show_project', project_id=t.project_id))
        project = Project.query.get(merge_form.project_id.data)
        if not project or project.status == 'closed':
            flash('Invalid project selection.', 'danger')
            return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
        from sqlalchemy import func
        max_pos = (
            db.session.query(func.coalesce(func.max(Ticket.project_position), 0))
            .filter(Ticket.project_id == project.id)
            .scalar()
            or 0
        )
        t.project_id = project.id
        t.project_position = max_pos + 1
        db.session.commit()
        flash('Ticket merged into project.', 'success')
        return redirect(url_for('projects.show_project', project_id=project.id))

    # Show oldest notes at the top, newest at the bottom
    from sqlalchemy.orm import joinedload
    notes = (
        t.notes.options(joinedload(TicketNote.author))
        .order_by(TicketNote.created_at.asc())
        .all()
    )
    tasks = TicketTask.query.filter_by(ticket_id=t.id).order_by(TicketTask.position.asc(), TicketTask.id.asc()).all()
    # Group tasks by list_name (None grouped under empty string key)
    tasks_by_list = {}
    for tk in tasks:
        key = tk.list_name or ''
        tasks_by_list.setdefault(key, []).append(tk)
    # Assign form setup
    templates = ProcessTemplate.query.order_by(ProcessTemplate.name.asc()).all()
    assign_form.template_id.choices = [(pt.id, pt.name) for pt in templates]
    # Handle assign process
    if assign_form.submit_assign.data and assign_form.validate_on_submit():
        if t.status == 'closed':
            flash('Cannot assign a process to a closed ticket.', 'warning')
            return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
        pt = ProcessTemplate.query.get(assign_form.template_id.data)
        if pt:
            tp = TicketProcess(ticket_id=t.id, template_id=pt.id, name=pt.name)
            db.session.add(tp)
            db.session.flush()
            # copy items
            pos = 1
            for pit in pt.items:
                db.session.add(TicketProcessItem(
                    ticket_process_id=tp.id,
                    type=pit.type,
                    label=pit.label,
                    assigned_tech_id=pit.assigned_tech_id,
                    position=pos,
                ))
                pos += 1
            db.session.commit()
            flash('Process assigned to ticket', 'success')
            return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    # Handle create tasks
    if task_form.submit_tasks.data and task_form.validate_on_submit():
        if t.status == 'closed':
            flash('Cannot add tasks to a closed ticket.', 'warning')
            return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
        base_pos = (TicketTask.query.filter_by(ticket_id=t.id).count() or 0) + 1
        assign_to = task_form.assigned_tech_id.data or 0
        list_name = (task_form.list_name.data or '').strip() or None
        for idx, raw in enumerate((task_form.tasks_text.data or '').splitlines(), start=0):
            label = (raw or '').strip()
            if not label:
                continue
            tt = TicketTask(
                ticket_id=t.id,
                list_name=list_name,
                label=label,
                assigned_tech_id=(None if assign_to == 0 else assign_to),
                position=base_pos + idx,
            )
            db.session.add(tt)
        db.session.commit()
        flash('Tasks created', 'success')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    # Initialize update_form defaults
    update_form.status.data = t.status
    update_form.priority.data = t.priority
    update_form.source.data = (t.source or 'email')
    update_form.assignee_id.data = t.assignee_id or 0
    order_items = OrderItem.query.filter_by(ticket_id=t.id).options(joinedload(OrderItem.purchase_order)).order_by(OrderItem.created_at.desc()).all()
    # Assets assigned to this contact (for asset linking widget)
    contact_assets = []
    if contact:
        try:
            contact_assets = Asset.query.filter_by(assigned_contact_id=contact.id).order_by(Asset.name.asc()).all()
        except Exception:
            contact_assets = []
    # Contacts for requester autocomplete (limit to 500 for performance)
    contacts = Contact.query.order_by(Contact.name.asc()).limit(500).all()
    return render_template('tickets/detail.html', t=t, notes=notes, tasks=tasks, tasks_by_list=tasks_by_list, order_items=order_items, note_form=note_form, update_form=update_form, assign_form=assign_form, task_form=task_form, contact=contact, techs=techs, merge_form=merge_form, contact_assets=contact_assets, contacts=contacts)


@tickets_bp.route('/<int:ticket_id>/attachments/<path:filename>')
@login_required
def download_attachment(ticket_id, filename):
    # Authorize basic access: must be logged in; additional checks could verify assignment or roles
    from ..models import TicketAttachment, Setting
    att = TicketAttachment.query.filter_by(ticket_id=ticket_id, filename=filename).first()
    if not att:
        return abort(404)
    # Determine storage base
    base = (Setting.get('ATTACHMENTS_BASE', 'instance') or 'instance').strip().lower()
    subdir = (Setting.get('ATTACHMENTS_DIR_REL', 'attachments') or 'attachments').strip()
    subdir = subdir.replace('\\','/').lstrip('/') or 'attachments'
    root = current_app.static_folder if base == 'static' else current_app.instance_path
    import os
    full_path = os.path.join(root, subdir, str(ticket_id), filename)
    if not os.path.isfile(full_path):
        return abort(404)
    # Let send_file infer content type; set as_attachment False for inline viewing
    return send_file(full_path, as_attachment=False, download_name=att.filename)


@tickets_bp.route('/api/recipient_search')
@login_required
def recipient_search():
    q = (request.args.get('q') or '').strip()
    results = []
    if q:
        like = f"%{q}%"
        # Search Contacts and Users by name or email
        contacts = Contact.query.filter(or_(Contact.name.ilike(like), Contact.email.ilike(like))).order_by(Contact.name.asc()).limit(10).all()
        users = User.query.filter(or_(User.name.ilike(like), User.email.ilike(like))).order_by(User.name.asc()).limit(10).all()
        seen = set()
        for c in contacts:
            if not c.email:
                continue
            key = c.email.lower()
            if key in seen:
                continue
            seen.add(key)
            results.append({ 'type': 'contact', 'id': c.id, 'name': c.name or c.email, 'email': c.email })
        for u in users:
            if not u.email:
                continue
            key = u.email.lower()
            if key in seen:
                continue
            seen.add(key)
            results.append({ 'type': 'user', 'id': u.id, 'name': u.name or u.email, 'email': u.email })
    return {'items': results[:20]}


@tickets_bp.route('/<int:ticket_id>/api/approval_data')
@login_required
def get_approval_data(ticket_id):
    """API endpoint to get current approval data for the modal (contact, manager, order items)."""
    t = Ticket.query.get_or_404(ticket_id)
    
    # Find the requester contact
    contact = None
    if t.requester_email:
        contact = Contact.query.filter_by(email=t.requester_email.lower()).first()
    
    # Get order items
    order_items = OrderItem.query.filter_by(ticket_id=t.id).order_by(OrderItem.created_at.desc()).all()
    
    result = {
        'contact': None,
        'manager': None,
        'order_items': [],
        'can_submit': False
    }
    
    if contact:
        result['contact'] = {
            'id': contact.id,
            'name': contact.name or 'Not specified',
            'email': contact.email
        }
        
        if contact.manager:
            result['manager'] = {
                'id': contact.manager.id,
                'name': contact.manager.name or contact.manager.email,
                'email': contact.manager.email
            }
            result['can_submit'] = True
    
    total_cost = 0
    for item in order_items:
        item_cost = (item.est_unit_cost or 0) * item.quantity
        total_cost += item_cost
        result['order_items'].append({
            'id': item.id,
            'description': item.description,
            'quantity': item.quantity,
            'vendor': item.target_vendor or '—',
            'unit_cost': item.est_unit_cost,
            'total_cost': item_cost
        })
    
    result['total_cost'] = total_cost
    
    return jsonify(result)


@tickets_bp.route('/<int:ticket_id>/forward_note', methods=['POST'])
@login_required
def forward_note(ticket_id):
    t = Ticket.query.get_or_404(ticket_id)
    to_email = (request.form.get('email') or '').strip()
    body_html = request.form.get('body_html') or ''
    if not to_email:
        flash('Email is required to forward.', 'danger')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    # Sanitize the provided HTML similar to notes
    allowed_tags = [
        'p', 'br', 'div', 'span', 'b', 'strong', 'i', 'em', 'u', 'ul', 'ol', 'li',
        'h3', 'h4', 'h5', 'h6', 'a', 'table', 'thead', 'tbody', 'tr', 'th', 'td'
    ]
    allowed_attrs = {
        'a': ['href', 'title', 'target', 'rel'],
        'td': ['colspan', 'rowspan'],
        'th': ['colspan', 'rowspan']
    }
    sanitized = bleach.clean(
        body_html,
        tags=allowed_tags,
        attributes=allowed_attrs,
        protocols=['http', 'https', 'mailto'],
        strip=True
    )
    def _set_target_rel(attrs, new=False):
        href = attrs.get('href')
        if href:
            attrs['target'] = '_blank'
            rel = attrs.get('rel', '') or ''
            rel_vals = set(rel.split()) if rel else set()
            rel_vals.update(['noopener', 'noreferrer'])
            attrs['rel'] = ' '.join(sorted(rel_vals))
        return attrs
    sanitized = bleach.linkify(sanitized, callbacks=[_set_target_rel])
    # Compose email
    try:
        from ..services.ms_graph import send_mail
        requester = t.requester_name or t.requester_email or t.requester or 'Unknown'
        header = f"""
            <div>
              <div><strong>Ticket #{t.id}</strong>: {bleach.clean(t.subject or '')}</div>
              <div><strong>From:</strong> {bleach.clean(requester)}</div>
            </div>
            <hr>
        """
        # Sanitize ticket description body and include after the note
        raw_desc = t.body or ''
        desc_clean = bleach.clean(
            raw_desc,
            tags=allowed_tags,
            attributes=allowed_attrs,
            protocols=['http', 'https', 'mailto'],
            strip=True
        )
        desc_clean = bleach.linkify(desc_clean, callbacks=[_set_target_rel])
        if not desc_clean:
            desc_section = '<div class="text-muted">(no description)</div>'
        else:
            desc_section = f'<div><div><strong>Description</strong></div><div>{desc_clean}</div></div>'
        html = header + (sanitized or '<p>(no note body)</p>') + '<hr>' + desc_section
        subject = f"FW: Ticket #{t.id} - {t.subject or ''}"
        send_mail(to_email, subject, html)
        # Save forwarded note to history with recipient log (always public)
        try:
            log_html = f"<div class=\"small text-muted\">Forwarded to: {bleach.clean(to_email)}</div>" + (sanitized or '')
            note = TicketNote(
                ticket_id=t.id,
                author_id=getattr(current_user, 'id', None),
                content=log_html,
                is_private=False,
            )
            db.session.add(note)
            db.session.commit()
        except Exception:
            db.session.rollback()
        flash('Note forwarded.', 'success')
    except Exception:
        flash('Failed to forward note.', 'danger')
    return redirect(url_for('tickets.show_ticket', ticket_id=t.id))


@tickets_bp.route('/<int:ticket_id>/notes/<int:note_id>/edit', methods=['POST'])
@login_required
def edit_note(ticket_id, note_id):
    """Update an existing TicketNote's content and privacy.
    Permissions: author or admin may edit. Received notes (no author) editable by admin only.
    """
    t = Ticket.query.get_or_404(ticket_id)
    note = TicketNote.query.filter_by(id=note_id, ticket_id=t.id).first_or_404()
    # Permission check
    role = getattr(current_user, 'role', 'tech') or 'tech'
    user_id = getattr(current_user, 'id', None)
    if not ((note.author_id and note.author_id == user_id) or (role == 'admin')):
        flash('You do not have permission to edit this note.', 'danger')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id, _anchor='notes'))
    # Sanitize incoming HTML similar to add note
    raw_content = (request.form.get('content') or '').strip()
    allowed_tags = [
        'p', 'br', 'div', 'span', 'b', 'strong', 'i', 'em', 'u', 'ul', 'ol', 'li',
        'h3', 'h4', 'h5', 'h6', 'a', 'table', 'thead', 'tbody', 'tr', 'th', 'td'
    ]
    allowed_attrs = {
        'a': ['href', 'title', 'target', 'rel'],
        'td': ['colspan', 'rowspan'],
        'th': ['colspan', 'rowspan']
    }
    sanitized_html = bleach.clean(
        raw_content,
        tags=allowed_tags,
        attributes=allowed_attrs,
        protocols=['http', 'https', 'mailto'],
        strip=True
    )
    def _set_target_rel(attrs, new=False):
        href = attrs.get('href')
        if href:
            attrs['target'] = '_blank'
            rel = attrs.get('rel', '') or ''
            rel_vals = set(rel.split()) if rel else set()
            rel_vals.update(['noopener', 'noreferrer'])
            attrs['rel'] = ' '.join(sorted(rel_vals))
        return attrs
    sanitized_html = bleach.linkify(sanitized_html, callbacks=[_set_target_rel])
    is_private_flag = True
    try:
        is_private_flag = bool(request.form.get('private'))
    except Exception:
        is_private_flag = True
    note.content = sanitized_html
    note.is_private = is_private_flag
    db.session.commit()
    flash('Note updated.', 'success')
    return redirect(url_for('tickets.show_ticket', ticket_id=t.id, _anchor='notes'))


@tickets_bp.route('/<int:ticket_id>/tasks/<int:task_id>/toggle', methods=['POST'])
@login_required
def toggle_task(ticket_id, task_id):
    t = Ticket.query.get_or_404(ticket_id)
    task = TicketTask.query.get_or_404(task_id)
    if task.ticket_id != t.id:
        flash('Not found', 'danger')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    if t.status == 'closed':
        flash('Ticket is closed; tasks cannot be modified.', 'warning')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    now_checked = not bool(task.checked)
    task.checked = now_checked
    if now_checked:
        task.checked_by_user_id = getattr(current_user, 'id', None)
        task.checked_at = datetime.utcnow()
    else:
        task.checked_by_user_id = None
        task.checked_at = None
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return ('', 204)
    flash('Task updated', 'success')
    return redirect(url_for('tickets.show_ticket', ticket_id=t.id))


@tickets_bp.route('/<int:ticket_id>/tasks/<int:task_id>/edit', methods=['POST'])
@login_required
def edit_task(ticket_id, task_id):
    t = Ticket.query.get_or_404(ticket_id)
    task = TicketTask.query.get_or_404(task_id)
    if task.ticket_id != t.id:
        flash('Not found', 'danger')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    if t.status == 'closed':
        flash('Ticket is closed; tasks cannot be modified.', 'warning')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    label = (request.form.get('label') or '').strip()
    list_name = (request.form.get('list_name') or '').strip() or None
    assigned_raw = request.form.get('assigned_tech_id')
    try:
        assigned_val = int(assigned_raw) if assigned_raw is not None else 0
    except Exception:
        assigned_val = 0
    if label:
        task.label = label
    task.list_name = list_name
    task.assigned_tech_id = None if assigned_val == 0 else assigned_val
    db.session.commit()
    flash('Task updated', 'success')
    return redirect(url_for('tickets.show_ticket', ticket_id=t.id))


@tickets_bp.route('/<int:ticket_id>/tasks/<int:task_id>/delete', methods=['POST'])
@login_required
def delete_task(ticket_id, task_id):
    t = Ticket.query.get_or_404(ticket_id)
    task = TicketTask.query.get_or_404(task_id)
    if task.ticket_id != t.id:
        flash('Not found', 'danger')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    if t.status == 'closed':
        flash('Ticket is closed; tasks cannot be deleted.', 'warning')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    db.session.delete(task)
    db.session.commit()
    flash('Task deleted', 'success')
    return redirect(url_for('tickets.show_ticket', ticket_id=t.id))


@tickets_bp.route('/<int:ticket_id>/tasks/delete_all', methods=['POST'])
@login_required
def delete_all_tasks(ticket_id):
    t = Ticket.query.get_or_404(ticket_id)
    if t.status == 'closed':
        flash('Ticket is closed; tasks cannot be deleted.', 'warning')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    # Bulk delete all tasks for this ticket
    TicketTask.query.filter_by(ticket_id=t.id).delete()
    db.session.commit()
    flash('All tasks deleted', 'success')
    return redirect(url_for('tickets.show_ticket', ticket_id=t.id))


@tickets_bp.route('/<int:ticket_id>/processes/<int:tp_id>/items/<int:item_id>', methods=['POST'])
@login_required
def update_process_item(ticket_id, tp_id, item_id):
    t = Ticket.query.get_or_404(ticket_id)
    if t.status == 'closed':
        flash('Ticket is closed; process items cannot be modified.', 'warning')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    item = TicketProcessItem.query.get_or_404(item_id)
    if item.ticket_process_id != tp_id:
        flash('Not found', 'danger')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    # Update based on type
    if item.type == 'checkbox':
        now_checked = bool(request.form.get('checked'))
        item.checked = now_checked
        if now_checked:
            item.checked_by_user_id = getattr(current_user, 'id', None)
            item.checked_at = datetime.utcnow()
        else:
            # If unchecked, clear audit
            item.checked_by_user_id = None
            item.checked_at = None
    else:
        item.text_value = request.form.get('text_value', '')
    db.session.commit()
    # If this is an AJAX request, return 204 to prevent page reload/scroll
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return ('', 204)
    flash('Updated', 'success')
    return redirect(url_for('tickets.show_ticket', ticket_id=t.id))


@tickets_bp.route('/<int:ticket_id>/processes/<int:tp_id>/delete', methods=['POST'])
@login_required
def delete_process(ticket_id, tp_id):
    t = Ticket.query.get_or_404(ticket_id)
    if t.status == 'closed':
        flash('Ticket is closed; processes cannot be deleted.', 'warning')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    tp = TicketProcess.query.get_or_404(tp_id)
    if tp.ticket_id != t.id:
        flash('Not found', 'danger')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    # Deleting the TicketProcess will cascade delete its items
    db.session.delete(tp)
    db.session.commit()
    flash('Process removed from ticket.', 'success')
    return redirect(url_for('tickets.show_ticket', ticket_id=t.id))


@tickets_bp.route('/<int:ticket_id>/delete', methods=['POST'])
@login_required
def delete_ticket(ticket_id):
    t = Ticket.query.get_or_404(ticket_id)
    # Validation: must have no notes, no order items, no assignee
    notes_count = t.notes.count() if hasattr(t.notes, 'count') else len(t.notes or [])
    order_items_count = t.order_items.count() if hasattr(t.order_items, 'count') else len(t.order_items or [])
    has_tasks = bool(t.tasks)
    if notes_count > 0 or order_items_count > 0 or t.assignee_id is not None:
        flash('Ticket cannot be deleted (must have no notes, no order items, and no assignee). Close it instead.', 'danger')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    # Hard delete (cascades remove tasks, processes, etc.)
    # Also remove attachment files from disk if present
    try:
        # Attempt to remove attachments directory: base/subdir/<ticket_id>
        try:
            from ..models import Setting
            import os, shutil
            subdir = (Setting.get('ATTACHMENTS_DIR_REL', 'attachments') or 'attachments').strip()
            subdir = subdir.replace('\\','/').lstrip('/') or 'attachments'
            base = (Setting.get('ATTACHMENTS_BASE', 'instance') or 'instance').strip().lower()
            root = current_app.static_folder if base == 'static' else current_app.instance_path
            ticket_dir = os.path.join(root, subdir, str(t.id))
            if os.path.isdir(ticket_dir):
                shutil.rmtree(ticket_dir, ignore_errors=True)
        except Exception:
            # Do not block deletion if filesystem cleanup fails
            pass
        db.session.delete(t)
        db.session.commit()
        flash('Ticket deleted', 'success')
        return redirect(url_for('tickets.list_tickets'))
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting ticket: {e}', 'danger')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))


@tickets_bp.route('/<int:ticket_id>/processes/<int:tp_id>/edit', methods=['POST'])
@login_required
def edit_process(ticket_id, tp_id):
    t = Ticket.query.get_or_404(ticket_id)
    if t.status == 'closed':
        flash('Ticket is closed; processes cannot be edited.', 'warning')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    tp = TicketProcess.query.get_or_404(tp_id)
    if tp.ticket_id != t.id:
        flash('Not found', 'danger')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    # Iterate over existing items and update fields from form data
    type_choices = {'checkbox', 'text'}
    for it in tp.items:
        label = (request.form.get(f'label_item_{it.id}') or '').strip()
        type_val = (request.form.get(f'type_item_{it.id}') or it.type).strip().lower()
        assigned_raw = request.form.get(f'assigned_item_{it.id}')
        try:
            assigned_val = int(assigned_raw) if assigned_raw is not None else None
        except Exception:
            assigned_val = None
        if label:
            it.label = label
        if type_val in type_choices:
            it.type = type_val
        it.assigned_tech_id = None if not assigned_val or assigned_val == 0 else assigned_val
    db.session.commit()
    flash('Process updated.', 'success')
    return redirect(url_for('tickets.show_ticket', ticket_id=t.id))


@tickets_bp.route('/<int:ticket_id>/processes/<int:tp_id>/items/<int:item_id>/delete_line', methods=['POST'])
@login_required
def delete_process_line(ticket_id, tp_id, item_id):
    t = Ticket.query.get_or_404(ticket_id)
    if t.status == 'closed':
        flash('Ticket is closed; process items cannot be deleted.', 'warning')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    tp = TicketProcess.query.get_or_404(tp_id)
    if tp.ticket_id != t.id:
        flash('Not found', 'danger')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    item = TicketProcessItem.query.get_or_404(item_id)
    if item.ticket_process_id != tp.id:
        flash('Not found', 'danger')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    db.session.delete(item)
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return ('', 204)
    flash('Line item deleted.', 'success')
    return redirect(url_for('tickets.show_ticket', ticket_id=t.id))


@tickets_bp.route('/<int:ticket_id>/assign_asset', methods=['POST'])
@login_required
def assign_asset(ticket_id):
    t = Ticket.query.get_or_404(ticket_id)
    if t.asset_id:
        flash('Asset already linked.', 'info')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    asset_id_raw = request.form.get('asset_id') or ''
    if not asset_id_raw:
        flash('Select an asset.', 'warning')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    try:
        aid = int(asset_id_raw)
    except Exception:
        flash('Invalid asset.', 'danger')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    a = Asset.query.get(aid)
    if not a:
        flash('Asset not found.', 'danger')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    # Validate contact match if possible
    if a.assigned_contact_id:
        # Match by requester email -> contact
        if t.requester_email:
            c = Contact.query.filter_by(email=t.requester_email.lower()).first()
            if c and c.id != a.assigned_contact_id:
                flash('Asset is not assigned to this requester.', 'danger')
                return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    t.asset_id = a.id
    db.session.commit()
    flash('Asset linked to ticket.', 'success')
    return redirect(url_for('tickets.show_ticket', ticket_id=t.id))


@tickets_bp.route('/<int:ticket_id>/request_approval', methods=['POST'])
@login_required
def request_approval(ticket_id):
    """Send an approval request to the requester's manager for order items."""
    from ..services.ms_graph import send_mail
    
    t = Ticket.query.get_or_404(ticket_id)
    
    # Get form data
    approval_note = (request.form.get('approval_note') or '').strip()
    
    # Find the requester contact
    contact = None
    if t.requester_email:
        contact = Contact.query.filter_by(email=t.requester_email.lower()).first()
    
    if not contact:
        flash('No contact found for this ticket requester.', 'danger')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    
    if not contact.manager_id:
        flash('No manager assigned to this contact. Please assign a manager first.', 'danger')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    
    manager = Contact.query.get(contact.manager_id)
    if not manager or not manager.email:
        flash('Manager has no email address configured.', 'danger')
        return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
    
    # Get order items for this ticket
    order_items = OrderItem.query.filter_by(ticket_id=t.id).all()
    
    # Create the approval request record
    approval = ApprovalRequest(
        ticket_id=t.id,
        requester_contact_id=contact.id,
        manager_contact_id=manager.id,
        requesting_tech_id=current_user.id,
        status='pending',
        request_note=approval_note,
    )
    db.session.add(approval)
    db.session.flush()  # Get the ID
    
    # Add a note to the ticket about the approval request
    note_content = f"<p><strong>Approval Request Sent</strong></p>"
    note_content += f"<p>Sent to: {manager.name or manager.email} ({manager.email})</p>"
    if approval_note:
        note_content += f"<p>Note: {approval_note}</p>"
    if order_items:
        note_content += "<p>Items:</p><ul>"
        for item in order_items:
            note_content += f"<li>{item.description} (x{item.quantity})"
            if item.est_unit_cost:
                note_content += f" - ${item.est_unit_cost:.2f} each"
            note_content += "</li>"
        note_content += "</ul>"
    
    note = TicketNote(
        ticket_id=t.id,
        author_id=current_user.id,
        content=note_content,
        is_private=True,
    )
    db.session.add(note)
    
    # Build email to manager
    tech_name = current_user.name or 'A technician'
    requester_name = contact.name or contact.email
    
    subject = f"Approval Request - Ticket#{t.id} - {t.subject}"
    
    # Build items list for email
    items_html = ""
    if order_items:
        items_html = "<h3>Items Requested:</h3><ul>"
        total_cost = 0
        for item in order_items:
            item_cost = (item.est_unit_cost or 0) * item.quantity
            total_cost += item_cost
            items_html += f"<li><strong>{item.description}</strong> (Qty: {item.quantity})"
            if item.est_unit_cost:
                items_html += f" - ${item.est_unit_cost:.2f} each = ${item_cost:.2f}"
            items_html += "</li>"
        items_html += f"</ul><p><strong>Estimated Total: ${total_cost:.2f}</strong></p>"
    else:
        items_html = "<p><em>No specific order items listed.</em></p>"
    
    email_body = f"""
    <p>Hello {manager.name or 'Manager'},</p>
    
    <p><strong>{tech_name}</strong> is requesting your approval for the following order for <strong>{requester_name}</strong>:</p>
    
    <hr>
    <h3>Ticket Information:</h3>
    <ul>
        <li><strong>Ticket #:</strong> {t.id}</li>
        <li><strong>Subject:</strong> {t.subject}</li>
        <li><strong>Requester:</strong> {requester_name}</li>
    </ul>
    
    {items_html}
    
    {f'<h3>Additional Notes from Tech:</h3><p>{approval_note}</p>' if approval_note else ''}
    
    <hr>
    <h3>How to Respond:</h3>
    <p>Please <strong>reply to this email</strong> with one of the following:</p>
    <ul>
        <li><strong>Approved</strong> - to approve this request</li>
        <li><strong>Denied</strong> - to deny this request (please include a reason)</li>
    </ul>
    
    <p>Thank you,<br>Help Desk</p>
    """
    
    # Send the email
    try:
        success = send_mail(manager.email, subject, email_body, to_name=manager.name)
        if success:
            db.session.commit()
            flash(f'Approval request sent to {manager.name or manager.email}.', 'success')
        else:
            db.session.rollback()
            flash('Failed to send approval request email.', 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f'Error sending approval request: {str(e)}', 'danger')
    
    return redirect(url_for('tickets.show_ticket', ticket_id=t.id))
