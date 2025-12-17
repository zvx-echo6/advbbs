"""
Tests for advBBS Crypto Module
"""

import pytest
from advbbs.core.crypto import CryptoManager, EncryptedData, MasterKeyManager


class TestCryptoManager:
    """Tests for CryptoManager class."""

    def setup_method(self):
        """Set up test fixtures."""
        # Use minimal memory for faster tests
        self.crypto = CryptoManager(
            time_cost=1,
            memory_cost_kb=8192,  # 8MB for tests
            parallelism=1
        )

    def test_generate_salt(self):
        """Test salt generation."""
        salt1 = self.crypto.generate_salt()
        salt2 = self.crypto.generate_salt()

        assert len(salt1) == 16
        assert len(salt2) == 16
        assert salt1 != salt2  # Should be unique

    def test_hash_password(self):
        """Test password hashing."""
        password = "test_password_123"
        hash1 = self.crypto.hash_password(password)
        hash2 = self.crypto.hash_password(password)

        # Hashes should be different (different salts)
        assert hash1 != hash2

        # Both should verify
        assert self.crypto.verify_password(password, hash1)
        assert self.crypto.verify_password(password, hash2)

    def test_verify_password_wrong(self):
        """Test password verification with wrong password."""
        password = "correct_password"
        wrong = "wrong_password"

        hash_str = self.crypto.hash_password(password)

        assert self.crypto.verify_password(password, hash_str) is True
        assert self.crypto.verify_password(wrong, hash_str) is False

    def test_derive_key(self):
        """Test key derivation."""
        password = "test_password"
        salt = self.crypto.generate_salt()

        key1 = self.crypto.derive_key(password, salt)
        key2 = self.crypto.derive_key(password, salt)

        assert len(key1) == 32  # 256 bits
        assert key1 == key2  # Same inputs = same key

    def test_derive_key_different_salt(self):
        """Test that different salts produce different keys."""
        password = "test_password"
        salt1 = self.crypto.generate_salt()
        salt2 = self.crypto.generate_salt()

        key1 = self.crypto.derive_key(password, salt1)
        key2 = self.crypto.derive_key(password, salt2)

        assert key1 != key2

    def test_encrypt_decrypt(self):
        """Test basic encryption and decryption."""
        password = "test_password"
        salt = self.crypto.generate_salt()
        key = self.crypto.derive_key(password, salt)

        plaintext = b"Hello, World! This is a test message."
        encrypted = self.crypto.encrypt(plaintext, key)

        assert isinstance(encrypted, EncryptedData)
        assert len(encrypted.nonce) == 12
        assert encrypted.ciphertext != plaintext

        decrypted = self.crypto.decrypt(encrypted, key)
        assert decrypted == plaintext

    def test_encrypt_with_aad(self):
        """Test encryption with associated data."""
        password = "test_password"
        salt = self.crypto.generate_salt()
        key = self.crypto.derive_key(password, salt)

        plaintext = b"Secret message"
        aad = b"uuid-12345|1234567890"

        encrypted = self.crypto.encrypt(plaintext, key, aad)
        decrypted = self.crypto.decrypt(encrypted, key, aad)

        assert decrypted == plaintext

    def test_decrypt_wrong_aad_fails(self):
        """Test that wrong AAD causes decryption to fail."""
        password = "test_password"
        salt = self.crypto.generate_salt()
        key = self.crypto.derive_key(password, salt)

        plaintext = b"Secret message"
        aad = b"uuid-12345|1234567890"
        wrong_aad = b"wrong-uuid|9999999999"

        encrypted = self.crypto.encrypt(plaintext, key, aad)

        with pytest.raises(Exception):  # InvalidTag
            self.crypto.decrypt(encrypted, key, wrong_aad)

    def test_encrypt_string(self):
        """Test string encryption convenience method."""
        password = "test_password"
        salt = self.crypto.generate_salt()
        key = self.crypto.derive_key(password, salt)

        plaintext = "Hello, World! 🌍"
        encrypted_bytes = self.crypto.encrypt_string(plaintext, key)

        assert isinstance(encrypted_bytes, bytes)

        decrypted = self.crypto.decrypt_string(encrypted_bytes, key)
        assert decrypted == plaintext

    def test_encrypt_with_password(self):
        """Test password-based encryption."""
        password = "test_password"
        plaintext = b"Secret data"

        encrypted, salt = self.crypto.encrypt_with_password(plaintext, password)

        assert isinstance(encrypted, bytes)
        assert isinstance(salt, bytes)
        assert len(salt) == 16

        decrypted = self.crypto.decrypt_with_password(encrypted, password, salt)
        assert decrypted == plaintext

    def test_encrypted_data_serialization(self):
        """Test EncryptedData to/from bytes."""
        nonce = b"123456789012"  # 12 bytes
        ciphertext = b"encrypted_content_here"

        data = EncryptedData(nonce=nonce, ciphertext=ciphertext)
        serialized = data.to_bytes()

        restored = EncryptedData.from_bytes(serialized)

        assert restored.nonce == nonce
        assert restored.ciphertext == ciphertext


class TestMasterKeyManager:
    """Tests for MasterKeyManager class."""

    def setup_method(self):
        """Set up test fixtures."""
        self.crypto = CryptoManager(
            time_cost=1,
            memory_cost_kb=8192,
            parallelism=1
        )
        self.master = MasterKeyManager(self.crypto)

    def test_initialize(self):
        """Test master key initialization."""
        admin_password = "admin_password_123"
        salt = self.master.initialize(admin_password)

        assert len(salt) == 16
        assert self.master.key is not None
        assert len(self.master.key) == 32

    def test_initialize_with_salt(self):
        """Test initialization with existing salt."""
        admin_password = "admin_password_123"
        fixed_salt = b"fixed_salt_value"

        salt = self.master.initialize(admin_password, fixed_salt)

        assert salt == fixed_salt
        assert self.master.salt == fixed_salt

    def test_encrypt_decrypt_user_key(self):
        """Test user key encryption/decryption."""
        self.master.initialize("admin_password")

        user_key = b"user_encryption_key_here_32bytes!"[:32]

        encrypted = self.master.encrypt_user_key(user_key)
        assert encrypted != user_key

        decrypted = self.master.decrypt_user_key(encrypted)
        assert decrypted == user_key

    def test_clear(self):
        """Test master key clearing."""
        self.master.initialize("admin_password")

        assert self.master._master_key is not None

        self.master.clear()

        assert self.master._master_key is None
        assert self.master._salt is None

    def test_key_not_initialized_raises(self):
        """Test that accessing key before init raises."""
        with pytest.raises(RuntimeError):
            _ = self.master.key

        with pytest.raises(RuntimeError):
            _ = self.master.salt
