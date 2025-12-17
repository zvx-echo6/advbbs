"""
Tests for advBBS Mail System
"""

import pytest
import time
from unittest.mock import MagicMock, AsyncMock

from advbbs.core.crypto import CryptoManager, MasterKeyManager
from advbbs.core.mail import MailService, DeliveryResult
from advbbs.db.connection import Database
from advbbs.db.users import UserRepository, NodeRepository, UserNodeRepository
from advbbs.db.messages import MessageRepository
from advbbs.db.models import MessageType


class MockBBS:
    """Mock BBS for testing mail service."""

    def __init__(self, db_path=":memory:"):
        self.crypto = CryptoManager(
            time_cost=1,
            memory_cost_kb=8192,
            parallelism=1
        )
        self.master_key = MasterKeyManager(self.crypto)
        self.master_key.initialize("test_admin_password")

        self.db = Database(db_path)
        self.db.initialize()

        self.mesh = None
        self.sync_manager = None

        # Mock config
        self.config = MagicMock()
        self.config.bbs.callsign = "TEST"


class TestMailCompose:
    """Tests for mail composition."""

    def setup_method(self):
        """Set up test fixtures."""
        self.bbs = MockBBS()
        self.mail_service = MailService(self.bbs)
        self.user_repo = UserRepository(self.bbs.db)
        self.node_repo = NodeRepository(self.bbs.db)
        self.user_node_repo = UserNodeRepository(self.bbs.db)

        # Create test users
        self._create_test_users()

    def _create_test_users(self):
        """Create test users with proper encryption keys."""
        # Create sender
        sender_salt = self.bbs.crypto.generate_salt()
        sender_key = self.bbs.crypto.derive_key("sender_pass", sender_salt)
        sender_key_enc = self.bbs.master_key.encrypt_user_key(sender_key)

        self.sender = self.user_repo.create_user(
            username="sender",
            password_hash=self.bbs.crypto.hash_password("sender_pass").encode(),
            salt=sender_salt,
            encryption_key=sender_key_enc
        )

        # Create recipient
        recipient_salt = self.bbs.crypto.generate_salt()
        recipient_key = self.bbs.crypto.derive_key("recipient_pass", recipient_salt)
        recipient_key_enc = self.bbs.master_key.encrypt_user_key(recipient_key)

        self.recipient = self.user_repo.create_user(
            username="recipient",
            password_hash=self.bbs.crypto.hash_password("recipient_pass").encode(),
            salt=recipient_salt,
            encryption_key=recipient_key_enc
        )

        # Create sender node
        self.sender_node = self.node_repo.get_or_create_node("!sender01")
        self.user_node_repo.associate_node(self.sender.id, self.sender_node.id)

    def test_compose_mail_success(self):
        """Test successful mail composition."""
        message, error = self.mail_service.compose_mail(
            sender_user_id=self.sender.id,
            sender_node_id="!sender01",
            recipient_username="recipient",
            body="Hello, this is a test message!"
        )

        assert error == ""
        assert message is not None
        assert message.sender_user_id == self.sender.id
        assert message.recipient_user_id == self.recipient.id
        assert message.body_enc is not None
        assert len(message.body_enc) > 0

    def test_compose_mail_with_subject(self):
        """Test mail composition with subject."""
        message, error = self.mail_service.compose_mail(
            sender_user_id=self.sender.id,
            sender_node_id="!sender01",
            recipient_username="recipient",
            body="Test body",
            subject="Test Subject"
        )

        assert error == ""
        assert message is not None
        assert message.subject_enc is not None

    def test_compose_mail_unknown_recipient(self):
        """Test mail to unknown recipient fails."""
        message, error = self.mail_service.compose_mail(
            sender_user_id=self.sender.id,
            sender_node_id="!sender01",
            recipient_username="unknown_user",
            body="Hello!"
        )

        assert message is None
        assert "not found" in error.lower()

    def test_compose_mail_to_self(self):
        """Test mail to self fails."""
        message, error = self.mail_service.compose_mail(
            sender_user_id=self.sender.id,
            sender_node_id="!sender01",
            recipient_username="sender",
            body="Hello myself!"
        )

        assert message is None
        assert "yourself" in error.lower()

    def test_compose_mail_to_banned_user(self):
        """Test mail to banned user fails."""
        self.user_repo.ban_user("recipient", "test ban", "admin")

        message, error = self.mail_service.compose_mail(
            sender_user_id=self.sender.id,
            sender_node_id="!sender01",
            recipient_username="recipient",
            body="Hello!"
        )

        assert message is None
        assert "banned" in error.lower()


