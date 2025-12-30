"""Scheduled AD Password Expiry Check Service.

This service runs daily to check Active Directory for expiring passwords
and creates a ticket if any users have passwords expiring within the warning threshold.
It also sends email notifications to users based on configured notification rules and templates.
"""
from datetime import datetime, timedelta
from typing import List, Dict, Any
from flask import current_app
import traceback
import sys

from .. import db
from ..models import Setting, Ticket, Contact, PasswordExpiryNotification


def run_ad_password_check(app=None) -> None:
    """Check AD for expiring passwords and create a ticket if any are found.
    
    This job:
    1. Queries Active Directory for all contacts' password expiry dates
    2. Updates the Contact records with the expiry information
    3. If any passwords are expiring within the warning threshold, creates a ticket
    """
    ctx = None
    logger = None
    
    # Ensure we have an application context
    try:
        if app is not None:
            ctx = app.app_context()
            ctx.push()
        else:
            try:
                ctx = current_app.app_context()
                ctx.push()
            except Exception:
                ctx = None
    except Exception as e:
        # Log to stderr if we can't get app context - critical error
        print(f'AD Password Check: Failed to create app context: {e}', file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return

    try:
        logger = current_app.logger if current_app else None
    except Exception:
        logger = None

    try:
        if logger:
            logger.info('AD Password Check: Job started')
        else:
            print('AD Password Check: Job started (no logger available)', file=sys.stderr)
        
        # Check if daily password check is enabled
        pwd_check_enabled = (Setting.get('AD_PWD_CHECK_ENABLED', '0') or '0') in ('1', 'true', 'on', 'yes')
        if not pwd_check_enabled:
            if logger:
                logger.info('AD Password Check: Skipped - feature is disabled in settings')
            return
        
        # Check if AD is configured
        ad_enabled = (Setting.get('AD_ENABLED', '0') or '0') in ('1', 'true', 'on', 'yes')
        if not ad_enabled:
            if logger:
                logger.warning('AD Password Check: AD is not enabled')
            return
        
        ad_server = Setting.get('AD_SERVER', '')
        if not ad_server:
            if logger:
                logger.warning('AD Password Check: AD server not configured')
            return
        
        # Run the password check
        if logger:
            logger.info('AD Password Check: Querying Active Directory...')
        expiring_users = _check_ad_passwords(logger)
        
        if expiring_users:
            # Send individual email notifications based on notification rules
            _send_password_expiry_notifications(expiring_users, logger)
            # Create summary ticket for helpdesk
            _create_expiring_passwords_ticket(expiring_users, logger)
        elif logger:
            logger.info('AD Password Check: No expiring passwords found within warning threshold')
        
        if logger:
            logger.info('AD Password Check: Job completed successfully')
            
    except Exception as e:
        if logger:
            logger.exception(f'AD Password Check failed: {e}')
        else:
            # Fallback to stderr if logger unavailable
            print(f'AD Password Check failed: {e}', file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
    finally:
        if ctx is not None:
            try:
                ctx.pop()
            except Exception:
                pass


def _check_ad_passwords(logger) -> List[Dict[str, Any]]:
    """Query AD for password expiry dates and return list of expiring users."""
    try:
        from ldap3 import Server, Connection, ALL, SUBTREE, Tls
        from ldap3.utils.conv import escape_filter_chars
        import ssl
    except ImportError:
        if logger:
            logger.error('AD Password Check: ldap3 package not installed')
        return []
    
    from sqlalchemy import or_
    
    # Get AD settings
    ad_server = Setting.get('AD_SERVER', '')
    ad_port = int(Setting.get('AD_PORT', '389') or '389')
    ad_use_ssl = (Setting.get('AD_USE_SSL', '0') or '0') in ('1', 'true', 'on', 'yes')
    ad_start_tls = (Setting.get('AD_START_TLS', '0') or '0') in ('1', 'true', 'on', 'yes')
    ad_base_dn = Setting.get('AD_BASE_DN', '')
    ad_bind_dn = Setting.get('AD_BIND_DN', '')
    ad_bind_password = Setting.get('AD_BIND_PASSWORD', '')
    warning_days = int(Setting.get('AD_PWD_WARNING_DAYS', '14') or '14')
    
    if not ad_base_dn:
        if logger:
            logger.warning('AD Password Check: Base DN not configured')
        return []
    
    expiring_users = []
    
    try:
        # Configure TLS if needed
        tls_config = None
        if ad_use_ssl or ad_start_tls:
            tls_config = Tls(validate=ssl.CERT_NONE)
        
        # Create server and connection
        server = Server(
            ad_server,
            port=ad_port,
            use_ssl=ad_use_ssl,
            tls=tls_config,
            get_info=ALL,
            connect_timeout=10
        )
        
        conn = Connection(
            server,
            user=ad_bind_dn,
            password=ad_bind_password,
            auto_bind=False,
            raise_exceptions=True
        )
        
        if ad_start_tls and not ad_use_ssl:
            conn.open()
            conn.start_tls()
        
        if not conn.bind():
            if logger:
                logger.error(f'AD Password Check: Failed to bind - {conn.result}')
            return []
        
        # Get all contacts with email addresses (non-archived)
        contacts = Contact.query.filter(
            Contact.email.isnot(None),
            Contact.email != '',
            or_(Contact.archived == False, Contact.archived.is_(None))
        ).all()
        
        # Get domain max password age from AD (default to 90 days if not found)
        max_pwd_age_days = 90
        try:
            conn.search(
                search_base=ad_base_dn,
                search_filter='(objectClass=domain)',
                search_scope=SUBTREE,
                attributes=['maxPwdAge']
            )
            if conn.entries:
                max_pwd_age = conn.entries[0].maxPwdAge.value
                if max_pwd_age:
                    max_pwd_age_days = abs(int(max_pwd_age)) / (10000000 * 60 * 60 * 24)
        except Exception:
            pass
        
        now = datetime.utcnow()
        
        for contact in contacts:
            email = contact.email.strip().lower()
            escaped_email = escape_filter_chars(email)
            
            # Search for user by email
            search_filter = f'(|(mail={escaped_email})(userPrincipalName={escaped_email})(proxyAddresses=smtp:{escaped_email})(proxyAddresses=SMTP:{escaped_email}))'
            
            conn.search(
                search_base=ad_base_dn,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=['sAMAccountName', 'pwdLastSet', 'userAccountControl', 'cn']
            )
            
            days_until_expiry = None
            expiry_date = None
            never_expires = False
            found_in_ad = False
            ad_username = None
            
            if conn.entries:
                entry = conn.entries[0]
                found_in_ad = True
                ad_username = str(entry.sAMAccountName) if hasattr(entry, 'sAMAccountName') and entry.sAMAccountName else None
                
                # Check if password never expires
                uac = int(entry.userAccountControl.value) if hasattr(entry, 'userAccountControl') and entry.userAccountControl.value else 0
                if uac & 0x10000:
                    never_expires = True
                else:
                    pwd_last_set = entry.pwdLastSet.value if hasattr(entry, 'pwdLastSet') and entry.pwdLastSet.value else None
                    
                    if pwd_last_set:
                        if isinstance(pwd_last_set, datetime):
                            pwd_set_date = pwd_last_set
                        else:
                            try:
                                pwd_set_date = datetime(1601, 1, 1) + timedelta(microseconds=int(pwd_last_set) / 10)
                            except Exception:
                                pwd_set_date = None
                        
                        if pwd_set_date:
                            expiry_date = pwd_set_date + timedelta(days=max_pwd_age_days)
                            if expiry_date.tzinfo is not None:
                                expiry_date = expiry_date.replace(tzinfo=None)
                            days_until_expiry = (expiry_date - now).days
            
            # Update Contact record
            if not found_in_ad:
                contact.password_expires_days = -999
            elif never_expires:
                contact.password_expires_days = -1
            elif days_until_expiry is not None:
                contact.password_expires_days = days_until_expiry
                # If password was reset (days_until > warning threshold), clear notification tracking
                # so they can receive new notifications when their password is about to expire again
                if days_until_expiry > warning_days and contact.last_notification_days_before is not None:
                    contact.last_notification_days_before = None
                    contact.password_notification_sent_at = None
            else:
                contact.password_expires_days = None
            contact.password_checked_at = now
            
            # Check if this user should be in the warning list
            if found_in_ad and not never_expires and days_until_expiry is not None:
                if 0 <= days_until_expiry <= warning_days:
                    expiring_users.append({
                        'contact_id': contact.id,
                        'name': contact.name,
                        'email': email,
                        'ad_username': ad_username,
                        'days_until_expiry': days_until_expiry,
                        'expiry_date': expiry_date.strftime('%Y-%m-%d') if expiry_date else None,
                        'is_expired': days_until_expiry < 0
                    })
                elif days_until_expiry < 0:
                    # Already expired
                    expiring_users.append({
                        'contact_id': contact.id,
                        'name': contact.name,
                        'email': email,
                        'ad_username': ad_username,
                        'days_until_expiry': days_until_expiry,
                        'expiry_date': expiry_date.strftime('%Y-%m-%d') if expiry_date else None,
                        'is_expired': True
                    })
        
        # Commit Contact updates
        db.session.commit()
        conn.unbind()
        
        # Sort: expired first (most negative), then by days until expiry ascending
        expiring_users.sort(key=lambda u: u['days_until_expiry'])
        
        if logger:
            logger.info(f'AD Password Check: Found {len(expiring_users)} users with expiring/expired passwords')
        
        return expiring_users
        
    except Exception as e:
        if logger:
            logger.exception(f'AD Password Check error: {e}')
        return []


def _send_password_expiry_notifications(expiring_users: List[Dict[str, Any]], logger) -> None:
    """Send email notifications to users based on configured notification rules and templates.
    
    For each user with an expiring password, checks the notification rules to see if
    an email should be sent. Only sends if:
    1. There's an enabled notification rule matching the user's days_until_expiry
    2. The user hasn't already received a notification for this tier (days_before)
    """
    from .ms_graph import send_mail
    
    # Get all enabled notification rules, sorted by days_before descending
    # (so we check higher thresholds first, e.g., 10 days before 5 days)
    notification_rules = PasswordExpiryNotification.query.filter(
        PasswordExpiryNotification.enabled == True
    ).order_by(PasswordExpiryNotification.days_before.desc()).all()
    
    if not notification_rules:
        if logger:
            logger.info('AD Password Check: No notification rules configured, skipping email notifications')
        return
    
    # Get days_before values for quick lookup
    rule_days = [rule.days_before for rule in notification_rules]
    
    if logger:
        logger.info(f'AD Password Check: Found {len(notification_rules)} notification rules: {rule_days}')
    
    emails_sent = 0
    emails_skipped = 0
    now = datetime.utcnow()
    
    for user in expiring_users:
        contact_id = user.get('contact_id')
        if not contact_id:
            continue
        
        contact = Contact.query.get(contact_id)
        if not contact or not contact.email:
            continue
        
        days_until = user['days_until_expiry']
        
        # Find the applicable notification rule for this user
        # The rule that applies is the lowest days_before that is >= days_until_expiry
        # E.g., if user has 3 days left and rules are [7, 5, 3, 1], the 3-day rule applies
        # We iterate from highest to lowest and keep updating until we find the closest match
        applicable_rule = None
        for rule in notification_rules:
            if days_until <= rule.days_before:
                applicable_rule = rule
                # Continue to find a more specific (lower threshold) rule
        
        if not applicable_rule:
            # User's days_until_expiry is greater than all notification thresholds
            if logger:
                logger.debug(f'AD Password Check: {contact.email} has {days_until} days, no rule applies')
            continue
        
        # Check if we've already sent a notification for this tier
        if contact.last_notification_days_before is not None:
            if contact.last_notification_days_before <= applicable_rule.days_before:
                # Already sent this tier or a lower (more urgent) one
                if logger:
                    logger.debug(f'AD Password Check: {contact.email} already notified at {contact.last_notification_days_before}-day tier')
                emails_skipped += 1
                continue
        
        # Get the email template
        template = applicable_rule.template
        if not template:
            if logger:
                logger.warning(f'AD Password Check: Rule {applicable_rule.days_before}-day has no template')
            continue
        
        # Build the email with template placeholders replaced
        subject = _replace_template_placeholders(template.subject, user, contact)
        body = _replace_template_placeholders(template.body, user, contact)
        
        # Send the email
        try:
            success = send_mail(
                to_address=contact.email,
                subject=subject,
                html_body=body,
                to_name=contact.name,
                category='password_expiry'
            )
            
            if success:
                # Update contact to record the notification was sent
                contact.password_notification_sent_at = now
                contact.last_notification_days_before = applicable_rule.days_before
                emails_sent += 1
                if logger:
                    logger.info(f'AD Password Check: Sent {applicable_rule.days_before}-day notification to {contact.email}')
            else:
                if logger:
                    logger.warning(f'AD Password Check: Failed to send email to {contact.email}')
        except Exception as e:
            if logger:
                logger.exception(f'AD Password Check: Error sending email to {contact.email}: {e}')
    
    # Commit all contact updates
    db.session.commit()
    
    if logger:
        logger.info(f'AD Password Check: Sent {emails_sent} notification emails, skipped {emails_skipped} (already notified)')


def _replace_template_placeholders(text: str, user: Dict[str, Any], contact: Contact) -> str:
    """Replace template placeholders with actual values.
    
    Available placeholders:
    - {{name}} or {{user_name}} - Contact's name
    - {{email}} or {{user_email}} - Contact's email
    - {{days}} or {{days_until_expiry}} - Days until password expires
    - {{expiry_date}} - Password expiry date
    - {{ad_username}} - AD username (sAMAccountName)
    """
    replacements = {
        '{{name}}': contact.name or '',
        '{{user_name}}': contact.name or '',
        '{{email}}': contact.email or '',
        '{{user_email}}': contact.email or '',
        '{{days}}': str(user.get('days_until_expiry', '')),
        '{{days_until_expiry}}': str(user.get('days_until_expiry', '')),
        '{{expiry_date}}': user.get('expiry_date', ''),
        '{{ad_username}}': user.get('ad_username', ''),
    }
    
    result = text
    for placeholder, value in replacements.items():
        result = result.replace(placeholder, value or '')
    
    return result


def _create_expiring_passwords_ticket(expiring_users: List[Dict[str, Any]], logger) -> None:
    """Create a ticket listing users with expiring passwords."""
    warning_days = int(Setting.get('AD_PWD_WARNING_DAYS', '14') or '14')
    
    # Build the ticket body
    body_lines = [
        f"<p>The following users have passwords expiring within <strong>{warning_days} days</strong>:</p>",
        "<table style='border-collapse: collapse; width: 100%;'>",
        "<thead>",
        "<tr style='background-color: #f2f2f2;'>",
        "<th style='border: 1px solid #ddd; padding: 8px; text-align: left;'>Name</th>",
        "<th style='border: 1px solid #ddd; padding: 8px; text-align: left;'>Email</th>",
        "<th style='border: 1px solid #ddd; padding: 8px; text-align: left;'>AD Username</th>",
        "<th style='border: 1px solid #ddd; padding: 8px; text-align: left;'>Expires</th>",
        "<th style='border: 1px solid #ddd; padding: 8px; text-align: left;'>Status</th>",
        "</tr>",
        "</thead>",
        "<tbody>",
    ]
    
    for user in expiring_users:
        if user['is_expired']:
            status = '<span style="color: red; font-weight: bold;">EXPIRED</span>'
            row_style = 'background-color: #ffebee;'
        elif user['days_until_expiry'] <= 3:
            status = f'<span style="color: red;">{user["days_until_expiry"]} day{"s" if user["days_until_expiry"] != 1 else ""}</span>'
            row_style = 'background-color: #fff3e0;'
        else:
            status = f'{user["days_until_expiry"]} days'
            row_style = ''
        
        body_lines.append(f"<tr style='{row_style}'>")
        body_lines.append(f"<td style='border: 1px solid #ddd; padding: 8px;'>{user['name'] or '—'}</td>")
        body_lines.append(f"<td style='border: 1px solid #ddd; padding: 8px;'>{user['email']}</td>")
        body_lines.append(f"<td style='border: 1px solid #ddd; padding: 8px;'>{user['ad_username'] or '—'}</td>")
        body_lines.append(f"<td style='border: 1px solid #ddd; padding: 8px;'>{user['expiry_date'] or '—'}</td>")
        body_lines.append(f"<td style='border: 1px solid #ddd; padding: 8px;'>{status}</td>")
        body_lines.append("</tr>")
    
    body_lines.extend([
        "</tbody>",
        "</table>",
        f"<p style='margin-top: 16px; color: #666; font-size: 0.9em;'>This ticket was automatically generated on {datetime.utcnow().strftime('%Y-%m-%d at %H:%M UTC')}.</p>"
    ])
    
    body_html = "\n".join(body_lines)
    
    # Count expired vs expiring
    expired_count = sum(1 for u in expiring_users if u['is_expired'])
    expiring_count = len(expiring_users) - expired_count
    
    # Create subject with counts
    subject_parts = []
    if expired_count > 0:
        subject_parts.append(f"{expired_count} expired")
    if expiring_count > 0:
        subject_parts.append(f"{expiring_count} expiring soon")
    
    subject = f"Passwords Expiring - {', '.join(subject_parts)}"
    
    # Create the ticket
    ticket = Ticket(
        subject=subject,
        body=body_html,
        status='open',
        priority='medium',
        source='system',
        requester_name='System',
        requester_email='system@helpdesk.local',
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    db.session.add(ticket)
    db.session.commit()
    
    # Note: Individual user email notifications and contact tracking are now handled 
    # in _send_password_expiry_notifications() which runs before this function
    
    if logger:
        logger.info(f'AD Password Check: Created ticket #{ticket.id} for {len(expiring_users)} users with expiring passwords')
