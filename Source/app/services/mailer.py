"""Outbound email queue (EmailOutbox) and drain logic.

Web routes call enqueue_mail() and return immediately; the scheduler process
drains the queue every 20 seconds via drain_outbox(). In single-process dev
(no HELPFULDJINN_ROLE set) enqueue_mail kicks a one-shot background thread so
mail still goes out without the scheduler process running.

Rows are claimed with an atomic UPDATE (pending/failed -> sending) so the
scheduler and the dev thread can never double-send the same row.
"""
import json
import os
import threading
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import text

from .. import db
from ..models import EmailOutbox

MAX_ATTEMPTS = 5
BACKOFF = [
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=6),
]
# A row stuck in 'sending' longer than this (crashed worker) is retried.
STALE_SENDING = timedelta(minutes=15)


def enqueue_mail(to_address, subject, html_body, to_name=None, save_to_sent=True,
                 attachments=None, category='other', ticket_id=None):
    """Queue an email for background delivery. Drop-in for ms_graph.send_mail."""
    if isinstance(to_address, (list, tuple, set)):
        addrs = [str(a).strip() for a in to_address if a and str(a).strip()]
    else:
        addrs = [str(to_address).strip()] if to_address else []
    if not addrs:
        return None
    row = EmailOutbox(
        to_json=json.dumps(addrs),
        to_name=to_name,
        subject=(subject or '')[:500],
        html_body=html_body or '',
        attachments_json=json.dumps(attachments) if attachments else None,
        save_to_sent=bool(save_to_sent),
        category=category or 'other',
        ticket_id=ticket_id,
    )
    db.session.add(row)
    db.session.commit()
    _kick_dev_drain()
    return row


def _kick_dev_drain():
    """In single-process dev there is no scheduler; drain opportunistically."""
    role = (os.getenv('HELPFULDJINN_ROLE') or '').strip().lower()
    if role in ('web', 'scheduler'):
        return
    try:
        app = current_app._get_current_object()
    except Exception:
        return
    threading.Thread(target=drain_outbox, args=(app,), daemon=True).start()


def drain_outbox(app, batch_size=25):
    """Send due queued emails. Safe to run concurrently across processes."""
    from .ms_graph import send_mail
    sent = 0
    with app.app_context():
        now = datetime.utcnow()
        # Recover rows stranded in 'sending' by a crashed process
        try:
            db.session.execute(
                text("UPDATE email_outbox SET status='failed' "
                     "WHERE status='sending' AND updated_at < :cutoff"),
                {'cutoff': now - STALE_SENDING}
            )
            db.session.commit()
        except Exception:
            db.session.rollback()

        rows = (EmailOutbox.query
                .filter(EmailOutbox.status.in_(('pending', 'failed')),
                        EmailOutbox.next_attempt_at <= now)
                .order_by(EmailOutbox.id.asc())
                .limit(batch_size)
                .all())
        for row in rows:
            claimed = db.session.execute(
                text("UPDATE email_outbox SET status='sending', attempts=attempts+1, "
                     "updated_at=:now WHERE id=:id AND status IN ('pending','failed')"),
                {'id': row.id, 'now': datetime.utcnow()}
            )
            db.session.commit()
            if claimed.rowcount != 1:
                continue  # another process claimed it first
            db.session.refresh(row)
            try:
                addrs = json.loads(row.to_json or '[]')
                attachments = json.loads(row.attachments_json) if row.attachments_json else None
                ok = send_mail(
                    addrs, row.subject, row.html_body,
                    to_name=row.to_name,
                    save_to_sent=bool(row.save_to_sent),
                    attachments=attachments,
                    category=row.category or 'other',
                    ticket_id=row.ticket_id,
                )
                err = None if ok else 'Send failed (see outgoing email log)'
            except Exception as e:
                ok = False
                err = str(e)[:1000]
            if ok:
                row.status = 'sent'
                row.sent_at = datetime.utcnow()
                row.last_error = None
                sent += 1
            else:
                row.last_error = err
                if (row.attempts or 0) >= MAX_ATTEMPTS:
                    row.status = 'dead'
                else:
                    row.status = 'failed'
                    backoff = BACKOFF[min(max((row.attempts or 1) - 1, 0), len(BACKOFF) - 1)]
                    row.next_attempt_at = datetime.utcnow() + backoff
            db.session.commit()
        if sent and app.logger:
            app.logger.info('Email outbox: sent %d message(s)', sent)
    return sent
