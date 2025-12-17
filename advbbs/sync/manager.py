"""
advBBS Sync Manager

Coordinates inter-BBS synchronization using FQ51 native protocol.
"""

import asyncio
import logging
import time
from typing import Optional, TYPE_CHECKING

from ..config import SyncConfig
from .compat.fq51_native import FQ51NativeSync

if TYPE_CHECKING:
    from ..core.bbs import advBBS

logger = logging.getLogger(__name__)


class SyncManager:
    """
    Manages synchronization with peer BBS nodes.

    Uses FQ51 native DM-based protocol (JSON/base64) for sync.

    Features:
    - Rate-limited sync to prevent mesh flooding
    - UUID-based deduplication
    - Acknowledgment tracking for reliable delivery
    - Only syncs general and help boards
    """

    def __init__(self, config: SyncConfig, db, mesh, bbs: Optional["advBBS"] = None):
        """
        Initialize sync manager.

        Args:
            config: Sync configuration
            db: Database instance
            mesh: Mesh interface instance
            bbs: advBBS instance (for encryption operations)
        """
        self.config = config
        self.db = db
        self.mesh = mesh
        self.bbs = bbs

        # Peer state tracking
        self._peers = {p.node_id: p for p in config.peers} if config.peers else {}
        self._peer_status = {}  # node_id -> {online, last_seen, last_sync}

        # Sync state
        self._pending_syncs = []
        self._sync_lock = asyncio.Lock()

        # Remote mail state
        self._pending_remote_mail = {}  # uuid -> {chunks, dest_node, timestamp, attempts, next_retry, mailreq}
        self._incoming_remote_mail = {}  # uuid -> {from/to info, received_parts}
        self._relay_mail = {}  # uuid -> {origin_node, dest_node}

        # MAILREQ retry configuration
        self._mailreq_retry_intervals = [30, 60, 90]  # seconds between retries
        self._mailreq_max_attempts = 3

        # Protocol handler
        self._fq51 = FQ51NativeSync(self)

        # Protocol response delay (avoid TX queue collision)
        self._protocol_delay = 2.5

        logger.info(f"SyncManager initialized with {len(self._peers)} peers")

    def _schedule_async(self, coro):
        """
        Schedule a coroutine to run on the BBS event loop.

        This is needed because sync protocol handlers are called from
        the Meshtastic callback thread, not the async event loop thread.
        """
        if self.bbs and self.bbs._loop:
            asyncio.run_coroutine_threadsafe(coro, self.bbs._loop)
        else:
            logger.error("Cannot schedule async task: no event loop available")

    async def _send_protocol_dm(self, message: str, dest_node: str):
        """Send a protocol DM with delay to avoid TX queue collision."""
        await asyncio.sleep(self._protocol_delay)
        logger.debug(f"Sending protocol DM to {dest_node}: {message[:50]}...")
        await self.mesh.send_dm(message, dest_node)

    @property
    def total_peer_count(self) -> int:
        """Total configured peers."""
        return len(self._peers)

    @property
    def online_peer_count(self) -> int:
        """Count of recently seen peers."""
        now = time.time()
        timeout = 300  # 5 minutes
        return sum(
            1 for status in self._peer_status.values()
            if now - status.get("last_seen", 0) < timeout
        )

    @property
    def fq51_handler(self) -> FQ51NativeSync:
        """Get FQ51 native protocol handler."""
        return self._fq51

    async def tick(self):
        """
        Called periodically from main loop.

        Handles pending mail operations and retries.
        """
        if not self.config.enabled:
            return

        # Process pending sync operations
        await self._process_pending()

        # Retry pending MAILREQ messages
        await self._retry_pending_mailreq()

        # Clean up stale pending ACKs
        await self._cleanup_pending_acks()

    def _get_last_sync_time(self, node_id: str) -> int:
        """Get last sync timestamp for peer in microseconds."""
        row = self.db.fetchone(
            "SELECT last_sync_us FROM bbs_peers WHERE node_id = ?",
            (node_id,)
        )
        return row[0] if row else 0

    async def _process_pending(self):
        """Process pending sync operations."""
        if not self._pending_syncs:
            return

        # Process up to 5 pending syncs per tick
        for _ in range(min(5, len(self._pending_syncs))):
            if not self._pending_syncs:
                break

            sync_op = self._pending_syncs.pop(0)
            try:
                await self._execute_sync_op(sync_op)
            except Exception as e:
                logger.error(f"Error processing pending sync: {e}")

    async def _execute_sync_op(self, sync_op: dict):
        """Execute a pending sync operation."""
        op_type = sync_op.get("type")
        node_id = sync_op.get("node_id")
        protocol = sync_op.get("protocol")

        if op_type == "full_sync":
            await self._sync_with_peer(node_id, protocol)
        elif op_type == "delete":
            uuid = sync_op.get("uuid")
            await self._fq51.send_delete(uuid, node_id)

    async def _cleanup_pending_acks(self):
        """Clean up stale pending ACKs (older than 10 minutes)."""
        now = time.time()
        timeout = 600  # 10 minutes

        # Clean FQ51 pending ACKs
        stale_fq51 = [
            uuid for uuid, (_, ts) in self._fq51._pending_acks.items()
            if now - ts > timeout
        ]
        for uuid in stale_fq51:
            del self._fq51._pending_acks[uuid]
            logger.warning(f"FQ51 ACK timeout for {uuid[:8]}")

    async def _retry_pending_mailreq(self):
        """Retry pending MAILREQ messages that haven't received MAILACK."""
        now = time.time()
        to_remove = []

        for mail_uuid, pending in self._pending_remote_mail.items():
            # Skip if not ready for retry
            next_retry = pending.get("next_retry", 0)
            if now < next_retry:
                continue

            attempts = pending.get("attempts", 1)
            mailreq = pending.get("mailreq")

            # No mailreq stored - can't retry (legacy entry)
            if not mailreq:
                continue

            # Check if we've exceeded max attempts
            if attempts >= self._mailreq_max_attempts:
                logger.warning(f"MAILREQ {mail_uuid[:8]} failed after {attempts} attempts, giving up")
                to_remove.append(mail_uuid)
                continue

            # Retry
            dest_node = pending["dest_node"]
            try:
                await self.mesh.send_dm(mailreq, dest_node)
                pending["attempts"] = attempts + 1

                # Calculate next retry interval
                retry_idx = min(attempts, len(self._mailreq_retry_intervals) - 1)
                pending["next_retry"] = now + self._mailreq_retry_intervals[retry_idx]

                logger.info(f"Retried MAILREQ for {mail_uuid[:8]} to {dest_node} (attempt {pending['attempts']}/{self._mailreq_max_attempts})")
            except Exception as e:
                logger.error(f"Failed to retry MAILREQ {mail_uuid[:8]}: {e}")

        # Remove failed entries
        for mail_uuid in to_remove:
            del self._pending_remote_mail[mail_uuid]

    def handle_sync_message(self, message: str, sender: str) -> bool:
        """
        Handle incoming sync message from peer.

        Returns True if message was handled, False otherwise.
        """
        if not self.config.enabled:
            return False

        # Update peer status
        self._update_peer_status(sender)

        # Check for remote mail protocol first
        if message.startswith("MAIL"):
            if self.handle_mail_protocol(message, sender):
                return True

        # Handle FQ51 native protocol
        if self._fq51.is_fq51_message(message):
            return self._fq51.handle_message(message, sender)

        return False

    def _update_peer_status(self, node_id: str):
        """Update peer's last seen timestamp."""
        now = time.time()
        if node_id not in self._peer_status:
            self._peer_status[node_id] = {}
        self._peer_status[node_id]["last_seen"] = now
        self._peer_status[node_id]["online"] = True


    async def propagate_delete(self, message_uuid: str):
        """
        Propagate message deletion to all peers.

        Args:
            message_uuid: UUID of the deleted message
        """
        for node_id, peer in self._peers.items():
            if not peer.protocol:
                continue

            self._pending_syncs.append({
                "type": "delete",
                "node_id": node_id,
                "protocol": peer.protocol,
                "uuid": message_uuid,
            })

        logger.debug(f"Queued delete propagation for {message_uuid[:8]}")

    def add_peer(self, node_id: str, name: str, protocol: str):
        """
        Add a new peer for sync.

        Args:
            node_id: Peer's Meshtastic node ID
            name: Human-readable peer name
            protocol: Sync protocol to use
        """
        from ..config import PeerConfig
        peer = PeerConfig(node_id=node_id, name=name, protocol=protocol)
        self._peers[node_id] = peer

        # Also add to database
        now_us = int(time.time() * 1_000_000)
        self.db.execute("""
            INSERT OR REPLACE INTO bbs_peers (node_id, name, protocol, last_sync_us)
            VALUES (?, ?, ?, ?)
        """, (node_id, name, protocol, now_us))

        logger.info(f"Added sync peer: {name} ({node_id}) using {protocol}")

    def remove_peer(self, node_id: str):
        """
        Remove a peer from sync.

        Args:
            node_id: Peer's Meshtastic node ID
        """
        if node_id in self._peers:
            peer = self._peers.pop(node_id)
            if node_id in self._peer_status:
                del self._peer_status[node_id]
            logger.info(f"Removed sync peer: {peer.name} ({node_id})")

    def get_peer_status(self) -> list[dict]:
        """
        Get status of all configured peers.

        Returns list of peer status dicts with:
        - node_id, name, protocol
        - online, last_seen, last_sync
        """
        result = []
        for node_id, peer in self._peers.items():
            status = self._peer_status.get(node_id, {})
            last_sync_us = self._get_last_sync_time(node_id)

            result.append({
                "node_id": node_id,
                "name": peer.name,
                "protocol": peer.protocol,
                "online": status.get("online", False),
                "last_seen": status.get("last_seen", 0),
                "last_sync_us": last_sync_us,
            })

        return result

    def get_sync_stats(self) -> dict:
        """
        Get sync statistics.

        Returns dict with:
        - total_peers, online_peers
        - pending_syncs, pending_acks
        """
        return {
            "total_peers": self.total_peer_count,
            "online_peers": self.online_peer_count,
            "pending_syncs": len(self._pending_syncs),
            "pending_acks": len(self._fq51._pending_acks),
            "sync_enabled": self.config.enabled,
        }

    def list_peers(self) -> list[dict]:
        """
        List all configured peers for PEERS command.

        Returns list of dicts with name, online status.
        """
        result = []
        now = time.time()
        timeout = 300  # 5 minutes for online status

        for node_id, peer in self._peers.items():
            status = self._peer_status.get(node_id, {})
            last_seen = status.get("last_seen", 0)
            online = (now - last_seen) < timeout if last_seen else False

            result.append({
                "name": peer.name.upper(),
                "node_id": node_id,
                "protocol": peer.protocol,
                "online": online,
            })

        return result

    def get_peer_by_name(self, name: str):
        """
        Find a peer by name (case-insensitive).

        Args:
            name: Peer BBS name to look up

        Returns:
            Peer config object or None if not found
        """
        name_upper = name.upper()
        for node_id, peer in self._peers.items():
            if peer.name.upper() == name_upper:
                return peer
        return None

    def get_peer_node_id(self, name: str) -> Optional[str]:
        """
        Get peer's node ID by name.

        Args:
            name: Peer BBS name

        Returns:
            Node ID or None if not found
        """
        name_upper = name.upper()
        for node_id, peer in self._peers.items():
            if peer.name.upper() == name_upper:
                return node_id
        return None

    def is_peer(self, node_id: str) -> bool:
        """
        Check if a node ID is a configured peer.

        Args:
            node_id: Node ID to check (e.g., !abcd1234)

        Returns:
            True if node is a configured and enabled peer
        """
        peer = self._peers.get(node_id)
        return peer is not None and peer.enabled

    # === Remote Mail Protocol ===

    async def send_remote_mail(
        self,
        sender_username: str,
        sender_bbs: str,
        recipient_username: str,
        recipient_bbs: str,
        body: str,
        mail_uuid: str
    ) -> tuple[bool, str]:
        """
        Send mail to a user on a remote BBS.

        Protocol:
        1. MAILREQ - Request delivery (check route)
        2. Wait for MAILACK/MAILNAK
        3. MAILDAT - Send body chunks (max 3 x 150 chars)

        Args:
            sender_username: Sending user
            sender_bbs: Sender's home BBS callsign
            recipient_username: Destination user
            recipient_bbs: Destination BBS callsign
            body: Message body (max 450 chars)
            mail_uuid: Unique message ID

        Returns:
            (success, error_message)
        """
        # Pre-flight check
        max_body_len = 450  # 3 chunks x 150 chars
        if len(body) > max_body_len:
            return False, f"Message too long for remote delivery (max {max_body_len} chars)"

        # Find route to destination
        dest_node = self.get_peer_node_id(recipient_bbs)

        if dest_node:
            # Direct peer - send straight there
            route = [sender_bbs]
        else:
            # Not a direct peer - try to find relay
            # For now, just try the first peer as relay
            if not self._peers:
                return False, f"No route to {recipient_bbs}"

            # Pick first peer as relay (could be smarter)
            relay_node = next(iter(self._peers.keys()))
            dest_node = relay_node
            route = [sender_bbs]

        # Calculate chunks
        chunks = self._chunk_message(body, 150)
        num_parts = len(chunks)

        # Build MAILREQ
        # Format: MAILREQ|uuid|from_user|from_bbs|to_user|to_bbs|hop|parts|route
        hop = 1
        route_str = ",".join(route)
        mailreq = f"MAILREQ|{mail_uuid}|{sender_username}|{sender_bbs}|{recipient_username}|{recipient_bbs}|{hop}|{num_parts}|{route_str}"

        # Send MAILREQ
        try:
            await self.mesh.send_dm(mailreq, dest_node)
            logger.info(f"Sent MAILREQ for {mail_uuid[:8]} to {dest_node} (attempt 1/{self._mailreq_max_attempts})")

            # Store pending mail for when we receive ACK, with retry info
            now = time.time()
            self._pending_remote_mail[mail_uuid] = {
                "chunks": chunks,
                "dest_node": dest_node,
                "timestamp": now,
                "recipient": f"{recipient_username}@{recipient_bbs}",
                "mailreq": mailreq,  # Store for retries
                "attempts": 1,
                "next_retry": now + self._mailreq_retry_intervals[0],  # First retry at 30s
            }

            return True, ""

        except Exception as e:
            logger.error(f"Failed to send MAILREQ: {e}")
            return False, f"Failed to send: {e}"

    async def forward_mail(self, message, dest_node: str):
        """
        Forward a queued mail message to a peer.

        Extracts addressing from message.forwarded_to field and sends.
        """
        # Parse addressing from forwarded_to field (format: sender@bbs>recipient@bbs)
        if not message.forwarded_to or ">" not in message.forwarded_to:
            logger.error(f"Invalid forwarded_to format: {message.forwarded_to}")
            return False, "Invalid addressing"

        try:
            sender_part, recipient_part = message.forwarded_to.split(">", 1)
            sender_username, sender_bbs = sender_part.split("@", 1)
            recipient_username, recipient_bbs = recipient_part.split("@", 1)

            # Decode body
            body = message.body_enc.decode('utf-8') if isinstance(message.body_enc, bytes) else message.body_enc

            return await self.send_remote_mail(
                sender_username=sender_username,
                sender_bbs=sender_bbs,
                recipient_username=recipient_username,
                recipient_bbs=recipient_bbs,
                body=body,
                mail_uuid=message.uuid
            )
        except Exception as e:
            logger.error(f"Failed to forward mail: {e}")
            return False, str(e)

    def _chunk_message(self, body: str, chunk_size: int) -> list[str]:
        """Split message into chunks."""
        chunks = []
        for i in range(0, len(body), chunk_size):
            chunks.append(body[i:i + chunk_size])
        return chunks

    def handle_mail_protocol(self, message: str, sender: str) -> bool:
        """
        Handle incoming mail protocol messages.

        Only accepts messages from configured peers for security.
        Returns True if message was handled.
        """
        # Security: Only accept BBS protocol messages from configured peers
        if not self.is_peer(sender):
            logger.warning(f"Rejected BBS protocol message from non-peer: {sender}")
            return False

        if message.startswith("MAILREQ|"):
            return self._handle_mailreq(message, sender)
        elif message.startswith("MAILACK|"):
            return self._handle_mailack(message, sender)
        elif message.startswith("MAILNAK|"):
            return self._handle_mailnak(message, sender)
        elif message.startswith("MAILDAT|"):
            return self._handle_maildat(message, sender)
        elif message.startswith("MAILDLV|"):
            return self._handle_maildlv(message, sender)
        return False

    def _handle_mailreq(self, message: str, sender: str) -> bool:
        """Handle incoming MAILREQ - route check."""
        try:
            parts = message.split("|")
            logger.debug(f"MAILREQ parts ({len(parts)}): {parts}")
            if len(parts) < 9:
                logger.warning(f"Invalid MAILREQ format ({len(parts)} parts, need 9): {message}")
                return False

            _, uuid, from_user, from_bbs, to_user, to_bbs, hop, num_parts, route = parts[:9]
            hop = int(hop)
            num_parts = int(num_parts)
            route_list = route.split(",")

            logger.info(f"MAILREQ received: {uuid[:8]} from {from_user}@{from_bbs} to {to_user}@{to_bbs} (hop {hop})")

            my_callsign = self.bbs.config.bbs.callsign.upper()

            # Check for loop (am I already in route?)
            if my_callsign in [r.upper() for r in route_list]:
                # Loop detected - NAK
                logger.warning(f"MAILREQ {uuid[:8]}: Loop detected, sending MAILNAK")
                self._schedule_async(
                    self._send_protocol_dm(f"MAILNAK|{uuid}|LOOP", sender)
                )
                return True

            # Check max hops
            if hop > 5:
                logger.warning(f"MAILREQ {uuid[:8]}: Max hops exceeded, sending MAILNAK")
                self._schedule_async(
                    self._send_protocol_dm(f"MAILNAK|{uuid}|MAXHOPS", sender)
                )
                return True

            # Is this mail for us (our BBS)?
            if to_bbs.upper() == my_callsign:
                # Check if recipient exists locally
                from ..db.users import UserRepository
                user_repo = UserRepository(self.db)
                recipient = user_repo.get_user_by_username(to_user)

                if not recipient:
                    logger.warning(f"MAILREQ {uuid[:8]}: User '{to_user}' not found, sending MAILNAK")
                    self._schedule_async(
                        self._send_protocol_dm(f"MAILNAK|{uuid}|NOUSER", sender)
                    )
                    return True

                # Accept - store pending and ACK
                self._incoming_remote_mail[uuid] = {
                    "from_user": from_user,
                    "from_bbs": from_bbs,
                    "to_user": to_user,
                    "to_bbs": to_bbs,
                    "num_parts": num_parts,
                    "received_parts": {},
                    "sender_node": sender,
                    "timestamp": time.time(),
                }
                logger.info(f"MAILREQ {uuid[:8]}: Accepted for {to_user}, sending MAILACK")
                self._schedule_async(
                    self._send_protocol_dm(f"MAILACK|{uuid}|OK", sender)
                )
                return True

            # Not for us - need to relay
            dest_node = self.get_peer_node_id(to_bbs)
            if not dest_node:
                # Don't know destination - try to relay to another peer
                logger.warning(f"MAILREQ {uuid[:8]}: No route to {to_bbs}, sending MAILNAK")
                self._schedule_async(
                    self._send_protocol_dm(f"MAILNAK|{uuid}|NOROUTE", sender)
                )
                return True

            # Relay the request
            route_list.append(my_callsign)
            new_route = ",".join(route_list)
            relay_req = f"MAILREQ|{uuid}|{from_user}|{from_bbs}|{to_user}|{to_bbs}|{hop+1}|{num_parts}|{new_route}"

            # Store relay state
            self._relay_mail[uuid] = {
                "origin_node": sender,
                "dest_node": dest_node,
                "timestamp": time.time(),
            }

            logger.info(f"MAILREQ {uuid[:8]}: Relaying to {to_bbs} via {dest_node}")
            self._schedule_async(self._send_protocol_dm(relay_req, dest_node))
            return True

        except Exception as e:
            logger.error(f"Error handling MAILREQ: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def _handle_mailack(self, message: str, sender: str) -> bool:
        """Handle MAILACK - send message chunks."""
        try:
            parts = message.split("|")
            if len(parts) < 3:
                return False

            _, uuid, status = parts[:3]

            logger.info(f"MAILACK received: {uuid[:8]} status={status} from {sender}")

            # Check if this is a relay ACK
            if uuid in self._relay_mail:
                # Forward ACK back to origin
                relay = self._relay_mail[uuid]
                logger.info(f"MAILACK {uuid[:8]}: Relaying back to origin {relay['origin_node']}")
                self._schedule_async(
                    self._send_protocol_dm(message, relay["origin_node"])
                )
                return True

            # Check if we have pending mail for this UUID
            if uuid not in self._pending_remote_mail:
                logger.warning(f"MAILACK {uuid[:8]}: No pending mail found (already sent or expired)")
                return False

            pending = self._pending_remote_mail[uuid]
            chunks = pending["chunks"]
            dest_node = pending["dest_node"]
            attempts = pending.get("attempts", 1)

            logger.info(f"MAILACK {uuid[:8]}: Sending {len(chunks)} chunk(s) to {dest_node} (after {attempts} attempt(s))")

            # Send chunks with delay
            self._schedule_async(self._send_mail_chunks(uuid, chunks, dest_node))

            return True

        except Exception as e:
            logger.error(f"Error handling MAILACK: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    async def _send_mail_chunks(self, uuid: str, chunks: list[str], dest_node: str):
        """Send message body chunks with delays."""
        import random
        total = len(chunks)

        # Initial delay to avoid TX queue collision after MAILACK
        await asyncio.sleep(self._protocol_delay)

        for i, chunk in enumerate(chunks, 1):
            # Format: MAILDAT|uuid|part/total|data
            maildat = f"MAILDAT|{uuid}|{i}/{total}|{chunk}"
            await self.mesh.send_dm(maildat, dest_node)

            if i < total:
                # Delay between chunks
                await asyncio.sleep(random.uniform(2.2, 2.6))

        logger.info(f"Sent {total} chunks for {uuid[:8]}")

        # Clean up pending
        if uuid in self._pending_remote_mail:
            del self._pending_remote_mail[uuid]

    def _handle_mailnak(self, message: str, sender: str) -> bool:
        """Handle MAILNAK - delivery rejected."""
        try:
            parts = message.split("|")
            if len(parts) < 3:
                return False

            _, uuid, reason = parts[:3]

            logger.warning(f"MAILNAK received: {uuid[:8]} reason={reason} from {sender}")

            # Check if relay
            if uuid in self._relay_mail:
                relay = self._relay_mail.pop(uuid)
                logger.info(f"MAILNAK {uuid[:8]}: Relaying back to origin {relay['origin_node']}")
                self._schedule_async(
                    self._send_protocol_dm(message, relay["origin_node"])
                )
                return True

            # Clean up pending
            if uuid in self._pending_remote_mail:
                pending = self._pending_remote_mail.pop(uuid)
                recipient = pending.get("recipient", "unknown")
                logger.warning(f"MAILNAK {uuid[:8]}: Remote mail to {recipient} rejected: {reason}")
                # Store failure for user feedback
                self._store_mail_failure(uuid, recipient, reason)

            return True

        except Exception as e:
            logger.error(f"Error handling MAILNAK: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    def _store_mail_failure(self, uuid: str, recipient: str, reason: str):
        """Store mail failure in database for user notification."""
        try:
            from ..db.messages import MessageRepository
            msg_repo = MessageRepository(self.db)
            # Mark the message as failed with reason
            msg_repo.mark_remote_mail_failed(uuid, reason)
            logger.info(f"Stored mail failure for {uuid[:8]}: {reason}")
        except Exception as e:
            logger.error(f"Failed to store mail failure: {e}")

    def _handle_maildat(self, message: str, sender: str) -> bool:
        """Handle MAILDAT - message chunk received."""
        try:
            parts = message.split("|", 3)
            if len(parts) < 4:
                return False

            _, uuid, part_info, data = parts
            part_num, total_parts = map(int, part_info.split("/"))

            logger.info(f"MAILDAT received: {uuid[:8]} part {part_num}/{total_parts} from {sender}")

            # Check if relay
            if uuid in self._relay_mail:
                relay = self._relay_mail[uuid]
                logger.info(f"MAILDAT {uuid[:8]}: Relaying part {part_num}/{total_parts} to {relay['dest_node']}")
                self._schedule_async(
                    self._send_protocol_dm(message, relay["dest_node"])
                )
                return True

            # Store chunk
            if uuid not in self._incoming_remote_mail:
                logger.warning(f"MAILDAT {uuid[:8]}: No pending incoming mail found")
                return False

            incoming = self._incoming_remote_mail[uuid]
            incoming["received_parts"][part_num] = data

            logger.info(f"MAILDAT {uuid[:8]}: Stored part {part_num}, have {len(incoming['received_parts'])}/{incoming['num_parts']}")

            # Check if all parts received
            if len(incoming["received_parts"]) >= incoming["num_parts"]:
                logger.info(f"MAILDAT {uuid[:8]}: All parts received, delivering")
                self._schedule_async(self._deliver_remote_mail(uuid, incoming))

            return True

        except Exception as e:
            logger.error(f"Error handling MAILDAT: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False

    async def _deliver_remote_mail(self, uuid: str, incoming: dict):
        """Reassemble and deliver remote mail locally."""
        try:
            # Reassemble body
            body_parts = []
            for i in range(1, incoming["num_parts"] + 1):
                if i in incoming["received_parts"]:
                    body_parts.append(incoming["received_parts"][i])
            body = "".join(body_parts)

            logger.info(f"Delivering remote mail {uuid[:8]}: {incoming['from_user']}@{incoming['from_bbs']} -> {incoming['to_user']}")

            # Look up local recipient
            from ..db.users import UserRepository, UserNodeRepository
            user_repo = UserRepository(self.db)
            user_node_repo = UserNodeRepository(self.db)
            recipient = user_repo.get_user_by_username(incoming["to_user"])

            if not recipient:
                logger.error(f"DELIVER {uuid[:8]}: Recipient '{incoming['to_user']}' not found")
                return

            # Create local mail message
            from ..db.messages import MessageRepository
            msg_repo = MessageRepository(self.db)

            result = msg_repo.create_incoming_remote_mail(
                uuid=uuid,
                from_user=incoming["from_user"],
                from_bbs=incoming["from_bbs"],
                to_user_id=recipient.id,
                body=body
            )

            if not result:
                logger.error(f"DELIVER {uuid[:8]}: Failed to store in database")
                return

            logger.info(f"DELIVER {uuid[:8]}: Stored in database for {incoming['to_user']}")

            # Send notification to recipient if they have registered nodes
            recipient_nodes = user_node_repo.get_user_nodes(recipient.id)
            if recipient_nodes:
                notification = f"[MAIL] From: {incoming['from_user']}@{incoming['from_bbs']}. DM !mail to check."
                await asyncio.sleep(2.5)  # Delay to avoid TX queue collision
                # Send to first (primary) node
                await self.mesh.send_dm(notification, recipient_nodes[0])
                logger.info(f"DELIVER {uuid[:8]}: Sent notification to {recipient_nodes[0]}")

            # Send delivery confirmation back (with delay)
            dlv = f"MAILDLV|{uuid}|OK|{incoming['to_user']}@{self.bbs.config.bbs.callsign}"
            await self._send_protocol_dm(dlv, incoming["sender_node"])

            logger.info(f"DELIVER {uuid[:8]}: Sent MAILDLV confirmation to {incoming['sender_node']}")

            # Clean up
            if uuid in self._incoming_remote_mail:
                del self._incoming_remote_mail[uuid]

        except Exception as e:
            logger.error(f"Error delivering remote mail {uuid[:8]}: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _handle_maildlv(self, message: str, sender: str) -> bool:
        """Handle MAILDLV - delivery confirmation."""
        try:
            parts = message.split("|")
            if len(parts) < 4:
                return False

            _, uuid, status, dest = parts[:4]

            logger.info(f"MAILDLV received: {uuid[:8]} status={status} dest={dest} from {sender}")

            # Check if relay
            if uuid in self._relay_mail:
                relay = self._relay_mail.pop(uuid)
                logger.info(f"MAILDLV {uuid[:8]}: Relaying back to origin {relay['origin_node']}")
                self._schedule_async(
                    self._send_protocol_dm(message, relay["origin_node"])
                )
                return True

            logger.info(f"MAILDLV {uuid[:8]}: Remote mail successfully delivered to {dest}")

            # Update message status in database
            try:
                from ..db.messages import MessageRepository
                msg_repo = MessageRepository(self.db)
                msg_repo.mark_remote_mail_delivered(uuid, dest)
            except Exception as e:
                logger.error(f"Failed to update delivery status for {uuid[:8]}: {e}")

            return True

        except Exception as e:
            logger.error(f"Error handling MAILDLV: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False
