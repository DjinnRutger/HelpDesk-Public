from flask import Blueprint, render_template, redirect, url_for, request, flash, jsonify
from flask_login import login_required, current_user
from .. import db
from ..models import DocumentCategory, Document, DocumentFavorite
from ..permissions import CREATE, EDIT, DELETE, require_permission, protect_blueprint


documents_bp = Blueprint('documents', __name__, url_prefix='/documents')


def _favorite_ids_for_user(user_id):
    rows = DocumentFavorite.query.filter_by(user_id=user_id).all()
    return {r.document_id for r in rows}


@documents_bp.route('/')
@login_required
def index():
    # Only return root categories; subcategories are accessed via the relationship
    cats = DocumentCategory.query.filter_by(parent_id=None).order_by(DocumentCategory.position.asc(), DocumentCategory.name.asc()).all()
    fav_rows = DocumentFavorite.query.filter_by(user_id=current_user.id).all()
    fav_ids = [r.document_id for r in fav_rows]
    favorites = []
    if fav_ids:
        docs = Document.query.filter(Document.id.in_(fav_ids)).order_by(Document.name.asc()).all()
        cat_map = {c.id: c for c in DocumentCategory.query.filter(DocumentCategory.id.in_({d.category_id for d in docs})).all()}
        favorites = [{'doc': d, 'category': cat_map.get(d.category_id)} for d in docs]
    return render_template('documents/index.html', categories=cats, favorites=favorites)


@documents_bp.route('/category/<int:category_id>')
@login_required
def category(category_id):
    cat = DocumentCategory.query.get_or_404(category_id)
    docs = Document.query.filter_by(category_id=cat.id).order_by(Document.name.asc()).all()
    fav_ids = _favorite_ids_for_user(current_user.id)
    return render_template('documents/category.html', category=cat, documents=docs, favorite_ids=fav_ids)


@documents_bp.route('/favorite/<int:doc_id>', methods=['POST'])
@login_required
def toggle_favorite(doc_id):
    doc = Document.query.get_or_404(doc_id)
    existing = DocumentFavorite.query.filter_by(user_id=current_user.id, document_id=doc.id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        favorited = False
    else:
        db.session.add(DocumentFavorite(user_id=current_user.id, document_id=doc.id))
        db.session.commit()
        favorited = True
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.is_json:
        return jsonify({'ok': True, 'favorited': favorited, 'doc_id': doc.id})
    next_url = request.form.get('next') or request.referrer or url_for('documents.category', category_id=doc.category_id)
    return redirect(next_url)


@documents_bp.route('/category/<int:category_id>/new', methods=['POST'])
@login_required
@require_permission('documents', CREATE)
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
    # Pass root categories with their subcategories for grouped dropdown in edit modal
    root_cats = DocumentCategory.query.filter_by(parent_id=None).order_by(DocumentCategory.position.asc(), DocumentCategory.name.asc()).all()
    return render_template('documents/show.html', category=cat, categories=root_cats, doc=doc)


@documents_bp.route('/edit/<int:doc_id>', methods=['POST'])
@login_required
@require_permission('documents', EDIT)
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
@require_permission('documents', DELETE)
def delete(doc_id):
    doc = Document.query.get_or_404(doc_id)
    cat_id = doc.category_id
    DocumentFavorite.query.filter_by(document_id=doc.id).delete()
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


@documents_bp.route('/search')
@login_required
def search():
    """Search documents by name and content"""
    q = (request.args.get('q') or '').strip()
    results = []
    if q:
        # Search in both name and body fields
        search_pattern = f'%{q}%'
        results = Document.query.filter(
            db.or_(
                Document.name.ilike(search_pattern),
                Document.body.ilike(search_pattern)
            )
        ).order_by(Document.name.asc()).all()
    
    # Get categories for each result
    result_data = []
    for doc in results:
        cat = DocumentCategory.query.get(doc.category_id)
        result_data.append({
            'doc': doc,
            'category': cat
        })
    
    return render_template('documents/search_results.html', 
                         query=q, 
                         results=result_data,
                         result_count=len(results))


@documents_bp.route('/api/body/<int:doc_id>')
@login_required
def api_body(doc_id):
    d = Document.query.get_or_404(doc_id)
    return jsonify({ 'id': d.id, 'name': d.name, 'body': d.body or '' })


protect_blueprint(documents_bp, 'documents')