class TestMailRead:
    """Tests for mail reading."""

    def setup_method(self):
        """Set up test fixtures."""
        self.bbs = MockBBS()
        self.mail_service = MailService(self.bbs)
        self.user_repo = UserRepository(self.bbs.db)
        self.node_repo = NodeRepository(self.bbs.db)
        self.msg_repo = MessageRepository(self.bbs.db)

        # Create test users
        self._create_test_users()

        # Create test message
        self._create_test_message()

    def _create_test_users(self):
        """Create test users."""
        sender_salt = self.bbs.crypto.generate_salt()
        sender_key = self.bbs.crypto.derive_key("sender_pass", sender_salt)
        sender_key_enc = self.bbs.master_key.encrypt_user_key(sender_key)

        self.sender = self.user_repo.create_user(
            username="sender",
            password_hash=self.bbs.crypto.hash_password("sender_pass").encode(),
            salt=sender_salt,
            encryption_key=sender_key_enc
        )

        recipient_salt = self.bbs.crypto.generate_salt()
        recipient_key = self.bbs.crypto.derive_key("recipient_pass", recipient_salt)
        recipient_key_enc = self.bbs.master_key.encrypt_user_key(recipient_key)

        self.recipient = self.user_repo.create_user(
            username="recipient",
            password_hash=self.bbs.crypto.hash_password("recipient_pass").encode(),
            salt=recipient_salt,
            encryption_key=recipient_key_enc
        )

        self.sender_node = self.node_repo.get_or_create_node("!sender01")

    def _create_test_message(self):
        """Create a test message."""
        self.test_body = "This is a test message for reading!"

        message, _ = self.mail_service.compose_mail(
            sender_user_id=self.sender.id,
            sender_node_id="!sender01",
            recipient_username="recipient",
            body=self.test_body,
            subject="Test Subject"
        )
        self.test_message = message

    def test_read_mail_success(self):
        """Test successful mail reading."""
        mail, error = self.mail_service.read_mail(
            self.recipient.id,
            self.test_message.id
        )

        assert error == ""
        assert mail is not None
        assert mail["body"] == self.test_body
        assert mail["from"] == "sender"

    def test_read_mail_wrong_recipient(self):
        """Test reading mail addressed to someone else fails."""
        mail, error = self.mail_service.read_mail(
            self.sender.id,  # Not the recipient
            self.test_message.id
        )

        assert mail is None
        assert "not addressed" in error.lower()

    def test_read_mail_marks_as_read(self):
        """Test that reading mail marks it as read."""
        # First read
        mail, _ = self.mail_service.read_mail(
            self.recipient.id,
            self.test_message.id
        )

        # Verify marked as read
        updated_msg = self.msg_repo.get_message_by_id(self.test_message.id)
        assert updated_msg.read_at_us is not None


