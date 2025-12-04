import os
import time
from datetime import datetime, timezone
from flask import current_app
from .. import db
from ..models import Ticket, Setting, AllowedDomain, TicketAttachment, Contact, DenyFilter, TicketNote, User, EmailCheck, EmailCheckEntry, Asset
from .ms_graph import (
    get_msal_app,
    get_access_token,
    get_unread_messages,
    get_message_html,
    list_attachments,
    download_file_attachment,
    mark_message_read,
    send_mail,
)
from pathlib import Path
import re
import html as _html
import bleach
import ftplib
from io import BytesIO


def _html_to_text_lite(html: str) -> str:
    """Very light HTML to text: strip tags and unescape entities."""
    if not html:
        return ''
    # Remove script/style content
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.I | re.S)
    # Replace <br> and </p> with newlines
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"</p>", "\n", html, flags=re.I)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", html)
    # Unescape entities
    return _html.unescape(text).strip()


def _extract_new_message_segment(text: str) -> str:
    """Keep only the new reply text and stop at common reply markers."""
    if not text:
        return ''
    lines = text.splitlines()
    out = []
    # Common markers that indicate the start of quoted/original message
    stop_markers = [
        'from: help desk',
        'from: helpdesk',
        '-----original message-----',
        '________________________________',
        'on ', # "On Dec 3, 2025, at" style quotes
        'sent from',
        '> ',  # Quoted line marker
        'approval request -',  # Our own subject being quoted
    ]
    for line in lines:
        line_lower = line.lower().strip()
        # Stop at quoted previous message marker
        should_stop = False
        for marker in stop_markers:
            if marker in line_lower:
                # For "on " marker, be more specific - must look like a date reference
                if marker == 'on ' and not any(x in line_lower for x in ['wrote:', 'sent:', 'at ']):
                    continue
                should_stop = True
                break
        if should_stop:
            break
        out.append(line)
    # Trim trailing blank lines
    while out and not out[-1].strip():
        out.pop()
    result = "\n".join(out).strip()
    # Limit to reasonable length (500 chars max for response)
    if len(result) > 500:
        result = result[:500] + '...'
    return result


