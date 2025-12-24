from datetime import datetime
from typing import Optional
from flask import current_app, url_for

from .. import db
from ..models import Ticket, TicketNote, User
from .ms_graph import send_mail


def _build_ticket_link(ticket_id: int) -> Optional[str]:
    """Best-effort absolute link to the ticket. Returns None if not resolvable."""
    try:
        # Requires SERVER_NAME configured to build external URL without request context
        return url_for('tickets.show_ticket', ticket_id=ticket_id, _external=True)
    except Exception:
        return None


def process_wakeups(app=None) -> None:
    """Find snoozed tickets whose wake time has arrived, notify assigned techs, and clear snooze.

    Behavior:
    - Select tickets where snoozed_until is not null and <= now (UTC).
    - If ticket has an assignee with an email, send a notification email.
    - Add a system note indicating the ticket woke from snooze.
    - Clear snoozed_until to prevent duplicate notifications.
    """
    # Ensure we have an application context
    if app is not None:
        ctx = app.app_context()
        ctx.push()
    else:
        try:
            ctx = current_app.app_context()
            ctx.push()
        except Exception:
            ctx = None

    logger = None
    try:
        logger = current_app.logger if current_app else None
    except Exception:
        logger = None

    try:
        now = datetime.utcnow()
        candidates = (
            Ticket.query
            .filter(Ticket.snoozed_until.isnot(None))
            .filter(Ticket.snoozed_until <= now)
            .all()
        )
        if not candidates:
            return

        sent = 0
        for t in candidates:
            tech: Optional[User] = t.assignee
            # Compose message
            subj = f"Ticket #{t.id} is active again"
            link = None
            try:
                link = _build_ticket_link(t.id)
            except Exception:
                link = None
            requester = t.requester_name or t.requester_email or t.requester or 'Requester'
            body_lines = []
            body_lines.append(f"<p><strong>Ticket #{t.id}</strong>: {t.subject or ''}</p>")
            body_lines.append(f"<p><strong>From:</strong> {requester}</p>")
            if link:
                body_lines.append(f'<p><a href="{link}">Open ticket</a></p>')
            body_lines.append('<p>This ticket has automatically woken from snooze and is visible again.</p>')
            html_body = "\n".join(body_lines)

            # Add system note regardless of email outcome
            try:
                note = TicketNote(
                    ticket_id=t.id,
                    author_id=None,
                    content="<em>System:</em> Ticket woke from snooze.",
                    is_private=True,
                )
                db.session.add(note)
            except Exception:
                pass

            # Clear snooze first to avoid duplicate selection on next run
            t.snoozed_until = None

            # Notify assignee if available
            if tech and tech.email:
                try:
                    ok = send_mail(tech.email, subj, html_body, to_name=getattr(tech, 'name', None), category='ticket_snooze', ticket_id=t.id)
                    if ok:
                        sent += 1
                except Exception:
                    # Don't block the loop on email issues
                    pass
        # Persist changes
        db.session.commit()
        if logger:
            logger.info("snooze_wakeup: processed=%d emailed=%d", len(candidates), sent)
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass
        if logger:
            logger.exception("snooze_wakeup: error while processing wakeups")
    finally:
        if ctx is not None:
            ctx.pop()
