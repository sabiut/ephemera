"""
Encryption utilities for secure credential storage.
Uses Fernet (symmetric encryption) from cryptography library.
"""

from typing import Optional

from cryptography.fernet import Fernet

from app.config import get_settings


class CredentialEncryption:
    """Handle encryption/decryption of cloud credentials"""

    def __init__(self, encryption_key: Optional[str] = None):
        """
        Initialize encryption handler.

        Args:
            encryption_key: Base64-encoded Fernet key. Defaults to the
                ENCRYPTION_KEY setting.
        """
        if encryption_key is None:
            encryption_key = get_settings().encryption_key
            if not encryption_key:
                raise ValueError(
                    "ENCRYPTION_KEY must be set. "
                    "Generate one with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
                )

        self.fernet = Fernet(encryption_key.encode() if isinstance(encryption_key, str) else encryption_key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a string and return the base64 token."""
        if not plaintext:
            raise ValueError("Cannot encrypt empty string")
        return self.fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a base64 token back to the original string."""
        if not ciphertext:
            raise ValueError("Cannot decrypt empty string")
        return self.fernet.decrypt(ciphertext.encode()).decode()

    @staticmethod
    def generate_key() -> str:
        """Generate a new base64-encoded Fernet key."""
        return Fernet.generate_key().decode()


# Global instance
_encryption: Optional[CredentialEncryption] = None


def get_encryption() -> CredentialEncryption:
    """Get or create global encryption instance"""
    global _encryption
    if _encryption is None:
        _encryption = CredentialEncryption()
    return _encryption


def encrypt_credentials(credentials_json: str) -> str:
    """Encrypt cloud credentials JSON for database storage."""
    return get_encryption().encrypt(credentials_json)


def decrypt_credentials(encrypted_data: str) -> str:
    """Decrypt cloud credentials from the database."""
    return get_encryption().decrypt(encrypted_data)
