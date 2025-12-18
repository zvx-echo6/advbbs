"""
advBBS Native Sync Protocol 

DM-based sync for advBBS inter-BBS communication.
Designed for efficient, secure mesh sync between advBBS instances.

Protocol format: advBBS|<version>|<msg_type>|<payload>

Message types:
- HELLO: Handshake with capabilities
- SYNC_REQ: Request messages since timestamp
- SYNC_MSG: Send message data (JSON)
- SYNC_ACK: Acknowledge receipt
- SYNC_DONE: Signal sync complete
- DELETE: Delete message by UUID

RAP (Route Announcement Protocol) types:
- RAP_PING: Lightweight heartbeat probe
- RAP_PONG: Heartbeat response with reachable routes
- RAP_ROUTES: Route advertisement (full route table share)
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
class AdvBBSSyncMessage:
    """Message format for advBBS sync."""
    uuid: str
    msg_type: str  # "bulletin" or "mail"
    board: Optional[str] = None
    sender: Optional[str] = None
    recipient: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    timestamp_us: int = 0
    origin_bbs: Optional[str] = None


class AdvBBSNativeSync:
    """
    Native advBBS sync via DM (advBBS protocol).

    Protocol format: advBBS|<version>|<msg_type>|<payload>

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
    # RAP (Route Announcement Protocol) message types
    MSG_RAP_PING = "RAP_PING"
    MSG_RAP_PONG = "RAP_PONG"
    MSG_RAP_ROUTES = "RAP_ROUTES"

    VALID_TYPES = {
        MSG_HELLO, MSG_SYNC_REQ, MSG_SYNC_MSG, MSG_SYNC_ACK, MSG_SYNC_DONE, MSG_DELETE,
        MSG_RAP_PING, MSG_RAP_PONG, MSG_RAP_ROUTES,
    }

    def __init__(self, sync_manager: "SyncManager"):
        """
        Initialize advBBS native sync.

        Args:
            sync_manager: Parent sync manager instance
        """
        self.sync_manager = sync_manager
        self.config = sync_manager.config
        self.db = sync_manager.db
        self.mesh = sync_manager.mesh
        self.bbs = getattr(sync_manager, 'bbs', None)

        # Get BBS name and callsign from the full config hierarchy
        if self.bbs and hasattr(self.bbs, 'config'):
            self.my_name = self.bbs.config.bbs.name
            self.my_callsign = self.bbs.config.bbs.callsign
        else:
            self.my_name = getattr(self.config, 'bbs_name', 'advBBS')
            self.my_callsign = getattr(self.config, 'callsign', 'ADV')

        # Track pending ACKs: uuid -> (peer_id, timestamp)
        self._pending_acks: dict[str, tuple[str, float]] = {}

        # Track sync state per peer
        self._sync_state: dict[str, dict] = {}

    async def sync_with_peer(self, peer_id: str, since_us: int = 0):
        """
        Initiate sync with peer BBS.

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

        logger.info(f"Initiated advBBS sync with {peer_id}")

    async def send_sync_message(self, msg: AdvBBSSyncMessage, peer_id: str):
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
                logger.debug(f"Sent advBBS SYNC_MSG to {peer_id}: {msg.uuid[:8]}")

        except Exception as e:
            logger.error(f"Failed to send advBBS sync message: {e}")

    async def send_sync_ack(self, uuid: str, peer_id: str):
        """Send acknowledgment for received message."""
        ack = self._format_message(self.MSG_SYNC_ACK, uuid)
        if self.mesh:
            await self.mesh.send_dm(ack, peer_id)
            logger.debug(f"Sent advBBS SYNC_ACK to {peer_id}: {uuid[:8]}")

    async def send_sync_done(self, peer_id: str):
        """Signal sync completion."""
        count = self._sync_state.get(peer_id, {}).get("sent_count", 0)
        done = self._format_message(self.MSG_SYNC_DONE, str(count))
        if self.mesh:
            await self.mesh.send_dm(done, peer_id)
            logger.debug(f"Sent advBBS SYNC_DONE to {peer_id}: {count} messages")

    async def send_delete(self, uuid: str, peer_id: str):
        """Send delete request for message."""
        delete = self._format_message(self.MSG_DELETE, uuid)
        if self.mesh:
            await self.mesh.send_dm(delete, peer_id)
            logger.debug(f"Sent advBBS DELETE to {peer_id}: {uuid[:8]}")

    def handle_message(self, raw: str, sender: str) -> bool:
        """
        Handle incoming advBBS protocol message.

        Returns True if handled, False otherwise.
        """
        if not raw.startswith("advBBS|"):
            return False

        parts = raw.split("|", 3)
        if len(parts) < 3:
            return False

        version = parts[1]
        msg_type = parts[2]
        payload = parts[3] if len(parts) > 3 else ""

        if msg_type not in self.VALID_TYPES:
            return False

        logger.debug(f"Received advBBS {msg_type} from {sender}")

        handlers = {
            self.MSG_HELLO: self._handle_hello,
            self.MSG_SYNC_REQ: self._handle_sync_request,
            self.MSG_SYNC_MSG: self._handle_sync_message,
            self.MSG_SYNC_ACK: self._handle_sync_ack,
            self.MSG_SYNC_DONE: self._handle_sync_done,
            self.MSG_DELETE: self._handle_delete,
            # RAP handlers
            self.MSG_RAP_PING: self._handle_rap_ping,
            self.MSG_RAP_PONG: self._handle_rap_pong,
            self.MSG_RAP_ROUTES: self._handle_rap_routes,
        }

        handler = handlers.get(msg_type)
        if handler:
            try:
                handler(payload, sender)
            except Exception as e:
                logger.error(f"Error handling advBBS {msg_type}: {e}")

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

        logger.info(f"advBBS handshake from {peer_name} ({peer_callsign}) [{sender}]: {capabilities}")

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

        logger.info(f"advBBS sync request from {sender}: since={since_us}, types={capabilities}")
        # Bulletin sync removed - mail is handled via MAILREQ/MAILDAT protocol

    def _handle_sync_message(self, payload: str, sender: str):
        """Handle incoming sync message."""
        try:
            # Decode base64 -> JSON -> dict
            json_str = base64.b64decode(payload).decode()
            msg_dict = json.loads(json_str)

            msg = AdvBBSSyncMessage(**msg_dict)

            logger.debug(f"Received advBBS sync message from {sender}: {msg.uuid[:8]}")

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
            logger.error(f"Failed to parse advBBS sync message: {e}")

    def _handle_sync_ack(self, payload: str, sender: str):
        """Handle sync acknowledgment."""
        uuid = payload.strip()

        logger.debug(f"Received advBBS SYNC_ACK from {sender}: {uuid[:8] if uuid else 'empty'}")

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

        logger.info(f"advBBS sync complete from {sender}: {count} messages")

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
            logger.info(f"Deleted message by advBBS request from {sender}: {uuid[:8]}")
        else:
            logger.warning(f"Rejected delete request from {sender} for {uuid[:8]}: not origin BBS")

    def _store_sync_message(self, msg: AdvBBSSyncMessage, sender: str):
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
            logger.debug(f"Duplicate advBBS message ignored: {msg.uuid[:8]}")
            return

        # Get sender node
        sender_node = node_repo.get_or_create_node(sender)

        if msg.msg_type == "mail":
            # Handle mail sync
            from ...db.users import UserRepository
            user_repo = UserRepository(self.db)

            recipient = user_repo.get_user_by_username(msg.recipient) if msg.recipient else None
            if not recipient:
                logger.warning(f"advBBS mail recipient not found: {msg.recipient}")
                return

            # Encrypt for recipient
            body_enc = self._encrypt_for_recipient(msg.body or "", recipient)
            subject_enc = self._encrypt_for_recipient(msg.subject, recipient) if msg.subject else None

            if body_enc is None:
                logger.error(f"Failed to encrypt advBBS mail for {msg.recipient}")
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
            logger.info(f"Stored advBBS mail from {sender} to {msg.recipient}: {msg.uuid[:8]}")

    def _format_message(self, msg_type: str, payload: str) -> str:
        """Format a protocol message."""
        return f"advBBS|{self.VERSION}|{msg_type}|{payload}"

    async def _send_dm(self, peer_id: str, message: str):
        """Send direct message to peer."""
        if self.mesh:
            await self.mesh.send_dm(message, peer_id)

    def is_advbbs_message(self, raw: str) -> bool:
        """Check if message is advBBS format."""
        if not raw.startswith("advBBS|"):
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
                "INSERT INTO bbs_peers (node_id, protocol, last_sync_us) VALUES (?, 'advbbs', ?)",
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
                VALUES (?, ?, ?, 'advbbs', ?, ?)
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

    # === RAP (Route Announcement Protocol) Methods ===

    async def send_rap_ping(self, peer_id: str):
        """
        Send RAP_PING heartbeat to peer.

        Format: advBBS|1|RAP_PING|timestamp_us
        """
        timestamp_us = int(time.time() * 1_000_000)
        ping = self._format_message(self.MSG_RAP_PING, str(timestamp_us))
        if self.mesh:
            await self.mesh.send_dm(ping, peer_id)
            logger.debug(f"Sent RAP_PING to {peer_id}")

    async def send_rap_pong(self, peer_id: str, ping_timestamp_us: int):
        """
        Send RAP_PONG response to peer.

        Format: advBBS|1|RAP_PONG|ping_ts|route1;route2;...
        Each route: bbs_name:hop_count:quality_score
        """
        routes = self._get_route_table_string()
        payload = f"{ping_timestamp_us}|{routes}"
        pong = self._format_message(self.MSG_RAP_PONG, payload)
        if self.mesh:
            await self.mesh.send_dm(pong, peer_id)
            logger.debug(f"Sent RAP_PONG to {peer_id} with {len(routes.split(';')) if routes else 0} routes")

    async def send_rap_routes(self, peer_id: str):
        """
        Send full route table advertisement to peer.

        Format: advBBS|1|RAP_ROUTES|route1;route2;...
        """
        routes = self._get_route_table_string()
        msg = self._format_message(self.MSG_RAP_ROUTES, routes)
        if self.mesh:
            await self.mesh.send_dm(msg, peer_id)
            logger.debug(f"Sent RAP_ROUTES to {peer_id}")

    def _handle_rap_ping(self, payload: str, sender: str):
        """
        Handle RAP_PING heartbeat.

        Updates peer health status and sends RAP_PONG response.
        """
        try:
            ping_timestamp_us = int(payload) if payload else 0
        except ValueError:
            ping_timestamp_us = 0

        logger.debug(f"Received RAP_PING from {sender}")

        # Update peer health - any message from peer means they're alive
        self._update_peer_health(sender, "alive")

        # Schedule PONG response on the BBS event loop
        # (handlers are called from Meshtastic callback thread)
        self.sync_manager._schedule_async(self.send_rap_pong(sender, ping_timestamp_us))

    def _handle_rap_pong(self, payload: str, sender: str):
        """
        Handle RAP_PONG heartbeat response.

        Updates peer health status, calculates RTT, and processes route table.
        """
        parts = payload.split("|", 1)
        try:
            ping_timestamp_us = int(parts[0]) if parts else 0
        except ValueError:
            ping_timestamp_us = 0

        routes_str = parts[1] if len(parts) > 1 else ""

        # Calculate RTT if we have the ping timestamp
        now_us = int(time.time() * 1_000_000)
        rtt_ms = (now_us - ping_timestamp_us) / 1000 if ping_timestamp_us else 0

        logger.info(f"Received RAP_PONG from {sender} (RTT: {rtt_ms:.1f}ms)")

        # Update peer health - PONG received means peer is alive
        self._update_peer_health(sender, "alive", pong_received=True)

        # Process routes advertised by peer
        if routes_str:
            self._process_peer_routes(sender, routes_str)

    def _handle_rap_routes(self, payload: str, sender: str):
        """
        Handle RAP_ROUTES route advertisement.

        Updates route table with routes from peer.
        """
        logger.debug(f"Received RAP_ROUTES from {sender}")

        # Update peer health - any message means alive
        self._update_peer_health(sender, "alive")

        # Process routes
        if payload:
            self._process_peer_routes(sender, payload)

    def _get_route_table_string(self) -> str:
        """
        Get route table as string for RAP messages.

        Format: bbs1:hop:quality;bbs2:hop:quality;...
        Includes self as hop 0 and direct peers as hop 1.
        """
        routes = []

        # Add self as hop 0
        routes.append(f"{self.my_callsign}:0:1.0")

        # Add direct peers as hop 1
        # Use callsign if set, otherwise fall back to name
        peer_rows = self.db.fetchall("""
            SELECT COALESCE(callsign, name), quality_score
            FROM bbs_peers
            WHERE (callsign IS NOT NULL OR name IS NOT NULL)
              AND health_status IN ('unknown', 'alive')
              AND sync_enabled = 1
        """)

        for peer in peer_rows:
            callsign = peer[0]
            quality = peer[1] if peer[1] is not None else 1.0
            routes.append(f"{callsign}:1:{quality:.2f}")

        # Add learned routes (hop >= 2)
        now_us = int(time.time() * 1_000_000)
        route_rows = self.db.fetchall("""
            SELECT r.dest_bbs, r.hop_count, r.quality_score
            FROM rap_routes r
            WHERE r.expires_at_us > ?
            ORDER BY r.hop_count ASC
        """, (now_us,))

        for route in route_rows:
            dest_bbs = route[0]
            hop_count = route[1]
            quality = route[2] if route[2] is not None else 1.0
            # Don't include if already in direct peers
            if not any(r.startswith(f"{dest_bbs}:") for r in routes):
                routes.append(f"{dest_bbs}:{hop_count}:{quality:.2f}")

        return ";".join(routes)

    def _process_peer_routes(self, sender: str, routes_str: str):
        """
        Process routes received from peer.

        Updates rap_routes table with new/better routes.
        """
        if not routes_str:
            return

        now_us = int(time.time() * 1_000_000)

        # Get peer ID
        peer_row = self.db.fetchone(
            "SELECT id FROM bbs_peers WHERE node_id = ?",
            (sender,)
        )
        if not peer_row:
            logger.warning(f"Unknown peer {sender} in route processing")
            return

        peer_id = peer_row[0]

        # Get route expiry from config (default 1 hour)
        expiry_seconds = 3600
        if hasattr(self.sync_manager, 'sync_config'):
            expiry_seconds = getattr(self.sync_manager.sync_config, 'rap_route_expiry_seconds', 3600)
        expires_at_us = now_us + (expiry_seconds * 1_000_000)

        routes = routes_str.split(";")
        for route in routes:
            if not route:
                continue

            parts = route.split(":")
            if len(parts) < 2:
                continue

            dest_bbs = parts[0]
            try:
                hop_count = int(parts[1]) + 1  # Add 1 for the hop through this peer
            except ValueError:
                continue

            try:
                quality = float(parts[2]) if len(parts) > 2 else 1.0
                quality = min(1.0, max(0.0, quality))  # Clamp to 0-1
            except ValueError:
                quality = 1.0

            # Skip if it's our own BBS
            if dest_bbs.upper() == self.my_callsign.upper():
                continue

            # Skip if hop count too high (max 5)
            max_hops = 5
            if hasattr(self.sync_manager, 'sync_config'):
                max_hops = getattr(self.sync_manager.sync_config, 'mail_max_hops', 5)
            if hop_count > max_hops:
                continue

            # Insert or update route
            self.db.execute("""
                INSERT INTO rap_routes (dest_bbs, via_peer_id, hop_count, quality_score, last_updated_us, expires_at_us)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(dest_bbs, via_peer_id) DO UPDATE SET
                    hop_count = CASE WHEN excluded.hop_count < rap_routes.hop_count THEN excluded.hop_count ELSE rap_routes.hop_count END,
                    quality_score = excluded.quality_score,
                    last_updated_us = excluded.last_updated_us,
                    expires_at_us = excluded.expires_at_us
            """, (dest_bbs, peer_id, hop_count, quality, now_us, expires_at_us))

        logger.debug(f"Processed {len(routes)} routes from {sender}")

    def _update_peer_health(self, node_id: str, new_status: str, pong_received: bool = False):
        """
        Update peer health status.

        State machine:
        - unknown -> alive (on PONG)
        - alive -> unreachable (on 2 failed pings)
        - unreachable -> dead (on 5 total fails)
        - dead -> alive (on any message)
        - * -> alive (on PONG)
        """
        now_us = int(time.time() * 1_000_000)

        peer_row = self.db.fetchone(
            "SELECT id, health_status, failed_heartbeats FROM bbs_peers WHERE node_id = ?",
            (node_id,)
        )

        if not peer_row:
            # Create peer entry if doesn't exist
            self.db.execute("""
                INSERT INTO bbs_peers (node_id, protocol, health_status, last_seen_us)
                VALUES (?, 'advbbs', ?, ?)
            """, (node_id, new_status, now_us))
            logger.info(f"Peer {node_id} registered with status: {new_status}")
            return

        peer_id = peer_row[0]
        old_status = peer_row[1] or "unknown"

        # Update status and reset failed heartbeats on alive
        if new_status == "alive":
            updates = {
                "health_status": "alive",
                "failed_heartbeats": 0,
                "last_seen_us": now_us,
            }
            if pong_received:
                updates["last_pong_us"] = now_us

            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [peer_id]
            self.db.execute(
                f"UPDATE bbs_peers SET {set_clause} WHERE id = ?",
                tuple(values)
            )

            # State transition callback
            if old_status != "alive":
                self._on_peer_state_change(node_id, old_status, "alive")

        logger.debug(f"Peer {node_id} health: {old_status} -> {new_status}")

    def _on_peer_state_change(self, node_id: str, old_state: str, new_state: str):
        """
        Handle peer state transitions.

        When peer comes online (alive), trigger pending mail retry.
        When peer goes dead, expire routes through that peer.
        """
        if new_state == "alive" and old_state in ("unknown", "unreachable", "dead"):
            # Route came online - trigger mail retry via sync manager
            logger.info(f"Peer {node_id} came online ({old_state} -> alive), triggering pending mail retry")
            if hasattr(self.sync_manager, '_retry_pending_mail_for_peer'):
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(self.sync_manager._retry_pending_mail_for_peer(node_id))
                except Exception as e:
                    logger.error(f"Failed to trigger pending mail retry: {e}")

        elif new_state == "dead":
            # Invalidate routes through this peer
            logger.info(f"Peer {node_id} is dead, expiring routes via this peer")
            self._expire_routes_via_peer(node_id)

    def _expire_routes_via_peer(self, node_id: str):
        """Expire all routes that go through a specific peer."""
        peer_row = self.db.fetchone(
            "SELECT id FROM bbs_peers WHERE node_id = ?",
            (node_id,)
        )
        if peer_row:
            self.db.execute(
                "DELETE FROM rap_routes WHERE via_peer_id = ?",
                (peer_row[0],)
            )
            logger.info(f"Expired routes via peer {node_id}")