class TestMailList:
    """Tests for mail listing."""

    def setup_method(self):
        """Set up test fixtures."""
        self.bbs = MockBBS()
        self.mail_service = MailService(self.bbs)
        self.user_repo = UserRepository(self.bbs.db)
        self.node_repo = NodeRepository(self.bbs.db)

        self._create_test_users()
        self._create_multiple_messages()

    def _create_test_users(self):
        """Create test users."""
        sender_salt = self.bbs.crypto.generate_salt()
        sender_key = self.bbs.crypto.derive_key("sender_pass", sender_salt)
        sender_key_enc = self.bbs.master_key.encrypt_user_key(sender_key)

        self.sender = self.user_repo.create_user(
            username="sender",
            password_hash=b"hash",
            salt=sender_salt,
            encryption_key=sender_key_enc
        )

        recipient_salt = self.bbs.crypto.generate_salt()
        recipient_key = self.bbs.crypto.derive_key("recipient_pass", recipient_salt)
        recipient_key_enc = self.bbs.master_key.encrypt_user_key(recipient_key)

        self.recipient = self.user_repo.create_user(
            username="recipient",
            password_hash=b"hash",
            salt=recipient_salt,
            encryption_key=recipient_key_enc
        )

        self.sender_node = self.node_repo.get_or_create_node("!sender01")

    def _create_multiple_messages(self):
        """Create multiple test messages."""
        for i in range(5):
            self.mail_service.compose_mail(
                sender_user_id=self.sender.id,
                sender_node_id="!sender01",
                recipient_username="recipient",
                body=f"Test message {i}"
            )

    def test_list_mail(self):
        """Test listing mail."""
        messages = self.mail_service.list_mail(self.recipient.id)

        assert len(messages) == 5
        for msg in messages:
            assert "from" in msg
            assert "date" in msg
            assert msg["from"] == "sender"

    def test_list_mail_limit(self):
        """Test listing with limit."""
        messages = self.mail_service.list_mail(self.recipient.id, limit=3)

        assert len(messages) == 3

    def test_inbox_summary(self):
        """Test inbox summary."""
        summary = self.mail_service.get_inbox_summary(self.recipient.id)

        assert summary["total"] == 5
        assert summary["unread"] == 5


class TestMailDelete:
    """Tests for mail deletion."""

    def setup_method(self):
        """Set up test fixtures."""
        self.bbs = MockBBS()
        self.mail_service = MailService(self.bbs)
        self.user_repo = UserRepository(self.bbs.db)
        self.node_repo = NodeRepository(self.bbs.db)
        self.msg_repo = MessageRepository(self.bbs.db)

        self._create_test_users()

    def _create_test_users(self):
        """Create test users."""
        sender_salt = self.bbs.crypto.generate_salt()
        sender_key = self.bbs.crypto.derive_key("sender_pass", sender_salt)
        sender_key_enc = self.bbs.master_key.encrypt_user_key(sender_key)

        self.sender = self.user_repo.create_user(
            username="sender",
            password_hash=b"hash",
            salt=sender_salt,
            encryption_key=sender_key_enc
        )

        recipient_salt = self.bbs.crypto.generate_salt()
        recipient_key = self.bbs.crypto.derive_key("recipient_pass", recipient_salt)
        recipient_key_enc = self.bbs.master_key.encrypt_user_key(recipient_key)

        self.recipient = self.user_repo.create_user(
            username="recipient",
            password_hash=b"hash",
            salt=recipient_salt,
            encryption_key=recipient_key_enc
        )

        self.sender_node = self.node_repo.get_or_create_node("!sender01")

    def test_delete_mail_as_recipient(self):
        """Test recipient can delete mail."""
        message, _ = self.mail_service.compose_mail(
            sender_user_id=self.sender.id,
            sender_node_id="!sender01",
            recipient_username="recipient",
            body="Delete me"
        )

        success, error = self.mail_service.delete_mail(self.recipient.id, message.id)

        assert success
        assert error == ""
        assert self.msg_repo.get_message_by_id(message.id) is None

    def test_delete_mail_as_sender(self):
        """Test sender can delete mail."""
        message, _ = self.mail_service.compose_mail(
            sender_user_id=self.sender.id,
            sender_node_id="!sender01",
            recipient_username="recipient",
            body="Delete me"
        )

        success, error = self.mail_service.delete_mail(self.sender.id, message.id)

        assert success

    def test_delete_mail_wrong_user(self):
        """Test non-owner cannot delete mail."""
        # Create another user
        other_salt = self.bbs.crypto.generate_salt()
        other_key = self.bbs.crypto.derive_key("other_pass", other_salt)
        other_key_enc = self.bbs.master_key.encrypt_user_key(other_key)

        other_user = self.user_repo.create_user(
            username="other",
            password_hash=b"hash",
            salt=other_salt,
            encryption_key=other_key_enc
        )

        message, _ = self.mail_service.compose_mail(
            sender_user_id=self.sender.id,
            sender_node_id="!sender01",
            recipient_username="recipient",
            body="Don't delete me"
        )

        success, error = self.mail_service.delete_mail(other_user.id, message.id)

        assert not success
        assert "permission" in error.lower()
