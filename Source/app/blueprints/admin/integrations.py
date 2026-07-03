from flask import Blueprint, render_template, redirect, url_for, flash, request, send_file, current_app, jsonify, make_response, session
from flask_login import login_required, current_user
from ...forms import MSGraphForm, TechForm, ProcessTemplateForm, ProcessTemplateItemForm, AllowedDomainForm, DenyFilterForm, ClientApiForm
from ...models import Setting, User, Role, ProcessTemplate, ProcessTemplateItem, AllowedDomain, DenyFilter, Vendor, PurchaseOrder, Company, ShippingLocation, DocumentCategory, AssetAudit, Asset, AssetCategory, AssetManufacturer, AssetCondition, AssetLocation, ScheduledTicket, Ticket, TicketTask, TicketStatus, Tag, Report, ReportRun, ApiToken
from ... import db
from ...permissions import (
    MODULES, LEVEL_CHOICES, VIEW, EDIT,
    has_permission, is_administrator,
)
from ...utils.security import hash_password
from ...services.email_poll import poll_ms_graph
from ...services.ms_graph import get_msal_app, get_access_token
import sqlite3
import io
import tempfile
import shutil
import zipfile
from datetime import datetime
import os
import requests
import ftplib

from . import admin_bp, admin_required, _bump_schedule_version  # noqa: F401


@admin_bp.route('/msgraph', methods=['GET', 'POST'])
@login_required
def msgraph():
    form = MSGraphForm()
    if request.method == 'GET':
        form.client_id.data = Setting.get('MS_CLIENT_ID', '')
        form.client_secret.data = Setting.get('MS_CLIENT_SECRET', '')
        form.tenant_id.data = Setting.get('MS_TENANT_ID', '')
        form.user_email.data = Setting.get('MS_USER_EMAIL', '')
        try:
            form.poll_interval.data = int(Setting.get('POLL_INTERVAL_SECONDS', '60'))
        except Exception:
            form.poll_interval.data = 60
    # Save settings
    if form.validate_on_submit() and 'submit' in request.form:
        Setting.set('MS_CLIENT_ID', form.client_id.data)
        Setting.set('MS_CLIENT_SECRET', form.client_secret.data)
        Setting.set('MS_TENANT_ID', form.tenant_id.data)
        Setting.set('MS_USER_EMAIL', form.user_email.data)
        Setting.set('POLL_INTERVAL_SECONDS', str(form.poll_interval.data))
        _bump_schedule_version()
        flash('Saved Microsoft Graph settings', 'success')
        return redirect(url_for('admin.index'))
    # Force a poll now
    if request.method == 'POST' and request.form.get('action') == 'check_now':
        poll_ms_graph()
        flash('Mailbox checked for new unread messages.', 'success')
        return redirect(url_for('admin.msgraph'))
    # Test connection
    if request.method == 'POST' and request.form.get('action') == 'test':
        app = get_msal_app()
        if not app:
            flash('Missing or invalid Graph credentials. Save valid settings first.', 'danger')
            return redirect(url_for('admin.msgraph'))
        token = get_access_token(app)
        if token:
            flash('Connection test succeeded: token acquired.', 'success')
        else:
            flash('Connection test failed: could not acquire token.', 'danger')
        return redirect(url_for('admin.msgraph'))
    return render_template('admin/msgraph.html', form=form)


def _client_api_base_url():
    """Resolve the base URL shown to IT for the client config (override or detected)."""
    override = (Setting.get('CLIENTAPI_BASE_URL', '') or '').strip()
    return override or request.url_root.rstrip('/')


