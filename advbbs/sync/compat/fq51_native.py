"""
advBBS Native Sync Protocol

DM-based sync for advBBS-to-advBBS communication.
Designed for efficient, secure mesh sync between advBBS instances.

Protocol format: FQ51|<version>|<msg_type>|<payload>

Message types:
- HELLO: Handshake with capabilities
- SYNC_REQ: Request messages since timestamp
- SYNC_MSG: Send message data (JSON)
- SYNC_ACK: Acknowledge receipt
- SYNC_DONE: Signal sync complete
- DELETE: Delete message by UUID
"""

import base64
import json
import logging
import time
from dataclasses import dataclass, asdict
from typing import Optional, List, TYPE_CHECKING

if TYPE_CHECKING:
    from ..manager import SyncManager

logger = logging.getLogger(__name__)


@dataclass
class FQ51SyncMessage:
    """Message format for FQ51 sync."""
    uuid: str
    msg_type: str  # "bulletin" or "mail"
    board: Optional[str] = None
    sender: Optional[str] = None
    recipient: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    timestamp_us: int = 0
    origin_bbs: Optional[str] = None


class FQ51NativeSync:
    """
    Native advBBS-to-advBBS sync via DM.

    Protocol format: FQ51|<version>|<msg_type>|<payload>

    Features:
    - JSON message encoding for structured data
    - Base64 encoding for binary-safe transport
    - UUID-based deduplication
    - Acknowledgment-based reliable delivery
    - Timestamp-based incremental sync
    """

    VERSION = "1"

    # Message types
    MSG_HELLO = "HELLO"
    MSG_SYNC_REQ = "SYNC_REQ"
    MSG_SYNC_MSG = "SYNC_MSG"
    MSG_SYNC_ACK = "SYNC_ACK"
    MSG_SYNC_DONE = "SYNC_DONE"
    MSG_DELETE = "DELETE"

    VALID_TYPES = {MSG_HELLO, MSG_SYNC_REQ, MSG_SYNC_MSG, MSG_SYNC_ACK, MSG_SYNC_DONE, MSG_DELETE}

    def __init__(self, sync_manager: "SyncManager"):
        """
        Initialize FQ51 native sync.

        Args:
            sync_manager: Parent sync manager instance
        """
        self.sync_manager = sync_manager
        self.config = sync_manager.config
        self.db = sync_manager.db
        self.mesh = sync_manager.mesh
        self.bbs = getattr(sync_manager, 'bbs', None)

        self.my_name = getattr(self.config, 'bbs_name', 'advBBS')
        self.my_callsign = getattr(self.config, 'callsign', 'FQ51')

        # Track pending ACKs: uuid -> (peer_id, timestamp)
        self._pending_acks: dict[str, tuple[str, float]] = {}

        # Track sync state per peer
        self._sync_state: dict[str, dict] = {}

    async def sync_with_peer(self, peer_id: str, since_us: int = 0):
        """
        Initiate sync with advBBS peer.

        Args:
            peer_id: Peer node ID
            since_us: Timestamp to sync from (microseconds)
        """
        # Initialize sync state
        self._sync_state[peer_id] = {
            "state": "handshake",
            "since_us": since_us,
            "sent_count": 0,
            "acked_count": 0,
        }

        # Send handshake - only mail sync supported
        capabilities = "mail"
        hello = self._format_message(self.MSG_HELLO, f"{self.my_callsign}:{self.my_name}|{capabilities}")
        await self._send_dm(peer_id, hello)

        logger.info(f"Initiated FQ51 sync with {peer_id}")

    async def send_sync_message(self, msg: FQ51SyncMessage, peer_id: str):
        """
        Send message using SYNC_MSG format.

        Payload is JSON encoded then base64 for safety.
        """
        try:
            # Convert to dict and encode
            msg_dict = asdict(msg)
            json_str = json.dumps(msg_dict, separators=(',', ':'))
            encoded = base64.b64encode(json_str.encode()).decode()

            sync_msg = self._format_message(self.MSG_SYNC_MSG, encoded)

            if self.mesh:
                await self.mesh.send_dm(sync_msg, peer_id)
                logger.debug(f"Sent FQ51 SYNC_MSG to {peer_id}: {msg.uuid[:8]}")

        except Exception as e:
            logger.error(f"Failed to send FQ51 sync message: {e}")

    async def send_sync_ack(self, uuid: str, peer_id: str):
        """Send acknowledgment for received message."""
        ack = self._format_message(self.MSG_SYNC_ACK, uuid)
        if self.mesh:
            await self.mesh.send_dm(ack, peer_id)
            logger.debug(f"Sent FQ51 SYNC_ACK to {peer_id}: {uuid[:8]}")

    async def send_sync_done(self, peer_id: str):
        """Signal sync completion."""
        count = self._sync_state.get(peer_id, {}).get("sent_count", 0)
        done = self._format_message(self.MSG_SYNC_DONE, str(count))
        if self.mesh:
            await self.mesh.send_dm(done, peer_id)
            logger.debug(f"Sent FQ51 SYNC_DONE to {peer_id}: {count} messages")

    async def send_delete(self, uuid: str, peer_id: str):
        """Send delete request for message."""
        delete = self._format_message(self.MSG_DELETE, uuid)
        if self.mesh:
            await self.mesh.send_dm(delete, peer_id)
            logger.debug(f"Sent FQ51 DELETE to {peer_id}: {uuid[:8]}")

    def handle_message(self, raw: str, sender: str) -> bool:
        """
        Handle incoming FQ51 protocol message.

        Returns True if handled, False otherwise.
        """
        if not raw.startswith("FQ51|"):
            return False

        parts = raw.split("|", 3)
        if len(parts) < 3:
            return False

        version = parts[1]
        msg_type = parts[2]
        payload = parts[3] if len(parts) > 3 else ""

        if msg_type not in self.VALID_TYPES:
            return False

        logger.debug(f"Received FQ51 {msg_type} from {sender}")

        handlers = {
            self.MSG_HELLO: self._handle_hello,
            self.MSG_SYNC_REQ: self._handle_sync_request,
            self.MSG_SYNC_MSG: self._handle_sync_message,
            self.MSG_SYNC_ACK: self._handle_sync_ack,
            self.MSG_SYNC_DONE: self._handle_sync_done,
            self.MSG_DELETE: self._handle_delete,
        }

        handler = handlers.get(msg_type)
        if handler:
            try:
                handler(payload, sender)
            except Exception as e:
                logger.error(f"Error handling FQ51 {msg_type}: {e}")

        return True

    def _handle_hello(self, payload: str, sender: str):
        """Handle HELLO handshake."""
        parts = payload.split("|")
        peer_info = parts[0] if parts else "Unknown"
        capabilities = parts[1].split(",") if len(parts) > 1 else []

        # Parse peer info (callsign:name format)
        if ":" in peer_info:
            peer_callsign, peer_name = peer_info.split(":", 1)
        else:
            peer_callsign = peer_info
            peer_name = peer_info

        logger.info(f"FQ51 handshake from {peer_name} ({peer_callsign}) [{sender}]: {capabilities}")

        # Update peer in database
        self._register_peer(sender, peer_callsign, peer_name, capabilities)

        # Respond with our hello - only mail sync supported
        import asyncio
        response = self._format_message(
            self.MSG_HELLO,
            f"{self.my_callsign}:{self.my_name}|mail"
        )
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self._send_dm(sender, response))
            else:
                loop.run_until_complete(self._send_dm(sender, response))
        except Exception as e:
            logger.error(f"Failed to respond to HELLO: {e}")

    def _handle_sync_request(self, payload: str, sender: str):
        """Handle sync request - mail sync only, no bulletin sync."""
        parts = payload.split("|")
        try:
            since_us = int(parts[0]) if parts else 0
        except ValueError:
            since_us = 0

        capabilities = parts[1].split(",") if len(parts) > 1 else ["mail"]

        logger.info(f"FQ51 sync request from {sender}: since={since_us}, types={capabilities}")
        # Bulletin sync removed - mail is handled via MAILREQ/MAILDAT protocol

    def _handle_sync_message(self, payload: str, sender: str):
        """Handle incoming sync message."""
        try:
            # Decode base64 -> JSON -> dict
            json_str = base64.b64decode(payload).decode()
            msg_dict = json.loads(json_str)

            msg = FQ51SyncMessage(**msg_dict)

            logger.debug(f"Received FQ51 sync message from {sender}: {msg.uuid[:8]}")

            # Store the message
            self._store_sync_message(msg, sender)

            # Send ACK
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.create_task(self.send_sync_ack(msg.uuid, sender))
                else:
                    loop.run_until_complete(self.send_sync_ack(msg.uuid, sender))
            except Exception as e:
                logger.error(f"Failed to send sync ACK: {e}")

        except Exception as e:
            logger.error(f"Failed to parse FQ51 sync message: {e}")

    def _handle_sync_ack(self, payload: str, sender: str):
        """Handle sync acknowledgment."""
        uuid = payload.strip()

        logger.debug(f"Received FQ51 SYNC_ACK from {sender}: {uuid[:8] if uuid else 'empty'}")

        # Mark sync as complete
        if uuid in self._pending_acks:
            del self._pending_acks[uuid]
            self._log_sync(uuid, sender, "sent", status="acked")
            logger.info(f"Message {uuid[:8]} acknowledged by {sender}")

        # Update sync state
        if sender in self._sync_state:
            self._sync_state[sender]["acked_count"] = self._sync_state[sender].get("acked_count", 0) + 1

    def _handle_sync_done(self, payload: str, sender: str):
        """Handle sync completion."""
        try:
            count = int(payload) if payload else 0
        except ValueError:
            count = 0

        logger.info(f"FQ51 sync complete from {sender}: {count} messages")

        # Update peer sync timestamp
        now_us = int(time.time() * 1_000_000)
        self._update_peer_sync_time(sender, now_us)

        # Clean up sync state
        if sender in self._sync_state:
            del self._sync_state[sender]

    def _handle_delete(self, payload: str, sender: str):
        """Handle delete message request."""
        uuid = payload.strip()
        if not uuid:
            return

        from ...db.messages import MessageRepository

        msg_repo = MessageRepository(self.db)
        message = msg_repo.get_message_by_uuid(uuid)

        # Only delete if message originated from requesting peer's BBS
        if message and message.origin_bbs == sender:
            msg_repo.delete_message(message.id)
            logger.info(f"Deleted message by FQ51 request from {sender}: {uuid[:8]}")
        else:
            logger.warning(f"Rejected delete request from {sender} for {uuid[:8]}: not origin BBS")

    def _store_sync_message(self, msg: FQ51SyncMessage, sender: str):
        """Store received sync message - mail only, bulletin sync disabled."""
        from ...db.messages import MessageRepository
        from ...db.users import NodeRepository

        # Bulletin sync is disabled - ignore bulletin messages
        if msg.msg_type == "bulletin":
            logger.debug(f"Ignoring bulletin sync message from {sender}: {msg.uuid[:8]}")
            return

        msg_repo = MessageRepository(self.db)
        node_repo = NodeRepository(self.db)

        # Check for duplicate
        if msg_repo.message_exists(msg.uuid):
            logger.debug(f"Duplicate FQ51 message ignored: {msg.uuid[:8]}")
            return

        # Get sender node
        sender_node = node_repo.get_or_create_node(sender)

        if msg.msg_type == "mail":
            # Handle mail sync
            from ...db.users import UserRepository
            user_repo = UserRepository(self.db)

            recipient = user_repo.get_user_by_username(msg.recipient) if msg.recipient else None
            if not recipient:
                logger.warning(f"FQ51 mail recipient not found: {msg.recipient}")
                return

            # Encrypt for recipient
            body_enc = self._encrypt_for_recipient(msg.body or "", recipient)
            subject_enc = self._encrypt_for_recipient(msg.subject, recipient) if msg.subject else None

            if body_enc is None:
                logger.error(f"Failed to encrypt FQ51 mail for {msg.recipient}")
                return

            from ...db.models import MessageType
            msg_repo.create_message(
                msg_type=MessageType.MAIL,
                sender_node_id=sender_node.id,
                recipient_user_id=recipient.id,
                body_enc=body_enc,
                subject_enc=subject_enc,
                origin_bbs=msg.origin_bbs or sender,
                message_uuid=msg.uuid
            )

            self._log_sync(msg.uuid, sender, "received")
            logger.info(f"Stored FQ51 mail from {sender} to {msg.recipient}: {msg.uuid[:8]}")

    def _format_message(self, msg_type: str, payload: str) -> str:
        """Format a protocol message."""
        return f"FQ51|{self.VERSION}|{msg_type}|{payload}"

    async def _send_dm(self, peer_id: str, message: str):
        """Send direct message to peer."""
        if self.mesh:
            await self.mesh.send_dm(message, peer_id)

    def is_fq51_message(self, raw: str) -> bool:
        """Check if message is FQ51 format."""
        if not raw.startswith("FQ51|"):
            return False
        parts = raw.split("|", 3)
        if len(parts) < 3:
            return False
        return parts[2] in self.VALID_TYPES

    # Helper methods

    def _already_synced(self, uuid: str, peer_id: str) -> bool:
        """Check if message already synced to peer."""
        row = self.db.fetchone("""
            SELECT 1 FROM sync_log
            WHERE message_uuid = ? AND peer_id = (
                SELECT id FROM bbs_peers WHERE node_id = ?
            ) AND direction = 'sent' AND status = 'acked'
        """, (uuid, peer_id))
        return row is not None

    def _log_sync(self, uuid: str, peer_node_id: str, direction: str, status: str = "acked"):
        """Log sync operation."""
        now_us = int(time.time() * 1_000_000)

        # Get or create peer
        peer_row = self.db.fetchone(
            "SELECT id FROM bbs_peers WHERE node_id = ?",
            (peer_node_id,)
        )

        if not peer_row:
            cursor = self.db.execute(
                "INSERT INTO bbs_peers (node_id, protocol, last_sync_us) VALUES (?, 'fq51', ?)",
                (peer_node_id, now_us)
            )
            peer_id = cursor.lastrowid
        else:
            peer_id = peer_row[0]
            self.db.execute(
                "UPDATE bbs_peers SET last_sync_us = ? WHERE id = ?",
                (now_us, peer_id)
            )

        # Log sync
        self.db.execute("""
            INSERT OR REPLACE INTO sync_log
            (message_uuid, peer_id, direction, status, attempts, last_attempt_us)
            VALUES (?, ?, ?, ?, 1, ?)
        """, (uuid, peer_id, direction, status, now_us))

    def _register_peer(self, node_id: str, callsign: str, name: str, capabilities: List[str]):
        """Register or update peer information."""
        now_us = int(time.time() * 1_000_000)
        caps_str = ",".join(capabilities)

        peer_row = self.db.fetchone(
            "SELECT id FROM bbs_peers WHERE node_id = ?",
            (node_id,)
        )

        if not peer_row:
            self.db.execute("""
                INSERT INTO bbs_peers (node_id, callsign, name, protocol, capabilities, last_seen_us)
                VALUES (?, ?, ?, 'fq51', ?, ?)
            """, (node_id, callsign, name, caps_str, now_us))
        else:
            self.db.execute("""
                UPDATE bbs_peers
                SET callsign = ?, name = ?, capabilities = ?, last_seen_us = ?
                WHERE node_id = ?
            """, (callsign, name, caps_str, now_us, node_id))

    def _update_peer_sync_time(self, node_id: str, sync_us: int):
        """Update peer's last sync timestamp."""
        self.db.execute(
            "UPDATE bbs_peers SET last_sync_us = ? WHERE node_id = ?",
            (sync_us, node_id)
        )


    def _encrypt_for_recipient(self, plaintext: str, recipient) -> Optional[bytes]:
        """Encrypt content for specific recipient."""
        try:
            if not self.bbs:
                return plaintext.encode()
            master_key = self.bbs.master_key
            crypto = self.bbs.crypto

            # Decrypt recipient's key
            recipient_key = master_key.decrypt_user_key(recipient.encryption_key)
            return crypto.encrypt_string(plaintext, recipient_key)
        except Exception as e:
            logger.error(f"Encrypt for recipient error: {e}")
            return None

    async def _rate_limit_delay(self):
        """Apply rate limiting delay."""
        import asyncio
        await asyncio.sleep(3)  # 1 message per 3 seconds
