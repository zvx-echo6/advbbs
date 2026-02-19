"""
advBBS Message Database Operations

CRUD operations for messages (mail and bulletins).
"""

import time
import traceback
import uuid
import logging
from typing import Optional

from .connection import Database
from .models import Message, MessageType

logger = logging.getLogger(__name__)


class MessageRepository:
    """Repository for message-related database operations."""

    def __init__(self, db: Database):
        self.db = db

    def create_message(
        self,
        msg_type: MessageType,
        sender_node_id: int,
        body_enc: bytes,
        sender_user_id: Optional[int] = None,
        recipient_user_id: Optional[int] = None,
        recipient_node_id: Optional[int] = None,
        board_id: Optional[int] = None,
        subject_enc: Optional[bytes] = None,
        origin_bbs: Optional[str] = None,
        message_uuid: Optional[str] = None,
        expires_at_us: Optional[int] = None,
        forwarded_to: Optional[str] = None
    ) -> Message:
        """Create a new message."""
        now_us = int(time.time() * 1_000_000)
        msg_uuid = message_uuid or str(uuid.uuid4())

        cursor = self.db.execute("""
            INSERT INTO messages (
                uuid, msg_type, board_id, sender_user_id, sender_node_id,
                recipient_user_id, recipient_node_id, subject_enc, body_enc,
                created_at_us, origin_bbs, expires_at_us, forwarded_to
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            msg_uuid,
            msg_type.value,
            board_id,
            sender_user_id,
            sender_node_id,
            recipient_user_id,
            recipient_node_id,
            subject_enc,
            body_enc,
            now_us,
            origin_bbs,
            expires_at_us,
            forwarded_to
        ))

        return Message(
            id=cursor.lastrowid,
            uuid=msg_uuid,
            msg_type=msg_type,
            board_id=board_id,
            sender_user_id=sender_user_id,
            sender_node_id=sender_node_id,
            recipient_user_id=recipient_user_id,
            recipient_node_id=recipient_node_id,
            subject_enc=subject_enc,
            body_enc=body_enc,
            created_at_us=now_us,
            origin_bbs=origin_bbs,
            expires_at_us=expires_at_us,
            forwarded_to=forwarded_to
        )

    def get_message_by_id(self, message_id: int) -> Optional[Message]:
        """Get message by ID."""
        row = self.db.fetchone("SELECT * FROM messages WHERE id = ?", (message_id,))
        return self._row_to_message(row) if row else None

    def get_message_by_uuid(self, uuid: str) -> Optional[Message]:
        """Get message by UUID."""
        row = self.db.fetchone("SELECT * FROM messages WHERE uuid = ?", (uuid,))
        return self._row_to_message(row) if row else None

    def get_user_mail(
        self,
        user_id: int,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0
    ) -> list[Message]:
        """Get mail messages for a user."""
        sql = """
            SELECT * FROM messages
            WHERE recipient_user_id = ? AND msg_type = 'mail'
        """
        params = [user_id]

        if unread_only:
            sql += " AND read_at_us IS NULL"

        sql += " ORDER BY created_at_us DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self.db.fetchall(sql, tuple(params))
        return [self._row_to_message(row) for row in rows]

    def get_mail_for_node(
        self,
        node_id: int,
        unread_only: bool = False,
        limit: int = 50
    ) -> list[Message]:
        """Get mail messages addressed to a specific node."""
        sql = """
            SELECT * FROM messages
            WHERE recipient_node_id = ? AND msg_type = 'mail'
        """
        params = [node_id]

        if unread_only:
            sql += " AND read_at_us IS NULL"

        sql += " ORDER BY created_at_us DESC LIMIT ?"
        params.append(limit)

        rows = self.db.fetchall(sql, tuple(params))
        return [self._row_to_message(row) for row in rows]

    def count_unread_mail(self, user_id: int) -> int:
        """Count unread mail for a user."""
        row = self.db.fetchone("""
            SELECT COUNT(*) FROM messages
            WHERE recipient_user_id = ?
            AND msg_type = 'mail'
            AND read_at_us IS NULL
        """, (user_id,))
        return row[0] if row else 0

    def mark_as_read(self, message_id: int):
        """Mark a message as read."""
        now_us = int(time.time() * 1_000_000)
        self.db.execute(
            "UPDATE messages SET read_at_us = ? WHERE id = ?",
            (now_us, message_id)
        )

    def mark_as_delivered(self, message_id: int):
        """Mark a message as delivered."""
        now_us = int(time.time() * 1_000_000)
        self.db.execute(
            "UPDATE messages SET delivered_at_us = ? WHERE id = ?",
            (now_us, message_id)
        )

    def update_delivery_attempt(self, message_id: int, forwarded_to: Optional[str] = None):
        """Update delivery attempt tracking."""
        now_us = int(time.time() * 1_000_000)

        if forwarded_to:
            self.db.execute("""
                UPDATE messages
                SET delivery_attempts = delivery_attempts + 1,
                    last_attempt_us = ?,
                    forwarded_to = ?,
                    hop_count = hop_count + 1
                WHERE id = ?
            """, (now_us, forwarded_to, message_id))
        else:
            self.db.execute("""
                UPDATE messages
                SET delivery_attempts = delivery_attempts + 1,
                    last_attempt_us = ?
                WHERE id = ?
            """, (now_us, message_id))

    def delete_message(self, message_id: int) -> bool:
        """Delete a message."""
        cursor = self.db.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        return cursor.rowcount > 0

    def delete_user_messages(self, user_id: int) -> int:
        """Delete all messages for a user (sent and received)."""
        cursor = self.db.execute("""
            DELETE FROM messages
            WHERE sender_user_id = ? OR recipient_user_id = ?
        """, (user_id, user_id))
        return cursor.rowcount

    def get_board_messages(
        self,
        board_id: int,
        limit: int = 50,
        offset: int = 0,
        since_us: Optional[int] = None,
        ascending: bool = True
    ) -> list[Message]:
        """
        Get messages for a bulletin board.

        Args:
            board_id: Board ID
            limit: Max messages to return
            offset: Number of messages to skip
            since_us: Only messages after this timestamp
            ascending: If True, oldest first (for proper #1, #2 numbering)
        """
        sql = """
            SELECT * FROM messages
            WHERE board_id = ? AND msg_type = 'bulletin'
        """
        params = [board_id]

        if since_us is not None:
            sql += " AND created_at_us > ?"
            params.append(since_us)

        order = "ASC" if ascending else "DESC"
        sql += f" ORDER BY created_at_us {order} LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self.db.fetchall(sql, tuple(params))
        return [self._row_to_message(row) for row in rows]

    def count_board_messages(self, board_id: int) -> int:
        """Count messages on a board."""
        row = self.db.fetchone(
            "SELECT COUNT(*) FROM messages WHERE board_id = ? AND msg_type = 'bulletin'",
            (board_id,)
        )
        return row[0] if row else 0

    def get_pending_deliveries(self, limit: int = 10) -> list[Message]:
        """Get messages pending delivery (excludes remote mail which is already delivered)."""
        rows = self.db.fetchall("""
            SELECT * FROM messages
            WHERE msg_type = 'mail'
            AND delivered_at_us IS NULL
            AND delivery_attempts < 3
            AND hop_count < 3
            AND sender_node_id IS NOT NULL
            ORDER BY created_at_us
            LIMIT ?
        """, (limit,))
        return [self._row_to_message(row) for row in rows]

    def get_messages_since(
        self,
        since_us: int,
        msg_types: Optional[list[str]] = None
    ) -> list[Message]:
        """Get messages created since timestamp (for sync)."""
        sql = "SELECT * FROM messages WHERE created_at_us > ?"
        params = [since_us]

        if msg_types:
            placeholders = ",".join("?" for _ in msg_types)
            sql += f" AND msg_type IN ({placeholders})"
            params.extend(msg_types)

        sql += " ORDER BY created_at_us"

        rows = self.db.fetchall(sql, tuple(params))
        return [self._row_to_message(row) for row in rows]

    def delete_expired_messages(self) -> int:
        """Delete expired messages."""
        now_us = int(time.time() * 1_000_000)
        cursor = self.db.execute(
            "DELETE FROM messages WHERE expires_at_us IS NOT NULL AND expires_at_us < ?",
            (now_us,)
        )
        deleted = cursor.rowcount
        if deleted > 0:
            logger.info(f"Deleted {deleted} expired messages")
        return deleted

    def message_exists(self, uuid: str) -> bool:
        """Check if a message with UUID exists (for deduplication)."""
        row = self.db.fetchone(
            "SELECT 1 FROM messages WHERE uuid = ?",
            (uuid,)
        )
        return row is not None

    def _row_to_message(self, row) -> Message:
        """Convert database row to Message object."""
        return Message(
            id=row["id"],
            uuid=row["uuid"],
            msg_type=MessageType(row["msg_type"]),
            board_id=row["board_id"],
            sender_user_id=row["sender_user_id"],
            sender_node_id=row["sender_node_id"],
            recipient_user_id=row["recipient_user_id"],
            recipient_node_id=row["recipient_node_id"],
            subject_enc=row["subject_enc"],
            body_enc=row["body_enc"],
            created_at_us=row["created_at_us"],
            delivered_at_us=row["delivered_at_us"],
            read_at_us=row["read_at_us"],
            expires_at_us=row["expires_at_us"],
            origin_bbs=row["origin_bbs"],
            delivery_attempts=row["delivery_attempts"],
            last_attempt_us=row["last_attempt_us"],
            forwarded_to=row["forwarded_to"],
            hop_count=row["hop_count"]
        )

    # === Remote Mail Methods ===

    def create_remote_mail(
        self,
        sender_username: str,
        sender_bbs: str,
        sender_node_id: int,
        recipient_username: str,
        recipient_bbs: str,
        body: str,
        origin_bbs: str
    ) -> Optional[Message]:
        """
        Create an outgoing remote mail message (queued for delivery).

        Stores the message with remote addressing info for sync to send.
        Body is stored in plaintext since it will be encrypted by receiving BBS.
        """
        now_us = int(time.time() * 1_000_000)
        msg_uuid = str(uuid.uuid4())

        # Store remote addressing in forwarded_to field as JSON-like format
        remote_addr = f"{sender_username}@{sender_bbs}>{recipient_username}@{recipient_bbs}"

        try:
            cursor = self.db.execute("""
                INSERT INTO messages (
                    uuid, msg_type, sender_node_id, body_enc, created_at_us,
                    origin_bbs, forwarded_to, hop_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                msg_uuid,
                "mail",  # Use 'mail' type - remote addressing is in forwarded_to
                sender_node_id,
                body.encode('utf-8'),  # Store plaintext for remote
                now_us,
                origin_bbs,
                remote_addr,
                0
            ))

            logger.info(f"Created remote mail {msg_uuid[:8]}: {sender_username}@{sender_bbs} -> {recipient_username}@{recipient_bbs}")

            return Message(
                id=cursor.lastrowid,
                uuid=msg_uuid,
                msg_type=MessageType.MAIL,
                body_enc=body.encode('utf-8'),
                created_at_us=now_us,
                origin_bbs=origin_bbs,
                forwarded_to=remote_addr,
                hop_count=0
            )

        except Exception as e:
            logger.error(f"Failed to create remote mail: {e}")
            return None

    def create_incoming_remote_mail(
        self,
        uuid: str,
        from_user: str,
        from_bbs: str,
        to_user_id: int,
        body: str
    ) -> Optional[Message]:
        """
        Create a mail message received from a remote BBS.

        This mail is stored locally for the recipient to read.
        Body is stored in plaintext (encryption happens at read time if needed).
        """
        now_us = int(time.time() * 1_000_000)

        # Check for duplicate
        if self.message_exists(uuid):
            logger.info(f"Remote mail {uuid[:8]} already exists (duplicate), skipping")
            return "duplicate"

        try:
            # Store sender info in a way read_mail can parse
            sender_info = f"{from_user}@{from_bbs}"

            # Remote mail has no local sender node - use NULL
            cursor = self.db.execute("""
                INSERT INTO messages (
                    uuid, msg_type, recipient_user_id, body_enc,
                    created_at_us, origin_bbs, forwarded_to
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                uuid,
                "mail",
                to_user_id,
                body.encode('utf-8'),
                now_us,
                from_bbs,
                sender_info  # Store sender info for display
            ))

            logger.info(f"Stored incoming remote mail {uuid[:8]} from {from_user}@{from_bbs}")

            return Message(
                id=cursor.lastrowid,
                uuid=uuid,
                msg_type=MessageType.MAIL,
                recipient_user_id=to_user_id,
                body_enc=body.encode('utf-8'),
                created_at_us=now_us,
                origin_bbs=from_bbs,
                forwarded_to=sender_info
            )

        except Exception as e:
            logger.error(f"Failed to store incoming remote mail: {e}")
            logger.error(traceback.format_exc())
            return None

    def get_pending_remote_mail(self, limit: int = 10) -> list[Message]:
        """Get remote mail waiting to be sent."""
        rows = self.db.fetchall("""
            SELECT * FROM messages
            WHERE msg_type = 'remote_mail'
            AND delivered_at_us IS NULL
            AND delivery_attempts < 3
            ORDER BY created_at_us
            LIMIT ?
        """, (limit,))
        return [self._row_to_message(row) for row in rows]

    def mark_remote_mail_delivered(self, mail_uuid: str, dest: str):
        """Mark outgoing remote mail as successfully delivered."""
        now_us = int(time.time() * 1_000_000)
        self.db.execute("""
            UPDATE messages
            SET delivered_at_us = ?, forwarded_to = ?
            WHERE uuid = ?
        """, (now_us, f"DELIVERED:{dest}", mail_uuid))
        logger.info(f"Marked remote mail {mail_uuid[:8]} as delivered to {dest}")

    def mark_remote_mail_failed(self, mail_uuid: str, reason: str):
        """Mark outgoing remote mail as failed."""
        now_us = int(time.time() * 1_000_000)
        self.db.execute("""
            UPDATE messages
            SET delivery_attempts = 99, forwarded_to = ?
            WHERE uuid = ?
        """, (f"FAILED:{reason}", mail_uuid))
        logger.info(f"Marked remote mail {mail_uuid[:8]} as failed: {reason}")

    def is_remote_mail(self, message_id: int) -> bool:
        """Check if a message is remote mail (stored as plaintext)."""
        row = self.db.fetchone("""
            SELECT origin_bbs, forwarded_to FROM messages WHERE id = ?
        """, (message_id,))
        if not row:
            return False
        origin_bbs, forwarded_to = row
        # Remote mail has origin_bbs set and forwarded_to contains sender info (user@bbs format)
        return origin_bbs is not None and forwarded_to is not None and "@" in (forwarded_to or "")

    def get_sent_remote_mail(self, user_id: int, limit: int = 10) -> list[dict]:
        """
        Get outgoing remote mail sent by user with delivery status.

        Returns list of dicts with: id, uuid, to, status, date
        """
        # Get user's username first
        from .users import UserRepository
        user_repo = UserRepository(self.db)
        user = user_repo.get_user_by_id(user_id)
        if not user:
            return []

        # Find mail where forwarded_to starts with "username@" (outgoing remote)
        # and origin_bbs matches our BBS (we sent it)
        rows = self.db.fetchall("""
            SELECT id, uuid, forwarded_to, delivered_at_us, delivery_attempts, created_at_us
            FROM messages
            WHERE msg_type = 'mail'
            AND forwarded_to LIKE ?
            ORDER BY created_at_us DESC
            LIMIT ?
        """, (f"{user.username}@%>%", limit))

        result = []
        for row in rows:
            forwarded_to = row["forwarded_to"] or ""

            # Parse forwarded_to format: "sender@senderBBS>recipient@recipientBBS"
            if ">" in forwarded_to:
                _, dest = forwarded_to.split(">", 1)
            else:
                dest = forwarded_to

            # Determine status
            if row["delivered_at_us"]:
                if "DELIVERED:" in forwarded_to:
                    status = "delivered"
                else:
                    status = "delivered"
            elif "FAILED:" in forwarded_to:
                status = "failed"
            elif row["delivery_attempts"] >= 3:
                status = "failed"
            else:
                status = "pending"

            # Format date
            created_time = time.strftime(
                "%m/%d %H:%M",
                time.localtime(row["created_at_us"] / 1_000_000)
            )

            result.append({
                "id": row["id"],
                "uuid": row["uuid"][:8],
                "to": dest,
                "status": status,
                "date": created_time
            })

        return result
