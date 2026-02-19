"""
advBBS Data Models

Dataclasses representing database entities.
"""

from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class MessageType(Enum):
    """Message type enumeration."""
    MAIL = "mail"
    BULLETIN = "bulletin"
    SYSTEM = "system"


class BoardType(Enum):
    """Board type enumeration."""
    PUBLIC = "public"
    RESTRICTED = "restricted"


class SyncDirection(Enum):
    """Sync direction enumeration."""
    SENT = "sent"
    RECEIVED = "received"


class SyncStatus(Enum):
    """Sync status enumeration."""
    PENDING = "pending"
    ACKED = "acked"
    FAILED = "failed"


@dataclass
class User:
    """Registered BBS user."""
    id: Optional[int] = None
    username: str = ""
    password_hash: bytes = b""
    salt: bytes = b""
    encryption_key: bytes = b""
    recovery_key_enc: Optional[bytes] = None
    created_at_us: int = 0
    last_seen_at_us: Optional[int] = None
    is_admin: bool = False
    is_banned: bool = False
    banned_by: Optional[str] = None
    ban_reason: Optional[str] = None
    ban_origin: Optional[str] = None
    banned_at_us: Optional[int] = None


@dataclass
class Node:
    """Known Meshtastic node."""
    id: Optional[int] = None
    node_id: str = ""  # Meshtastic node ID (!abcdef12)
    short_name: Optional[str] = None
    long_name: Optional[str] = None
    first_seen_us: int = 0
    last_seen_us: int = 0
    last_snr: Optional[float] = None
    last_rssi: Optional[int] = None


@dataclass
class UserNode:
    """Association between user and Meshtastic node."""
    id: Optional[int] = None
    user_id: int = 0
    node_id: int = 0
    registered_at_us: int = 0
    is_primary: bool = False


@dataclass
class Message:
    """BBS message (mail or bulletin)."""
    id: Optional[int] = None
    uuid: str = ""
    msg_type: MessageType = MessageType.MAIL
    board_id: Optional[int] = None
    sender_user_id: Optional[int] = None
    sender_node_id: int = 0
    recipient_user_id: Optional[int] = None
    recipient_node_id: Optional[int] = None
    subject_enc: Optional[bytes] = None
    body_enc: bytes = b""
    created_at_us: int = 0
    delivered_at_us: Optional[int] = None
    read_at_us: Optional[int] = None
    expires_at_us: Optional[int] = None
    origin_bbs: Optional[str] = None
    delivery_attempts: int = 0
    last_attempt_us: Optional[int] = None
    forwarded_to: Optional[str] = None
    hop_count: int = 0


@dataclass
class Board:
    """Bulletin board."""
    id: Optional[int] = None
    name: str = ""
    description: Optional[str] = None
    created_at_us: int = 0
    is_restricted: bool = False
    board_type: BoardType = BoardType.PUBLIC
    board_key_enc: Optional[bytes] = None
    sync_enabled: bool = False


@dataclass
class BoardAccess:
    """Per-user board access for restricted boards."""
    id: Optional[int] = None
    board_id: int = 0
    user_id: int = 0
    board_key_enc: bytes = b""
    granted_at_us: int = 0
    granted_by: Optional[int] = None


@dataclass
class BoardState:
    """Per-user reading position in a board."""
    id: Optional[int] = None
    user_id: int = 0
    board_id: int = 0
    last_read_us: int = 0


@dataclass
class BBSPeer:
    """Known peer BBS node for inter-BBS sync."""
    id: Optional[int] = None
    node_id: str = ""
    bbs_name: Optional[str] = None
    protocol: str = "advbbs"
    last_sync_us: Optional[int] = None
    sync_enabled: bool = True
    trust_level: int = 0  # 0=untrusted, 1=trusted, 2=full


@dataclass
class SyncLog:
    """Track message sync status for inter-BBS protocol."""
    id: Optional[int] = None
    message_uuid: str = ""
    peer_id: int = 0
    direction: SyncDirection = SyncDirection.SENT
    status: SyncStatus = SyncStatus.PENDING
    attempts: int = 0
    last_attempt_us: Optional[int] = None


@dataclass
class PendingChunk:
    """Pending message chunk for reassembly."""
    sender: str = ""
    total: int = 0
    chunks: list[str] = field(default_factory=list)
    received: set[int] = field(default_factory=set)
    created_us: int = 0
    last_chunk_us: int = 0
