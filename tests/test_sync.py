"""
Tests for advBBS Sync Protocols

Tests advBBS native sync protocol.
"""

import base64
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from advbbs.sync.compat.advbbs_native import AdvBBSNativeSync, AdvBBSSyncMessage


class MockSyncManager:
    """Mock sync manager for testing protocol handlers."""

    def __init__(self):
        self.db = MagicMock()
        self.mesh = AsyncMock()
        self.bbs = MagicMock()
        self.config = MagicMock()
        self.config.bbs_name = "TestBBS"
        self.config.callsign = "TEST"

        # Mock database methods
        self.db.fetchone = MagicMock(return_value=None)
        self.db.execute = MagicMock()

        # Mock BBS crypto
        self.bbs.crypto = MagicMock()
        self.bbs.crypto.encrypt_string = MagicMock(return_value=b"encrypted")
        self.bbs.crypto.decrypt_string = MagicMock(return_value="decrypted")
        self.bbs.master_key = MagicMock()
        self.bbs.master_key.key = b"testkey"


class TestAdvBBSProtocol:
    """Tests for advBBS native protocol."""

    def setup_method(self):
        self.sync_manager = MockSyncManager()
        self.native = AdvBBSNativeSync(self.sync_manager)

    def test_is_advbbs_message(self):
        """Test advBBS message detection."""
        assert self.native.is_advbbs_message("advBBS|1|HELLO|payload")
        assert self.native.is_advbbs_message("advBBS|1|SYNC_REQ|1234|bulletin")
        assert self.native.is_advbbs_message("advBBS|1|SYNC_ACK|uuid")
        assert self.native.is_advbbs_message("advBBS|1|SYNC_DONE|5")
        assert self.native.is_advbbs_message("advBBS|1|DELETE|uuid")

    def test_is_not_advbbs_message(self):
        """Test non-advBBS messages."""
        assert not self.native.is_advbbs_message("Hello world")
        assert not self.native.is_advbbs_message("BULLETIN|general|user|subj|body|uuid")
        assert not self.native.is_advbbs_message("bbslink data")
        assert not self.native.is_advbbs_message("advBBS|1|UNKNOWN|payload")
        assert not self.native.is_advbbs_message("advBBS|")

    def test_format_message(self):
        """Test message formatting."""
        msg = self.native._format_message("HELLO", "TEST:TestBBS|mail,bulletin")
        assert msg == "advBBS|1|HELLO|TEST:TestBBS|mail,bulletin"

        msg = self.native._format_message("SYNC_ACK", "uuid-123")
        assert msg == "advBBS|1|SYNC_ACK|uuid-123"

    def test_sync_message_encoding(self):
        """Test sync message JSON/base64 encoding."""
        sync_msg = AdvBBSSyncMessage(
            uuid="test-uuid-123",
            msg_type="bulletin",
            board="general",
            sender="testuser",
            subject="Test Subject",
            body="Test body content",
            timestamp_us=1702000000000000,
            origin_bbs="TEST"
        )

        # Encode like the protocol does
        from dataclasses import asdict
        msg_dict = asdict(sync_msg)
        json_str = json.dumps(msg_dict, separators=(',', ':'))
        encoded = base64.b64encode(json_str.encode()).decode()

        # Decode and verify
        decoded_json = base64.b64decode(encoded).decode()
        decoded_dict = json.loads(decoded_json)

        assert decoded_dict["uuid"] == "test-uuid-123"
        assert decoded_dict["msg_type"] == "bulletin"
        assert decoded_dict["board"] == "general"
        assert decoded_dict["sender"] == "testuser"
        assert decoded_dict["subject"] == "Test Subject"
        assert decoded_dict["body"] == "Test body content"

    def test_handle_hello(self):
        """Test handling HELLO message."""
        # The handler should register the peer
        self.native._handle_hello("PEER:PeerBBS|mail,bulletin", "!peernode")

        # Verify peer was registered
        self.sync_manager.db.execute.assert_called()


class TestProtocolDetection:
    """Tests for protocol auto-detection in sync manager."""

    def setup_method(self):
        self.sync_manager = MockSyncManager()
        self.native = AdvBBSNativeSync(self.sync_manager)

    def test_detect_advbbs(self):
        """Test advBBS protocol detection."""
        msg = "advBBS|1|HELLO|TEST:TestBBS|mail,bulletin"
        assert self.native.is_advbbs_message(msg)

    def test_detect_none(self):
        """Test detection of non-sync messages."""
        msg = "Hello, this is a regular chat message"
        assert not self.native.is_advbbs_message(msg)


class TestSyncMessageDataclasses:
    """Tests for sync message dataclasses."""

    def test_advbbs_sync_message(self):
        """Test AdvBBSSyncMessage dataclass."""
        msg = AdvBBSSyncMessage(
            uuid="test-uuid",
            msg_type="bulletin",
            board="general",
            sender="testuser",
            subject="Subject",
            body="Body",
            timestamp_us=1702000000000000,
            origin_bbs="TEST"
        )
        assert msg.uuid == "test-uuid"
        assert msg.msg_type == "bulletin"
        assert msg.timestamp_us == 1702000000000000


class TestEdgeCases:
    """Edge case tests for sync protocols."""

    def setup_method(self):
        self.sync_manager = MockSyncManager()

    def test_advbbs_empty_payload(self):
        """Test advBBS with empty payload."""
        native = AdvBBSNativeSync(self.sync_manager)

        # SYNC_DONE with empty payload
        assert native.is_advbbs_message("advBBS|1|SYNC_DONE|")

    def test_unicode_content(self):
        """Test advBBS with unicode content."""
        sync_msg = AdvBBSSyncMessage(
            uuid="test-uuid",
            msg_type="bulletin",
            board="general",
            sender="用户",
            subject="主题",
            body="内容",
            timestamp_us=1702000000000000,
            origin_bbs="TEST"
        )
        assert sync_msg.sender == "用户"
        assert sync_msg.subject == "主题"
        assert sync_msg.body == "内容"
