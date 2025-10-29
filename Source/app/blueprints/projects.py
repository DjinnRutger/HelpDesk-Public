from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from sqlalchemy import func
from ..models import Project, Ticket, Contact
from sqlalchemy import or_
from .. import db
from ..forms import TicketForm

projects_bp = Blueprint('projects', __name__, url_prefix='/projects')


@projects_bp.route('/')
@login_required
def list_projects():
	"""List projects.

	Default: show only open (status != 'closed') projects.
	If a search query (?q=...) is provided, search across open & closed projects plus related ticket subjects/bodies.
	"""
	q = (request.args.get('q') or '').strip()
	base = Project.query
	is_search = False
	if q:
		is_search = True
		like = f"%{q}%"
		# Join tickets to allow searching ticket fields; use outerjoin so projects with no matching tickets can still match on project fields
		query = base.outerjoin(Ticket, Ticket.project_id == Project.id).filter(
			or_(
				Project.name.ilike(like),
				Project.description.ilike(like),
				Ticket.subject.ilike(like),
				Ticket.body.ilike(like),
			)
		).distinct()
	else:
		# Only open projects
		query = base.filter(Project.status != 'closed')
	projects = query.order_by(Project.created_at.desc()).all()
	return render_template('projects/list.html', projects=projects, q=q, is_search=is_search)


@projects_bp.route('/new', methods=['GET', 'POST'])
@login_required
def new_project():
	if request.method == 'POST':
		name = (request.form.get('name') or '').strip()
		desc = (request.form.get('description') or '').strip()
		if not name:
			flash('Project name is required.', 'danger')
			return render_template('projects/new.html')
		p = Project(name=name, description=desc)
		db.session.add(p)
		db.session.commit()
		flash('Project created.', 'success')
		return redirect(url_for('projects.show_project', project_id=p.id))
	return render_template('projects/new.html')


@projects_bp.route('/<int:project_id>')
@login_required
def show_project(project_id):
	p = Project.query.get_or_404(project_id)
	show = (request.args.get('show') or 'open').lower()
	query = p.tickets
	# "open" means not closed (includes open + in_progress)
	if show != 'all':
		query = query.filter(Ticket.status != 'closed')
	tickets = query.order_by(Ticket.project_position.asc(), Ticket.created_at.desc()).all()
	form = TicketForm()
	return render_template('projects/detail.html', p=p, tickets=tickets, form=form, show=show)


@projects_bp.route('/<int:project_id>/tickets/new', methods=['GET', 'POST'])
@login_required
def new_project_ticket(project_id):
	p = Project.query.get_or_404(project_id)
	form = TicketForm()
	if form.validate_on_submit():
		# Determine next position
		max_pos = (
			db.session.query(func.coalesce(func.max(Ticket.project_position), 0))
			.filter(Ticket.project_id == p.id)
			.scalar()
			or 0
		)
		t = Ticket(
			subject=form.subject.data,
			requester=form.requester.data,
			requester_email=form.requester.data,
			body=form.body.data,
			status=form.status.data,
			priority=form.priority.data or 'medium',
			source=form.source.data or 'manual',
			project_id=p.id,
			project_position=max_pos + 1,
		)
		db.session.add(t)
		db.session.commit()
		flash('Ticket created in project.', 'success')
		return redirect(url_for('projects.show_project', project_id=p.id))
	contacts = Contact.query.order_by(Contact.name.asc()).limit(500).all()
	return render_template('tickets/new.html', form=form, contacts=contacts)


@projects_bp.route('/<int:project_id>/reorder', methods=['POST'])
@login_required
def reorder_project_tickets(project_id):
	p = Project.query.get_or_404(project_id)
	order = request.json if request.is_json else None
	if not isinstance(order, list):
		return ({'error': 'Invalid payload'}, 400)
	# order is a list of ticket IDs in new order
	pos = 1
	id_set = set(order)
	# Only update tickets that belong to this project
	tickets = Ticket.query.filter(Ticket.project_id == p.id, Ticket.id.in_(id_set)).all()
	# Map for quick access
	by_id = {t.id: t for t in tickets}
	for tid in order:
		t = by_id.get(tid)
		if t:
			t.project_position = pos
			pos += 1
	db.session.commit()
	return ('', 204)


@projects_bp.route('/<int:project_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_project(project_id):
	p = Project.query.get_or_404(project_id)
	if request.method == 'POST':
		name = (request.form.get('name') or '').strip()
		desc = (request.form.get('description') or '').strip()
		if not name:
			flash('Project name is required.', 'danger')
			open_count = p.tickets.filter(Ticket.status != 'closed').count()
			return render_template('projects/edit.html', p=p, open_count=open_count)
		p.name = name
		p.description = desc
		db.session.commit()
		flash('Project updated.', 'success')
		return redirect(url_for('projects.show_project', project_id=p.id))
	open_count = p.tickets.filter(Ticket.status != 'closed').count()
	return render_template('projects/edit.html', p=p, open_count=open_count)


@projects_bp.route('/<int:project_id>/close', methods=['POST'])
@login_required
def close_project(project_id):
	p = Project.query.get_or_404(project_id)
	# require all tickets to be closed
	open_count = p.tickets.filter(Ticket.status != 'closed').count()
	if open_count > 0:
		flash('Close all project tickets before closing the project.', 'warning')
		return redirect(url_for('projects.edit_project', project_id=p.id))
	p.status = 'closed'
	from datetime import datetime as _dt
	p.closed_at = _dt.utcnow()
	db.session.commit()
	flash('Project closed.', 'success')
	return redirect(url_for('projects.show_project', project_id=p.id))


@projects_bp.route('/<int:project_id>/delete', methods=['POST'])
@login_required
def delete_project(project_id):
	p = Project.query.get_or_404(project_id)
	db.session.delete(p)
	db.session.commit()
	flash('Project deleted.', 'success')
	return redirect(url_for('projects.list_projects'))

