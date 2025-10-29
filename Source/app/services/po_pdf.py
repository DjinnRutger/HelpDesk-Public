from io import BytesIO
from typing import List
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont, TTFError
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER

from app.models import PurchaseOrder


# Modern color palette
PRIMARY_BLUE = colors.Color(0.3, 0.5, 0.8)      # Lighter professional blue for better readability
LIGHT_BLUE = colors.Color(0.94, 0.96, 0.99)     # Very light blue background
ACCENT_BLUE = colors.Color(0.85, 0.92, 0.98)    # Light accent
DARK_GRAY = colors.Color(0.3, 0.3, 0.3)         # Dark text
MEDIUM_GRAY = colors.Color(0.5, 0.5, 0.5)       # Medium gray
LIGHT_GRAY = colors.Color(0.9, 0.9, 0.9)        # Light borders
WHITE = colors.white


def _fmt_currency(v: float) -> str:
    try:
        return f"${float(v):,.2f}"
    except (ValueError, TypeError):
        return "—"


def _register_fonts_once():
    # Optionally register a nicer font if available in system; fallback to built-ins
    try:
        pdfmetrics.registerFont(TTFont("Inter", "Inter-Regular.ttf"))
        pdfmetrics.registerFont(TTFont("Inter-Bold", "Inter-Bold.ttf"))
        return "Inter", "Inter-Bold"
    except (OSError, FileNotFoundError, TTFError):
        return "Helvetica", "Helvetica-Bold"


