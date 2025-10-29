from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, Response
from flask_login import login_required
from app.models import OrderItem, PurchaseOrder, Vendor, Company, ShippingLocation
from app import db
from app.forms import OrderItemForm
from datetime import datetime
import base64
from sqlalchemy import func, cast, Integer
from sqlalchemy.exc import IntegrityError

orders_bp = Blueprint('orders', __name__, url_prefix='/orders')


@orders_bp.route('/')
@login_required
def list_items():
    """Show planned items not yet on a PO plus recent POs with pagination.

    Also supports filtering the Purchase Orders list to hide completed POs by default,
    with a toggle to show all (including completed).
    """
    search_query = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 5, type=int)  # Default 5, can be increased to 20
    # Robust boolean parsing for query params
    def _get_bool_arg(name: str, default: bool = False) -> bool:
        val = request.args.get(name)
        if val is None:
            return default
        val = str(val).strip().lower()
        return val in ('1', 'true', 'yes', 'on')

    show_all = _get_bool_arg('show_all', False)
    # When False (default), hide completed POs from the Recent list
    show_completed = _get_bool_arg('show_completed', False)
    
    # Validate per_page values
    if per_page not in [5, 20]:
        per_page = 5
    
    planned = OrderItem.query.filter(OrderItem.po_id.is_(None)).order_by(OrderItem.created_at.desc()).limit(500).all()
    vendors = Vendor.query.order_by(Vendor.company_name.asc()).all()
    
    # Base query for Purchase Orders
    pos_query = PurchaseOrder.query
    
    # Search functionality
    if search_query:
        # Search in POs: vendor info, line items, and notes using a single query with subqueries
        search_pattern = f'%{search_query}%'
        
        # Create a subquery for POs that have matching line items
        item_subquery = db.session.query(OrderItem.po_id).filter(
            OrderItem.description.ilike(search_pattern)
        ).distinct().subquery()
        
        # Main query that searches PO fields OR has matching line items
        pos_query = pos_query.filter(
            db.or_(
                # Direct PO field matches
                PurchaseOrder.vendor_name.ilike(search_pattern),
                PurchaseOrder.vendor_contact_name.ilike(search_pattern),
                PurchaseOrder.vendor_email.ilike(search_pattern),
                PurchaseOrder.vendor_address.ilike(search_pattern),
                PurchaseOrder.po_number.ilike(search_pattern),
                PurchaseOrder.quote_number.ilike(search_pattern),
                PurchaseOrder.notes.ilike(search_pattern),
                PurchaseOrder.company_name.ilike(search_pattern),
                PurchaseOrder.shipping_name.ilike(search_pattern),
                # PO has matching line items
                PurchaseOrder.id.in_(db.session.query(item_subquery.c.po_id))
            )
        )
    
    # Filter out completed unless explicitly showing completed
    if not show_completed:
        pos_query = pos_query.filter(PurchaseOrder.status != 'complete')

    # Order and paginate
    pos_query = pos_query.order_by(PurchaseOrder.created_at.desc())
    
    if show_all:
        pos_pagination = pos_query.paginate(
            page=page, per_page=50, error_out=False
        )
    else:
        pos_pagination = pos_query.paginate(
            page=page, per_page=per_page, error_out=False
        )
    
    pos = pos_pagination.items
    
    return render_template('orders/index.html', 
                         planned=planned, 
                         pos=pos, 
                         vendors=vendors,
                         search_query=search_query,
                         is_search_results=bool(search_query),
                         pagination=pos_pagination,
                         current_page=page,
                         per_page=per_page,
                         show_all=show_all,
                         show_completed=show_completed)


