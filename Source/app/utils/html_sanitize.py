"""Shared HTML sanitization helpers.

Single source of truth for the bleach allowlists used across the app:
- sanitize_rich_text: ticket/PO notes and other toolbar-editor content
- sanitize_document_html: documents (adds table class for Bootstrap styling)
- sanitize_ticket_body: web-form ticket descriptions (plain text passes through
  untouched so the template's preserve-ws branch keeps rendering it escaped)
- sanitize_email_html: inbound email bodies (wider allowlist so real emails
  stay readable: images, tables, safe inline styles)
"""
import bleach

RICH_TEXT_TAGS = [
    'p', 'br', 'div', 'span', 'b', 'strong', 'i', 'em', 'u', 'ul', 'ol', 'li',
    'h3', 'h4', 'h5', 'h6', 'a', 'table', 'thead', 'tbody', 'tr', 'th', 'td'
]
RICH_TEXT_ATTRS = {
    'a': ['href', 'title', 'target', 'rel'],
    'td': ['colspan', 'rowspan'],
    'th': ['colspan', 'rowspan']
}
ALLOWED_PROTOCOLS = ['http', 'https', 'mailto']

# Documents keep Bootstrap table classes inserted by the toolbar editor.
DOCUMENT_ATTRS = dict(RICH_TEXT_ATTRS)
DOCUMENT_ATTRS['table'] = ['class']

# Inbound email: wider so legitimate mail stays readable. Scripts, event
# handlers and javascript: URLs are stripped by the allowlist itself.
EMAIL_TAGS = RICH_TEXT_TAGS + [
    'h1', 'h2', 'img', 'blockquote', 'pre', 'hr', 'code', 'small', 'big',
    'sub', 'sup', 's', 'strike', 'font', 'center', 'caption', 'col',
    'colgroup', 'dl', 'dt', 'dd', 'address'
]
EMAIL_ATTRS = {
    '*': ['style', 'align', 'valign', 'dir'],
    'a': ['href', 'title', 'target', 'rel', 'name'],
    'img': ['src', 'alt', 'title', 'width', 'height', 'border'],
    'font': ['color', 'face', 'size'],
    'table': ['border', 'cellpadding', 'cellspacing', 'width', 'bgcolor'],
    'td': ['colspan', 'rowspan', 'width', 'height', 'bgcolor', 'nowrap'],
    'th': ['colspan', 'rowspan', 'width', 'height', 'bgcolor', 'nowrap'],
    'tr': ['bgcolor'],
    'col': ['span', 'width'],
    'colgroup': ['span', 'width'],
}
# cid: allows inline-image references from email clients to degrade gracefully.
EMAIL_PROTOCOLS = ['http', 'https', 'mailto', 'cid']

try:
    from bleach.css_sanitizer import CSSSanitizer
    _CSS_SANITIZER = CSSSanitizer()
except Exception:  # tinycss2 not installed: drop style attrs rather than crash
    _CSS_SANITIZER = None
    EMAIL_ATTRS = {k: [a for a in v if a != 'style'] for k, v in EMAIL_ATTRS.items()}


def _set_target_rel(attrs, new=False):
    # bleach >= 2 linkify callbacks key attrs by (namespace, name) tuples
    href = attrs.get((None, 'href')) or attrs.get('href')
    if href:
        attrs[(None, 'target')] = '_blank'
        rel = attrs.get((None, 'rel'), '') or ''
        rel_vals = set(rel.split()) if rel else set()
        rel_vals.update(['noopener', 'noreferrer'])
        attrs[(None, 'rel')] = ' '.join(sorted(rel_vals))
    return attrs


def sanitize_rich_text(raw, tags=None, attrs=None, protocols=None, linkify=True):
    """Bleach + linkify with the standard rich-text allowlist."""
    cleaned = bleach.clean(
        raw or '',
        tags=tags or RICH_TEXT_TAGS,
        attributes=attrs or RICH_TEXT_ATTRS,
        protocols=protocols or ALLOWED_PROTOCOLS,
        strip=True
    )
    if linkify:
        cleaned = bleach.linkify(cleaned, callbacks=[_set_target_rel])
    return cleaned


def sanitize_document_html(raw):
    return sanitize_rich_text(raw, attrs=DOCUMENT_ATTRS)


def sanitize_ticket_body(raw):
    """Sanitize a web-form ticket description.

    Bodies without angle brackets render through the escaped preserve-ws
    branch in tickets/detail.html, so they must pass through untouched
    (bleaching plain text would entity-encode '&' and display artifacts).
    """
    raw = raw or ''
    if '<' not in raw and '>' not in raw:
        return raw
    return sanitize_rich_text(raw)


def sanitize_email_html(raw):
    """Sanitize inbound email HTML with the email-friendly allowlist."""
    kwargs = {}
    if _CSS_SANITIZER is not None:
        kwargs['css_sanitizer'] = _CSS_SANITIZER
    cleaned = bleach.clean(
        raw or '',
        tags=EMAIL_TAGS,
        attributes=EMAIL_ATTRS,
        protocols=EMAIL_PROTOCOLS,
        strip=True,
        **kwargs
    )
    return cleaned
