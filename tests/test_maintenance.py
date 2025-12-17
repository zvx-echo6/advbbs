"""
Tests for advBBS Maintenance Module

Tests auto-announcements, message expiration, and backup functionality.
"""

import os
import time
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

from advbbs.config import Config, BBSConfig, DatabaseConfig
from advbbs.db.connection import Database
from advbbs.core.maintenance import MaintenanceManager


class MockBBS:
    """Mock BBS for testing maintenance manager."""

    def __init__(self, db_path=":memory:"):
        self.config = Config()
        self.config.bbs = BBSConfig(
            name="TestBBS",
            callsign="TEST",
            max_message_age_days=30,
            announcement_interval_hours=12,
        )
        self.config.database = DatabaseConfig(
            path=db_path,
            backup_path=tempfile.mkdtemp(),
            backup_interval_hours=24,
        )

        self.db = Database(db_path)
        self.db.initialize()

        self.mesh = AsyncMock()
        self.mesh.connected = True

        self.start_time = time.time()
        self._sessions = {}


class TestAnnouncements:
    """Tests for auto-announcement system."""

    def setup_method(self):
        self.bbs = MockBBS()
        self.maintenance = MaintenanceManager(self.bbs)

    @pytest.mark.asyncio
    async def test_send_announcement(self):
        """Test sending BBS announcement."""
        await self.maintenance.send_announcement()

        self.bbs.mesh.send_broadcast.assert_called_once()
        call_args = self.bbs.mesh.send_broadcast.call_args
        msg = call_args[0][0]

        assert "TEST" in msg  # Callsign
        assert "TestBBS" in msg  # Name
        assert "help" in msg.lower()

    @pytest.mark.asyncio
    async def test_send_custom_announcement(self):
        """Test sending custom announcement."""
        custom_msg = "Custom test announcement"
        await self.maintenance.send_announcement(custom_msg)

        self.bbs.mesh.send_broadcast.assert_called_once()
        call_args = self.bbs.mesh.send_broadcast.call_args
        assert call_args[0][0] == custom_msg

    @pytest.mark.asyncio
    async def test_announcement_interval(self):
        """Test announcement respects interval."""
        # Set last announcement to now
        self.maintenance._last_announcement = time.time()

        # Check should not trigger new announcement
        await self.maintenance._check_announcements(time.time())
        self.bbs.mesh.send_broadcast.assert_not_called()

    @pytest.mark.asyncio
    async def test_announcement_disabled(self):
        """Test announcements disabled when interval is 0."""
        self.bbs.config.bbs.announcement_interval_hours = 0
        await self.maintenance._check_announcements(time.time())
        self.bbs.mesh.send_broadcast.assert_not_called()


class TestMessageExpiration:
    """Tests for message expiration/cleanup."""

    def setup_method(self):
        self.bbs = MockBBS()
        self.maintenance = MaintenanceManager(self.bbs)
        self._create_test_data()

    def _create_test_data(self):
        """Create test messages with different ages."""
        now_us = int(time.time() * 1_000_000)
        old_us = now_us - (40 * 86400 * 1_000_000)  # 40 days ago

        # Create test node
        self.bbs.db.execute(
            "INSERT INTO nodes (node_id, first_seen_us, last_seen_us) VALUES (?, ?, ?)",
            ("!testnode", now_us, now_us)
        )

        # Create old bulletin (should expire)
        self.bbs.db.execute("""
            INSERT INTO messages (uuid, msg_type, sender_node_id, body_enc, created_at_us, board_id)
            VALUES (?, 'bulletin', 1, ?, ?, 1)
        """, ("old-bulletin-1", b"old content", old_us))

        # Create recent bulletin (should not expire)
        self.bbs.db.execute("""
            INSERT INTO messages (uuid, msg_type, sender_node_id, body_enc, created_at_us, board_id)
            VALUES (?, 'bulletin', 1, ?, ?, 1)
        """, ("new-bulletin-1", b"new content", now_us))

        # Create old read mail (should expire)
        self.bbs.db.execute("""
            INSERT INTO messages (uuid, msg_type, sender_node_id, body_enc, created_at_us, read_at_us)
            VALUES (?, 'mail', 1, ?, ?, ?)
        """, ("old-mail-1", b"old mail", old_us, old_us + 1000))

        # Create old unread mail (should NOT expire)
        self.bbs.db.execute("""
            INSERT INTO messages (uuid, msg_type, sender_node_id, body_enc, created_at_us)
            VALUES (?, 'mail', 1, ?, ?)
        """, ("old-unread-mail", b"old unread", old_us))

    @pytest.mark.asyncio
    async def test_expire_old_bulletins(self):
        """Test old bulletins are expired."""
        result = await self.maintenance.run_expiration()

        assert result["bulletins_expired"] == 1

        # Verify old bulletin is marked deleted
        row = self.bbs.db.fetchone(
            "SELECT deleted_at_us FROM messages WHERE uuid = 'old-bulletin-1'"
        )
        assert row[0] is not None

        # Verify new bulletin is NOT deleted
        row = self.bbs.db.fetchone(
            "SELECT deleted_at_us FROM messages WHERE uuid = 'new-bulletin-1'"
        )
        assert row[0] is None

    @pytest.mark.asyncio
    async def test_expire_read_mail_only(self):
        """Test only read mail is expired."""
        result = await self.maintenance.run_expiration()

        assert result["mail_expired"] == 1

        # Read mail should be expired
        row = self.bbs.db.fetchone(
            "SELECT deleted_at_us FROM messages WHERE uuid = 'old-mail-1'"
        )
        assert row[0] is not None

        # Unread mail should NOT be expired
        row = self.bbs.db.fetchone(
            "SELECT deleted_at_us FROM messages WHERE uuid = 'old-unread-mail'"
        )
        assert row[0] is None

    @pytest.mark.asyncio
    async def test_expiration_disabled(self):
        """Test expiration disabled when max_age is 0."""
        self.bbs.config.bbs.max_message_age_days = 0
        result = await self.maintenance.run_expiration()

        assert result["bulletins_expired"] == 0
        assert result["mail_expired"] == 0


