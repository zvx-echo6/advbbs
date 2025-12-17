"""
advBBS Cryptography Module

Handles password hashing (Argon2id) and message encryption (ChaCha20-Poly1305).
Designed for low-memory environments like Raspberry Pi Zero 2 W.
"""

import os
import secrets
import logging
from dataclasses import dataclass

from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

logger = logging.getLogger(__name__)


@dataclass
class EncryptedData:
    """Container for encrypted data with nonce."""
    nonce: bytes  # 12 bytes for ChaCha20-Poly1305
    ciphertext: bytes

    def to_bytes(self) -> bytes:
        """Serialize to bytes for storage."""
        return self.nonce + self.ciphertext

    @classmethod
    def from_bytes(cls, data: bytes) -> "EncryptedData":
        """Deserialize from bytes."""
        if len(data) < 12:
            raise ValueError("Invalid encrypted data: too short")
        return cls(nonce=data[:12], ciphertext=data[12:])


class CryptoManager:
    """
    Manages cryptographic operations for advBBS.

    Key derivation: Argon2id (memory-hard, resistant to GPU attacks)
    Encryption: ChaCha20-Poly1305 (AEAD, fast on ARM without AES-NI)
    """

    # Argon2id parameters optimized for RPi Zero 2 W
    SALT_LENGTH = 16
    KEY_LENGTH = 32  # 256 bits for ChaCha20
    NONCE_LENGTH = 12  # ChaCha20-Poly1305 nonce size

    def __init__(
        self,
        time_cost: int = 3,
        memory_cost_kb: int = 32768,  # 32MB
        parallelism: int = 1
    ):
        """
        Initialize crypto manager with Argon2id parameters.

        Args:
            time_cost: Number of iterations (higher = slower + more secure)
            memory_cost_kb: Memory usage in KB (32MB default for RPi)
            parallelism: Number of parallel threads (1 for single-core friendly)
        """
        self.time_cost = time_cost
        self.memory_cost_kb = memory_cost_kb
        self.parallelism = parallelism

        # Configure Argon2id hasher
        self._hasher = PasswordHasher(
            time_cost=time_cost,
            memory_cost=memory_cost_kb,
            parallelism=parallelism,
            hash_len=self.KEY_LENGTH,
            salt_len=self.SALT_LENGTH,
            type=Type.ID  # Argon2id - hybrid of Argon2i and Argon2d
        )

        logger.debug(
            f"CryptoManager initialized: time={time_cost}, "
            f"memory={memory_cost_kb}KB, parallelism={parallelism}"
        )

    def generate_salt(self) -> bytes:
        """Generate a cryptographically secure random salt."""
        return secrets.token_bytes(self.SALT_LENGTH)

    def hash_password(self, password: str) -> str:
        """
        Hash a password using Argon2id.

        Returns the full Argon2 hash string including parameters and salt.
        """
        return self._hasher.hash(password)

    def verify_password(self, password: str, hash_str: str) -> bool:
        """
        Verify a password against an Argon2id hash.

        Returns True if password matches, False otherwise.
        """
        try:
            self._hasher.verify(hash_str, password)
            return True
        except VerifyMismatchError:
            return False

    def derive_key(self, password: str, salt: bytes) -> bytes:
        """
        Derive a 256-bit encryption key from password and salt.

        Uses Argon2id for key derivation.
        """
        from argon2.low_level import hash_secret_raw, Type

        key = hash_secret_raw(
            secret=password.encode('utf-8'),
            salt=salt,
            time_cost=self.time_cost,
            memory_cost=self.memory_cost_kb,
            parallelism=self.parallelism,
            hash_len=self.KEY_LENGTH,
            type=Type.ID
        )
        return key

    def encrypt(
        self,
        plaintext: bytes,
        key: bytes,
        associated_data: bytes | None = None
    ) -> EncryptedData:
        """
        Encrypt data using ChaCha20-Poly1305.

        Args:
            plaintext: Data to encrypt
            key: 256-bit encryption key
            associated_data: Optional authenticated data (UUID, timestamp, etc.)

        Returns:
            EncryptedData containing nonce and ciphertext
        """
        if len(key) != self.KEY_LENGTH:
            raise ValueError(f"Key must be {self.KEY_LENGTH} bytes")

        cipher = ChaCha20Poly1305(key)
        nonce = secrets.token_bytes(self.NONCE_LENGTH)

        ciphertext = cipher.encrypt(nonce, plaintext, associated_data)

        return EncryptedData(nonce=nonce, ciphertext=ciphertext)

    def decrypt(
        self,
        encrypted: EncryptedData,
        key: bytes,
        associated_data: bytes | None = None
    ) -> bytes:
        """
        Decrypt data using ChaCha20-Poly1305.

        Args:
            encrypted: EncryptedData containing nonce and ciphertext
            key: 256-bit encryption key
            associated_data: Must match what was used during encryption

        Returns:
            Decrypted plaintext bytes

        Raises:
            cryptography.exceptions.InvalidTag: If decryption fails (wrong key or tampered data)
        """
        if len(key) != self.KEY_LENGTH:
            raise ValueError(f"Key must be {self.KEY_LENGTH} bytes")

        cipher = ChaCha20Poly1305(key)

        plaintext = cipher.decrypt(encrypted.nonce, encrypted.ciphertext, associated_data)

        return plaintext

    def encrypt_string(
        self,
        plaintext: str,
        key: bytes,
        associated_data: bytes | None = None
    ) -> bytes:
        """Convenience method to encrypt a string, returns serialized bytes."""
        encrypted = self.encrypt(plaintext.encode('utf-8'), key, associated_data)
        return encrypted.to_bytes()

    def decrypt_string(
        self,
        data: bytes,
        key: bytes,
        associated_data: bytes | None = None
    ) -> str:
        """Convenience method to decrypt to string from serialized bytes."""
        encrypted = EncryptedData.from_bytes(data)
        plaintext = self.decrypt(encrypted, key, associated_data)
        return plaintext.decode('utf-8')

    def encrypt_with_password(
        self,
        plaintext: bytes,
        password: str,
        salt: bytes | None = None
    ) -> tuple[bytes, bytes]:
        """
        Encrypt data directly with a password.

        Returns (encrypted_data, salt) tuple.
        """
        if salt is None:
            salt = self.generate_salt()

        key = self.derive_key(password, salt)
        encrypted = self.encrypt(plaintext, key)

        return encrypted.to_bytes(), salt

    def decrypt_with_password(
        self,
        data: bytes,
        password: str,
        salt: bytes
    ) -> bytes:
        """Decrypt data using a password and salt."""
        key = self.derive_key(password, salt)
        encrypted = EncryptedData.from_bytes(data)
        return self.decrypt(encrypted, key)