@admin_bp.route('/client_api', methods=['GET', 'POST'])
@login_required
def client_api():
    form = ClientApiForm()
    if request.method == 'GET':
        form.enabled.data = (Setting.get('CLIENTAPI_ENABLED', '0') or '0') in ('1', 'true', 'on', 'yes')
        form.auth_scheme.data = Setting.get('CLIENTAPI_AUTH_SCHEME', 'Bearer') or 'Bearer'
        form.header_name.data = Setting.get('CLIENTAPI_HEADER_NAME', 'X-Api-Key') or 'X-Api-Key'
        try:
            form.max_upload_mb.data = int(Setting.get('CLIENTAPI_MAX_UPLOAD_MB', '25') or '25')
        except Exception:
            form.max_upload_mb.data = 25
        form.require_https.data = (Setting.get('CLIENTAPI_REQUIRE_HTTPS', '0') or '0') in ('1', 'true', 'on', 'yes')
        form.default_priority.data = Setting.get('CLIENTAPI_DEFAULT_PRIORITY', 'medium') or 'medium'
        try:
            form.default_assignee_id.data = int(Setting.get('CLIENTAPI_DEFAULT_ASSIGNEE_ID', '0') or '0')
        except Exception:
            form.default_assignee_id.data = 0
        form.base_url.data = Setting.get('CLIENTAPI_BASE_URL', '') or ''

    if form.validate_on_submit() and 'submit' in request.form:
        Setting.set('CLIENTAPI_ENABLED', '1' if form.enabled.data else '0')
        Setting.set('CLIENTAPI_AUTH_SCHEME', form.auth_scheme.data)
        Setting.set('CLIENTAPI_HEADER_NAME', (form.header_name.data or 'X-Api-Key').strip())
        Setting.set('CLIENTAPI_MAX_UPLOAD_MB', str(form.max_upload_mb.data))
        Setting.set('CLIENTAPI_REQUIRE_HTTPS', '1' if form.require_https.data else '0')
        Setting.set('CLIENTAPI_DEFAULT_PRIORITY', form.default_priority.data)
        Setting.set('CLIENTAPI_DEFAULT_ASSIGNEE_ID', str(form.default_assignee_id.data or 0))
        Setting.set('CLIENTAPI_BASE_URL', (form.base_url.data or '').strip())
        flash('Saved Client API settings', 'success')
        return redirect(url_for('admin.client_api'))

    tokens = ApiToken.query.order_by(ApiToken.created_at.desc()).all()
    endpoint_url = _client_api_base_url() + '/api/tickets'
    # Plaintext token shown once immediately after generation.
    new_token = session.pop('new_api_token', None)
    return render_template(
        'admin/client_api.html',
        form=form,
        tokens=tokens,
        endpoint_url=endpoint_url,
        base_url=_client_api_base_url(),
        new_token=new_token,
    )


@admin_bp.route('/client_api/tokens/generate', methods=['POST'])
@login_required
def client_api_token_generate():
    label = (request.form.get('label') or '').strip() or None
    _, plaintext = ApiToken.generate(label=label)
    # Stash for one-time display on the next page render.
    session['new_api_token'] = plaintext
    flash('Token generated. Copy it now — it will not be shown again.', 'success')
    return redirect(url_for('admin.client_api'))


@admin_bp.route('/client_api/tokens/<int:token_id>/revoke', methods=['POST'])
@login_required
def client_api_token_revoke(token_id):
    tok = ApiToken.query.get_or_404(token_id)
    tok.revoked = True
    db.session.commit()
    flash('Token revoked.', 'success')
    return redirect(url_for('admin.client_api'))


