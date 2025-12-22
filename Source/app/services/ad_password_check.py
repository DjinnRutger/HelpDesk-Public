"""Scheduled AD Password Expiry Check Service.

This service runs daily to check Active Directory for expiring passwords
and creates a ticket if any users have passwords expiring within the warning threshold.
"""
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from flask import current_app

from .. import db
from ..models import Setting, Ticket, Contact


def run_ad_password_check(app=None) -> None:
    """Check AD for expiring passwords and create a ticket if any are found.
    
    This job:
    1. Queries Active Directory for all contacts' password expiry dates
    2. Updates the Contact records with the expiry information
    3. If any passwords are expiring within the warning threshold, creates a ticket
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
        # Check if daily password check is enabled
        pwd_check_enabled = (Setting.get('AD_PWD_CHECK_ENABLED', '0') or '0') in ('1', 'true', 'on', 'yes')
        if not pwd_check_enabled:
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
        expiring_users = _check_ad_passwords(logger)
        
        if expiring_users:
            _create_expiring_passwords_ticket(expiring_users, logger)
        elif logger:
            logger.info('AD Password Check: No expiring passwords found within warning threshold')
            
    except Exception as e:
        if logger:
            logger.exception(f'AD Password Check failed: {e}')
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
    
    # Mark all notified users with the notification timestamp
    now = datetime.utcnow()
    contact_ids = [u['contact_id'] for u in expiring_users if u.get('contact_id')]
    if contact_ids:
        Contact.query.filter(Contact.id.in_(contact_ids)).update(
            {'password_notification_sent_at': now},
            synchronize_session=False
        )
        db.session.commit()
    
    if logger:
        logger.info(f'AD Password Check: Created ticket #{ticket.id} for {len(expiring_users)} users with expiring passwords')