class TestBackup:
    """Tests for database backup functionality."""

    def setup_method(self):
        # Use temp file for database
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.backup_dir = os.path.join(self.temp_dir, "backups")

        self.bbs = MockBBS(self.db_path)
        self.bbs.config.database.backup_path = self.backup_dir
        self.maintenance = MaintenanceManager(self.bbs)

    def teardown_method(self):
        # Clean up temp files
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_create_backup(self):
        """Test creating database backup."""
        backup_path = await self.maintenance.run_backup()

        assert backup_path is not None
        assert os.path.exists(backup_path)
        assert "advbbs_backup_" in backup_path
        assert backup_path.endswith(".db")

    @pytest.mark.asyncio
    async def test_backup_creates_directory(self):
        """Test backup creates backup directory if needed."""
        # Remove backup directory
        import shutil
        shutil.rmtree(self.backup_dir, ignore_errors=True)

        backup_path = await self.maintenance.run_backup()

        assert backup_path is not None
        assert os.path.exists(self.backup_dir)

    @pytest.mark.asyncio
    async def test_backup_cleanup(self):
        """Test old backups are cleaned up."""
        # Create 10 backups with unique names by manipulating timestamp
        import os
        from datetime import datetime

        # Ensure backup directory exists
        os.makedirs(self.backup_dir, exist_ok=True)

        for i in range(10):
            # Create backup file directly with unique timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{i:02d}"
            backup_name = f"advbbs_backup_{timestamp}.db"
            backup_path = os.path.join(self.backup_dir, backup_name)
            with open(backup_path, "wb") as f:
                f.write(b"test backup data")

        # Now run cleanup
        await self.maintenance._cleanup_old_backups(Path(self.backup_dir), keep=7)

        backups = self.maintenance.list_backups()

        # Should keep only 7 most recent
        assert len(backups) == 7

    def test_list_backups(self):
        """Test listing available backups."""
        # Create backup directory and some files
        os.makedirs(self.backup_dir, exist_ok=True)

        for i in range(3):
            path = os.path.join(self.backup_dir, f"advbbs_backup_2024010{i}_120000.db")
            with open(path, "w") as f:
                f.write("test")

        backups = self.maintenance.list_backups()

        assert len(backups) == 3
        assert all("path" in b for b in backups)
        assert all("filename" in b for b in backups)
        assert all("size_bytes" in b for b in backups)


class TestStatistics:
    """Tests for statistics collection."""

    def setup_method(self):
        self.bbs = MockBBS()
        self.maintenance = MaintenanceManager(self.bbs)

    def test_get_stats(self):
        """Test getting BBS statistics."""
        stats = self.maintenance.get_stats()

        assert "users" in stats
        assert "messages" in stats
        assert "mail" in stats
        assert "bulletins" in stats
        assert "boards" in stats
        assert "uptime_hours" in stats
        assert "uptime_seconds" in stats

    def test_stats_with_data(self):
        """Test statistics reflect actual data."""
        # Add test user
        now_us = int(time.time() * 1_000_000)
        self.bbs.db.execute(
            "INSERT INTO users (username, password_hash, salt, encryption_key, created_at_us) VALUES (?, ?, ?, ?, ?)",
            ("testuser", b"hash", b"salt", b"key", now_us)
        )

        stats = self.maintenance.get_stats()
        assert stats["users"] == 1


class TestMaintenanceTick:
    """Tests for the maintenance tick loop."""

    def setup_method(self):
        self.bbs = MockBBS()
        self.maintenance = MaintenanceManager(self.bbs)

    @pytest.mark.asyncio
    async def test_tick_runs_checks(self):
        """Test tick runs all maintenance checks."""
        # Set timestamps to trigger all checks
        self.maintenance._last_announcement = 0
        self.maintenance._last_expiration = 0
        self.maintenance._last_backup = 0

        # Disable backup to speed up test
        self.bbs.config.database.backup_interval_hours = 0

        await self.maintenance.tick()

        # Should have sent announcement
        self.bbs.mesh.send_broadcast.assert_called()

    @pytest.mark.asyncio
    async def test_force_operations(self):
        """Test forcing maintenance operations."""
        # Force announcement
        await self.maintenance.force_announcement("Test message")
        self.bbs.mesh.send_broadcast.assert_called()

        # Force expiration
        result = await self.maintenance.force_expiration()
        assert isinstance(result, dict)

        # Force backup (skip if in-memory db)
        if self.bbs.config.database.path != ":memory:":
            result = await self.maintenance.force_backup()
            # Result depends on actual database