def poll_ms_graph(app=None):
    """Poll Microsoft Graph for unread emails.

    Enhancements:
      - Persistent lock in Setting keys to avoid overlapping runs and detect stale locks.
      - Detailed start/finish logging with duration and counts.
      - Per-message timing (every few messages) and early abort if exceeding max runtime.
      - Stale lock recovery: if previous run marked running but exceeded threshold, proceed and overwrite lock.
    """
    # Ensure app context exists
    if app is not None:
        ctx = app.app_context()
        ctx.push()
    else:
        # Try to use current_app if available
        try:
            ctx = current_app.app_context()
            ctx.push()
        except Exception:
            ctx = None

    # ---------------- New lock + metrics section (clean) ----------------
    logger = None
    try:
        logger = current_app.logger if current_app else None
    except Exception:
        logger = None

    start_ts = time.time()
    now_iso = datetime.utcnow().replace(tzinfo=timezone.utc).isoformat()
    LOCK_FLAG_KEY = "EMAIL_POLL_RUNNING"
    LOCK_STARTED_KEY = "EMAIL_POLL_STARTED_AT"
    LAST_FINISHED_KEY = "EMAIL_POLL_LAST_FINISHED_AT"
    LAST_DURATION_KEY = "EMAIL_POLL_LAST_DURATION_MS"
    LAST_RESULT_KEY = "EMAIL_POLL_LAST_RESULT"

    try:
        interval_setting = int(Setting.get("POLL_INTERVAL_SECONDS", os.getenv("POLL_INTERVAL_SECONDS", "60")))
    except Exception:
        interval_setting = 60
    stale_threshold = max(180, interval_setting * 5)
    try:
        max_sec_env = int(os.getenv("POLL_MAX_SECONDS", "0"))
    except Exception:
        max_sec_env = 0
    max_runtime = max_sec_env or (50 if interval_setting <= 90 else int(interval_setting * 0.8))

    # Acquire / check lock
    skip_due_active = False
    try:
        running_flag = Setting.get(LOCK_FLAG_KEY, "0")
        started_at_val = Setting.get(LOCK_STARTED_KEY, "")
        stale = False
        if running_flag == "1" and started_at_val:
            try:
                prev_dt = datetime.fromisoformat(started_at_val)
                age = (datetime.utcnow().replace(tzinfo=timezone.utc) - prev_dt).total_seconds()
                if age > stale_threshold:
                    stale = True
                    if logger:
                        logger.warning("email_poll: stale lock age=%ss > %s; overriding", int(age), stale_threshold)
            except Exception:
                stale = True
        if running_flag == "1" and not stale:
            if logger:
                logger.info("email_poll: previous run still active; skipping")
            skip_due_active = True
        else:
            Setting.set(LOCK_FLAG_KEY, "1")
            Setting.set(LOCK_STARTED_KEY, now_iso)
    except Exception:
        if logger:
            logger.warning("email_poll: lock acquisition failed; proceeding anyway")

    result_status = "unknown"
    if skip_due_active:
        if ctx is not None:
            ctx.pop()
        return

    try:
        # Determine which services are enabled
        ms_enabled = (Setting.get('MS_ENABLED', '1') or '1') in ('1','true','on','yes')
        # Create an EmailCheck row up front so FTP-only runs still have a place to log
        check = EmailCheck(checked_at=datetime.utcnow(), new_count=0)
        db.session.add(check)
        db.session.commit()

        messages = []
        token = None
        user_email = None
        if ms_enabled:
            msal_app = get_msal_app()
            user_email = Setting.get("MS_USER_EMAIL", None) or os.getenv("MS_USER_EMAIL")
            if msal_app and user_email:
                token = get_access_token(msal_app)
                if token:
                    messages = get_unread_messages(token, user_email) or []
                    # Update check with message count
                    try:
                        check.new_count = len(messages)
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                else:
                    if logger:
                        logger.warning("email_poll: could not acquire token; skipping MS Graph")
            else:
                if logger:
                    logger.info("email_poll: MS disabled or missing config; skipping MS Graph")

        # If MS enabled and we had no messages, add a 'none' log for visibility
        if ms_enabled and not messages:
            try:
                db.session.add(EmailCheckEntry(check_id=check.id, sender='', subject='No New Messages', action='none', ticket_id=None, note=''))
                db.session.commit()
            except Exception:
                db.session.rollback()
        tickets_created = 0
        notes_created = 0
        to_mark_read = []
        allowed = set(d.domain.lower() for d in AllowedDomain.query.all())
        deny_phrases = [d.phrase.lower() for d in DenyFilter.query.all()]

        if logger:
            logger.info("email_poll: start messages=%d interval=%ds max_runtime=%ds (ms_enabled=%s)", len(messages), interval_setting, max_runtime, str(ms_enabled))

        for idx, m in enumerate(messages):
            if (time.time() - start_ts) > max_runtime:
                if logger:
                    logger.error("email_poll: aborting run after %ss (max=%s) processed=%d", int(time.time()-start_ts), max_runtime, idx)
                result_status = "timeout_abort"
                break
            msg_id = m.get("id")
            subject = m.get("subject") or "(no subject)"
            from_addr = None
            from_name = None
            try:
                email_obj = m.get("from", {}).get("emailAddress", {})
                from_addr = email_obj.get("address")
                from_name = email_obj.get("name")
            except Exception:
                pass

            # Prefer Reply-To over From for requester identity
            reply_addr = None
            reply_name = None
            try:
                rlist = m.get("replyTo") or []
                if isinstance(rlist, list) and rlist:
                    r_email = (rlist[0] or {}).get("emailAddress", {})
                    reply_addr = r_email.get("address")
                    reply_name = r_email.get("name")
            except Exception:
                pass

            requester_addr = reply_addr or from_addr
            requester_name = reply_name or from_name
            # Prefer full HTML body
            body_html = get_message_html(token, user_email, msg_id) or m.get("bodyPreview") or ""

            # Deny filter: if any phrase appears in subject, mark read and skip
            subj_lc = subject.lower()
            if deny_phrases and any(p in subj_lc for p in deny_phrases):
                try:
                    mark_message_read(token, user_email, msg_id)
                except Exception:
                    pass
                try:
                    db.session.add(EmailCheckEntry(check_id=check.id, sender=(reply_addr or from_addr or ''), subject=subject or '', action='filtered_deny', ticket_id=None, note='Matched deny filter'))
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                continue

            # Check for Approval Request replies first (before regular ticket replies)
            # Subject format: "RE: Approval Request - Ticket#<id> - ..."
            approval_match = re.search(r"Approval\s+Request\s*[-–—]\s*Ticket\s*#?\s*(\d+)", subject or "", flags=re.I)
            if approval_match:
                try:
                    tid = int(approval_match.group(1))
                except Exception:
                    tid = None
                if tid:
                    from ..models import ApprovalRequest
                    existing_ticket = Ticket.query.get(tid)
                    if existing_ticket:
                        # Find pending approval request for this ticket where the sender is the manager
                        sender_email = (from_addr or '').strip().lower()
                        pending_approval = (
                            ApprovalRequest.query
                            .join(Contact, ApprovalRequest.manager_contact_id == Contact.id)
                            .filter(
                                ApprovalRequest.ticket_id == tid,
                                ApprovalRequest.status == 'pending',
                                Contact.email.ilike(sender_email)
                            )
                            .first()
                        )
                        if pending_approval:
                            # Extract the response text
                            text_body = _html_to_text_lite(body_html)
                            new_text = _extract_new_message_segment(text_body)
                            response_text = (new_text or text_body or (m.get("bodyPreview") or "")).strip().lower()
                            
                            # Determine if approved or denied using expanded word lists
                            # Approval keywords (partial matches work - e.g., "approving" contains "approv")
                            approval_keywords = ['approv', 'yes', 'confirmed', 'confirm', 'authorize', 'authoriz', 'granted', 'grant', 'accept', 'agreed', 'agree', 'proceed', 'go ahead', 'lgtm', 'looks good', 'sounds good', 'fine', 'okay', 'ok', '👍', '✓', '✅']
                            # Denial keywords
                            denial_keywords = ['deny', 'denied', 'denial', 'no', 'reject', 'declined', 'decline', 'refused', 'refuse', 'disapprov', 'not approved', 'cannot approve', 'can\'t approve', 'dont approve', 'don\'t approve', 'hold off', 'wait', 'stop', '👎', '❌', '✗']
                            
                            is_approved = any(kw in response_text for kw in approval_keywords)
                            is_denied = any(kw in response_text for kw in denial_keywords)
                            
                            # If both or neither, check the first word more strictly
                            if (is_approved and is_denied) or (not is_approved and not is_denied):
                                first_word = response_text.split()[0] if response_text.split() else ''
                                # Strict first-word matching for ambiguous cases
                                approval_first_words = ['approved', 'approve', 'yes', 'ok', 'okay', 'confirmed', 'granted', 'accepted', 'agreed', 'proceed', 'fine', 'lgtm', '👍', '✓', '✅']
                                denial_first_words = ['denied', 'deny', 'no', 'rejected', 'reject', 'declined', 'refused', 'stop', 'wait', 'hold', '👎', '❌', '✗']
                                if first_word in approval_first_words:
                                    is_approved = True
                                    is_denied = False
                                elif first_word in denial_first_words:
                                    is_denied = True
                                    is_approved = False
                            
                            # Update the approval request
                            from datetime import datetime as _dt
                            if is_approved:
                                pending_approval.status = 'approved'
                                status_text = 'APPROVED'
                            elif is_denied:
                                pending_approval.status = 'denied'
                                status_text = 'DENIED'
                            else:
                                # Couldn't determine - add as note but don't change status
                                status_text = 'RESPONSE RECEIVED (unclear)'
                            
                            pending_approval.response_note = new_text or text_body or ''
                            pending_approval.responded_at = _dt.utcnow()
                            
                            # Add a note to the ticket
                            manager = pending_approval.manager_contact
                            note_content = f"<p><strong>Approval Response: {status_text}</strong></p>"
                            note_content += f"<p>From: {manager.name or manager.email} ({manager.email})</p>"
                            if new_text:
                                note_content += f"<p>Response: {_html.escape(new_text).replace(chr(10),'<br>')}</p>"
                            
                            approval_note = TicketNote(
                                ticket_id=existing_ticket.id,
                                author_id=None,
                                content=note_content,
                                is_private=False
                            )
                            db.session.add(approval_note)
                            
                            # Notify the requesting tech
                            try:
                                tech = pending_approval.requesting_tech
                                if tech and tech.email:
                                    tech_subject = f"Approval {status_text} - Ticket#{existing_ticket.id}"
                                    tech_body = f"""
                                    <p>Hello {tech.name or 'Tech'},</p>
                                    <p>The approval request for <strong>Ticket #{existing_ticket.id} - {existing_ticket.subject}</strong> has been <strong>{status_text}</strong> by {manager.name or manager.email}.</p>
                                    {f'<p><strong>Manager Response:</strong> {_html.escape(new_text or "").replace(chr(10),"<br>")}</p>' if new_text else ''}
                                    <p>Thank you,<br>Help Desk</p>
                                    """
                                    send_mail(tech.email, tech_subject, tech_body, to_name=tech.name)
                            except Exception:
                                pass
                            
                            try:
                                db.session.add(EmailCheckEntry(
                                    check_id=check.id,
                                    sender=sender_email,
                                    subject=subject or '',
                                    action='approval_response',
                                    ticket_id=existing_ticket.id,
                                    note=f'Approval {status_text}'
                                ))
                                db.session.commit()
                            except Exception:
                                db.session.rollback()
                            
                            to_mark_read.append(msg_id)
                            continue
                        else:
                            # No pending approval found (already processed, wrong sender, etc.)
                            # Still mark as read and skip to avoid duplicate processing
                            try:
                                db.session.add(EmailCheckEntry(
                                    check_id=check.id,
                                    sender=sender_email,
                                    subject=subject or '',
                                    action='approval_no_pending',
                                    ticket_id=existing_ticket.id,
                                    note='Approval reply but no pending request found'
                                ))
                                db.session.commit()
                            except Exception:
                                db.session.rollback()
                            to_mark_read.append(msg_id)
                            continue

            # If subject includes Ticket #<id> (with or without space), treat as a reply note instead of a new ticket
            ticket_match = re.search(r"Ticket\s*#\s*(\d+)", subject or "", flags=re.I)
            if ticket_match:
                try:
                    tid = int(ticket_match.group(1))
                except Exception:
                    tid = None
                if tid:
                    existing = Ticket.query.get(tid)
                    if existing:
                        # Extract only the new portion of the message
                        text_body = _html_to_text_lite(body_html)
                        new_text = _extract_new_message_segment(text_body)
                        note_content = new_text or text_body or (m.get("bodyPreview") or "")
                        # Convert to safe HTML with links and line breaks
                        def _set_target_rel(attrs, new=False):
                            href = attrs.get('href')
                            if href:
                                attrs['target'] = '_blank'
                                rel = attrs.get('rel', '') or ''
                                rel_vals = set(rel.split()) if rel else set()
                                rel_vals.update(['noopener', 'noreferrer'])
                                attrs['rel'] = ' '.join(sorted(rel_vals))
                            return attrs
                        # If ticket was closed, move it back to in_progress on customer reply
                        try:
                            if (existing.status or '').lower() == 'closed':
                                existing.status = 'in_progress'
                                existing.closed_at = None
                        except Exception:
                            pass
                        # Build sanitized HTML for note
                        note_html = bleach.linkify(_html.escape(note_content).replace('\n','<br>'), callbacks=[_set_target_rel])
                        # Notes created from replies are system-received; leave is_private as None/False
                        note = TicketNote(ticket_id=existing.id, author_id=None, content=note_html, is_private=False)
                        db.session.add(note)
                        # Save attachments for replies as well (PDFs and images)
                        try:
                            atts = list_attachments(token, user_email, msg_id)
                            subdir = (Setting.get('ATTACHMENTS_DIR_REL', 'attachments') or 'attachments').strip()
                            subdir = subdir.replace('\\','/').lstrip('/') or 'attachments'
                            base = (Setting.get('ATTACHMENTS_BASE', 'instance') or 'instance').strip().lower()
                            root = current_app.static_folder if base == 'static' else current_app.instance_path
                            save_dir = Path(root) / subdir / str(existing.id)
                            save_dir.mkdir(parents=True, exist_ok=True)
                            for a in atts:
                                if a.get('@odata.type') != '#microsoft.graph.fileAttachment':
                                    continue
                                name = a.get('name') or 'attachment'
                                ctype = a.get('contentType') or ''
                                if not (ctype.startswith('image/') or ctype == 'application/pdf' or name.lower().endswith(('.png','.jpg','.jpeg','.gif','.pdf'))):
                                    continue
                                content = a.get('contentBytes')
                                if content is None:
                                    full = download_file_attachment(token, user_email, a.get('id'))
                                    if full:
                                        content = full.get('contentBytes')
                                if not content:
                                    continue
                                import base64
                                data = base64.b64decode(content)
                                target = save_dir / name
                                i = 1
                                while target.exists():
                                    stem = Path(name).stem
                                    suffix = Path(name).suffix
                                    target = save_dir / f"{stem}_{i}{suffix}"
                                    i += 1
                                target.write_bytes(data)
                                rel_path = f"{subdir}/{existing.id}/{target.name}"
                                db.session.add(TicketAttachment(ticket_id=existing.id, filename=target.name, content_type=ctype, static_path=rel_path, size_bytes=len(data)))
                            # If no files were saved, remove the empty attachment directory
                            try:
                                if save_dir.exists() and not any(save_dir.iterdir()):
                                    save_dir.rmdir()
                            except Exception:
                                pass
                        except Exception:
                            pass
                        notes_created += 1
                        try:
                            db.session.add(EmailCheckEntry(check_id=check.id, sender=(reply_addr or from_addr or ''), subject=subject or '', action='append_ticket', ticket_id=existing.id, note=f'Reply to Ticket #{existing.id}'))
                            db.session.commit()
                        except Exception:
                            db.session.rollback()
                        to_mark_read.append(msg_id)
                        # Notify assigned tech, if any
                        try:
                            if existing.assignee_id:
                                tech = User.query.get(existing.assignee_id)
                                if tech and tech.email:
                                    subj = f"Ticket#{existing.id} - New reply"
                                    html_body = f"<p>{_html.escape(note_content).replace('\n','<br>')}</p>"
                                    send_mail(tech.email, subj, html_body, to_name=getattr(tech, 'name', None))
                        except Exception:
                            pass
                        # Continue to next message (do not create a new ticket)
                        continue

            # Domain filter: use actual From address for allowlist checks (only for new tickets)
            if allowed and from_addr:
                domain = (from_addr.split('@')[-1] or '').lower()
                if domain not in allowed:
                    # Not allowed: mark as read and skip importing
                    try:
                        mark_message_read(token, user_email, msg_id)
                    except Exception:
                        pass
                    try:
                        db.session.add(EmailCheckEntry(check_id=check.id, sender=(reply_addr or from_addr or ''), subject=subject or '', action='filtered_domain', ticket_id=None, note=f'Domain not allowed: {domain}'))
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                    continue

            # Deduplicate by external_id (skip importing duplicates) for new tickets
            if Ticket.query.filter_by(external_id=msg_id).first():
                try:
                    db.session.add(EmailCheckEntry(check_id=check.id, sender=(reply_addr or from_addr or ''), subject=subject or '', action='duplicate', ticket_id=None, note='Duplicate external_id'))
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                continue

            # Upsert contact by email
            contact = None
            if requester_addr:
                contact = Contact.query.filter_by(email=(requester_addr or '').lower()).first()
                if not contact:
                    contact = Contact(email=requester_addr.lower(), name=requester_name)
                    db.session.add(contact)
                else:
                    # Update name if we have one and it changed
                    if requester_name and contact.name != requester_name:
                        contact.name = requester_name

            t = Ticket(
                external_id=msg_id,
                subject=subject,
                requester=requester_addr,  # legacy
                requester_name=requester_name,
                requester_email=requester_addr,
                body=body_html,
                status="open",
                priority="medium",
            )
            db.session.add(t)
            tickets_created += 1
            db.session.flush()  # ensure t.id
            try:
                db.session.add(EmailCheckEntry(check_id=check.id, sender=(reply_addr or from_addr or ''), subject=subject or '', action='new_ticket', ticket_id=t.id, note='Created new ticket'))
                db.session.commit()
            except Exception:
                db.session.rollback()
            to_mark_read.append(msg_id)
            # Save attachments (PDFs and images)
            try:
                atts = list_attachments(token, user_email, msg_id)
                subdir = (Setting.get('ATTACHMENTS_DIR_REL', 'attachments') or 'attachments').strip()
                subdir = subdir.replace('\\','/').lstrip('/') or 'attachments'
                base = (Setting.get('ATTACHMENTS_BASE', 'instance') or 'instance').strip().lower()
                root = current_app.static_folder if base == 'static' else current_app.instance_path
                save_dir = Path(root) / subdir / str(t.id)
                save_dir.mkdir(parents=True, exist_ok=True)
                for a in atts:
                    # Only file attachments (not item/refs)
                    if a.get('@odata.type') != '#microsoft.graph.fileAttachment':
                        continue
                    name = a.get('name') or 'attachment'
                    ctype = a.get('contentType') or ''
                    # Filter to PDFs and images
                    if not (ctype.startswith('image/') or ctype == 'application/pdf' or name.lower().endswith(('.png','.jpg','.jpeg','.gif','.pdf'))):
                        continue
                    # Download attachment content (base64 in contentBytes via attachments/{id} or inline in list)
                    content = a.get('contentBytes')
                    if content is None:
                        full = download_file_attachment(token, user_email, a.get('id'))
                        if full:
                            content = full.get('contentBytes')
                    if not content:
                        continue
                    import base64
                    data = base64.b64decode(content)
                    target = save_dir / name
                    # Avoid overwriting with same name
                    i = 1
                    while target.exists():
                        stem = Path(name).stem
                        suffix = Path(name).suffix
                        target = save_dir / f"{stem}_{i}{suffix}"
                        i += 1
                    target.write_bytes(data)
                    # Build a URL path with forward slashes for static serving
                    rel_path = f"{subdir}/{t.id}/{target.name}"
                    db.session.add(TicketAttachment(ticket_id=t.id, filename=target.name, content_type=ctype, static_path=rel_path, size_bytes=len(data)))
                # If no files were saved, remove the empty attachment directory
                try:
                    if save_dir.exists() and not any(save_dir.iterdir()):
                        save_dir.rmdir()
                except Exception:
                    pass
            except Exception:
                # Don’t fail the whole cycle on attachment issues
                pass

        # After processing email, optionally poll FTP (HDWish)
        try:
            ftp_enabled = (Setting.get('FTP_ENABLED', '0') or '0') in ('1','true','on','yes')
        except Exception:
            ftp_enabled = False
        if ftp_enabled:
            try:
                created_ftp = _poll_ftp_and_import(check)
                tickets_created += (created_ftp or 0)
            except Exception as _e:
                # Log a failure entry for visibility
                try:
                    db.session.add(EmailCheckEntry(check_id=check.id, sender='', subject='FTP Import Error', action='error', ticket_id=None, note=str(_e)))
                    db.session.commit()
                except Exception:
                    db.session.rollback()

        if tickets_created or notes_created:
            db.session.commit()
        # After successful processing, mark messages as read
        if to_mark_read:
            for mid in to_mark_read:
                try:
                    mark_message_read(token, user_email, mid)
                except Exception:
                    pass
        if result_status == "unknown":
            result_status = "ok"
        # Cleanup old email logs (>7 days)
        try:
            from datetime import timedelta as _td
            cutoff = datetime.utcnow() - _td(days=7)
            old = EmailCheck.query.filter(EmailCheck.checked_at < cutoff).all()
            if old:
                for c in old:
                    try:
                        db.session.delete(c)
                    except Exception:
                        pass
                db.session.commit()
        except Exception:
            db.session.rollback()
        if logger:
            logger.info("email_poll: finished tickets_created=%d notes_created=%d duration_ms=%d status=%s", tickets_created, notes_created, int((time.time()-start_ts)*1000), result_status)
    except Exception as e:
        # Log to Flask logger if available
        try:
            current_app.logger.exception("Error polling MS Graph: %s", e)
        except Exception:
            pass
        result_status = "exception"
    finally:
        # Release lock & persist metrics
        try:
            Setting.set(LOCK_FLAG_KEY, "0")
            Setting.set(LAST_FINISHED_KEY, datetime.utcnow().replace(tzinfo=timezone.utc).isoformat())
            Setting.set(LAST_DURATION_KEY, str(int((time.time()-start_ts)*1000)))
            Setting.set(LAST_RESULT_KEY, result_status)
        except Exception:
            if logger:
                logger.warning("email_poll: failed to record metrics / release lock")
        if ctx is not None:
            ctx.pop()


