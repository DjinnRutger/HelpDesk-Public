import os
import msal
import requests
from typing import List, Dict, Optional
from flask import current_app
from app.models import Setting

SCOPES = ["https://graph.microsoft.com/.default"]

def get_msal_app() -> Optional[msal.ConfidentialClientApplication]:
    # Prefer DB settings; fall back to environment
    client_id = Setting.get("MS_CLIENT_ID", None) or os.getenv("MS_CLIENT_ID")
    client_secret = Setting.get("MS_CLIENT_SECRET", None) or os.getenv("MS_CLIENT_SECRET")
    tenant_id = Setting.get("MS_TENANT_ID", None) or os.getenv("MS_TENANT_ID", "common")
    if not client_id or not client_secret:
        return None
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    return msal.ConfidentialClientApplication(client_id, authority=authority, client_credential=client_secret)


def get_access_token(app: msal.ConfidentialClientApplication) -> Optional[str]:
    if not app:
        return None
    result = app.acquire_token_silent(SCOPES, account=None)
    if not result:
        result = app.acquire_token_for_client(scopes=SCOPES)
    if "access_token" in result:
        return result["access_token"]
    return None


def get_unread_messages(access_token: str, user_email: str) -> List[Dict]:
    headers = {"Authorization": f"Bearer {access_token}"}
    # Get unread messages ordered by receivedDateTime desc
    url = f"https://graph.microsoft.com/v1.0/users/{user_email}/mailFolders/Inbox/messages?$filter=isRead eq false&$orderby=receivedDateTime desc&$top=25"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("value", [])


def get_message_html(access_token: str, user_email: str, message_id: str) -> Optional[str]:
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://graph.microsoft.com/v1.0/users/{user_email}/messages/{message_id}?$select=body"
    resp = requests.get(url, headers=headers, timeout=20)
    if resp.status_code != 200:
        return None
    body = resp.json().get("body", {})
    if body.get("contentType") == "html":
        return body.get("content")
    return body.get("content")


def list_attachments(access_token: str, user_email: str, message_id: str) -> List[Dict]:
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://graph.microsoft.com/v1.0/users/{user_email}/messages/{message_id}/attachments"
    resp = requests.get(url, headers=headers, timeout=20)
    resp.raise_for_status()
    return resp.json().get("value", [])


def download_file_attachment(access_token: str, user_email: str, attachment_id: str) -> Optional[Dict]:
    headers = {"Authorization": f"Bearer {access_token}"}
    url = f"https://graph.microsoft.com/v1.0/users/{user_email}/attachments/{attachment_id}"
    resp = requests.get(url, headers=headers, timeout=30)
    if resp.status_code != 200:
        return None
    return resp.json()


def mark_message_read(access_token: str, user_email: str, message_id: str):
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    url = f"https://graph.microsoft.com/v1.0/users/{user_email}/messages/{message_id}"
    requests.patch(url, headers=headers, json={"isRead": True}, timeout=20)


def send_mail(to_address: str, subject: str, html_body: str, to_name: Optional[str] = None, save_to_sent: bool = True, attachments: Optional[List[Dict]] = None) -> bool:
    """Send an email via Microsoft Graph using the configured mailbox user.

    Returns True on success, False otherwise.
    """
    user_email = Setting.get("MS_USER_EMAIL", None) or os.getenv("MS_USER_EMAIL")
    app = get_msal_app()
    token = get_access_token(app) if app else None
    if not user_email or not token:
        return False
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    message: Dict = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": html_body},
        "toRecipients": [
            {"emailAddress": {"address": to_address, "name": to_name or to_address}}
        ],
    }
    # Attachments: each item should be a dict with keys: name, contentType, contentBytes (base64 string)
    if attachments:
        atts = []
        for a in attachments:
            if not a:
                continue
            name = a.get("name")
            ctype = a.get("contentType") or "application/octet-stream"
            content_b64 = a.get("contentBytes")
            if not name or not content_b64:
                continue
            atts.append({
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": name,
                "contentType": ctype,
                "contentBytes": content_b64,
            })
        if atts:
            message["attachments"] = atts
    payload = {"message": message, "saveToSentItems": save_to_sent}
    url = f"https://graph.microsoft.com/v1.0/users/{user_email}/sendMail"
    try:
        if current_app:
            current_app.logger.info("Graph send_mail: to=%s subj=%s attachments=%d", to_address, subject, len(message.get("attachments", [])))
        resp = requests.post(url, headers=headers, json=payload, timeout=25)
        if current_app:
            current_app.logger.info("Graph send_mail: status=%s", resp.status_code)
            if resp.status_code >= 300:
                current_app.logger.warning("Graph send_mail error body: %s", resp.text[:1000])
        return resp.status_code in (202, 200)
    except requests.RequestException as e:
        if current_app:
            current_app.logger.exception("Graph send_mail exception: %s", e)
        return False