class MasterKeyManager:
    """
    Manages the BBS master key for encrypting user keys.

    The master key is derived from the admin password at startup
    and held only in memory - never written to disk.
    """

    def __init__(self, crypto: CryptoManager):
        self.crypto = crypto
        self._master_key: bytes | None = None
        self._salt: bytes | None = None

    def initialize(self, admin_password: str, salt: bytes | None = None) -> bytes:
        """
        Initialize master key from admin password.

        Args:
            admin_password: The BBS admin password
            salt: Existing salt (for restart) or None to generate new

        Returns:
            The salt used (save this to config/db for restarts)
        """
        if salt is None:
            salt = self.crypto.generate_salt()

        self._salt = salt
        self._master_key = self.crypto.derive_key(admin_password, salt)

        logger.info("Master key initialized")
        return salt

    @property
    def key(self) -> bytes:
        """Get the master key. Raises if not initialized."""
        if self._master_key is None:
            raise RuntimeError("Master key not initialized")
        return self._master_key

    @property
    def salt(self) -> bytes:
        """Get the master key salt. Raises if not initialized."""
        if self._salt is None:
            raise RuntimeError("Master key not initialized")
        return self._salt

    def encrypt_user_key(self, user_key: bytes) -> bytes:
        """Encrypt a user's encryption key with the master key."""
        encrypted = self.crypto.encrypt(user_key, self.key)
        return encrypted.to_bytes()

    def decrypt_user_key(self, encrypted_key: bytes) -> bytes:
        """Decrypt a user's encryption key with the master key."""
        encrypted = EncryptedData.from_bytes(encrypted_key)
        return self.crypto.decrypt(encrypted, self.key)

    def clear(self):
        """Securely clear the master key from memory."""
        if self._master_key:
            # Overwrite with zeros before releasing
            self._master_key = bytes(len(self._master_key))
            self._master_key = None
        self._salt = None
        logger.info("Master key cleared from memory")
