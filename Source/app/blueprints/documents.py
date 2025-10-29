from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_required
from .. import db
from ..models import DocumentCategory, Document


documents_bp = Blueprint('documents', __name__, url_prefix='/documents')


@documents_bp.route('/')
@login_required
def index():
    cats = DocumentCategory.query.order_by(DocumentCategory.name.asc()).all()
    return render_template('documents/index.html', categories=cats)


@documents_bp.route('/category/<int:category_id>')
@login_required
def category(category_id):
    cat = DocumentCategory.query.get_or_404(category_id)
    docs = Document.query.filter_by(category_id=cat.id).order_by(Document.name.asc()).all()
    return render_template('documents/category.html', category=cat, documents=docs)


@documents_bp.route('/category/<int:category_id>/new', methods=['POST'])
@login_required
def new_document(category_id):
    cat = DocumentCategory.query.get_or_404(category_id)
    name = (request.form.get('name') or '').strip()
    body = request.form.get('body') or ''
    if not name:
        flash('Name is required', 'danger')
        return redirect(url_for('documents.category', category_id=cat.id))
    d = Document(category_id=cat.id, name=name, body=body)
    db.session.add(d)
    db.session.commit()
    flash('Document created', 'success')
    return redirect(url_for('documents.category', category_id=cat.id))


@documents_bp.route('/view/<int:doc_id>')
@login_required
def view(doc_id):
    doc = Document.query.get_or_404(doc_id)
    cat = DocumentCategory.query.get(doc.category_id)
    # Provide all categories for editing/moving documents between categories
    categories = DocumentCategory.query.order_by(DocumentCategory.name.asc()).all()
    return render_template('documents/show.html', category=cat, categories=categories, doc=doc)


@documents_bp.route('/edit/<int:doc_id>', methods=['POST'])
@login_required
def edit(doc_id):
    doc = Document.query.get_or_404(doc_id)
    name = (request.form.get('name') or '').strip()
    body = request.form.get('body') or ''
    # Optional: category change
    category_id_raw = request.form.get('category_id')
    new_category = None
    if category_id_raw:
        try:
            cid = int(category_id_raw)
            new_category = DocumentCategory.query.get(cid)
            if not new_category:
                flash('Invalid category selected', 'danger')
                return redirect(url_for('documents.view', doc_id=doc.id))
        except ValueError:
            flash('Invalid category selected', 'danger')
            return redirect(url_for('documents.view', doc_id=doc.id))
    if not name:
        flash('Name is required', 'danger')
        return redirect(url_for('documents.view', doc_id=doc.id))
    doc.name = name
    doc.body = body
    if new_category and new_category.id != doc.category_id:
        doc.category_id = new_category.id
    db.session.commit()
    flash('Document updated', 'success')
    return redirect(url_for('documents.view', doc_id=doc.id))


@documents_bp.route('/delete/<int:doc_id>', methods=['POST'])
@login_required
def delete(doc_id):
    doc = Document.query.get_or_404(doc_id)
    cat_id = doc.category_id
    db.session.delete(doc)
    db.session.commit()
    flash('Document deleted', 'success')
    return redirect(url_for('documents.category', category_id=cat_id))


@documents_bp.route('/api/search')
@login_required
def api_search():
    q = (request.args.get('q') or request.args.get('query') or '').strip()
    query = Document.query
    if q:
        query = query.filter(Document.name.ilike(f'%{q}%'))
    docs = query.order_by(Document.name.asc()).limit(50).all()
    return jsonify([{ 'id': d.id, 'name': d.name } for d in docs])


@documents_bp.route('/api/body/<int:doc_id>')
@login_required
def api_body(doc_id):
    d = Document.query.get_or_404(doc_id)
    return jsonify({ 'id': d.id, 'name': d.name, 'body': d.body or '' })