def _poll_ftp_and_import(check_row: EmailCheck) -> int:
    """Poll configured FTP for HDWish ticket folders and import them.

    Returns count of tickets created.
    """
    host = Setting.get('FTP_HOST', '')
    try:
        port = int(Setting.get('FTP_PORT', '21') or '21')
    except Exception:
        port = 21
    user = Setting.get('FTP_USER', '') or 'anonymous'
    pwd = Setting.get('FTP_PASS', '') or ''
    base = (Setting.get('FTP_BASE_DIR', '') or '').strip()
    subdir = (Setting.get('FTP_SUBDIR', 'HDWish Data') or 'HDWish Data').strip()
    if not host:
        return 0
    ftp = ftplib.FTP()
    ftp.connect(host, port, timeout=15)
    ftp.login(user=user, passwd=pwd)
    # Navigate to base/subdir
    if base:
        ftp.cwd(base)
    if subdir:
        ftp.cwd(subdir)

    # Helper to check if name is directory by attempting cwd
    def is_dir(name: str) -> bool:
        cur = ftp.pwd()
        try:
            ftp.cwd(name)
            ftp.cwd(cur)
            return True
        except Exception:
            try:
                ftp.cwd(cur)
            except Exception:
                pass
            return False

    folders = ftp.nlst()
    created = 0
    for folder in folders:
        # Ignore obvious files
        if '.' in (folder or ''):
            continue
        if not is_dir(folder):
            continue
        # Unique external_id for dedupe
        external_id = f"ftp://{host}/{(base + '/' if base else '')}{subdir}/{folder}"
        if Ticket.query.filter_by(external_id=external_id).first():
            try:
                db.session.add(EmailCheckEntry(check_id=check_row.id, sender='', subject=folder, action='duplicate', ticket_id=None, note='FTP folder already imported'))
                db.session.commit()
            except Exception:
                db.session.rollback()
            continue
        # Enter folder and find note.txt (case-insensitive)
        ftp.cwd(folder)
        items = ftp.nlst()
        notes_name = None
        for nm in items:
            if nm.lower() == 'note.txt':
                notes_name = nm
                break
        if not notes_name:
            # No notes file; skip folder
            ftp.cwd('..')
            try:
                db.session.add(EmailCheckEntry(check_id=check_row.id, sender='', subject=folder, action='skip', ticket_id=None, note='No note.txt'))
                db.session.commit()
            except Exception:
                db.session.rollback()
            continue
        # Download note.txt
        buf = BytesIO()
        ftp.retrbinary(f'RETR {notes_name}', buf.write)
        text = buf.getvalue().decode('utf-8', errors='replace')
        lines = [ln.strip() for ln in text.splitlines()]
        requester_email = (lines[0] if len(lines) >= 1 else '').strip()
        serial_no = (lines[1] if len(lines) >= 2 else '').strip()
        rest = '\n'.join(lines[2:]) if len(lines) > 2 else ''
        subject = (lines[2].strip() if len(lines) >= 3 and lines[2].strip() else f"HDWish submission ({folder})")
        # Upsert contact
        contact = None
        if requester_email:
            contact = Contact.query.filter_by(email=requester_email.lower()).first()
            if not contact:
                contact = Contact(email=requester_email.lower())
                db.session.add(contact)
        # Build body as simple HTML
        body_html = _html.escape(rest or '').replace('\n', '<br>') if rest else '(no details)'
        t = Ticket(
            external_id=external_id,
            subject=subject,
            requester=requester_email,
            requester_email=requester_email,
            requester_name=getattr(contact, 'name', None),
            body=body_html,
            status='open',
            priority='medium',
            source='ftp'
        )
        # Attempt asset match by serial number
        try:
            if serial_no:
                a = Asset.query.filter_by(serial_number=serial_no).first()
                if a:
                    t.asset_id = a.id
        except Exception:
            pass
        db.session.add(t)
        db.session.flush()
        
        # Delete note.txt from FTP after successful ticket creation
        try:
            ftp.delete(notes_name)
        except Exception:
            pass
        
        # Download images as attachments (if any exist)
        try:
            subdir_rel = (Setting.get('ATTACHMENTS_DIR_REL', 'attachments') or 'attachments').strip()
            subdir_rel = subdir_rel.replace('\\','/').lstrip('/') or 'attachments'
            base_loc = (Setting.get('ATTACHMENTS_BASE', 'instance') or 'instance').strip().lower()
            root = current_app.static_folder if base_loc == 'static' else current_app.instance_path
            save_dir = Path(root) / subdir_rel / str(t.id)
            save_dir.mkdir(parents=True, exist_ok=True)
            exts = ('.png','.jpg','.jpeg','.gif','.bmp')
            for nm in items:
                if nm.lower() == notes_name.lower():
                    continue
                if not nm.lower().endswith(exts):
                    continue
                out = BytesIO()
                ftp.retrbinary(f'RETR {nm}', out.write)
                data = out.getvalue()
                if not data:
                    continue
                target = save_dir / nm
                i = 1
                while target.exists():
                    stem = Path(nm).stem
                    suffix = Path(nm).suffix
                    target = save_dir / f"{stem}_{i}{suffix}"
                    i += 1
                target.write_bytes(data)
                rel_path = f"{subdir_rel}/{t.id}/{target.name}"
                db.session.add(TicketAttachment(ticket_id=t.id, filename=target.name, content_type='', static_path=rel_path, size_bytes=len(data)))
                # Remove image from FTP after successful import of this file
                try:
                    ftp.delete(nm)
                except Exception:
                    pass
            # If no files were saved, remove the empty attachment directory
            try:
                if save_dir.exists() and not any(save_dir.iterdir()):
                    save_dir.rmdir()
            except Exception:
                pass
        except Exception:
            pass
        # Log creation
        try:
            db.session.add(EmailCheckEntry(check_id=check_row.id, sender=requester_email or '', subject=subject or folder, action='new_ticket', ticket_id=t.id, note='FTP import'))
            db.session.commit()
        except Exception:
            db.session.rollback()
        created += 1
        # After successful import, delete the ticket folder on the FTP server
        try:
            # Delete any remaining files in the folder (note.txt already deleted above, but check for any stragglers)
            try:
                remaining = ftp.nlst()
                for nm in remaining:
                    try:
                        ftp.delete(nm)
                    except Exception:
                        pass
            except Exception:
                pass
            # Move up one level and delete the now-empty ticket folder
            ftp.cwd('..')
            try:
                ftp.rmd(folder)
            except Exception:
                pass
        except Exception:
            # If cleanup fails, ensure we're back at the parent directory
            try:
                ftp.cwd('..')
            except Exception:
                pass

    try:
        ftp.quit()
    except Exception:
        pass
    return created


