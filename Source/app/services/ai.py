"""AI assistant service: Ollama client, ticket embedding index, suggested replies.

The helpdesk talks to an Ollama server (Admin -> AI Assistant) for two things:
embeddings (ticket similarity index) and chat completions (suggested replies).
Vectors are stored in ticket_embedding as packed little-endian float32,
L2-normalized at write time so cosine similarity is a plain dot product.

Suggestion rows move through: pending -> generating -> ready | failed, with
dismissed set from the UI. Rows are claimed with an atomic UPDATE so the
scheduler job and a web-kicked thread never generate the same row twice.
"""
import hashlib
import html as _html
import math
import re
import threading
from array import array
from datetime import datetime, timedelta

import requests
from flask import current_app
from sqlalchemy import text as sql_text

from .. import db
from ..models import Setting, Ticket, TicketEmbedding, TicketAISuggestion

HEALTH_TIMEOUT = 10
EMBED_TIMEOUT = 180
CHAT_TIMEOUT = 900
# Cap text sent for embedding/prompting so huge email chains don't blow context
MAX_INDEX_CHARS = 6000
MAX_CONTEXT_TICKETS_CHARS = 2500
# A row stuck in 'generating' longer than this (crashed worker) is retried.
STALE_GENERATING = timedelta(minutes=30)
# How far back the auto-suggest job looks for tickets without a suggestion.
AUTO_SUGGEST_WINDOW = timedelta(days=3)
SUGGESTIONS_PER_RUN = 3


# --- Config -----------------------------------------------------------------

def get_ai_config():
    return {
        'enabled': (Setting.get('AI_ENABLED', '0') or '0') in ('1', 'true', 'on', 'yes'),
        'host': (Setting.get('AI_HOST', '127.0.0.1') or '127.0.0.1').strip(),
        'port': (Setting.get('AI_PORT', '11434') or '11434').strip(),
        'chat_model': (Setting.get('AI_CHAT_MODEL', 'qwen2.5:14b') or 'qwen2.5:14b').strip(),
        'embed_model': (Setting.get('AI_EMBED_MODEL', 'nomic-embed-text') or 'nomic-embed-text').strip(),
        'auto_suggest': (Setting.get('AI_AUTO_SUGGEST_ENABLED', '0') or '0') in ('1', 'true', 'on', 'yes'),
        'similar_count': _to_int(Setting.get('AI_SIMILAR_COUNT', '5'), 5),
        'index_interval': _to_int(Setting.get('AI_INDEX_INTERVAL_MINUTES', '10'), 10),
    }


def ai_enabled():
    try:
        return get_ai_config()['enabled']
    except Exception:
        return False


def _to_int(value, default):
    try:
        return max(1, int(value))
    except Exception:
        return default


def _base_url(host=None, port=None):
    cfg = None
    if host is None or port is None:
        cfg = get_ai_config()
    host = (host or cfg['host']).strip()
    port = str(port or cfg['port']).strip()
    if not host.startswith(('http://', 'https://')):
        host = 'http://' + host
    return f"{host}:{port}"


# --- Ollama HTTP client -----------------------------------------------------

def check_health(host=None, port=None):
    """Probe the Ollama server. Returns (ok, message, installed_model_names)."""
    url = _base_url(host, port) + '/api/tags'
    try:
        resp = requests.get(url, timeout=HEALTH_TIMEOUT)
        resp.raise_for_status()
        models = [m.get('name', '') for m in (resp.json().get('models') or [])]
        return True, f'Connected. {len(models)} model(s) installed.', models
    except requests.exceptions.ConnectionError:
        return False, f'Cannot reach Ollama at {url} (connection refused).', []
    except Exception as e:
        return False, f'Ollama error: {e}', []