def render_po_pdf(po: PurchaseOrder) -> bytes:
    """Render a professional-looking PO as PDF and return bytes."""
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title=f"Purchase Order {po.po_number or po.id}",
        author="HelpDesk System",
    )

    font, font_bold = _register_fonts_once()

    styles = getSampleStyleSheet()
    
    # Custom styles for modern look
    title_style = ParagraphStyle(
        name="Title",
        parent=styles["Normal"],
        fontName=font_bold,
        fontSize=24,
        textColor=PRIMARY_BLUE,
        spaceAfter=20,
        alignment=TA_LEFT,
    )
    
    po_number_style = ParagraphStyle(
        name="PONumber",
        parent=styles["Normal"],
        fontName=font_bold,
        fontSize=18,
        textColor=DARK_GRAY,
        alignment=TA_RIGHT,
    )

    section_header_style = ParagraphStyle(
        name="SectionHeader",
        parent=styles["Normal"],
        fontName=font_bold,
        fontSize=12,
        textColor=PRIMARY_BLUE,
        spaceAfter=6,
        spaceBefore=12,
    )
    
    body_style = ParagraphStyle(
        name="Body",
        parent=styles["Normal"],
        fontName=font,
        fontSize=10,
        textColor=DARK_GRAY,
        leading=12,
    )
    
    small_style = ParagraphStyle(
        name="Small",
        parent=styles["Normal"],
        fontName=font,
        fontSize=9,
        textColor=MEDIUM_GRAY,
        leading=11,
    )

    body: List = []

    # Header with company name and PO number
    company_name = po.company_name or "PURCHASE ORDER"
    header_data = [
        [
            Paragraph(company_name, title_style),
            Paragraph(f"PO #{po.po_number or po.id}", po_number_style)
        ]
    ]
    
    header_table = Table(header_data, colWidths=[4.5 * inch, 2.5 * inch])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (1, 0), (1, 0), 0),
    ]))
    body.append(header_table)
    
    # Add horizontal line
    body.append(Spacer(1, 12))
    body.append(HRFlowable(width="100%", thickness=2, color=PRIMARY_BLUE))
    body.append(Spacer(1, 20))

    # Order details in a clean layout
    order_date = po.ordered_at.strftime("%B %d, %Y") if po.ordered_at else "Draft"
    details_data = [
        ["Order Date:", order_date, "Status:", po.status.upper()]
    ]
    
    details_table = Table(details_data, colWidths=[1*inch, 2*inch, 1*inch, 2*inch])
    details_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), MEDIUM_GRAY),
        ("TEXTCOLOR", (2, 0), (2, -1), MEDIUM_GRAY),
        ("TEXTCOLOR", (1, 0), (1, -1), DARK_GRAY),
        ("TEXTCOLOR", (3, 0), (3, -1), DARK_GRAY),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    body.append(details_table)
    body.append(Spacer(1, 20))

    # Address section with modern card-like appearance
    vendor_content = [Paragraph("VENDOR", section_header_style)]
    vendor_content.append(Paragraph(po.vendor_name or "—", body_style))
    if po.vendor_contact_name:
        vendor_content.append(Paragraph(po.vendor_contact_name, body_style))
    if po.vendor_email:
        vendor_content.append(Paragraph(po.vendor_email, small_style))
    if po.vendor_phone:
        vendor_content.append(Paragraph(po.vendor_phone, small_style))
    if po.vendor_address:
        for line in (po.vendor_address or "").strip().split('\n'):
            if line.strip():
                vendor_content.append(Paragraph(line.strip(), small_style))

    ship_content = [Paragraph("SHIP TO", section_header_style)]
    ship_name = po.shipping_name or "—"
    ship_content.append(Paragraph(ship_name, body_style))
    if po.shipping_address:
        ship_content.append(Paragraph(po.shipping_address, small_style))
    ship_city_line = f"{po.shipping_city or ''} {po.shipping_state or ''} {po.shipping_zip or ''}".strip()
    if ship_city_line:
        ship_content.append(Paragraph(ship_city_line, small_style))

    bill_content = [Paragraph("BILL TO", section_header_style)]
    bill_name = po.company_name or "—"
    bill_content.append(Paragraph(bill_name, body_style))
    if po.company_address:
        bill_content.append(Paragraph(po.company_address, small_style))
    bill_city_line = f"{po.company_city or ''} {po.company_state or ''} {po.company_zip or ''}".strip()
    if bill_city_line:
        bill_content.append(Paragraph(bill_city_line, small_style))

    addr_data = [[vendor_content, ship_content, bill_content]]
    addr_table = Table(addr_data, colWidths=[2.3 * inch, 2.3 * inch, 2.4 * inch])
    addr_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BLUE),
        ("BOX", (0, 0), (-1, -1), 1, LIGHT_GRAY),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, LIGHT_GRAY),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    body.append(addr_table)
    body.append(Spacer(1, 25))

    # Line items with modern table design
    body.append(Paragraph("ORDER ITEMS", section_header_style))
    body.append(Spacer(1, 8))

    # Table headers
    headers = [
        Paragraph("<b>QTY</b>", body_style),
        Paragraph("<b>DESCRIPTION</b>", body_style),
        Paragraph("<b>DEPT/CC</b>", body_style),
        Paragraph("<b>UNIT PRICE</b>", body_style),
        Paragraph("<b>TOTAL</b>", body_style),
    ]
    
    data = [headers]

    # Add line items
    for item in po.items:
        qty = str(item.quantity or 0)
        desc = Paragraph(item.description or "", body_style)
        dept = item.dept_code or "—"
        unit_price = _fmt_currency(item.est_unit_cost) if item.est_unit_cost else "—"
        total = _fmt_currency((item.est_unit_cost or 0) * (item.quantity or 0)) if item.est_unit_cost else "—"
        
        data.append([qty, desc, dept, unit_price, total])

    col_widths = [0.6 * inch, 3.2 * inch, 1.0 * inch, 1.1 * inch, 1.1 * inch]
    items_table = Table(data, colWidths=col_widths, repeatRows=1)
    items_table.setStyle(TableStyle([
        # Header row styling
        ("BACKGROUND", (0, 0), (-1, 0), PRIMARY_BLUE),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), font_bold),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
        ("TOPPADDING", (0, 0), (-1, 0), 12),
        
        # Data rows styling
        ("FONTNAME", (0, 1), (-1, -1), font),
        ("FONTSIZE", (0, 1), (-1, -1), 10),
        ("TEXTCOLOR", (0, 1), (-1, -1), DARK_GRAY),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),  # Qty column
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),   # Price columns
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        
        # Alternating row colors for readability
        ("BACKGROUND", (0, 1), (-1, -1), WHITE),
        
        # Borders
        ("BOX", (0, 0), (-1, -1), 1, LIGHT_GRAY),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, LIGHT_GRAY),
        
        # Padding
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 1), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 8),
    ]))
    
    # Add alternating row colors
    for i in range(1, len(data)):
        if i % 2 == 0:  # Even rows (0-indexed, so actually odd visual rows)
            items_table.setStyle(TableStyle([
                ("BACKGROUND", (0, i), (-1, i), ACCENT_BLUE),
            ]))
    
    body.append(items_table)
    body.append(Spacer(1, 15))

    # Total section
    subtotal = sum((item.est_unit_cost or 0) * (item.quantity or 0) for item in po.items)
    tax_rate_pct = (getattr(po, 'effective_tax_rate', 0.0) or 0.0) * 100.0
    tax_amount = getattr(po, 'total_tax', 0.0) or 0.0
    shipping_amount = getattr(po, 'total_shipping', 0.0) or 0.0
    grand_total = getattr(po, 'grand_total', subtotal + tax_amount + shipping_amount) or 0.0
    total_data = [
        ["", "", "", "SUBTOTAL:", _fmt_currency(subtotal)],
        ["", "", "", f"TAX ({tax_rate_pct:.2f}%):", _fmt_currency(tax_amount)],
        ["", "", "", "SHIPPING:", _fmt_currency(shipping_amount)],
        ["", "", "", "TOTAL:", _fmt_currency(grand_total)]
    ]
    
    totals_table = Table(total_data, colWidths=col_widths)
    totals_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_bold),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("TEXTCOLOR", (0, 0), (-1, -1), DARK_GRAY),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    # Make the final total row stand out
    ("BACKGROUND", (3, 3), (-1, 3), PRIMARY_BLUE),
    ("TEXTCOLOR", (3, 3), (-1, 3), WHITE),
    ("BOX", (3, 3), (-1, 3), 1, PRIMARY_BLUE),
    ("LEFTPADDING", (3, 3), (-1, 3), 8),
    ]))
    body.append(totals_table)

    # Notes section
    if po.notes:
        body.append(Spacer(1, 25))
        body.append(Paragraph("NOTES", section_header_style))
        body.append(Spacer(1, 8))
        
        # Create a styled box for notes
        notes_content = po.notes.replace('\n', '<br/>')
        notes_para = Paragraph(notes_content, body_style)
        
        notes_table = Table([[notes_para]], colWidths=[7 * inch])
        notes_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), LIGHT_BLUE),
            ("BOX", (0, 0), (-1, -1), 1, LIGHT_GRAY),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
        ]))
        body.append(notes_table)

    # Footer removed per request

    doc.build(body)
    return buf.getvalue()