@admin_bp.route('/ftp_settings', methods=['POST'])
@login_required
def ftp_settings():
    """Save Ticket Import settings.

    This endpoint serves two small forms in the Ticket Import modal:
    - MS Graph enable toggle (form_section=ms)
    - FTP enable + connection settings (form_section=ftp)

    Each form should only update its own settings so MS and FTP can be enabled/disabled independently.
    """
    section = (request.form.get('form_section') or '').strip().lower()
    try:
        if section == 'ms':
            # Checkbox sends when checked; absence means off
            raw = (request.form.get('ms_enabled') or '').strip().lower()
            ms_enabled = raw in ('1', 'true', 'on', 'yes')
            Setting.set('MS_ENABLED', '1' if ms_enabled else '0')
            flash('Saved MS Graph import setting.', 'success')
        elif section == 'ftp':
            # Read FTP fields; only update FTP-related settings
            raw_enabled = (request.form.get('ftp_enabled') or '').strip().lower()
            enabled = raw_enabled in ('1', 'true', 'on', 'yes')
            host = (request.form.get('ftp_host') or '').strip()
            port_raw = (request.form.get('ftp_port') or '').strip() or '21'
            user = (request.form.get('ftp_user') or '').strip()
            pwd = (request.form.get('ftp_pass') or '').strip()
            base = (request.form.get('ftp_base') or '').strip()
            subdir = (request.form.get('ftp_subdir') or '').strip() or 'HDWish Data'
            try:
                port = int(port_raw)
                if port <= 0:
                    port = 21
            except Exception:
                port = 21
            Setting.set('FTP_ENABLED', '1' if enabled else '0')
            Setting.set('FTP_HOST', host)
            Setting.set('FTP_PORT', str(port))
            Setting.set('FTP_USER', user)
            if pwd:
                Setting.set('FTP_PASS', pwd)
            Setting.set('FTP_BASE_DIR', base)
            Setting.set('FTP_SUBDIR', subdir)
            flash('Saved FTP import settings.', 'success')
        else:
            # Fallback: be conservative and update only keys that are present
            if 'ms_enabled' in request.form:
                raw = (request.form.get('ms_enabled') or '').strip().lower()
                ms_enabled = raw in ('1', 'true', 'on', 'yes')
                Setting.set('MS_ENABLED', '1' if ms_enabled else '0')
            if any(k in request.form for k in ('ftp_enabled','ftp_host','ftp_port','ftp_user','ftp_pass','ftp_base','ftp_subdir')):
                raw_enabled = (request.form.get('ftp_enabled') or '').strip().lower()
                enabled = raw_enabled in ('1', 'true', 'on', 'yes') if 'ftp_enabled' in request.form else ((Setting.get('FTP_ENABLED','0') or '0') in ('1','true','on','yes'))
                host = (request.form.get('ftp_host') or Setting.get('FTP_HOST','')).strip()
                port_raw = (request.form.get('ftp_port') or Setting.get('FTP_PORT','21')).strip() or '21'
                user = (request.form.get('ftp_user') or Setting.get('FTP_USER','')).strip()
                pwd = (request.form.get('ftp_pass') or '').strip()
                base = (request.form.get('ftp_base') or Setting.get('FTP_BASE_DIR','')).strip()
                subdir = (request.form.get('ftp_subdir') or Setting.get('FTP_SUBDIR','HDWish Data')).strip() or 'HDWish Data'
                try:
                    port = int(port_raw)
                    if port <= 0:
                        port = 21
                except Exception:
                    port = 21
                Setting.set('FTP_ENABLED', '1' if enabled else '0')
                Setting.set('FTP_HOST', host)
                Setting.set('FTP_PORT', str(port))
                Setting.set('FTP_USER', user)
                if pwd:
                    Setting.set('FTP_PASS', pwd)
                Setting.set('FTP_BASE_DIR', base)
                Setting.set('FTP_SUBDIR', subdir)
            flash('Ticket Import settings saved.', 'success')
    except Exception:
        flash('Failed to save settings.', 'danger')
    return redirect(url_for('admin.index'))


@admin_bp.route('/ftp_test', methods=['POST'])
@login_required
def ftp_test():
    """Test the FTP connection using saved settings and attempt to list the HDWish folder."""
    try:
        host = Setting.get('FTP_HOST', '')
        port = int(Setting.get('FTP_PORT', '21') or '21')
        user = Setting.get('FTP_USER', '')
        pwd = Setting.get('FTP_PASS', '')
        base = (Setting.get('FTP_BASE_DIR', '') or '').strip()
        subdir = (Setting.get('FTP_SUBDIR', 'HDWish Data') or 'HDWish Data').strip()
        if not host:
            flash('FTP host is required. Save settings first.', 'danger')
            return redirect(url_for('admin.index'))
        ftp = ftplib.FTP()
        ftp.connect(host, port, timeout=10)
        ftp.login(user=user or 'anonymous', passwd=pwd or '')
        # Navigate to base/subdir if provided
        if base:
            ftp.cwd(base)
        if subdir:
            ftp.cwd(subdir)
        # Try listing entries
        entries = ftp.nlst()
        ftp.quit()
        flash(f'FTP test succeeded. Found {len(entries)} item(s) under {base}/{subdir}.', 'success')
    except Exception as e:
        try:
            ftp.quit()
        except Exception:
            pass
        flash(f'FTP test failed: {e}', 'danger')
    return redirect(url_for('admin.index'))