def embed_text(text, cfg=None):
    """Embed text via Ollama; returns a normalized list of floats."""
    cfg = cfg or get_ai_config()
    base = _base_url(cfg['host'], cfg['port'])
    payload_text = (text or '')[:MAX_INDEX_CHARS]
    try:
        resp = requests.post(base + '/api/embed',
                             json={'model': cfg['embed_model'], 'input': payload_text},
                             timeout=EMBED_TIMEOUT)
        resp.raise_for_status()
        vec = (resp.json().get('embeddings') or [[]])[0]
    except requests.exceptions.HTTPError:
        # Older Ollama versions only expose /api/embeddings
        resp = requests.post(base + '/api/embeddings',
                             json={'model': cfg['embed_model'], 'prompt': payload_text},
                             timeout=EMBED_TIMEOUT)
        resp.raise_for_status()
        vec = resp.json().get('embedding') or []
    if not vec:
        raise RuntimeError('Ollama returned an empty embedding')
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def chat(messages, cfg=None):
    """Non-streaming chat completion; returns the assistant message text."""
    cfg = cfg or get_ai_config()
    base = _base_url(cfg['host'], cfg['port'])
    resp = requests.post(base + '/api/chat',
                         json={'model': cfg['chat_model'], 'messages': messages,
                               'stream': False, 'options': {'temperature': 0.3}},
                         timeout=CHAT_TIMEOUT)
    resp.raise_for_status()
    content = ((resp.json().get('message') or {}).get('content') or '').strip()
    # Reasoning models may prepend a think block; drop it.
    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
    if not content:
        raise RuntimeError('Ollama returned an empty response')
    return content


# --- Ticket text / index ----------------------------------------------------

def _strip_html(s):
    return re.sub(r'<[^>]+>', ' ', s or '')


def ticket_index_text(ticket):
    """Plain-text representation of a ticket used for embedding and prompts."""
    parts = [ticket.subject or '', _strip_html(ticket.body)]
    try:
        for n in ticket.notes.order_by(sql_text('created_at asc')).all():
            if not n.is_private:
                parts.append(_strip_html(n.content))
    except Exception:
        pass
    joined = '\n'.join(p.strip() for p in parts if p and p.strip())
    joined = re.sub(r'[ \t]+', ' ', joined)
    return joined[:MAX_INDEX_CHARS]


def _content_hash(text):
    return hashlib.sha256((text or '').encode('utf-8', 'replace')).hexdigest()


def _pack_vector(vec):
    return array('f', vec).tobytes()


def _unpack_vector(blob):
    a = array('f')
    a.frombytes(blob or b'')
    return a


def _ensure_embedding(ticket, cfg=None, force=False):
    """Upsert the ticket's embedding if missing or stale. Returns the row or None."""
    cfg = cfg or get_ai_config()
    text_ = ticket_index_text(ticket)
    if not text_.strip():
        return None
    h = _content_hash(text_ + '|' + cfg['embed_model'])
    row = TicketEmbedding.query.filter_by(ticket_id=ticket.id).first()
    if row and row.content_hash == h and not force:
        return row
    vec = embed_text(text_, cfg)
    if row is None:
        row = TicketEmbedding(ticket_id=ticket.id)
        db.session.add(row)
    row.model = cfg['embed_model']
    row.content_hash = h
    row.vector = _pack_vector(vec)
    row.dim = len(vec)
    row.updated_at = datetime.utcnow()
    db.session.commit()
    return row


