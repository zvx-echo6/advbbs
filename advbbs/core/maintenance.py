"""
advBBS Maintenance Module

Handles periodic maintenance tasks:
- Auto-announcements
- Message expiration and cleanup
- Database backup
- Statistics collection
"""

import asyncio
import logging
import os
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .bbs import advBBS

logger = logging.getLogger(__name__)


class MaintenanceManager:
    """
    Manages periodic maintenance tasks for advBBS.

    Tasks:
    - Periodic BBS announcements
    - Expired message cleanup
    - Database backups
    - Session cleanup
    - Statistics logging
    """

    def __init__(self, bbs: "advBBS"):
        """
        Initialize maintenance manager.

        Args:
            bbs: advBBS instance
        """
        self.bbs = bbs
        self.config = bbs.config

        # Last run timestamps
        self._last_announcement = 0
        self._last_expiration = 0
        self._last_backup = 0

        # Load last backup time from database
        self._load_last_backup_time()

    def _load_last_backup_time(self):
        """Load last backup timestamp from database."""
        if not self.bbs.db:
            return

        try:
            row = self.bbs.db.fetchone(
                "SELECT value FROM bbs_settings WHERE key = 'last_backup_us'"
            )
            if row:
                self._last_backup = int(row[0]) / 1_000_000
        except Exception:
            pass

    async def tick(self):
        """
        Run periodic maintenance checks.

        Called from main BBS loop.
        """
        now = time.time()

        # Auto-announcements (configurable interval)
        await self._check_announcements(now)

        # Message expiration (daily)
        await self._check_expiration(now)

        # Database backup (configurable interval)
        await self._check_backup(now)

    async def _check_announcements(self, now: float):
        """Check and send periodic announcements."""
        # Check if announcements are enabled
        if not self.config.bbs.announcements_enabled:
            return

        interval_hours = self.config.bbs.announcement_interval_hours
        if interval_hours <= 0:
            return

        interval_secs = interval_hours * 3600

        if now - self._last_announcement < interval_secs:
            return

        self._last_announcement = now

        await self.send_announcement()

    async def send_announcement(self, custom_message: Optional[str] = None):
        """
        Send BBS announcement to mesh.

        Args:
            custom_message: Optional custom message (uses default if None)
        """
        if not self.bbs.mesh or not self.bbs.mesh.connected:
            return

        if custom_message:
            msg = custom_message
        elif self.config.bbs.announcement_message:
            # Use configured custom message with variable substitution
            msg = self._format_announcement(self.config.bbs.announcement_message)
        else:
            # Default announcement with stats
            msg = self._format_announcement("[{callsign}] {name} online. {users} users, {msgs} msgs. DM !bbs for help.")

        try:
            channel = self.config.bbs.announcement_channel
            await self.bbs.mesh.send_broadcast(msg, channel)
            logger.info(f"Sent announcement on channel {channel}: {msg}")
        except Exception as e:
            logger.error(f"Failed to send announcement: {e}")

    def _format_announcement(self, template: str) -> str:
        """
        Format announcement template with variables.

        Supported variables:
            {callsign} - BBS callsign
            {name} - BBS name
            {users} - User count
            {msgs} - Message count
        """
        return template.format(
            callsign=self.config.bbs.callsign,
            name=self.config.bbs.name,
            users=self._get_user_count(),
            msgs=self._get_message_count()
        )

    def _get_user_count(self) -> int:
        """Get total registered user count."""
        if not self.bbs.db:
            return 0
        try:
            row = self.bbs.db.fetchone("SELECT COUNT(*) FROM users")
            return row[0] if row else 0
        except Exception:
            return 0

    def _get_message_count(self) -> int:
        """Get total message count (active messages)."""
        if not self.bbs.db:
            return 0
        try:
            row = self.bbs.db.fetchone(
                "SELECT COUNT(*) FROM messages WHERE deleted_at_us IS NULL"
            )
            return row[0] if row else 0
        except Exception:
            return 0

    async def _check_expiration(self, now: float):
        """Check and run message expiration."""
        # Run once per day (86400 seconds)
        if now - self._last_expiration < 86400:
            return

        self._last_expiration = now

        await self.run_expiration()

    async def run_expiration(self) -> dict:
        """
        Expire old messages based on configuration.

        Returns dict with:
        - bulletins_expired: Count of expired bulletins
        - mail_expired: Count of expired mail
        - sessions_cleaned: Count of cleaned sessions
        """
        result = {
            "bulletins_expired": 0,
            "mail_expired": 0,
            "sessions_cleaned": 0,
        }

        if not self.bbs.db:
            return result

        max_age_days = self.config.bbs.max_message_age_days
        if max_age_days <= 0:
            logger.debug("Message expiration disabled")
            return result

        cutoff_us = int((time.time() - (max_age_days * 86400)) * 1_000_000)
        now_us = int(time.time() * 1_000_000)

        try:
            # Expire bulletins
            cursor = self.bbs.db.execute("""
                UPDATE messages
                SET deleted_at_us = ?
                WHERE msg_type = 'bulletin'
                  AND created_at_us < ?
                  AND deleted_at_us IS NULL
            """, (now_us, cutoff_us))
            result["bulletins_expired"] = cursor.rowcount

            # Expire mail (only if read)
            cursor = self.bbs.db.execute("""
                UPDATE messages
                SET deleted_at_us = ?
                WHERE msg_type = 'mail'
                  AND created_at_us < ?
                  AND deleted_at_us IS NULL
                  AND read_at_us IS NOT NULL
            """, (now_us, cutoff_us))
            result["mail_expired"] = cursor.rowcount

            # Clean up expired sessions
            result["sessions_cleaned"] = self._cleanup_sessions()

            # Clean up orphaned data
            await self._cleanup_orphaned_data()

            logger.info(
                f"Expiration complete: {result['bulletins_expired']} bulletins, "
                f"{result['mail_expired']} mail, {result['sessions_cleaned']} sessions"
            )

        except Exception as e:
            logger.error(f"Error during expiration: {e}")

        return result

    def _cleanup_sessions(self) -> int:
        """Clean up expired sessions from memory."""
        if not hasattr(self.bbs, '_sessions'):
            return 0

        now = time.time()
        timeout = 3600  # 1 hour

        expired = [
            node_id for node_id, session in self.bbs._sessions.items()
            if now - session.get("last_activity", 0) > timeout
        ]

        for node_id in expired:
            del self.bbs._sessions[node_id]

        return len(expired)

    async def _cleanup_orphaned_data(self):
        """Clean up orphaned sync logs, read positions, etc."""
        if not self.bbs.db:
            return

        try:
            # Clean sync logs for deleted messages (older than 7 days)
            cutoff_us = int((time.time() - (7 * 86400)) * 1_000_000)
            self.bbs.db.execute("""
                DELETE FROM sync_log
                WHERE last_attempt_us < ?
                  AND message_uuid NOT IN (SELECT uuid FROM messages WHERE uuid IS NOT NULL)
            """, (cutoff_us,))

            # Clean read positions for deleted messages
            self.bbs.db.execute("""
                DELETE FROM board_read_positions
                WHERE last_read_msg_id NOT IN (SELECT id FROM messages)
            """)

        except Exception as e:
            logger.debug(f"Orphan cleanup error (non-fatal): {e}")

    async def _check_backup(self, now: float):
        """Check and run database backup."""
        interval_hours = self.config.database.backup_interval_hours
        if interval_hours <= 0:
            return

        interval_secs = interval_hours * 3600

        if now - self._last_backup < interval_secs:
            return

        self._last_backup = now

        await self.run_backup()

    async def run_backup(self) -> Optional[str]:
        """
        Create database backup.

        Returns backup file path or None on failure.
        """
        if not self.bbs.db:
            return None

        backup_dir = Path(self.config.database.backup_path)
        db_path = Path(self.config.database.path)

        try:
            # Create backup directory if needed
            backup_dir.mkdir(parents=True, exist_ok=True)

            # Generate backup filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"advbbs_backup_{timestamp}.db"
            backup_path = backup_dir / backup_name

            # Use SQLite backup API for consistency
            source_conn = self.bbs.db._conn
            backup_conn = sqlite3.connect(str(backup_path))

            try:
                source_conn.backup(backup_conn)
                backup_conn.close()
            except AttributeError:
                # Fallback for older SQLite - simple file copy
                backup_conn.close()
                shutil.copy2(str(db_path), str(backup_path))

            # Record backup time
            now_us = int(time.time() * 1_000_000)
            self.bbs.db.execute(
                "INSERT OR REPLACE INTO bbs_settings (key, value) VALUES ('last_backup_us', ?)",
                (str(now_us),)
            )

            # Clean up old backups (keep last 7)
            await self._cleanup_old_backups(backup_dir, keep=7)

            logger.info(f"Database backup created: {backup_path}")
            return str(backup_path)

        except Exception as e:
            logger.error(f"Backup failed: {e}")
            return None

    async def _cleanup_old_backups(self, backup_dir: Path, keep: int = 7):
        """Remove old backup files, keeping the most recent ones."""
        try:
            backups = sorted(
                backup_dir.glob("advbbs_backup_*.db"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )

            for old_backup in backups[keep:]:
                old_backup.unlink()
                logger.debug(f"Removed old backup: {old_backup}")

        except Exception as e:
            logger.debug(f"Backup cleanup error: {e}")

    async def restore_backup(self, backup_path: str) -> bool:
        """
        Restore database from backup.

        WARNING: This will replace the current database!

        Args:
            backup_path: Path to backup file

        Returns True on success, False on failure.
        """
        backup_file = Path(backup_path)
        if not backup_file.exists():
            logger.error(f"Backup file not found: {backup_path}")
            return False

        db_path = Path(self.config.database.path)

        try:
            # Close current database connection
            if self.bbs.db:
                self.bbs.db.close()

            # Create safety backup of current database
            if db_path.exists():
                safety_backup = db_path.with_suffix(".db.pre_restore")
                shutil.copy2(str(db_path), str(safety_backup))
                logger.info(f"Created safety backup: {safety_backup}")

            # Restore from backup
            shutil.copy2(str(backup_file), str(db_path))

            # Reconnect to database
            from ..db.connection import Database
            self.bbs.db = Database(str(db_path))
            self.bbs.db.initialize()

            logger.info(f"Database restored from: {backup_path}")
            return True

        except Exception as e:
            logger.error(f"Restore failed: {e}")
            return False

    def list_backups(self) -> list[dict]:
        """
        List available backups.

        Returns list of dicts with:
        - path: Backup file path
        - timestamp: Creation timestamp
        - size_bytes: File size
        """
        backup_dir = Path(self.config.database.backup_path)
        result = []

        if not backup_dir.exists():
            return result

        for backup_file in backup_dir.glob("advbbs_backup_*.db"):
            try:
                stat = backup_file.stat()
                result.append({
                    "path": str(backup_file),
                    "filename": backup_file.name,
                    "timestamp": stat.st_mtime,
                    "size_bytes": stat.st_size,
                })
            except Exception:
                continue

        # Sort by timestamp, newest first
        result.sort(key=lambda x: x["timestamp"], reverse=True)
        return result

    def get_stats(self) -> dict:
        """
        Get comprehensive BBS statistics.

        Returns dict with counts and status info.
        """
        stats = {
            "users": 0,
            "messages": 0,
            "mail": 0,
            "bulletins": 0,
            "boards": 0,
            "peers": 0,
            "uptime_hours": 0,
            "uptime_seconds": 0,
        }

        if not self.bbs.db:
            return stats

        try:
            # User count
            row = self.bbs.db.fetchone("SELECT COUNT(*) FROM users")
            stats["users"] = row[0] if row else 0

            # Total messages
            row = self.bbs.db.fetchone(
                "SELECT COUNT(*) FROM messages WHERE deleted_at_us IS NULL"
            )
            stats["messages"] = row[0] if row else 0

            # Mail count
            row = self.bbs.db.fetchone(
                "SELECT COUNT(*) FROM messages WHERE msg_type = 'mail' AND deleted_at_us IS NULL"
            )
            stats["mail"] = row[0] if row else 0

            # Bulletin count
            row = self.bbs.db.fetchone(
                "SELECT COUNT(*) FROM messages WHERE msg_type = 'bulletin' AND deleted_at_us IS NULL"
            )
            stats["bulletins"] = row[0] if row else 0

            # Board count
            row = self.bbs.db.fetchone("SELECT COUNT(*) FROM boards")
            stats["boards"] = row[0] if row else 0

            # Peer count
            row = self.bbs.db.fetchone("SELECT COUNT(*) FROM bbs_peers")
            stats["peers"] = row[0] if row else 0

            # Uptime
            if self.bbs.start_time > 0:
                stats["uptime_seconds"] = time.time() - self.bbs.start_time
                stats["uptime_hours"] = stats["uptime_seconds"] / 3600

        except Exception as e:
            logger.debug(f"Error getting stats: {e}")

        return stats

    async def force_announcement(self, message: Optional[str] = None):
        """Force an immediate announcement."""
        await self.send_announcement(message)
        self._last_announcement = time.time()

    async def force_expiration(self) -> dict:
        """Force immediate message expiration."""
        result = await self.run_expiration()
        self._last_expiration = time.time()
        return result

    async def force_backup(self) -> Optional[str]:
        """Force immediate database backup."""
        result = await self.run_backup()
        self._last_backup = time.time()
        return result