@admin_bp.route('/ad_settings', methods=['POST'])
@login_required
def ad_settings():
    """Save Active Directory connection settings."""
    ad_enabled = request.form.get('ad_enabled') in ('1', 'on', 'true', 'yes')
    ad_server = (request.form.get('ad_server') or '').strip()
    ad_port = (request.form.get('ad_port') or '389').strip()
    ad_use_ssl = request.form.get('ad_use_ssl') in ('1', 'on', 'true', 'yes')
    ad_start_tls = request.form.get('ad_start_tls') in ('1', 'on', 'true', 'yes')
    ad_base_dn = (request.form.get('ad_base_dn') or '').strip()
    ad_bind_dn = (request.form.get('ad_bind_dn') or '').strip()
    ad_bind_password = request.form.get('ad_bind_password') or ''
    
    Setting.set('AD_ENABLED', '1' if ad_enabled else '0')
    Setting.set('AD_SERVER', ad_server)
    Setting.set('AD_PORT', ad_port)
    Setting.set('AD_USE_SSL', '1' if ad_use_ssl else '0')
    Setting.set('AD_START_TLS', '1' if ad_start_tls else '0')
    Setting.set('AD_BASE_DN', ad_base_dn)
    Setting.set('AD_BIND_DN', ad_bind_dn)
    # Only update password if a new one is provided
    if ad_bind_password:
        Setting.set('AD_BIND_PASSWORD', ad_bind_password)
    
    flash('Active Directory settings saved.', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/ad_test', methods=['POST'])
@login_required
def ad_test():
    """Test the Active Directory connection using provided or saved settings."""
    try:
        from ldap3 import Server, Connection, ALL, SUBTREE, Tls
        import ssl
    except ImportError:
        return jsonify({'success': False, 'message': 'ldap3 package not installed. Please run: pip install ldap3'})
    
    # Get settings from form (for testing before save) or fall back to saved settings
    ad_server = (request.form.get('ad_server') or Setting.get('AD_SERVER', '')).strip()
    ad_port = int((request.form.get('ad_port') or Setting.get('AD_PORT', '389') or '389').strip())
    ad_use_ssl = request.form.get('ad_use_ssl') in ('1', 'on', 'true', 'yes') if 'ad_use_ssl' in request.form else (Setting.get('AD_USE_SSL', '0') in ('1', 'true', 'on', 'yes'))
    ad_start_tls = request.form.get('ad_start_tls') in ('1', 'on', 'true', 'yes') if 'ad_start_tls' in request.form else (Setting.get('AD_START_TLS', '0') in ('1', 'true', 'on', 'yes'))
    ad_base_dn = (request.form.get('ad_base_dn') or Setting.get('AD_BASE_DN', '')).strip()
    ad_bind_dn = (request.form.get('ad_bind_dn') or Setting.get('AD_BIND_DN', '')).strip()
    ad_bind_password = request.form.get('ad_bind_password') or ''
    
    # If password is empty, use saved password
    if not ad_bind_password:
        ad_bind_password = Setting.get('AD_BIND_PASSWORD', '')
    
    if not ad_server:
        return jsonify({'success': False, 'message': 'AD server is required.'})
    
    if not ad_bind_dn:
        return jsonify({'success': False, 'message': 'Bind DN/Username is required.'})
    
    try:
        # Configure TLS if needed
        tls_config = None
        if ad_use_ssl or ad_start_tls:
            tls_config = Tls(validate=ssl.CERT_NONE)  # For self-signed certs; in production, consider CERT_REQUIRED
        
        # Create server object
        server = Server(
            ad_server,
            port=ad_port,
            use_ssl=ad_use_ssl,
            tls=tls_config,
            get_info=ALL,
            connect_timeout=10
        )
        
        # Create connection and bind
        conn = Connection(
            server,
            user=ad_bind_dn,
            password=ad_bind_password,
            auto_bind=False,
            raise_exceptions=True
        )
        
        # Start TLS if configured (and not already using SSL)
        if ad_start_tls and not ad_use_ssl:
            conn.open()
            conn.start_tls()
        
        # Bind to the server
        if not conn.bind():
            return jsonify({'success': False, 'message': f'Bind failed: {conn.result}'})
        
        # Test search if base DN is provided
        search_info = ''
        if ad_base_dn:
            conn.search(
                search_base=ad_base_dn,
                search_filter='(objectClass=user)',
                search_scope=SUBTREE,
                attributes=['cn'],
                size_limit=5
            )
            user_count = len(conn.entries)
            search_info = f' Found {user_count} user(s) in sample search.'
        
        # Get server info
        server_info = ''
        if server.info:
            naming_contexts = getattr(server.info, 'naming_contexts', None)
            if naming_contexts:
                server_info = f' Naming contexts: {", ".join(naming_contexts[:2])}...'
        
        conn.unbind()
        
        return jsonify({
            'success': True,
            'message': f'Successfully connected and authenticated to {ad_server}:{ad_port}.{search_info}{server_info}'
        })
        
    except Exception as e:
        error_msg = str(e)
        # Provide more helpful error messages for common issues
        if 'invalidCredentials' in error_msg or '49' in error_msg:
            error_msg = 'Invalid credentials. Check your Bind DN and password.'
        elif 'LDAP_SERVER_DOWN' in error_msg or 'socket' in error_msg.lower():
            error_msg = f'Cannot connect to {ad_server}:{ad_port}. Check server address and port.'
        elif 'timeout' in error_msg.lower():
            error_msg = f'Connection timed out. Server {ad_server}:{ad_port} may be unreachable.'
        
        return jsonify({'success': False, 'message': f'Connection failed: {error_msg}'})


@admin_bp.route('/ad_password_settings', methods=['POST'])
@login_required
def ad_password_settings():
    """Save AD password check schedule settings."""
    ad_pwd_check_enabled = request.form.get('ad_pwd_check_enabled') in ('1', 'on', 'true', 'yes')
    ad_pwd_check_time = (request.form.get('ad_pwd_check_time') or '07:00').strip()
    ad_pwd_warning_days = (request.form.get('ad_pwd_warning_days') or '14').strip()
    
    Setting.set('AD_PWD_CHECK_ENABLED', '1' if ad_pwd_check_enabled else '0')
    Setting.set('AD_PWD_CHECK_TIME', ad_pwd_check_time)
    Setting.set('AD_PWD_WARNING_DAYS', ad_pwd_warning_days)
    _bump_schedule_version()

    flash('AD password check settings saved.', 'success')
    return redirect(url_for('admin.index'))


@admin_bp.route('/ad_password_check_now', methods=['POST'])
@login_required
def ad_password_check_now():
    """Check AD password expiry for all contacts (users) in the system."""
    from ...models import Contact
    
    # Check if debug mode is enabled
    debug_mode = request.form.get('debug') in ('1', 'true', 'on', 'yes')
    debug_info = {} if debug_mode else None
    
    # Check if AD is configured
    ad_enabled = (Setting.get('AD_ENABLED', '0') or '0') in ('1', 'true', 'on', 'yes')
    if not ad_enabled:
        return jsonify({'error': 'Active Directory is not enabled. Configure it in AD Connect first.'})
    
    ad_server = Setting.get('AD_SERVER', '')
    if not ad_server:
        return jsonify({'error': 'AD server is not configured.'})
    
    try:
        from ldap3 import Server, Connection, ALL, SUBTREE, Tls
        import ssl
    except ImportError:
        return jsonify({'error': 'ldap3 package not installed. Please run: pip install ldap3'})
    
    # Get AD settings
    ad_port = int(Setting.get('AD_PORT', '389') or '389')
    ad_use_ssl = (Setting.get('AD_USE_SSL', '0') or '0') in ('1', 'true', 'on', 'yes')
    ad_start_tls = (Setting.get('AD_START_TLS', '0') or '0') in ('1', 'true', 'on', 'yes')
    ad_base_dn = Setting.get('AD_BASE_DN', '')
    ad_bind_dn = Setting.get('AD_BIND_DN', '')
    ad_bind_password = Setting.get('AD_BIND_PASSWORD', '')
    warning_days = int(Setting.get('AD_PWD_WARNING_DAYS', '14') or '14')
    
    if debug_mode:
        debug_info['settings'] = {
            'ad_server': ad_server,
            'ad_port': ad_port,
            'ad_use_ssl': ad_use_ssl,
            'ad_start_tls': ad_start_tls,
            'ad_base_dn': ad_base_dn,
            'ad_bind_dn': ad_bind_dn,
            'warning_days': warning_days
        }
    
    if not ad_base_dn:
        return jsonify({'error': 'AD Base DN is not configured.'})
    
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
            return jsonify({'error': f'Failed to bind to AD: {conn.result}'})
        
        # Get all contacts with email addresses (non-archived)
        # Include contacts where archived is False OR NULL (for older records without this field set)
        from sqlalchemy import or_
        contacts = Contact.query.filter(
            Contact.email.isnot(None),
            Contact.email != '',
            or_(Contact.archived == False, Contact.archived.is_(None))
        ).all()
        
        results = []
        found_count = 0
        expiring_soon_count = 0
        expired_count = 0
        
        from datetime import datetime, timedelta
        
        # Get domain max password age from AD (default to 90 days if not found)
        max_pwd_age_days = 90
        try:
            # Query the domain policy for maxPwdAge
            conn.search(
                search_base=ad_base_dn,
                search_filter='(objectClass=domain)',
                search_scope=SUBTREE,
                attributes=['maxPwdAge']
            )
            if conn.entries:
                max_pwd_age = conn.entries[0].maxPwdAge.value
                if max_pwd_age:
                    # maxPwdAge is in 100-nanosecond intervals (negative value)
                    # Convert to days
                    max_pwd_age_days = abs(int(max_pwd_age)) / (10000000 * 60 * 60 * 24)
        except Exception:
            pass  # Use default if we can't get the policy
        
        if debug_mode:
            debug_info['searches'] = []
        
        for contact in contacts:
            email = contact.email.strip().lower()
            user_result = {
                'name': contact.name,
                'email': email,
                'found_in_ad': False,
                'ad_username': None,
                'password_expiry': None,
                'days_until_expiry': None,
                'is_expiring_soon': False,
                'is_expired': False,
                'never_expires': False
            }
            
            # Escape special LDAP characters in email
            from ldap3.utils.conv import escape_filter_chars
            escaped_email = escape_filter_chars(email)
            
            # Search for user by multiple email-related attributes
            # mail: primary email, userPrincipalName: UPN (often email format), proxyAddresses: all email aliases
            search_filter = f'(|(mail={escaped_email})(userPrincipalName={escaped_email})(proxyAddresses=smtp:{escaped_email})(proxyAddresses=SMTP:{escaped_email}))'
            
            if debug_mode:
                search_debug = {
                    'contact_email': email,
                    'escaped_email': escaped_email,
                    'search_filter': search_filter,
                    'search_base': ad_base_dn,
                }
            
            conn.search(
                search_base=ad_base_dn,
                search_filter=search_filter,
                search_scope=SUBTREE,
                attributes=['sAMAccountName', 'userPrincipalName', 'pwdLastSet', 'userAccountControl', 'mail', 'cn', 'proxyAddresses']
            )
            
            if debug_mode:
                search_debug['entries_found'] = len(conn.entries)
                search_debug['result'] = str(conn.result)
                if conn.entries:
                    # Show first entry details
                    entry = conn.entries[0]
                    search_debug['first_entry'] = {
                        'dn': str(entry.entry_dn),
                        'cn': str(entry.cn) if hasattr(entry, 'cn') else None,
                        'mail': str(entry.mail) if hasattr(entry, 'mail') and entry.mail else None,
                        'userPrincipalName': str(entry.userPrincipalName) if hasattr(entry, 'userPrincipalName') and entry.userPrincipalName else None,
                        'sAMAccountName': str(entry.sAMAccountName) if hasattr(entry, 'sAMAccountName') and entry.sAMAccountName else None,
                    }
                debug_info['searches'].append(search_debug)
            
            if conn.entries:
                entry = conn.entries[0]
                user_result['found_in_ad'] = True
                found_count += 1
                
                # Get username
                user_result['ad_username'] = str(entry.sAMAccountName) if hasattr(entry, 'sAMAccountName') and entry.sAMAccountName else None
                
                # Check if password never expires (bit 65536 in userAccountControl)
                uac = int(entry.userAccountControl.value) if hasattr(entry, 'userAccountControl') and entry.userAccountControl.value else 0
                if uac & 0x10000:  # DONT_EXPIRE_PASSWORD flag
                    user_result['never_expires'] = True
                else:
                    # Calculate password expiry
                    pwd_last_set = entry.pwdLastSet.value if hasattr(entry, 'pwdLastSet') and entry.pwdLastSet.value else None
                    
                    if pwd_last_set:
                        # pwdLastSet is a datetime in ldap3
                        if isinstance(pwd_last_set, datetime):
                            pwd_set_date = pwd_last_set
                        else:
                            # If it's a Windows FILETIME (100-nanosecond intervals since 1601)
                            try:
                                pwd_set_date = datetime(1601, 1, 1) + timedelta(microseconds=int(pwd_last_set) / 10)
                            except Exception:
                                pwd_set_date = None
                        
                        if pwd_set_date:
                            expiry_date = pwd_set_date + timedelta(days=max_pwd_age_days)
                            user_result['password_expiry'] = expiry_date.strftime('%Y-%m-%d %H:%M')
                            
                            # Make both datetimes timezone-naive for comparison
                            now = datetime.utcnow()
                            # If expiry_date is timezone-aware, convert to naive UTC
                            if expiry_date.tzinfo is not None:
                                expiry_date_naive = expiry_date.replace(tzinfo=None)
                            else:
                                expiry_date_naive = expiry_date
                            days_until = (expiry_date_naive - now).days
                            user_result['days_until_expiry'] = days_until
                            
                            if days_until < 0:
                                user_result['is_expired'] = True
                                expired_count += 1
                            elif days_until <= warning_days:
                                user_result['is_expiring_soon'] = True
                                expiring_soon_count += 1
            
            # Save password expiry data to Contact record
            now = datetime.utcnow()
            if not user_result['found_in_ad']:
                contact.password_expires_days = -999  # Not found in AD
            elif user_result['never_expires']:
                contact.password_expires_days = -1  # Never expires
            elif user_result['days_until_expiry'] is not None:
                contact.password_expires_days = user_result['days_until_expiry']
            else:
                contact.password_expires_days = None  # Could not determine
            contact.password_checked_at = now
            
            results.append(user_result)
        
        # Commit all Contact updates
        db.session.commit()
        
        conn.unbind()
        
        # Sort results: expired first, then expiring soon, then not found, then OK
        def sort_key(r):
            if r['is_expired']:
                return (0, r.get('days_until_expiry') or 0)
            if r['is_expiring_soon']:
                return (1, r.get('days_until_expiry') or 0)
            if not r['found_in_ad']:
                return (3, 0)
            return (2, r.get('days_until_expiry') or 999)
        
        results.sort(key=sort_key)
        
        return jsonify({
            'results': results,
            'summary': {
                'total_users': len(contacts),
                'found_in_ad': found_count,
                'expiring_soon': expiring_soon_count,
                'expired': expired_count,
                'max_pwd_age_days': int(max_pwd_age_days)
            },
            'debug': debug_info
        })
        
    except Exception as e:
        import traceback
        error_details = str(e)
        if debug_mode:
            error_details += '\n\nTraceback:\n' + traceback.format_exc()
        return jsonify({'error': f'Error checking AD: {error_details}', 'debug': debug_info})


@admin_bp.route('/import_check_now', methods=['POST'])
@login_required
def import_check_now():
    """Run an immediate Ticket Import for enabled services (MS Graph and/or FTP)."""
    # We reuse poll_ms_graph, which respects MS_ENABLED and FTP_ENABLED and will skip
    # MS processing if disabled/misconfigured while still running FTP if enabled.
    poll_ms_graph()
    flash('Ticket import run completed.', 'success')
    return redirect(url_for('admin.index'))