@orders_bp.route('/items/new', methods=['POST'])
@login_required
def create_item():
    form = OrderItemForm()
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if form.validate_on_submit():
        # Parse unit cost (allow blank or currency characters)
        raw_cost = (form.est_unit_cost.data or '').strip()
        est_cost = None
        if raw_cost:
            cleaned = ''.join(ch for ch in raw_cost if ch.isdigit() or ch in '.-')
            try:
                est_cost = float(cleaned)
            except ValueError:
                est_cost = None
        needed_by = None
        needed_by_text = None
        if form.needed_by.data:
            raw_date = form.needed_by.data.strip()
            # input[type=date] submits yyyy-mm-dd; store both datetime and mm-dd-yyyy text
            try:
                needed_by = datetime.strptime(raw_date, '%Y-%m-%d')
                needed_by_text = needed_by.strftime('%m-%d-%Y')
            except ValueError:
                # Accept mm-dd-yyyy if browser sends custom text
                try:
                    needed_by = datetime.strptime(raw_date, '%m-%d-%Y')
                    needed_by_text = raw_date
                except ValueError:
                    needed_by = None
                    needed_by_text = None
        itm = OrderItem(
            description=form.description.data.strip(),
            quantity=form.quantity.data or 1,
            target_vendor=(form.target_vendor.data or '').strip() or None,
            source_url=(form.source_url.data or '').strip() or None,
            est_unit_cost=est_cost,
            needed_by=needed_by,
            needed_by_text=needed_by_text,
            ticket_id=int(form.ticket_id.data) if form.ticket_id.data else None,
            dept_code=(request.form.get('dept_code') or '').strip() or None,
        )
        db.session.add(itm)
        db.session.commit()
        if is_ajax:
            return ('', 204)
        flash('Order item added', 'success')
    else:
        if is_ajax:
            return abort(400)
        flash('Failed to add item', 'danger')
    # If a ticket context provided, redirect back to ticket; else orders list
    if form.ticket_id.data:
        return redirect(url_for('tickets.show_ticket', ticket_id=form.ticket_id.data))
    return redirect(url_for('orders.list_items'))


@orders_bp.route('/po/<int:po_id>')
@login_required
def show_po(po_id):
    po = PurchaseOrder.query.get_or_404(po_id)
    companies = Company.query.order_by(Company.name.asc()).all()
    shipping = ShippingLocation.query.order_by(ShippingLocation.name.asc()).all()
    return render_template('orders/po_detail.html', po=po, companies=companies, shipping=shipping)


@orders_bp.route('/ticket/<int:ticket_id>/items')
@login_required
def ticket_items_fragment(ticket_id):
    items = OrderItem.query.filter_by(ticket_id=ticket_id).order_by(OrderItem.created_at.desc()).all()
    return render_template('orders/_ticket_items_list.html', items=items)


@orders_bp.route('/items/<int:item_id>/update', methods=['POST'])
@login_required
def update_item(item_id):
    itm = OrderItem.query.get_or_404(item_id)
    # Update simple editable fields
    if 'description' in request.form:
        desc = request.form.get('description','').strip()
        if desc:
            itm.description = desc
    if 'quantity' in request.form:
        try:
            q = int(request.form.get('quantity') or 1)
            if q < 1:
                q = 1
            itm.quantity = q
        except ValueError:
            pass
    if 'target_vendor' in request.form:
        vendor = request.form.get('target_vendor','').strip()
        itm.target_vendor = vendor or None
    if 'est_unit_cost' in request.form:
        raw = (request.form.get('est_unit_cost') or '').strip()
        if raw:
            cleaned = ''.join(ch for ch in raw if ch.isdigit() or ch in '.-')
            try:
                itm.est_unit_cost = float(cleaned)
            except ValueError:
                pass
        else:
            itm.est_unit_cost = None
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return ('', 204)
    # Redirect back to ticket
    return redirect(url_for('tickets.show_ticket', ticket_id=itm.ticket_id))


@orders_bp.route('/items/<int:item_id>/delete', methods=['POST'])
@login_required
def delete_item(item_id):
    itm = OrderItem.query.get_or_404(item_id)
    ticket_id = itm.ticket_id
    db.session.delete(itm)
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return ('', 204)
    return redirect(url_for('tickets.show_ticket', ticket_id=ticket_id))


