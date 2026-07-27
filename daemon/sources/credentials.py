"""Credential manager for encrypting sensitive API tokens."""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)

# Canonical encryption-key env var. This is the name operators should set.
SYSTEM_ENCRYPTION_KEY_ENV = "SYSTEM_ENCRYPTION_KEY"
# Deprecated alias kept for backward compatibility. Read only as a fallback
# when SYSTEM_ENCRYPTION_KEY is unset/empty. New deployments must use
# SYSTEM_ENCRYPTION_KEY.
_LEGACY_SOURCE_CREDENTIAL_KEY_ENV = "SOURCE_CREDENTIAL_KEY"


def get_encryption_key() -> str | None:
    """Resolve the Fernet encryption key from the environment.

    Reads ``SYSTEM_ENCRYPTION_KEY`` first. If that is unset or empty, falls
    back to the deprecated ``SOURCE_CREDENTIAL_KEY`` and emits a deprecation
    warning. Returns ``None`` when neither variable is set, leaving callers
    free to decide on the unencrypted-storage policy.
    """
    new_key = os.environ.get(SYSTEM_ENCRYPTION_KEY_ENV)
    if new_key:
        return new_key

    legacy_key = os.environ.get(_LEGACY_SOURCE_CREDENTIAL_KEY_ENV)
    if legacy_key:
        logger.warning(
            "SYSTEM_ENCRYPTION_KEY is not set; falling back to deprecated "
            "SOURCE_CREDENTIAL_KEY. Please rename the env var to SYSTEM_ENCRYPTION_KEY."
        )
        return legacy_key

    return None


try:
    from cryptography.fernet import Fernet
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    logger.warning("cryptography package not installed, credentials will be stored unencrypted")


class CredentialManager:
    """Manages encrypted credential storage.
    
    Uses Fernet symmetric encryption for secure storage of API tokens.
    Requires SYSTEM_ENCRYPTION_KEY environment variable or encryption_key parameter.
    The legacy SOURCE_CREDENTIAL_KEY is still honored as a fallback (with a
    deprecation warning).
    """
    
    def __init__(self, encryption_key: bytes | None = None):
        """Initialize credential manager.
        
        Args:
            encryption_key: Optional encryption key. If not provided,
                          reads from SYSTEM_ENCRYPTION_KEY env var (or the
                          deprecated SOURCE_CREDENTIAL_KEY fallback).
        """
        self._fernet: Fernet | None = None
        
        if CRYPTO_AVAILABLE:
            key = encryption_key or get_encryption_key()
            if key:
                if isinstance(key, str):
                    key = key.encode()
                self._fernet = Fernet(key)
            else:
                logger.warning(
                    "No SYSTEM_ENCRYPTION_KEY provided, credentials will be stored unencrypted"
                )
        else:
            logger.warning("Credential encryption disabled - cryptography package not available")
    
    def is_encryption_available(self) -> bool:
        """Check if encryption is available."""
        return self._fernet is not None
    
    def encrypt(self, credentials: dict) -> str:
        """Encrypt credentials dict to string.
        
        Args:
            credentials: Dictionary of credentials to encrypt.
            
        Returns:
            Encrypted string representation.
        """
        if self._fernet is None:
            # No encryption - return json
            return json.dumps(credentials)
        
        return self._fernet.encrypt(json.dumps(credentials).encode()).decode()
    
    def decrypt(self, encrypted: str) -> dict:
        """Decrypt string back to credentials dict.
        
        Args:
            encrypted: Encrypted string to decrypt.
            
        Returns:
            Decrypted credentials dictionary.
        """
        if self._fernet is None:
            # No encryption - parse json
            return json.loads(encrypted)
        
        return json.loads(self._fernet.decrypt(encrypted.encode()).decode())