def email_poll_watchdog(app=None):
    """Watchdog to clear stale email poll locks and optionally trigger immediate re-run.

    Runs periodically (e.g., every 5 minutes) to detect a stuck poll run that never cleared its lock.
    """
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
        running_flag = Setting.get("EMAIL_POLL_RUNNING", "0")
        started_at_val = Setting.get("EMAIL_POLL_STARTED_AT", "")
        if running_flag == "1" and started_at_val:
            try:
                prev_dt = datetime.fromisoformat(started_at_val)
                age = (datetime.utcnow().replace(tzinfo=timezone.utc) - prev_dt).total_seconds()
                # Use 15 minutes as emergency stale threshold (independent of interval)
                if age > 900:
                    Setting.set("EMAIL_POLL_RUNNING", "0")
                    Setting.set("EMAIL_POLL_LAST_RESULT", f"watchdog_cleared_stale_after_{int(age)}s")
                    if logger:
                        logger.error("email_poll_watchdog: cleared stale poll lock age=%ss", int(age))
            except Exception:
                # On parse failure just clear
                Setting.set("EMAIL_POLL_RUNNING", "0")
                Setting.set("EMAIL_POLL_LAST_RESULT", "watchdog_cleared_parse_error")
                if logger:
                    logger.error("email_poll_watchdog: cleared lock due to parse error")
    except Exception:
        if logger:
            logger.warning("email_poll_watchdog: encountered exception")
    finally:
        if ctx is not None:
            ctx.pop()