@orders_bp.route('/items/delete_selected', methods=['POST'])
@login_required
def delete_selected_items():
    """Delete multiple selected order items."""
    item_ids = request.form.getlist('item_ids')
    if not item_ids:
        flash('No items selected for deletion', 'warning')
        return redirect(url_for('orders.list_items'))
    
    try:
        item_ids = [int(id) for id in item_ids]
        items = OrderItem.query.filter(OrderItem.id.in_(item_ids)).all()
        
        if not items:
            flash('No items found for deletion', 'warning')
            return redirect(url_for('orders.list_items'))
        
        count = len(items)
        for item in items:
            db.session.delete(item)
        
        db.session.commit()
        flash(f'Successfully deleted {count} item{"s" if count > 1 else ""}', 'success')
        
    except ValueError:
        flash('Invalid item IDs provided', 'danger')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting items: {str(e)}', 'danger')
    
    return redirect(url_for('orders.list_items'))


def _next_po_number():
    """Compute the next sequential PO number based on max numeric po_number in DB."""
    # SQLite CAST(non-numeric AS INTEGER) yields 0, so max will be among numeric rows
    max_val = db.session.query(func.max(cast(PurchaseOrder.po_number, Integer))).scalar()
    try:
        current_max = int(max_val or 999)
    except (ValueError, TypeError):
        current_max = 999
    return str(current_max + 1)


@orders_bp.route('/create_po', methods=['POST'])
@login_required
def create_po_from_items():
    ids = request.form.getlist('item_id')
    vendor = (request.form.get('vendor') or '').strip() or 'Vendor'
    if not ids:
        flash('Select at least one item', 'warning')
        return redirect(url_for('orders.list_items'))
    items = OrderItem.query.filter(OrderItem.id.in_(ids), OrderItem.po_id.is_(None)).all()
    if not items:
        flash('No valid items selected', 'warning')
        return redirect(url_for('orders.list_items'))
    po = PurchaseOrder(vendor_name=vendor, status='draft')
    # Link to known vendor if exists and snapshot details
    v = Vendor.query.filter(Vendor.company_name.ilike(vendor)).first()
    if v:
        po.vendor_id = v.id
        po.vendor_contact_name = v.contact_name
        po.vendor_email = v.email
        po.vendor_address = v.address
        po.vendor_phone = v.phone
    db.session.add(po)
    db.session.flush()
    for it in items:
        it.po_id = po.id
        it.status = 'ordered'
    db.session.commit()
    flash('Draft PO created', 'success')
    return redirect(url_for('orders.show_po', po_id=po.id))


@orders_bp.route('/po/<int:po_id>/meta', methods=['POST'])
@login_required
def update_po_meta(po_id):
    po = PurchaseOrder.query.get_or_404(po_id)
    # Update company
    cid = request.form.get('company_id') or ''
    po.company_id = int(cid) if cid.isdigit() else None
    if po.company_id:
        c = Company.query.get(po.company_id)
        if c:
            po.company_name = c.name
            po.company_address = c.address
            po.company_city = c.city
            po.company_state = c.state
            po.company_zip = c.zip_code
    else:
        po.company_name = po.company_address = po.company_city = po.company_state = po.company_zip = None
    # Update shipping
    sid = request.form.get('shipping_id') or ''
    po.shipping_location_id = int(sid) if sid.isdigit() else None
    if po.shipping_location_id:
        s = ShippingLocation.query.get(po.shipping_location_id)
        if s:
            po.shipping_name = s.name
            po.shipping_address = s.address
            po.shipping_city = s.city
            po.shipping_state = s.state
            po.shipping_zip = s.zip_code
    else:
        po.shipping_name = po.shipping_address = po.shipping_city = po.shipping_state = po.shipping_zip = None
    # Update quote number (optional)
    po.quote_number = (request.form.get('quote_number') or '').strip() or None
    # Update shipping cost (optional)
    raw_ship = (request.form.get('shipping_cost') or '').strip()
    if raw_ship == '':
        # Leave as-is if blank, default to 0.0 if None
        if po.shipping_cost is None:
            po.shipping_cost = 0.0
    else:
        try:
            po.shipping_cost = max(0.0, float(raw_ship))
        except ValueError:
            pass
    db.session.commit()
    flash('PO details updated', 'success')
    return redirect(url_for('orders.show_po', po_id=po.id))


