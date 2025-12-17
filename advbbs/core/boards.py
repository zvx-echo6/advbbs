"""
advBBS Bulletin Board System

Handles board management, posting, reading, and board-level encryption.
"""

import time
import logging
from dataclasses import dataclass
from typing import Optional, TYPE_CHECKING

from ..db.models import Message, MessageType, Board, BoardType, BoardAccess, BoardState
from ..db.messages import MessageRepository
from ..db.users import UserRepository
from .crypto import CryptoManager, MasterKeyManager

if TYPE_CHECKING:
    from .bbs import advBBS

logger = logging.getLogger(__name__)


# Board configuration
DEFAULT_POST_LIMIT = 5
MAX_SUBJECT_LENGTH = 64
MAX_BODY_LENGTH = 2000
BULLETIN_EXPIRY_DAYS = 90

# Sync boards - these are the only boards that sync between BBS nodes
# and can be read without login
SYNC_BOARDS = ["general", "help"]


@dataclass
class PostSummary:
    """Summary of a bulletin post for listing."""
    id: int
    number: int  # Sequential number on board
    subject: str
    author: str
    date: str
    replies: int = 0


@dataclass
class Post:
    """Full bulletin post with decrypted content."""
    id: int
    number: int
    subject: str
    body: str
    author: str
    date: str
    board: str