def run_ai_index(app, batch_size=200):
    """Scheduler job: embed tickets that are new or whose text changed."""
    with app.app_context():
        cfg = get_ai_config()
        if not cfg['enabled']:
            return 0
        ok, msg, _models = check_health(cfg['host'], cfg['port'])
        if not ok:
            Setting.set('AI_LAST_ERROR', msg)
            app.logger.warning('AI index skipped: %s', msg)
            return 0
        done = 0
        errors = 0
        tickets = Ticket.query.order_by(Ticket.id.asc()).all()
        by_ticket = {e.ticket_id: e for e in TicketEmbedding.query.all()}
        for t in tickets:
            existing = by_ticket.get(t.id)
            # Cheap pre-check to avoid recomputing text for indexed tickets that
            # haven't been updated since their embedding was written.
            if existing and existing.content_hash and existing.updated_at and \
                    (t.updated_at or t.created_at) and (t.updated_at or t.created_at) <= existing.updated_at:
                continue
            try:
                _ensure_embedding(t, cfg)
                done += 1
            except Exception as e:
                errors += 1
                db.session.rollback()
                Setting.set('AI_LAST_ERROR', f'Embedding ticket #{t.id}: {str(e)[:300]}')
                app.logger.error('AI index: failed to embed ticket %s: %s', t.id, e)
                if errors >= 3:
                    app.logger.warning('AI index: aborting run after repeated errors')
                    break
            if done >= batch_size:
                break
        Setting.set('AI_INDEX_LAST_RUN', datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC'))
        if done and not errors:
            Setting.set('AI_LAST_ERROR', '')
        if done:
            app.logger.info('AI index: embedded %d ticket(s)', done)
        return done


def find_similar(ticket, top_n=None, cfg=None):
    """Return [{'ticket': Ticket, 'score': float}] most similar to the given ticket.

    Uses only stored vectors — works even when the AI box is offline, as long
    as the ticket itself has been indexed.
    """
    cfg = cfg or get_ai_config()
    top_n = top_n or cfg['similar_count']
    me = TicketEmbedding.query.filter_by(ticket_id=ticket.id).first()
    if me is None or not me.vector:
        return []
    rows = (TicketEmbedding.query
            .filter(TicketEmbedding.ticket_id != ticket.id,
                    TicketEmbedding.dim == me.dim,
                    TicketEmbedding.model == me.model)
            .all())
    if not rows:
        return []
    try:
        import numpy as np
        matrix = np.frombuffer(b''.join(r.vector for r in rows), dtype=np.float32).reshape(len(rows), me.dim)
        query = np.frombuffer(me.vector, dtype=np.float32)
        scores = matrix @ query
        order = np.argsort(scores)[::-1][:top_n]
        ranked = [(rows[i], float(scores[i])) for i in order]
    except ImportError:
        query = _unpack_vector(me.vector)
        scored = []
        for r in rows:
            v = _unpack_vector(r.vector)
            scored.append((r, sum(a * b for a, b in zip(query, v))))
        scored.sort(key=lambda x: x[1], reverse=True)
        ranked = scored[:top_n]
    ids = [r.ticket_id for r, _ in ranked]
    tickets = {t.id: t for t in Ticket.query.filter(Ticket.id.in_(ids)).all()}
    return [{'ticket': tickets[r.ticket_id], 'score': s}
            for r, s in ranked if r.ticket_id in tickets]


# --- Suggested replies ------------------------------------------------------

SUGGESTION_SYSTEM_PROMPT = (
    'You are an experienced IT helpdesk technician writing a first reply to a '
    'support ticket. Write in plain text (no markdown, no headers, no bullet '
    'symbols like ** or #). Be professional, friendly, and concise. Suggest '
    'concrete troubleshooting steps or a resolution when the ticket and past '
    'similar tickets give you enough information; otherwise ask the most useful '
    'clarifying questions. Never invent account details, policies, or promises. '
    'Do not include a subject line. Sign off simply as "IT Support" unless told '
    'otherwise.'
)


def _build_suggestion_prompt(ticket, similar, cfg):
    lines = ['A new support ticket needs a reply.', '',
             f'TICKET #{ticket.id}',
             f'Subject: {ticket.subject or "(none)"}',
             f'From: {ticket.requester_name or ticket.requester_email or "unknown requester"}',
             'Description:',
             _strip_html(ticket.body)[:MAX_INDEX_CHARS] or '(no description)']
    if similar:
        lines += ['', 'PAST SIMILAR TICKETS (how similar issues were handled before):']
        for item in similar:
            st = item['ticket']
            lines.append(f'--- Ticket #{st.id}: {st.subject or "(no subject)"} [status: {st.status}]')
            body = _strip_html(st.body).strip()
            if body:
                lines.append('Description: ' + body[:800])
            try:
                public_notes = [n for n in st.notes.order_by(sql_text('created_at asc')).all() if not n.is_private]
            except Exception:
                public_notes = []
            notes_text = ' | '.join(_strip_html(n.content).strip()[:400] for n in public_notes[-3:])
            if notes_text:
                lines.append('Replies sent: ' + notes_text[:MAX_CONTEXT_TICKETS_CHARS])
    lines += ['', 'Write the reply to the requester now.']
    return '\n'.join(lines)


def _plain_text_to_html(text):
    paragraphs = re.split(r'\n\s*\n', (text or '').strip())
    html_parts = []
    for p in paragraphs:
        if p.strip():
            html_parts.append('<p>' + _html.escape(p.strip()).replace('\n', '<br>') + '</p>')
    return ''.join(html_parts)


def generate_suggestion(app, ticket_id):
    """Generate (or regenerate) the AI-suggested reply for one ticket."""
    from ..utils.html_sanitize import sanitize_rich_text
    with app.app_context():
        cfg = get_ai_config()
        if not cfg['enabled']:
            return False
        ticket = db.session.get(Ticket, ticket_id)
        if ticket is None:
            return False
        row = TicketAISuggestion.query.filter_by(ticket_id=ticket_id).first()
        if row is None:
            row = TicketAISuggestion(ticket_id=ticket_id, status='pending')
            db.session.add(row)
            try:
                db.session.commit()
            except Exception:
                # Lost a create race with another worker; use its row.
                db.session.rollback()
                row = TicketAISuggestion.query.filter_by(ticket_id=ticket_id).first()
                if row is None:
                    return False
        # Atomic claim so the scheduler and a web-kicked thread can't both run.
        claimed = db.session.execute(
            sql_text("UPDATE ticket_ai_suggestion SET status='generating', updated_at=:now "
                     "WHERE id=:id AND status IN ('pending','failed','ready','dismissed')"),
            {'id': row.id, 'now': datetime.utcnow()}
        )
        db.session.commit()
        if claimed.rowcount != 1:
            return False
        db.session.refresh(row)
        try:
            try:
                _ensure_embedding(ticket, cfg)
            except Exception:
                db.session.rollback()  # similar-ticket context is best-effort
            similar = find_similar(ticket, cfg=cfg)
            prompt = _build_suggestion_prompt(ticket, similar, cfg)
            reply = chat([{'role': 'system', 'content': SUGGESTION_SYSTEM_PROMPT},
                          {'role': 'user', 'content': prompt}], cfg)
            row.content = sanitize_rich_text(_plain_text_to_html(reply))
            row.model = cfg['chat_model']
            row.status = 'ready'
            row.error = None
        except Exception as e:
            db.session.rollback()
            row = TicketAISuggestion.query.filter_by(ticket_id=ticket_id).first()
            if row is None:
                return False
            row.status = 'failed'
            row.error = str(e)[:1000]
            app.logger.error('AI suggestion failed for ticket %s: %s', ticket_id, e)
        row.updated_at = datetime.utcnow()
        db.session.commit()
        return row.status == 'ready'


def run_ai_auto_suggest(app):
    """Scheduler job: draft replies for recent tickets and retry stuck rows."""
    with app.app_context():
        cfg = get_ai_config()
        if not cfg['enabled']:
            return 0
        ok, msg, _models = check_health(cfg['host'], cfg['port'])
        if not ok:
            Setting.set('AI_LAST_ERROR', msg)
            return 0
        now = datetime.utcnow()
        # Recover rows stranded in 'generating' by a crashed process
        try:
            db.session.execute(
                sql_text("UPDATE ticket_ai_suggestion SET status='pending' "
                         "WHERE status='generating' AND updated_at < :cutoff"),
                {'cutoff': now - STALE_GENERATING}
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

        todo = []
        # Explicit requests first (web 'Generate' clicks the scheduler should honor)
        pending = (TicketAISuggestion.query
                   .filter_by(status='pending')
                   .order_by(TicketAISuggestion.updated_at.asc())
                   .limit(SUGGESTIONS_PER_RUN).all())
        todo.extend(r.ticket_id for r in pending)
        # Then new tickets without any suggestion yet
        if cfg['auto_suggest'] and len(todo) < SUGGESTIONS_PER_RUN:
            cutoff = now - AUTO_SUGGEST_WINDOW
            with_suggestion = {r.ticket_id for r in TicketAISuggestion.query.all()}
            fresh = (Ticket.query
                     .filter(Ticket.created_at >= cutoff, Ticket.closed_at.is_(None))
                     .order_by(Ticket.created_at.asc())
                     .all())
            todo.extend(t.id for t in fresh if t.id not in with_suggestion)
        count = 0
        for ticket_id in todo[:SUGGESTIONS_PER_RUN]:
            if generate_suggestion(app, ticket_id):
                count += 1
        if count:
            app.logger.info('AI auto-suggest: drafted %d repl(y/ies)', count)
        return count


def kick_generate(ticket_id):
    """Fire-and-forget generation from a web request (mirrors mailer dev drain)."""
    try:
        app = current_app._get_current_object()
    except Exception:
        return
    threading.Thread(target=generate_suggestion, args=(app, ticket_id), daemon=True).start()


def kick_index():
    """In single-process dev there is no scheduler; index opportunistically."""
    import os
    role = (os.getenv('HELPFULDJINN_ROLE') or '').strip().lower()
    if role in ('web', 'scheduler'):
        return
    try:
        app = current_app._get_current_object()
    except Exception:
        return
    threading.Thread(target=run_ai_index, args=(app,), daemon=True).start()
