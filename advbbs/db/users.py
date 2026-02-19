"""
advBBS User Database Operations

CRUD operations for users, nodes, and user-node associations.
"""

import time
import logging
from typing import Optional

from .connection import Database
from .models import User, Node, UserNode

logger = logging.getLogger(__name__)


class UserRepository:
    """Repository for user-related database operations."""

    def __init__(self, db: Database):
        self.db = db

    def create_user(
        self,
        username: str,
        password_hash: bytes,
        salt: bytes,
        encryption_key: bytes,
        recovery_key_enc: Optional[bytes] = None,
        is_admin: bool = False
    ) -> User:
        """Create a new user."""
        now_us = int(time.time() * 1_000_000)

        cursor = self.db.execute("""
            INSERT INTO users (
                username, password_hash, salt, encryption_key,
                recovery_key_enc, created_at_us, is_admin
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            username.lower(),
            password_hash,
            salt,
            encryption_key,
            recovery_key_enc,
            now_us,
            1 if is_admin else 0
        ))

        return User(
            id=cursor.lastrowid,
            username=username.lower(),
            password_hash=password_hash,
            salt=salt,
            encryption_key=encryption_key,
            recovery_key_enc=recovery_key_enc,
            created_at_us=now_us,
            is_admin=is_admin
        )

    def get_user_by_id(self, user_id: int) -> Optional[User]:
        """Get user by ID."""
        row = self.db.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
        return self._row_to_user(row) if row else None

    def get_user_by_username(self, username: str) -> Optional[User]:
        """Get user by username (case-insensitive)."""
        row = self.db.fetchone(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (username.lower(),)
        )
        return self._row_to_user(row) if row else None

    def get_user_by_node(self, node_id: str) -> Optional[User]:
        """Get user associated with a node ID."""
        row = self.db.fetchone("""
            SELECT u.* FROM users u
            JOIN user_nodes un ON u.id = un.user_id
            JOIN nodes n ON un.node_id = n.id
            WHERE n.node_id = ?
        """, (node_id,))
        return self._row_to_user(row) if row else None

    def update_last_seen(self, user_id: int):
        """Update user's last seen timestamp."""
        now_us = int(time.time() * 1_000_000)
        self.db.execute(
            "UPDATE users SET last_seen_at_us = ? WHERE id = ?",
            (now_us, user_id)
        )

    def update_password(
        self,
        user_id: int,
        password_hash: bytes,
        salt: bytes,
        encryption_key: bytes
    ):
        """Update user's password and encryption key."""
        self.db.execute("""
            UPDATE users
            SET password_hash = ?, salt = ?, encryption_key = ?
            WHERE id = ?
        """, (password_hash, salt, encryption_key, user_id))

    def ban_user(
        self,
        username: str,
        reason: str,
        banned_by: str,
        ban_origin: str = "local"
    ) -> bool:
        """Ban a user."""
        now_us = int(time.time() * 1_000_000)
        cursor = self.db.execute("""
            UPDATE users
            SET is_banned = 1, banned_by = ?, ban_reason = ?,
                ban_origin = ?, banned_at_us = ?
            WHERE username = ? COLLATE NOCASE
        """, (banned_by, reason, ban_origin, now_us, username.lower()))
        return cursor.rowcount > 0

    def unban_user(self, username: str) -> bool:
        """Unban a user."""
        cursor = self.db.execute("""
            UPDATE users
            SET is_banned = 0, banned_by = NULL, ban_reason = NULL,
                ban_origin = NULL, banned_at_us = NULL
            WHERE username = ? COLLATE NOCASE
        """, (username.lower(),))
        return cursor.rowcount > 0

    def delete_user(self, user_id: int) -> bool:
        """Delete a user and all associated data."""
        cursor = self.db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return cursor.rowcount > 0

    def list_users(self, limit: int = 50, offset: int = 0) -> list[User]:
        """List users with pagination."""
        rows = self.db.fetchall(
            "SELECT * FROM users ORDER BY username LIMIT ? OFFSET ?",
            (limit, offset)
        )
        return [self._row_to_user(row) for row in rows]

    def _row_to_user(self, row) -> User:
        """Convert database row to User object."""
        return User(
            id=row["id"],
            username=row["username"],
            password_hash=row["password_hash"],
            salt=row["salt"],
            encryption_key=row["encryption_key"],
            recovery_key_enc=row["recovery_key_enc"],
            created_at_us=row["created_at_us"],
            last_seen_at_us=row["last_seen_at_us"],
            is_admin=bool(row["is_admin"]),
            is_banned=bool(row["is_banned"]),
            banned_by=row["banned_by"],
            ban_reason=row["ban_reason"],
            ban_origin=row["ban_origin"],
            banned_at_us=row["banned_at_us"]
        )


class NodeRepository:
    """Repository for node-related database operations."""

    def __init__(self, db: Database):
        self.db = db

    def get_or_create_node(
        self,
        node_id: str,
        short_name: Optional[str] = None,
        long_name: Optional[str] = None
    ) -> Node:
        """Get existing node or create new one."""
        now_us = int(time.time() * 1_000_000)

        # Try to get existing
        row = self.db.fetchone(
            "SELECT * FROM nodes WHERE node_id = ?",
            (node_id,)
        )

        if row:
            # Update last seen
            self.db.execute(
                "UPDATE nodes SET last_seen_us = ? WHERE id = ?",
                (now_us, row["id"])
            )
            return self._row_to_node(row)

        # Create new
        cursor = self.db.execute("""
            INSERT INTO nodes (node_id, short_name, long_name, first_seen_us, last_seen_us)
            VALUES (?, ?, ?, ?, ?)
        """, (node_id, short_name, long_name, now_us, now_us))

        return Node(
            id=cursor.lastrowid,
            node_id=node_id,
            short_name=short_name,
            long_name=long_name,
            first_seen_us=now_us,
            last_seen_us=now_us
        )

    def get_node_by_id(self, node_id: str) -> Optional[Node]:
        """Get node by Meshtastic node ID."""
        row = self.db.fetchone(
            "SELECT * FROM nodes WHERE node_id = ?",
            (node_id,)
        )
        return self._row_to_node(row) if row else None

    def update_node_info(
        self,
        node_id: str,
        short_name: Optional[str] = None,
        long_name: Optional[str] = None,
        snr: Optional[float] = None,
        rssi: Optional[int] = None
    ):
        """Update node information."""
        now_us = int(time.time() * 1_000_000)

        updates = ["last_seen_us = ?"]
        params = [now_us]

        if short_name is not None:
            updates.append("short_name = ?")
            params.append(short_name)

        if long_name is not None:
            updates.append("long_name = ?")
            params.append(long_name)

        if snr is not None:
            updates.append("last_snr = ?")
            params.append(snr)

        if rssi is not None:
            updates.append("last_rssi = ?")
            params.append(rssi)

        params.append(node_id)

        self.db.execute(
            f"UPDATE nodes SET {', '.join(updates)} WHERE node_id = ?",
            tuple(params)
        )

    def _row_to_node(self, row) -> Node:
        """Convert database row to Node object."""
        return Node(
            id=row["id"],
            node_id=row["node_id"],
            short_name=row["short_name"],
            long_name=row["long_name"],
            first_seen_us=row["first_seen_us"],
            last_seen_us=row["last_seen_us"],
            last_snr=row["last_snr"],
            last_rssi=row["last_rssi"]
        )


class UserNodeRepository:
    """Repository for user-node association operations."""

    def __init__(self, db: Database):
        self.db = db

    def associate_node(self, user_id: int, node_db_id: int) -> UserNode:
        """Associate a node with a user."""
        now_us = int(time.time() * 1_000_000)

        self.db.execute("""
            INSERT OR REPLACE INTO user_nodes (user_id, node_id, registered_at_us)
            VALUES (?, ?, ?)
        """, (user_id, node_db_id, now_us))

        return UserNode(
            user_id=user_id,
            node_id=node_db_id,
            registered_at_us=now_us,
        )

    def remove_node(self, user_id: int, node_id: str) -> bool:
        """Remove a node association from a user."""
        cursor = self.db.execute("""
            DELETE FROM user_nodes
            WHERE user_id = ? AND node_id = (
                SELECT id FROM nodes WHERE node_id = ?
            )
        """, (user_id, node_id))
        return cursor.rowcount > 0

    def get_user_nodes(self, user_id: int) -> list[str]:
        """Get all node IDs associated with a user."""
        rows = self.db.fetchall("""
            SELECT n.node_id FROM nodes n
            JOIN user_nodes un ON n.id = un.node_id
            WHERE un.user_id = ?
            ORDER BY un.registered_at_us
        """, (user_id,))
        return [row["node_id"] for row in rows]

    def is_node_associated(self, user_id: int, node_db_id: int) -> bool:
        """Check if a node is associated with a user."""
        row = self.db.fetchone("""
            SELECT 1 FROM user_nodes
            WHERE user_id = ? AND node_id = ?
        """, (user_id, node_db_id))
        return row is not None