@orders_bp.route('/po/<int:po_id>/notes', methods=['POST'])
@login_required
def update_po_notes(po_id):
    po = PurchaseOrder.query.get_or_404(po_id)
    if po.status != 'draft':
        flash('Cannot edit notes after finalize', 'warning')
        return redirect(url_for('orders.show_po', po_id=po.id))
    notes = (request.form.get('notes') or '').strip()
    po.notes = notes or None
    db.session.commit()
    flash('Notes saved', 'success')
    return redirect(url_for('orders.show_po', po_id=po.id))


@orders_bp.route('/po/<int:po_id>/items/add', methods=['POST'])
@login_required
def add_item_to_po(po_id):
    po = PurchaseOrder.query.get_or_404(po_id)
    if po.status != 'draft':
        flash('Cannot add items to a finalized PO.', 'warning')
        return redirect(url_for('orders.show_po', po_id=po.id))
    desc = (request.form.get('description') or '').strip()
    if not desc:
        flash('Description is required', 'danger')
        return redirect(url_for('orders.show_po', po_id=po.id))
    # Quantity
    try:
        qty = int(request.form.get('quantity') or 1)
        if qty < 1:
            qty = 1
    except ValueError:
        qty = 1
    # Cost (allow currency formatting)
    raw_cost = (request.form.get('est_unit_cost') or '').strip()
    est_cost = None
    if raw_cost:
        cleaned = ''.join(ch for ch in raw_cost if ch.isdigit() or ch in '.-')
        try:
            est_cost = float(cleaned)
        except ValueError:
            est_cost = None
    dept_code = (request.form.get('dept_code') or '').strip() or None
    itm = OrderItem(
        description=desc,
        quantity=qty,
        est_unit_cost=est_cost,
        dept_code=dept_code,
        po_id=po.id,
        status='ordered',
    )
    db.session.add(itm)
    db.session.commit()
    flash('Item added to PO', 'success')
    return redirect(url_for('orders.show_po', po_id=po.id))


@orders_bp.route('/po/<int:po_id>/finalize', methods=['POST'])
@login_required
def finalize_po(po_id):
    po = PurchaseOrder.query.get_or_404(po_id)
    # Assign a unique PO number with retry in case of race/duplicate
    assigned = False
    attempts = 0
    while not assigned and attempts < 10:
        attempts += 1
        if not po.po_number:
            po.po_number = _next_po_number()
        po.status = 'sent'
        po.ordered_at = datetime.utcnow()
        try:
            db.session.commit()
            assigned = True
        except IntegrityError:
            db.session.rollback()
            # Clear po_number and retry with a new one
            po = PurchaseOrder.query.get(po_id)
            if po:
                po.po_number = None
            continue
    if not assigned:
        flash('Could not assign a unique PO number. Please try again.', 'danger')
        return redirect(url_for('orders.show_po', po_id=po_id))
    # Generate PDF and email to logged-in user (non-fatal if it fails)
    from flask_login import current_user
    from flask import current_app
    try:
        from app.services.po_pdf import render_po_pdf
        if current_app:
            current_app.logger.info("Generating PO PDF for PO %s", po.po_number)
        pdf_bytes = render_po_pdf(po)
    except ImportError:
        pdf_bytes = None
    except Exception:
        pdf_bytes = None
        if current_app:
            current_app.logger.exception("Error generating PDF for PO %s", po.id)
    try:
        from app.services.ms_graph import send_mail
        to_addr = getattr(current_user, 'email', None)
        to_name = getattr(current_user, 'name', None)
        if current_app:
            current_app.logger.info("Emailing PO %s to %s (has_pdf=%s)", po.po_number, to_addr, bool(pdf_bytes))
        if to_addr and pdf_bytes:
            filename = f"PO_{po.po_number or po.id}.pdf"
            attachment = {
                "name": filename,
                "contentType": "application/pdf",
                "contentBytes": base64.b64encode(pdf_bytes).decode('ascii'),
            }
            subj = f"Purchase Order {po.po_number}"
            html = (
                f"<p>Attached is Purchase Order <strong>{po.po_number}</strong> for {po.vendor_name}.</p>"
                f"<p>Total: ${po.grand_total:,.2f}</p>"
            )
            ok = send_mail(to_addr, subj, html, to_name=to_name, attachments=[attachment])
            if current_app:
                current_app.logger.info("Email send result: %s", ok)
    except ImportError:
        pass
    except Exception:
        # Ignore email errors
        if current_app:
            current_app.logger.exception("Error emailing PO %s", po.id)
    flash('PO finalized and emailed PDF to you', 'success')
    return redirect(url_for('orders.show_po', po_id=po.id))


