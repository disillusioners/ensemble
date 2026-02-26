"""Credential manager for encrypting sensitive API tokens."""

import json
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    logger.warning("cryptography package not installed, credentials will be stored unencrypted")


class CredentialManager:
    """Manages encrypted credential storage.
    
    Uses Fernet symmetric encryption for secure storage of API tokens.
    Requires SOURCE_CREDENTIAL_KEY environment variable or encryption_key parameter.
    """
    
    def __init__(self, encryption_key: Optional[bytes] = None):
        """Initialize credential manager.
        
        Args:
            encryption_key: Optional encryption key. If not provided,
                          reads from SOURCE_CREDENTIAL_KEY env var.
        """
        self._fernet: Optional[Fernet] = None
        
        if CRYPTO_AVAILABLE:
            key = encryption_key or os.environ.get("SOURCE_CREDENTIAL_KEY")
            if key:
                if isinstance(key, str):
                    key = key.encode()
                self._fernet = Fernet(key)
            else:
                logger.warning(
                    "No SOURCE_CREDENTIAL_KEY provided, credentials will be stored unencrypted"
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