class BoardRepository:
    """Repository for board-related database operations."""

    def __init__(self, db):
        self.db = db

    def get_all_boards(self) -> list[Board]:
        """Get all boards."""
        rows = self.db.fetchall(
            "SELECT * FROM boards ORDER BY name"
        )
        return [self._row_to_board(row) for row in rows]

    def get_board_by_name(self, name: str) -> Optional[Board]:
        """Get board by name (case-insensitive)."""
        row = self.db.fetchone(
            "SELECT * FROM boards WHERE name = ? COLLATE NOCASE",
            (name.lower(),)
        )
        return self._row_to_board(row) if row else None

    def get_board_by_id(self, board_id: int) -> Optional[Board]:
        """Get board by ID."""
        row = self.db.fetchone(
            "SELECT * FROM boards WHERE id = ?",
            (board_id,)
        )
        return self._row_to_board(row) if row else None

    def create_board(
        self,
        name: str,
        description: Optional[str] = None,
        is_restricted: bool = False,
        board_key_enc: Optional[bytes] = None
    ) -> Board:
        """Create a new board."""
        now_us = int(time.time() * 1_000_000)
        board_type = BoardType.RESTRICTED if is_restricted else BoardType.PUBLIC

        cursor = self.db.execute("""
            INSERT INTO boards (name, description, created_at_us, is_restricted, board_type, board_key_enc)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            name.lower(),
            description,
            now_us,
            1 if is_restricted else 0,
            board_type.value,
            board_key_enc
        ))

        return Board(
            id=cursor.lastrowid,
            name=name.lower(),
            description=description,
            created_at_us=now_us,
            is_restricted=is_restricted,
            board_type=board_type,
            board_key_enc=board_key_enc
        )

    def delete_board(self, board_id: int) -> bool:
        """Delete a board and all its posts."""
        # Delete posts first
        self.db.execute(
            "DELETE FROM messages WHERE board_id = ?",
            (board_id,)
        )
        # Delete board
        cursor = self.db.execute(
            "DELETE FROM boards WHERE id = ?",
            (board_id,)
        )
        return cursor.rowcount > 0

    def get_board_access(self, board_id: int, user_id: int) -> Optional[BoardAccess]:
        """Get user's access to a restricted board."""
        row = self.db.fetchone("""
            SELECT * FROM board_access
            WHERE board_id = ? AND user_id = ?
        """, (board_id, user_id))

        if not row:
            return None

        return BoardAccess(
            id=row["id"],
            board_id=row["board_id"],
            user_id=row["user_id"],
            board_key_enc=row["board_key_enc"],
            granted_at_us=row["granted_at_us"],
            granted_by=row["granted_by"]
        )

    def grant_board_access(
        self,
        board_id: int,
        user_id: int,
        board_key_enc: bytes,
        granted_by: int
    ) -> BoardAccess:
        """Grant a user access to a restricted board."""
        now_us = int(time.time() * 1_000_000)

        cursor = self.db.execute("""
            INSERT OR REPLACE INTO board_access
            (board_id, user_id, board_key_enc, granted_at_us, granted_by)
            VALUES (?, ?, ?, ?, ?)
        """, (board_id, user_id, board_key_enc, now_us, granted_by))

        return BoardAccess(
            id=cursor.lastrowid,
            board_id=board_id,
            user_id=user_id,
            board_key_enc=board_key_enc,
            granted_at_us=now_us,
            granted_by=granted_by
        )

    def revoke_board_access(self, board_id: int, user_id: int) -> bool:
        """Revoke a user's access to a restricted board."""
        cursor = self.db.execute("""
            DELETE FROM board_access
            WHERE board_id = ? AND user_id = ?
        """, (board_id, user_id))
        return cursor.rowcount > 0

    def get_board_state(self, board_id: int, user_id: int) -> Optional[BoardState]:
        """Get user's reading position on a board."""
        row = self.db.fetchone("""
            SELECT * FROM board_states
            WHERE board_id = ? AND user_id = ?
        """, (board_id, user_id))

        if not row:
            return None

        return BoardState(
            id=row["id"],
            user_id=row["user_id"],
            board_id=row["board_id"],
            last_read_us=row["last_read_us"]
        )

    def update_board_state(self, board_id: int, user_id: int, last_read_us: int):
        """Update user's reading position on a board."""
        self.db.execute("""
            INSERT OR REPLACE INTO board_states (board_id, user_id, last_read_us)
            VALUES (?, ?, ?)
        """, (board_id, user_id, last_read_us))

    def _row_to_board(self, row) -> Board:
        """Convert database row to Board object."""
        return Board(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            created_at_us=row["created_at_us"],
            is_restricted=bool(row["is_restricted"]),
            board_type=BoardType(row["board_type"]),
            board_key_enc=row["board_key_enc"]
        )


class BoardService:
    """
    Bulletin board service for advBBS.

    Features:
    - Public and restricted boards
    - Encrypted posts on restricted boards
    - Per-user reading position tracking
    - Post expiration
    """

    def __init__(self, bbs: "advBBS"):
        self.bbs = bbs
        self.crypto = bbs.crypto
        self.master_key = bbs.master_key
        self.board_repo = BoardRepository(bbs.db)

    def list_boards(self, user_id: Optional[int] = None) -> list[dict]:
        """
        List available boards.

        Returns list of board summaries with unread counts if user_id provided.
        """
        boards = self.board_repo.get_all_boards()
        msg_repo = MessageRepository(self.bbs.db)

        result = []
        for board in boards:
            # Check access for restricted boards
            if board.is_restricted and user_id:
                access = self.board_repo.get_board_access(board.id, user_id)
                if not access:
                    continue  # Don't show boards user can't access

            post_count = msg_repo.count_board_messages(board.id)

            # Get unread count if user provided
            unread = 0
            if user_id:
                state = self.board_repo.get_board_state(board.id, user_id)
                last_read = state.last_read_us if state else 0
                unread_posts = msg_repo.get_board_messages(board.id, limit=1000, since_us=last_read)
                unread = len(unread_posts)

            result.append({
                "name": board.name,
                "description": board.description or "",
                "posts": post_count,
                "unread": unread,
                "restricted": board.is_restricted
            })

        return result

    def enter_board(self, board_name: str, user_id: Optional[int] = None) -> tuple[Optional[Board], str]:
        """
        Enter a board.

        Returns:
            (Board, "") on success
            (None, error_message) on failure
        """
        board = self.board_repo.get_board_by_name(board_name)
        if not board:
            return None, f"Board '{board_name}' not found."

        # Check access for restricted boards
        if board.is_restricted:
            if not user_id:
                return None, "Please login to access restricted boards."

            access = self.board_repo.get_board_access(board.id, user_id)
            if not access:
                return None, "You don't have access to this board."

        return board, ""

    def count_posts(self, board_id: int) -> int:
        """Get total post count for a board."""
        msg_repo = MessageRepository(self.bbs.db)
        return msg_repo.count_board_messages(board_id)

    def list_posts(
        self,
        board_id: int,
        user_id: Optional[int] = None,
        limit: int = DEFAULT_POST_LIMIT,
        offset: int = 0
    ) -> list[PostSummary]:
        """
        List posts on a board (headers only).

        Returns list of post summaries.
        """
        msg_repo = MessageRepository(self.bbs.db)
        user_repo = UserRepository(self.bbs.db)
        board = self.board_repo.get_board_by_id(board_id)

        messages = msg_repo.get_board_messages(board_id, limit, offset)

        result = []
        for idx, msg in enumerate(messages):
            # Get author info
            author = user_repo.get_user_by_id(msg.sender_user_id) if msg.sender_user_id else None
            author_name = author.username if author else "anonymous"

            # Try to decrypt subject for display
            subject = self._decrypt_field(msg.subject_enc, board, user_id) if msg.subject_enc else "(no subject)"
            if subject is None:
                subject = "[encrypted]"

            # Format timestamp
            post_date = time.strftime(
                "%m/%d",
                time.localtime(msg.created_at_us / 1_000_000)
            )

            result.append(PostSummary(
                id=msg.id,
                number=offset + idx + 1,
                subject=subject[:40] + "..." if len(subject) > 40 else subject,
                author=author_name,
                date=post_date
            ))

        return result

    def read_post(
        self,
        board_id: int,
        post_number: int,
        user_id: Optional[int] = None
    ) -> tuple[Optional[Post], str]:
        """
        Read a specific post by number.

        Posts are numbered starting at 1 (oldest post).

        Returns:
            (Post, "") on success
            (None, error_message) on failure
        """
        msg_repo = MessageRepository(self.bbs.db)
        user_repo = UserRepository(self.bbs.db)
        board = self.board_repo.get_board_by_id(board_id)

        if not board:
            return None, "Board not found."

        # Get total count to validate post_number
        total = msg_repo.count_board_messages(board_id)

        if post_number < 1 or post_number > total:
            return None, f"Post #{post_number} not found."

        # Get the specific post using offset (ascending order, oldest=1)
        messages = msg_repo.get_board_messages(board_id, limit=1, offset=post_number - 1, ascending=True)
        if not messages:
            return None, f"Post #{post_number} not found."

        msg = messages[0]

        # Get author info
        author = user_repo.get_user_by_id(msg.sender_user_id) if msg.sender_user_id else None
        author_name = author.username if author else "anonymous"

        # Decrypt content
        subject = self._decrypt_field(msg.subject_enc, board, user_id) if msg.subject_enc else "(no subject)"
        body = self._decrypt_field(msg.body_enc, board, user_id)

        if body is None:
            return None, "Failed to decrypt post."

        # Format timestamp
        post_date = time.strftime(
            "%Y-%m-%d %H:%M",
            time.localtime(msg.created_at_us / 1_000_000)
        )

        # Update reading position
        if user_id:
            self.board_repo.update_board_state(board_id, user_id, msg.created_at_us)

        return Post(
            id=msg.id,
            number=post_number,
            subject=subject,
            body=body,
            author=author_name,
            date=post_date,
            board=board.name
        ), ""

    def create_post(
        self,
        board_id: int,
        user_id: int,
        sender_node_id: str,
        subject: str,
        body: str
    ) -> tuple[Optional[Message], str]:
        """
        Create a new post on a board.

        Returns:
            (Message, "") on success
            (None, error_message) on failure
        """
        board = self.board_repo.get_board_by_id(board_id)
        if not board:
            return None, "Board not found."

        # Validate input
        if len(subject) > MAX_SUBJECT_LENGTH:
            return None, f"Subject too long (max {MAX_SUBJECT_LENGTH} chars)."

        if len(body) > MAX_BODY_LENGTH:
            return None, f"Body too long (max {MAX_BODY_LENGTH} chars)."

        if not body.strip():
            return None, "Body cannot be empty."

        # Check access for restricted boards
        if board.is_restricted:
            access = self.board_repo.get_board_access(board_id, user_id)
            if not access:
                return None, "You don't have access to post on this board."

        try:
            # Get encryption key for board
            board_key = self._get_board_key(board, user_id)

            # Encrypt subject and body
            subject_enc = self.crypto.encrypt_string(subject, board_key) if subject else None
            body_enc = self.crypto.encrypt_string(body, board_key)

            # Calculate expiration
            expires_at_us = int((time.time() + BULLETIN_EXPIRY_DAYS * 86400) * 1_000_000)

            # Get sender node DB ID
            from ..db.users import NodeRepository
            node_repo = NodeRepository(self.bbs.db)
            sender_node = node_repo.get_or_create_node(sender_node_id)

            # Create message
            msg_repo = MessageRepository(self.bbs.db)
            message = msg_repo.create_message(
                msg_type=MessageType.BULLETIN,
                sender_node_id=sender_node.id,
                sender_user_id=user_id,
                board_id=board_id,
                subject_enc=subject_enc,
                body_enc=body_enc,
                origin_bbs=self.bbs.config.bbs.callsign,
                expires_at_us=expires_at_us
            )

            logger.info(f"Post created on {board.name} by user {user_id}")
            return message, ""

        except Exception as e:
            logger.error(f"Post creation error: {e}")
            return None, "Failed to create post."

    def delete_post(
        self,
        board_id: int,
        post_number: int,
        user_id: int,
        is_admin: bool = False
    ) -> tuple[bool, str]:
        """
        Delete a post.

        Returns:
            (True, "") on success
            (False, error_message) on failure
        """
        msg_repo = MessageRepository(self.bbs.db)

        # Get posts to find the one at post_number
        messages = msg_repo.get_board_messages(board_id, limit=1000)

        if post_number < 1 or post_number > len(messages):
            return False, f"Post #{post_number} not found."

        messages = list(reversed(messages))
        msg = messages[post_number - 1]

        # Check permission
        if not is_admin and msg.sender_user_id != user_id:
            return False, "You can only delete your own posts."

        if msg_repo.delete_message(msg.id):
            return True, ""
        return False, "Failed to delete post."

    def _get_board_key(self, board: Board, user_id: Optional[int] = None) -> bytes:
        """Get the encryption key for a board."""
        if board.is_restricted:
            if not user_id:
                raise ValueError("User ID required for restricted board")

            # Get user's encrypted board key
            access = self.board_repo.get_board_access(board.id, user_id)
            if not access:
                raise ValueError("User doesn't have access to this board")

            # Decrypt board key with user's key
            user_repo = UserRepository(self.bbs.db)
            user = user_repo.get_user_by_id(user_id)
            user_key = self.master_key.decrypt_user_key(user.encryption_key)

            from .crypto import EncryptedData
            encrypted = EncryptedData.from_bytes(access.board_key_enc)
            return self.crypto.decrypt(encrypted, user_key)
        else:
            # Public boards use the master key (all users can read)
            return self.master_key.key

    def _decrypt_field(
        self,
        encrypted_data: bytes,
        board: Board,
        user_id: Optional[int]
    ) -> Optional[str]:
        """Decrypt a field from a board post."""
        try:
            board_key = self._get_board_key(board, user_id)
            return self.crypto.decrypt_string(encrypted_data, board_key)
        except Exception as e:
            logger.debug(f"Decryption failed: {e}")
            return None

    def create_board(
        self,
        name: str,
        description: Optional[str] = None,
        is_restricted: bool = False,
        creator_id: Optional[int] = None
    ) -> tuple[Optional[Board], str]:
        """
        Create a new board (admin only).

        Returns:
            (Board, "") on success
            (None, error_message) on failure
        """
        # Validate name
        if len(name) < 2 or len(name) > 16:
            return None, "Board name must be 2-16 characters."

        if not name.replace("_", "").replace("-", "").isalnum():
            return None, "Board name can only contain letters, numbers, underscores, and hyphens."

        # Check if exists
        if self.board_repo.get_board_by_name(name):
            return None, f"Board '{name}' already exists."

        try:
            board_key_enc = None
            if is_restricted:
                # Generate a unique key for this board
                import secrets
                board_key = secrets.token_bytes(32)
                # Encrypt with master key for storage
                board_key_enc = self.crypto.encrypt(board_key, self.master_key.key).to_bytes()

                # If creator provided, grant them access
                if creator_id:
                    user_repo = UserRepository(self.bbs.db)
                    user = user_repo.get_user_by_id(creator_id)
                    user_key = self.master_key.decrypt_user_key(user.encryption_key)
                    user_board_key_enc = self.crypto.encrypt(board_key, user_key).to_bytes()

            board = self.board_repo.create_board(
                name=name,
                description=description,
                is_restricted=is_restricted,
                board_key_enc=board_key_enc
            )

            # Grant creator access if restricted
            if is_restricted and creator_id:
                self.board_repo.grant_board_access(
                    board.id,
                    creator_id,
                    user_board_key_enc,
                    creator_id
                )

            return board, ""

        except Exception as e:
            logger.error(f"Board creation error: {e}")
            return None, "Failed to create board."

    def delete_board(self, name: str) -> tuple[bool, str]:
        """
        Delete a board by name (admin only).

        Returns:
            (True, "") on success
            (False, error_message) on failure
        """
        board = self.board_repo.get_board_by_name(name)
        if not board:
            return False, f"Board '{name}' not found."

        try:
            self.board_repo.delete_board(board.id)
            return True, ""
        except Exception as e:
            logger.error(f"Board deletion error: {e}")
            return False, "Failed to delete board."

    def grant_access(
        self,
        board_id: int,
        target_user_id: int,
        granter_user_id: int
    ) -> tuple[bool, str]:
        """
        Grant a user access to a restricted board.

        Returns:
            (True, "") on success
            (False, error_message) on failure
        """
        board = self.board_repo.get_board_by_id(board_id)
        if not board:
            return False, "Board not found."

        if not board.is_restricted:
            return False, "Board is not restricted."

        # Verify granter has access
        granter_access = self.board_repo.get_board_access(board_id, granter_user_id)
        if not granter_access:
            return False, "You don't have access to this board."

        try:
            # Get the board key from granter's access
            user_repo = UserRepository(self.bbs.db)
            granter = user_repo.get_user_by_id(granter_user_id)
            granter_key = self.master_key.decrypt_user_key(granter.encryption_key)

            from .crypto import EncryptedData
            encrypted = EncryptedData.from_bytes(granter_access.board_key_enc)
            board_key = self.crypto.decrypt(encrypted, granter_key)

            # Encrypt board key for target user
            target = user_repo.get_user_by_id(target_user_id)
            if not target:
                return False, "Target user not found."

            target_key = self.master_key.decrypt_user_key(target.encryption_key)
            target_board_key_enc = self.crypto.encrypt(board_key, target_key).to_bytes()

            # Grant access
            self.board_repo.grant_board_access(
                board_id,
                target_user_id,
                target_board_key_enc,
                granter_user_id
            )

            return True, ""

        except Exception as e:
            logger.error(f"Grant access error: {e}")
            return False, "Failed to grant access."