@orders_bp.route('/items/<int:item_id>/receive', methods=['POST'])
@login_required
def receive_item(item_id):
    itm = OrderItem.query.get_or_404(item_id)
    itm.status = 'received'
    itm.received_at = datetime.utcnow()
    # If all items on PO received, update PO status
    if itm.po_id:
        po = PurchaseOrder.query.get(itm.po_id)
        if po and all(i.status == 'received' for i in po.items):
            po.status = 'complete'
    db.session.commit()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return ('', 204)
    if itm.po_id:
        return redirect(url_for('orders.show_po', po_id=itm.po_id))
    return redirect(url_for('orders.list_items'))


@orders_bp.route('/po/<int:po_id>/download', methods=['GET'])
@login_required
def download_po_pdf(po_id):
    """Download PDF of a finalized PO."""
    po = PurchaseOrder.query.get_or_404(po_id)
    if po.status == 'draft':
        flash('Cannot download PDF of draft PO', 'warning')
        return redirect(url_for('orders.show_po', po_id=po_id))
    
    try:
        from app.services.po_pdf import render_po_pdf
        pdf_bytes = render_po_pdf(po)
        filename = f"PO_{po.po_number or po.id}.pdf"
        
        return Response(
            pdf_bytes,
            mimetype='application/pdf',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Type': 'application/pdf',
            }
        )
    except Exception as e:
        flash(f'Error generating PDF: {str(e)}', 'danger')
        return redirect(url_for('orders.show_po', po_id=po_id))


@orders_bp.route('/items/<int:item_id>/edit', methods=['POST'])
@login_required
def edit_po_item(item_id):
    """Edit an item in a draft PO."""
    item = OrderItem.query.get_or_404(item_id)
    
    # Check if PO is in draft status
    if not item.po_id:
        flash('Item is not part of a PO', 'danger')
        return redirect(url_for('orders.list_items'))
    
    po = PurchaseOrder.query.get_or_404(item.po_id)
    if po.status != 'draft':
        flash('Cannot edit items in a finalized PO', 'danger')
        return redirect(url_for('orders.show_po', po_id=po.id))
    
    # Update item fields
    if 'description' in request.form:
        desc = request.form.get('description', '').strip()
        if desc:
            item.description = desc
    
    if 'quantity' in request.form:
        try:
            quantity = int(request.form.get('quantity', 1))
            if quantity < 1:
                quantity = 1
            item.quantity = quantity
        except ValueError:
            pass
    
    if 'est_unit_cost' in request.form:
        try:
            cost_str = request.form.get('est_unit_cost', '').strip()
            if cost_str:
                item.est_unit_cost = float(cost_str)
            else:
                item.est_unit_cost = None
        except ValueError:
            pass
    
    if 'dept_code' in request.form:
        dept_code = request.form.get('dept_code', '').strip()
        item.dept_code = dept_code if dept_code else None
    
    item.updated_at = datetime.utcnow()
    
    try:
        db.session.commit()
        flash('Item updated successfully', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating item: {str(e)}', 'danger')
    
    return redirect(url_for('orders.show_po', po_id=po.id))


@orders_bp.route('/items/<int:item_id>/delete_po', methods=['POST'])
@login_required
def delete_po_item(item_id):
    """Delete an item from a draft PO."""
    item = OrderItem.query.get_or_404(item_id)
    
    # Check if PO is in draft status
    if not item.po_id:
        flash('Item is not part of a PO', 'danger')
        return redirect(url_for('orders.list_items'))
    
    po = PurchaseOrder.query.get_or_404(item.po_id)
    if po.status != 'draft':
        flash('Cannot delete items from a finalized PO', 'danger')
        return redirect(url_for('orders.show_po', po_id=po.id))
    
    po_id = po.id
    
    try:
        db.session.delete(item)
        db.session.commit()
        flash('Item deleted successfully', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting item: {str(e)}', 'danger')
    
    return redirect(url_for('orders.show_po', po_id=po_id))
