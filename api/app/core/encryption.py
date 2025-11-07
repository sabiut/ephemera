"""
Encryption utilities for secure credential storage.
Uses Fernet (symmetric encryption) from cryptography library.
"""

import os
import base64
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2
from cryptography.hazmat.backends import default_backend


class CredentialEncryption:
    """Handle encryption/decryption of cloud credentials"""

    def __init__(self, encryption_key: str = None):
        """
        Initialize encryption handler.

        Args:
            encryption_key: Base64-encoded encryption key. If not provided,
                          will use ENCRYPTION_KEY from environment.
        """
        if encryption_key is None:
            encryption_key = os.getenv("ENCRYPTION_KEY")
            if not encryption_key:
                raise ValueError(
                    "ENCRYPTION_KEY environment variable must be set. "
                    "Generate one with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
                )

        self.fernet = Fernet(encryption_key.encode() if isinstance(encryption_key, str) else encryption_key)

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt plaintext string.

        Args:
            plaintext: String to encrypt (e.g., JSON credentials)

        Returns:
            Base64-encoded encrypted string
        """
        if not plaintext:
            raise ValueError("Cannot encrypt empty string")

        encrypted_bytes = self.fernet.encrypt(plaintext.encode())
        return encrypted_bytes.decode()

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt ciphertext string.

        Args:
            ciphertext: Base64-encoded encrypted string

        Returns:
            Decrypted plaintext string
        """
        if not ciphertext:
            raise ValueError("Cannot decrypt empty string")

        decrypted_bytes = self.fernet.decrypt(ciphertext.encode())
        return decrypted_bytes.decode()

    @staticmethod
    def generate_key() -> str:
        """
        Generate a new encryption key.

        Returns:
            Base64-encoded encryption key
        """
        return Fernet.generate_key().decode()


# Global instance
_encryption = None


def get_encryption() -> CredentialEncryption:
    """Get or create global encryption instance"""
    global _encryption
    if _encryption is None:
        _encryption = CredentialEncryption()
    return _encryption


def encrypt_credentials(credentials_json: str) -> str:
    """
    Encrypt cloud credentials JSON.

    Args:
        credentials_json: JSON string of credentials

    Returns:
        Encrypted string safe for database storage
    """
    return get_encryption().encrypt(credentials_json)


def decrypt_credentials(encrypted_data: str) -> str:
    """
    Decrypt cloud credentials.

    Args:
        encrypted_data: Encrypted credentials from database

    Returns:
        Decrypted JSON string
    """
    return get_encryption().decrypt(encrypted_data)
