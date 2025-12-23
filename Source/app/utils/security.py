from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import base64


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(hash_value: str, password: str) -> bool:
    return check_password_hash(hash_value, password)


# --- Encryption for sensitive settings (MSGraph secrets, AD passwords, etc.) ---

# Encryption key prefix to identify encrypted values
ENCRYPTED_PREFIX = "ENC:"

# Salt used for key derivation (fixed per installation for consistency)
# In production, this could be stored separately or derived from machine ID
_ENCRYPTION_SALT = b'helpdesk_settings_v1'


def _get_encryption_key(secret_key: str) -> bytes:
    """Derive a Fernet-compatible encryption key from Flask's SECRET_KEY."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_ENCRYPTION_SALT,
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret_key.encode('utf-8')))
    return key


def _get_fernet(secret_key: str = None) -> Fernet:
    """Get a Fernet cipher instance using the app's SECRET_KEY."""
    if secret_key is None:
        from flask import current_app
        secret_key = current_app.config.get('SECRET_KEY', 'dev')
    return Fernet(_get_encryption_key(secret_key))


def encrypt_value(plaintext: str, secret_key: str = None) -> str:
    """Encrypt a string value and return it with the ENC: prefix.
    
    Returns the original value if empty or already encrypted.
    """
    if not plaintext:
        return plaintext
    if plaintext.startswith(ENCRYPTED_PREFIX):
        # Already encrypted
        return plaintext
    try:
        fernet = _get_fernet(secret_key)
        encrypted = fernet.encrypt(plaintext.encode('utf-8'))
        return ENCRYPTED_PREFIX + encrypted.decode('utf-8')
    except Exception:
        # If encryption fails, return original (shouldn't happen)
        return plaintext


def decrypt_value(encrypted_value: str, secret_key: str = None) -> str:
    """Decrypt a value that was encrypted with encrypt_value.
    
    Returns the original value if not encrypted or decryption fails.
    """
    if not encrypted_value:
        return encrypted_value
    if not encrypted_value.startswith(ENCRYPTED_PREFIX):
        # Not encrypted, return as-is
        return encrypted_value
    try:
        fernet = _get_fernet(secret_key)
        encrypted_data = encrypted_value[len(ENCRYPTED_PREFIX):]
        decrypted = fernet.decrypt(encrypted_data.encode('utf-8'))
        return decrypted.decode('utf-8')
    except Exception:
        # If decryption fails (wrong key, corrupted), return empty
        # This prevents exposing encrypted data
        return ''


def is_encrypted(value: str) -> bool:
    """Check if a value is encrypted (has the ENC: prefix)."""
    return bool(value and value.startswith(ENCRYPTED_PREFIX))


# List of setting keys that should be encrypted
SENSITIVE_SETTING_KEYS = frozenset([
    'MS_CLIENT_SECRET',
    'AD_BIND_PASSWORD',
])
