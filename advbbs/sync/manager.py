"""
advBBS Sync Manager

Coordinates inter-BBS synchronization using advBBS native protocol.
"""

import asyncio
import logging
import time
import traceback
from typing import Optional, TYPE_CHECKING

from ..config import SyncConfig
from .compat.advbbs_native import AdvBBSNativeSync

if TYPE_CHECKING:
    from ..core.bbs import advBBS

logger = logging.getLogger(__name__)


class SyncManager:
    """
    Manages synchronization with peer BBS nodes.

    Uses advBBS native DM-based protocol (JSON/base64) for sync.

    Features:
    - Rate-limited sync to prevent mesh flooding
    - UUID-based deduplication
    - Acknowledgment tracking for reliable delivery
    - Batch board sync to reduce mesh traffic
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

        # Board sync — outbound batching
        self._board_sync_counters = {}    # board_name -> int (new local posts since last sync)
        self._last_board_sync_time = 0.0  # epoch seconds of last outbound board sync

        # Board sync — outbound pending (after BOARDREQ sent, awaiting ACK/DLV)
        self._pending_board_sync = {}     # (board_name, peer_node_id) -> {chunks, state, timestamp}

        # Board sync — inbound (receiving chunks from peer)
        self._incoming_board_sync = {}    # (board_name, sender_node_id) -> {num_parts, received_parts, timestamp}

        # MAILREQ retry configuration (from config)
        self._mailreq_retry_intervals = [30, 60, 90]  # seconds between retries
        self._mailreq_max_attempts = config.mail_retry_attempts

        # MAILDAT/MAILDLV retry configuration (from config)
        self._maildat_retry_interval = config.maildlv_retry_interval_seconds
        self._maildat_max_attempts = config.maildlv_max_attempts
        self._maildat_timeout = config.maildlv_timeout_seconds

        # MAILDLV awaiting state - track sent mail waiting for delivery confirmation
        self._awaiting_maildlv = {}  # uuid -> {dest_node, chunks, timestamp, attempts, next_retry}
        self._maildlv_retry_intervals = [60, 120, 180]  # seconds between retries (longer than MAILREQ)
        self._maildlv_max_attempts = 3

        # Protocol handler
        self._native = AdvBBSNativeSync(self)

        # Protocol response delay (avoid TX queue collision)
        self._protocol_delay = 2.5

        # RAP (Route Announcement Protocol) state
        self._last_heartbeat_time = 0
        self._last_route_share_time = 0
        self._pending_pings = {}  # node_id -> timestamp

        # Store sync config reference for RAP settings
        self.sync_config = config

        # Sync configured peers to database
        self._sync_peers_to_db()

        logger.info(f"SyncManager initialized with {len(self._peers)} peers")

    def _sync_peers_to_db(self):
        """
        Ensure all configured peers exist in the database.
        Uses peer name as callsign since config doesn't have separate callsign field.
        """
        for peer in self._peers.values():
            existing = self.db.fetchone(
                "SELECT id FROM bbs_peers WHERE node_id = ?",
                (peer.node_id,)
            )
            if not existing:
                self.db.execute("""
                    INSERT INTO bbs_peers (node_id, name, callsign, protocol, sync_enabled)
                    VALUES (?, ?, ?, ?, ?)
                """, (peer.node_id, peer.name, peer.name, peer.protocol, 1 if peer.enabled else 0))
            else:
                # Update existing peer with config values
                self.db.execute("""
                    UPDATE bbs_peers
                    SET name = ?, callsign = COALESCE(callsign, ?), protocol = ?, sync_enabled = ?
                    WHERE node_id = ?
                """, (peer.name, peer.name, peer.protocol, 1 if peer.enabled else 0, peer.node_id))

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
    def native_handler(self) -> AdvBBSNativeSync:
        """Get native protocol handler."""
        return self._native

    async def tick(self):
        """
        Called periodically from main loop.

        Handles pending mail operations and retries.
        """
        if not self.config.enabled:
            return

        # Process pending sync operations
        await self._process_pending()

        # Retry pending MAILREQ messages (waiting for MAILACK)
        await self._retry_pending_mailreq()

        # Retry pending MAILDLV confirmations (waiting for delivery confirmation)
        await self._retry_pending_maildlv()

        # Clean up stale incoming mail (incomplete chunk reception)
        await self._cleanup_stale_incoming()

        # Clean up stale incoming board sync (incomplete chunk reception)
        await self._cleanup_stale_incoming_board_sync()

        # Board sync batching: flush when counter >= 10 OR (>= 1 AND 1hr elapsed)
        total = sum(self._board_sync_counters.values())
        elapsed = time.time() - self._last_board_sync_time
        if (total >= 10) or (total >= 1 and elapsed >= 3600):
            await self._sync_boards_to_peers()

        # Clean up stale relay state
        await self._cleanup_stale_relay_mail()

        # Clean up stale pending ACKs
        await self._cleanup_pending_acks()

        # === RAP (Route Announcement Protocol) ===
        if getattr(self.config, 'rap_enabled', True):
            now = time.time()

            # Send heartbeat pings
            heartbeat_interval = getattr(self.config, 'rap_heartbeat_interval_seconds', 300)
            if now - self._last_heartbeat_time > heartbeat_interval:
                await self._send_rap_heartbeats()
                self._last_heartbeat_time = now

            # Check for heartbeat timeouts
            await self._check_heartbeat_timeouts()

            # Share route table periodically
            route_share_interval = getattr(self.config, 'rap_route_share_interval_seconds', 900)
            if now - self._last_route_share_time > route_share_interval:
                await self._share_route_table()
                self._last_route_share_time = now

            # Cleanup expired routes and pending mail
            await self._cleanup_expired_routes()
            await self._cleanup_expired_pending_mail()

    async def _sync_bulletins(self):
        """Sync bulletins with all peers."""
        async with self._sync_lock:
            logger.info("Starting scheduled bulletin sync...")

            for node_id, peer in self._peers.items():
                if not peer.protocol:
                    continue

                try:
                    await self._sync_with_peer(node_id, peer.protocol)
                except Exception as e:
                    logger.error(f"Error syncing with {peer.name}: {e}")

            logger.info("Scheduled bulletin sync complete")

    async def _sync_with_peer(self, node_id: str, protocol: str):
        """Sync with a specific peer using advBBS protocol."""
        logger.debug(f"Syncing with {node_id}")

        # Get last sync timestamp for this peer
        since_us = self._get_last_sync_time(node_id)

        # Only advBBS native protocol is supported
        await self._native.sync_bulletins_to_peer(node_id, since_us)

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
            await self._native.send_delete(uuid, node_id)

    async def _cleanup_pending_acks(self):
        """Clean up stale pending ACKs (older than 10 minutes)."""
        now = time.time()
        timeout = 600  # 10 minutes

        # Clean advBBS pending ACKs
        stale_native = [
            uuid for uuid, (_, ts) in self._native._pending_acks.items()
            if now - ts > timeout
        ]
        for uuid in stale_native:
            del self._native._pending_acks[uuid]
            logger.warning(f"advBBS ACK timeout for {uuid[:8]}")

    async def _retry_pending_mailreq(self):
        """Retry pending MAILREQ messages that haven't received MAILACK."""
        now = time.time()
        to_remove = []

        for mail_uuid, pending in self._pending_remote_mail.items():
            # Skip entries that have already sent chunks (waiting for MAILDLV)
            if pending.get("state") == "chunks_sent":
                continue

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

    async def _retry_pending_maildlv(self):
        """Retry sending MAILDAT chunks if MAILDLV not received."""
        now = time.time()
        to_remove = []

        for mail_uuid, awaiting in list(self._awaiting_maildlv.items()):
            # Skip if not ready for retry
            next_retry = awaiting.get("next_retry", 0)
            if now < next_retry:
                continue

            attempts = awaiting.get("attempts", 1)
            chunks = awaiting.get("chunks", [])
            dest_node = awaiting.get("dest_node")
            failed_chunks = awaiting.get("failed_chunks", [])

            # Check if we've exceeded max attempts
            if attempts >= self._maildlv_max_attempts:
                logger.warning(f"MAILDLV {mail_uuid[:8]} not received after {attempts} attempts, giving up")
                to_remove.append(mail_uuid)
                # Mark as failed in database
                self._store_mail_failure(mail_uuid, f"to {dest_node}", "NO_DLV_CONFIRM")
                continue

            # Resend chunks - prioritize failed chunks, but resend all if no MAILDLV
            try:
                if failed_chunks:
                    logger.info(f"Retrying {len(failed_chunks)} failed MAILDAT chunk(s) for {mail_uuid[:8]} to {dest_node} (attempt {attempts + 1}/{self._maildlv_max_attempts})")
                    new_failed = await self._resend_mail_chunks(mail_uuid, chunks, dest_node, failed_only=failed_chunks)
                else:
                    logger.info(f"Retrying all MAILDAT chunks for {mail_uuid[:8]} to {dest_node} - no MAILDLV received (attempt {attempts + 1}/{self._maildlv_max_attempts})")
                    new_failed = await self._resend_mail_chunks(mail_uuid, chunks, dest_node)

                awaiting["failed_chunks"] = new_failed
                awaiting["attempts"] = attempts + 1
                # Calculate next retry interval
                retry_idx = min(attempts, len(self._maildlv_retry_intervals) - 1)
                awaiting["next_retry"] = now + self._maildlv_retry_intervals[retry_idx]

            except Exception as e:
                logger.error(f"Failed to retry MAILDAT {mail_uuid[:8]}: {e}")

        # Remove failed/completed entries
        for mail_uuid in to_remove:
            del self._awaiting_maildlv[mail_uuid]

    async def _resend_mail_chunks(self, uuid: str, chunks: list[str], dest_node: str, failed_only: list[int] = None):
        """Resend message body chunks with ACK-based retry."""
        import random
        total = len(chunks)
        max_chunk_retries = 2  # Fewer retries on resend

        # Determine which chunks to send
        if failed_only:
            indices_to_send = failed_only
        else:
            indices_to_send = list(range(1, total + 1))

        new_failed = []

        for i in indices_to_send:
            chunk = chunks[i - 1]  # Convert 1-indexed to 0-indexed
            maildat = f"MAILDAT|{uuid}|{i}/{total}|{chunk}"

            success = False
            for attempt in range(max_chunk_retries):
                acked, error = await self.mesh.send_dm_wait_ack(maildat, dest_node, timeout=30.0)
                if acked:
                    logger.debug(f"MAILDAT {uuid[:8]} chunk {i}/{total} ACKed (resend)")
                    success = True
                    break
                else:
                    logger.warning(f"MAILDAT {uuid[:8]} chunk {i}/{total} resend failed: {error}")
                    if attempt < max_chunk_retries - 1:
                        await asyncio.sleep(random.uniform(3.0, 5.0))

            if not success:
                new_failed.append(i)

            # Delay between chunks
            await asyncio.sleep(random.uniform(2.2, 2.6))

        if new_failed:
            logger.warning(f"Resent chunks for {uuid[:8]}, {len(new_failed)} still failed: {new_failed}")
        else:
            logger.info(f"Resent {len(indices_to_send)} chunk(s) for {uuid[:8]}, all ACKed")

        return new_failed

    async def _cleanup_stale_incoming(self):
        """Clean up incomplete incoming mail that timed out."""
        now = time.time()
        timeout = 300  # 5 minutes to receive all chunks
        to_remove = []

        for mail_uuid, incoming in self._incoming_remote_mail.items():
            timestamp = incoming.get("timestamp", 0)
            if now - timestamp > timeout:
                received = len(incoming.get("received_parts", {}))
                expected = incoming.get("num_parts", 0)
                logger.warning(f"Incoming mail {mail_uuid[:8]} timed out: received {received}/{expected} chunks")
                to_remove.append(mail_uuid)

        for mail_uuid in to_remove:
            del self._incoming_remote_mail[mail_uuid]

    async def _cleanup_stale_relay_mail(self):
        """Clean up stale relay state entries (older than 10 minutes)."""
        now = time.time()
        timeout = 600  # 10 minutes
        to_remove = []

        for mail_uuid, relay in self._relay_mail.items():
            timestamp = relay.get("timestamp", 0)
            if now - timestamp > timeout:
                logger.debug(f"Relay state {mail_uuid[:8]} expired")
                to_remove.append(mail_uuid)

        for mail_uuid in to_remove:
            del self._relay_mail[mail_uuid]

    async def _cleanup_stale_incoming_board_sync(self):
        """Clean up incomplete incoming board sync that timed out."""
        now = time.time()
        timeout = 300  # 5 minutes
        to_remove = []

        for key, incoming in self._incoming_board_sync.items():
            timestamp = incoming.get("timestamp", 0)
            if now - timestamp > timeout:
                received = len(incoming.get("received_parts", {}))
                expected = incoming.get("num_parts", 0)
                board_name = key[0] if isinstance(key, tuple) else key
                logger.warning(f"Incoming board sync {board_name} timed out: {received}/{expected} chunks")
                to_remove.append(key)

        for key in to_remove:
            del self._incoming_board_sync[key]

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

        # Check for board sync protocol
        if message.startswith("BOARD"):
            if self.handle_board_protocol(message, sender):
                return True

        # Handle advBBS native protocol
        if self._native.is_advbbs_message(message):
            return self._native.handle_message(message, sender)

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
            "pending_acks": len(self._native._pending_acks),
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
            # First try direct peer lookup
            dest_node = self.get_peer_node_id(to_bbs)

            if not dest_node:
                # Not a direct peer - check RAP routing table
                route = self.find_best_route(to_bbs)
                if route:
                    dest_node = route[0]  # next_hop_node_id
                    logger.info(f"MAILREQ {uuid[:8]}: Using RAP route to {to_bbs} via {dest_node} ({route[1]} hops)")

            if not dest_node:
                # Don't know destination - send MAILNAK
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
            logger.error(traceback.format_exc())
            return False

    async def _send_mail_chunks(self, uuid: str, chunks: list[str], dest_node: str):
        """Send message body chunks with ACK-based retry per chunk."""
        import random
        total = len(chunks)
        max_chunk_retries = 3

        # Initial delay to avoid TX queue collision after MAILACK
        await asyncio.sleep(self._protocol_delay)

        failed_chunks = []

        for i, chunk in enumerate(chunks, 1):
            # Format: MAILDAT|uuid|part/total|data
            maildat = f"MAILDAT|{uuid}|{i}/{total}|{chunk}"

            # Try sending with ACK, retry on failure
            success = False
            for attempt in range(max_chunk_retries):
                acked, error = await self.mesh.send_dm_wait_ack(maildat, dest_node, timeout=30.0)
                if acked:
                    logger.debug(f"MAILDAT {uuid[:8]} chunk {i}/{total} ACKed")
                    success = True
                    break
                else:
                    logger.warning(f"MAILDAT {uuid[:8]} chunk {i}/{total} failed: {error} (attempt {attempt + 1}/{max_chunk_retries})")
                    if attempt < max_chunk_retries - 1:
                        await asyncio.sleep(random.uniform(3.0, 5.0))

            if not success:
                failed_chunks.append(i)
                logger.error(f"MAILDAT {uuid[:8]} chunk {i}/{total} failed after {max_chunk_retries} attempts")

            if i < total:
                # Delay between chunks
                await asyncio.sleep(random.uniform(2.2, 2.6))

        if failed_chunks:
            logger.error(f"MAILDAT {uuid[:8]}: {len(failed_chunks)} chunk(s) failed to send: {failed_chunks}")
        else:
            logger.info(f"Sent {total} chunks for {uuid[:8]}, all ACKed, awaiting MAILDLV")

        # Move from pending_remote_mail to awaiting_maildlv
        now = time.time()
        self._awaiting_maildlv[uuid] = {
            "dest_node": dest_node,
            "chunks": chunks,
            "failed_chunks": failed_chunks,
            "timestamp": now,
            "attempts": 1,
            "next_retry": now + self._maildlv_retry_intervals[0],
        }

        # Clean up pending MAILREQ state
        if uuid in self._pending_remote_mail:
            now = time.time()
            self._pending_remote_mail[uuid]["state"] = "chunks_sent"
            self._pending_remote_mail[uuid]["chunks_sent_at"] = now
            self._pending_remote_mail[uuid]["chunk_attempts"] = 1
            self._pending_remote_mail[uuid]["next_retry"] = now + self._maildat_retry_interval
            logger.info(f"MAILDAT {uuid[:8]}: Waiting for MAILDLV (will retry in {self._maildat_retry_interval}s if not received)")

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
                if not incoming.get("delivering"):
                    incoming["delivering"] = True
                    logger.info(f"MAILDAT {uuid[:8]}: All parts received, delivering")
                    self._schedule_async(self._deliver_remote_mail(uuid, incoming))
                else:
                    logger.info(f"MAILDAT {uuid[:8]}: Duplicate chunk (delivery already in progress)")

            return True

        except Exception as e:
            logger.error(f"Error handling MAILDAT: {e}")
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

            if result == "duplicate":
                logger.info(f"DELIVER {uuid[:8]}: Duplicate mail (already stored), sending MAILDLV anyway")
                # Still send delivery confirmation for duplicates so sender stops retrying
                dlv = f"MAILDLV|{uuid}|OK|{incoming['to_user']}@{self.bbs.config.bbs.callsign}"
                await self._send_protocol_dm(dlv, incoming["sender_node"])
                logger.info(f"DELIVER {uuid[:8]}: Sent MAILDLV confirmation to {incoming['sender_node']}")
                if uuid in self._incoming_remote_mail:
                    del self._incoming_remote_mail[uuid]
                return

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

            # Clean up awaiting state - delivery confirmed, no more retries needed
            if uuid in self._awaiting_maildlv:
                del self._awaiting_maildlv[uuid]
                logger.debug(f"MAILDLV {uuid[:8]}: Cleared awaiting state")

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
            logger.error(traceback.format_exc())
            return False

    # === RAP (Route Announcement Protocol) Methods ===

    async def _send_rap_heartbeats(self):
        """Send RAP_PING heartbeats to all peers."""
        logger.debug("Sending RAP heartbeat pings to peers")

        now = time.time()
        for node_id, peer in self._peers.items():
            if not peer.enabled:
                continue

            try:
                await self._native.send_rap_ping(node_id)
                self._pending_pings[node_id] = now
            except Exception as e:
                logger.error(f"Failed to send RAP_PING to {node_id}: {e}")

    async def _check_heartbeat_timeouts(self):
        """Check for peers that didn't respond to heartbeats."""
        now = time.time()
        timeout = getattr(self.config, 'rap_heartbeat_timeout_seconds', 30)
        unreachable_threshold = getattr(self.config, 'rap_unreachable_threshold', 2)
        dead_threshold = getattr(self.config, 'rap_dead_threshold', 5)

        for node_id in list(self._pending_pings.keys()):
            ping_time = self._pending_pings.get(node_id, 0)
            if now - ping_time > timeout:
                # Ping timed out - increment failed heartbeats
                del self._pending_pings[node_id]

                peer_row = self.db.fetchone(
                    "SELECT id, health_status, failed_heartbeats FROM bbs_peers WHERE node_id = ?",
                    (node_id,)
                )
                if not peer_row:
                    continue

                peer_id = peer_row[0]
                current_status = peer_row[1] or "unknown"
                failed = (peer_row[2] or 0) + 1

                # Determine new status based on failure count
                if failed >= dead_threshold:
                    new_status = "dead"
                elif failed >= unreachable_threshold:
                    new_status = "unreachable"
                else:
                    new_status = current_status

                # Update database
                self.db.execute(
                    "UPDATE bbs_peers SET failed_heartbeats = ?, health_status = ? WHERE id = ?",
                    (failed, new_status, peer_id)
                )

                # Trigger state change callback if status changed
                if new_status != current_status:
                    logger.warning(f"Peer {node_id} health: {current_status} -> {new_status} (failed={failed})")
                    self._native._on_peer_state_change(node_id, current_status, new_status)

    async def _share_route_table(self):
        """Share full route table with all peers."""
        logger.debug("Sharing route table with peers")

        for node_id, peer in self._peers.items():
            if not peer.enabled:
                continue

            try:
                await self._native.send_rap_routes(node_id)
            except Exception as e:
                logger.error(f"Failed to send RAP_ROUTES to {node_id}: {e}")

    async def _cleanup_expired_routes(self):
        """Remove expired routes from database."""
        now_us = int(time.time() * 1_000_000)
        result = self.db.execute(
            "DELETE FROM rap_routes WHERE expires_at_us < ?",
            (now_us,)
        )
        if result.rowcount > 0:
            logger.debug(f"Cleaned up {result.rowcount} expired routes")

    async def _cleanup_expired_pending_mail(self):
        """Remove expired pending mail from database."""
        now_us = int(time.time() * 1_000_000)
        expired = self.db.fetchall(
            "SELECT mail_uuid, recipient_bbs, sender_user_id FROM rap_pending_mail WHERE expires_at_us < ?",
            (now_us,)
        )

        for row in expired:
            mail_uuid = row[0]
            recipient_bbs = row[1]
            sender_user_id = row[2]

            logger.warning(f"Pending mail {mail_uuid[:8]} to {recipient_bbs} expired")

            # Send notification to sender
            await self._send_queued_mail_notification(sender_user_id, recipient_bbs, "expired")

            # Delete from pending
            self.db.execute(
                "DELETE FROM rap_pending_mail WHERE mail_uuid = ?",
                (mail_uuid,)
            )

    async def _retry_pending_mail_for_peer(self, peer_node_id: str):
        """
        Retry all pending mail that can now be delivered via this peer.

        Called when a peer comes online (state changes to 'alive').
        """
        # Get peer's BBS name
        peer_row = self.db.fetchone(
            "SELECT id, callsign, name FROM bbs_peers WHERE node_id = ?",
            (peer_node_id,)
        )
        if not peer_row:
            return

        peer_id = peer_row[0]
        peer_callsign = peer_row[1] or peer_row[2] or ""

        # Get destinations reachable via this peer
        now_us = int(time.time() * 1_000_000)
        reachable = {peer_callsign.upper()} if peer_callsign else set()

        # Add destinations from route table via this peer
        routes = self.db.fetchall(
            "SELECT dest_bbs FROM rap_routes WHERE via_peer_id = ? AND expires_at_us > ?",
            (peer_id, now_us)
        )
        for route in routes:
            reachable.add(route[0].upper())

        if not reachable:
            return

        logger.info(f"Peer {peer_node_id} online, checking pending mail for: {reachable}")

        # Find pending mail for these destinations
        placeholders = ','.join('?' * len(reachable))
        pending = self.db.fetchall(f"""
            SELECT * FROM rap_pending_mail
            WHERE UPPER(recipient_bbs) IN ({placeholders})
              AND expires_at_us > ?
        """, (*reachable, now_us))

        for mail in pending:
            mail_uuid = mail[1]  # mail_uuid column
            sender_user_id = mail[2]
            sender_username = mail[3]
            sender_bbs = mail[4]
            recipient_username = mail[5]
            recipient_bbs = mail[6]
            body_enc = mail[8]  # body_enc column

            logger.info(f"Route to {recipient_bbs} online, retrying {mail_uuid[:8]}")

            # Decrypt body for resend
            try:
                body = body_enc.decode('utf-8') if isinstance(body_enc, bytes) else str(body_enc)
            except Exception as e:
                logger.error(f"Failed to decode pending mail body: {e}")
                continue

            # Attempt to send
            success, error = await self.send_remote_mail(
                sender_username, sender_bbs,
                recipient_username, recipient_bbs,
                body, mail_uuid
            )

            if success:
                # Remove from pending queue
                self.db.execute(
                    "DELETE FROM rap_pending_mail WHERE mail_uuid = ?",
                    (mail_uuid,)
                )
                # Send notification to sender
                await self._send_queued_mail_notification(sender_user_id, recipient_bbs, "sent")
                logger.info(f"Pending mail {mail_uuid[:8]} to {recipient_bbs} resent successfully")
            else:
                # Update retry count
                retry_count = mail[10] + 1  # retry_count column
                self.db.execute(
                    "UPDATE rap_pending_mail SET retry_count = ?, last_retry_us = ?, last_status = ? WHERE mail_uuid = ?",
                    (retry_count, now_us, error, mail_uuid)
                )
                logger.warning(f"Pending mail {mail_uuid[:8]} retry failed: {error}")

    async def _send_queued_mail_notification(self, user_id: int, dest_bbs: str, status: str):
        """Send system message to user about queued mail status."""
        try:
            from ..db.messages import MessageRepository
            from ..db.models import MessageType

            msg_repo = MessageRepository(self.db)

            if status == "sent":
                subject = f"Queued mail to {dest_bbs} delivered"
                body = f"Your message to {dest_bbs} was queued while the route was unavailable. It has now been delivered successfully."
            elif status == "expired":
                subject = f"Queued mail to {dest_bbs} expired"
                body = f"Your message to {dest_bbs} could not be delivered within the retry period (24 hours) and has been discarded."
            else:
                return

            # Create system mail to user
            msg_repo.create_message(
                msg_type=MessageType.SYSTEM,
                recipient_user_id=user_id,
                body_enc=body.encode('utf-8'),
                subject_enc=subject.encode('utf-8'),
            )

            logger.info(f"Sent queued mail notification to user {user_id}: {status}")

        except Exception as e:
            logger.error(f"Failed to send queued mail notification: {e}")

    async def queue_pending_mail(
        self,
        mail_uuid: str,
        sender_user_id: int,
        sender_username: str,
        sender_bbs: str,
        recipient_username: str,
        recipient_bbs: str,
        body: str,
        status: str = "no_route"
    ):
        """
        Queue mail for later delivery when route becomes available.

        Called when send_remote_mail fails due to no route.
        """
        now_us = int(time.time() * 1_000_000)
        expiry_seconds = getattr(self.config, 'rap_pending_mail_expiry_seconds', 86400)
        expires_at_us = now_us + (expiry_seconds * 1_000_000)

        try:
            self.db.execute("""
                INSERT OR REPLACE INTO rap_pending_mail
                (mail_uuid, sender_user_id, sender_username, sender_bbs,
                 recipient_username, recipient_bbs, body_enc,
                 queued_at_us, expires_at_us, retry_count, last_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """, (
                mail_uuid, sender_user_id, sender_username, sender_bbs,
                recipient_username, recipient_bbs, body.encode('utf-8'),
                now_us, expires_at_us, status
            ))

            logger.info(f"Queued mail {mail_uuid[:8]} to {recipient_bbs} for later delivery (status: {status})")

        except Exception as e:
            logger.error(f"Failed to queue pending mail: {e}")

    def find_best_route(self, dest_bbs: str) -> Optional[tuple]:
        """
        Find best route to destination BBS.

        Returns: (next_hop_node_id, hop_count, quality) or None
        """
        dest_bbs_upper = dest_bbs.upper()

        # 1. Check if direct peer
        direct_node = self.get_peer_node_id(dest_bbs)
        if direct_node:
            peer_row = self.db.fetchone(
                "SELECT health_status, quality_score FROM bbs_peers WHERE node_id = ?",
                (direct_node,)
            )
            if peer_row:
                status = peer_row[0] or "unknown"
                quality = peer_row[1] if peer_row[1] is not None else 1.0
                if status in ("unknown", "alive"):
                    return (direct_node, 1, quality)

        # 2. Query route table for indirect routes
        now_us = int(time.time() * 1_000_000)
        route = self.db.fetchone("""
            SELECT p.node_id, r.hop_count, r.quality_score
            FROM rap_routes r
            JOIN bbs_peers p ON r.via_peer_id = p.id
            WHERE UPPER(r.dest_bbs) = ?
              AND r.expires_at_us > ?
              AND p.health_status IN ('unknown', 'alive')
            ORDER BY r.hop_count ASC, r.quality_score DESC
            LIMIT 1
        """, (dest_bbs_upper, now_us))

        if route:
            return (route[0], route[1], route[2])

        return None

    # === Board Sync Protocol ===

    def handle_board_protocol(self, message: str, sender: str) -> bool:
        """
        Handle incoming board sync protocol messages.

        Only accepts messages from configured peers.
        Returns True if message was handled.
        """
        if not self.is_peer(sender):
            logger.warning(f"Rejected board protocol message from non-peer: {sender}")
            return False

        if message.startswith("BOARDREQ|"):
            return self._handle_boardreq(message, sender)
        elif message.startswith("BOARDACK|"):
            return self._handle_boardack(message, sender)
        elif message.startswith("BOARDNAK|"):
            return self._handle_boardnak(message, sender)
        elif message.startswith("BOARDDAT|"):
            return self._handle_boarddat(message, sender)
        elif message.startswith("BOARDDLV|"):
            return self._handle_boarddlv(message, sender)
        return False

    def _handle_boardreq(self, message: str, sender: str) -> bool:
        """Handle incoming BOARDREQ — check if board exists and is synced.

        Format: BOARDREQ|board_name|count|since_us
        """
        try:
            parts = message.split("|")
            if len(parts) < 4:
                logger.warning(f"Invalid BOARDREQ format ({len(parts)} parts): {message}")
                return False

            _, board_name, count_str, since_us_str = parts[:4]
            count = int(count_str)

            logger.info(f"BOARDREQ received: board={board_name} count={count} from {sender}")

            # Check if board exists and has sync enabled
            from ..core.boards import BoardRepository
            board_repo = BoardRepository(self.db)
            board = board_repo.get_board_by_name(board_name)

            if not board:
                logger.warning(f"BOARDREQ: Board '{board_name}' not found")
                self._schedule_async(
                    self._send_protocol_dm(f"BOARDNAK|{board_name}|NOBOARD", sender)
                )
                return True

            if not board.sync_enabled:
                logger.warning(f"BOARDREQ: Board '{board_name}' sync not enabled")
                self._schedule_async(
                    self._send_protocol_dm(f"BOARDNAK|{board_name}|NOSYNC", sender)
                )
                return True

            # Accept — store pending and ACK
            key = (board_name, sender)
            self._incoming_board_sync[key] = {
                "board_name": board_name,
                "board_id": board.id,
                "count": count,
                "num_parts": 0,  # Will be set when BOARDDAT arrives
                "received_parts": {},
                "sender_node": sender,
                "timestamp": time.time(),
            }

            logger.info(f"BOARDREQ: Accepted for board '{board_name}', sending BOARDACK")
            self._schedule_async(
                self._send_protocol_dm(f"BOARDACK|{board_name}|OK", sender)
            )
            return True

        except Exception as e:
            logger.error(f"Error handling BOARDREQ: {e}")
            logger.error(traceback.format_exc())
            return False

    def _handle_boardack(self, message: str, sender: str) -> bool:
        """Handle BOARDACK — send post data chunks.

        Format: BOARDACK|board_name|OK
        """
        try:
            parts = message.split("|")
            if len(parts) < 3:
                return False

            _, board_name, status = parts[:3]

            logger.info(f"BOARDACK received: board={board_name} status={status} from {sender}")

            key = (board_name, sender)
            if key not in self._pending_board_sync:
                logger.warning(f"BOARDACK: No pending board sync for {board_name} to {sender}")
                return False

            pending = self._pending_board_sync[key]
            chunks = pending["chunks"]
            logger.info(f"BOARDACK: Sending {len(chunks)} chunk(s) for {board_name} to {sender}")

            pending["state"] = "sending"
            self._schedule_async(self._send_board_chunks(board_name, chunks, sender))
            return True

        except Exception as e:
            logger.error(f"Error handling BOARDACK: {e}")
            logger.error(traceback.format_exc())
            return False

    def _handle_boardnak(self, message: str, sender: str) -> bool:
        """Handle BOARDNAK — sync rejected by peer.

        Format: BOARDNAK|board_name|reason
        """
        try:
            parts = message.split("|")
            if len(parts) < 3:
                return False

            _, board_name, reason = parts[:3]

            logger.warning(f"BOARDNAK received: board={board_name} reason={reason} from {sender}")

            key = (board_name, sender)
            if key in self._pending_board_sync:
                del self._pending_board_sync[key]

            return True

        except Exception as e:
            logger.error(f"Error handling BOARDNAK: {e}")
            logger.error(traceback.format_exc())
            return False

    def _handle_boarddat(self, message: str, sender: str) -> bool:
        """Handle BOARDDAT — receive post data chunk.

        Format: BOARDDAT|board_name|seq/total|data
        """
        try:
            parts = message.split("|", 3)
            if len(parts) < 4:
                return False

            _, board_name, part_info, data = parts
            part_num, total_parts = map(int, part_info.split("/"))

            logger.info(f"BOARDDAT received: board={board_name} part {part_num}/{total_parts} from {sender}")

            key = (board_name, sender)
            if key not in self._incoming_board_sync:
                logger.warning(f"BOARDDAT: No pending incoming board sync for {board_name} from {sender}")
                return False

            incoming = self._incoming_board_sync[key]
            incoming["num_parts"] = total_parts
            incoming["received_parts"][part_num] = data

            logger.info(f"BOARDDAT: Stored part {part_num}, have {len(incoming['received_parts'])}/{total_parts}")

            # Check if all parts received
            if len(incoming["received_parts"]) >= total_parts:
                if not incoming.get("delivering"):
                    incoming["delivering"] = True
                    logger.info(f"BOARDDAT: All parts received for {board_name}, delivering")
                    self._schedule_async(self._deliver_board_posts(key, incoming))

            return True

        except Exception as e:
            logger.error(f"Error handling BOARDDAT: {e}")
            logger.error(traceback.format_exc())
            return False

    def _handle_boarddlv(self, message: str, sender: str) -> bool:
        """Handle BOARDDLV — delivery confirmation from peer.

        Format: BOARDDLV|board_name|OK
        """
        try:
            parts = message.split("|")
            if len(parts) < 3:
                return False

            _, board_name, status = parts[:3]

            logger.info(f"BOARDDLV received: board={board_name} status={status} from {sender}")

            key = (board_name, sender)
            if key in self._pending_board_sync:
                del self._pending_board_sync[key]
                logger.info(f"BOARDDLV: Board sync for {board_name} delivered to {sender}")

            return True

        except Exception as e:
            logger.error(f"Error handling BOARDDLV: {e}")
            logger.error(traceback.format_exc())
            return False

    async def _send_board_chunks(self, board_name: str, chunks: list[str], dest_node: str):
        """Send board post data chunks to a peer."""
        import random
        total = len(chunks)
        max_retries = 3

        # Initial delay to avoid TX queue collision after BOARDACK
        await asyncio.sleep(self._protocol_delay)

        for i, chunk in enumerate(chunks, 1):
            boarddat = f"BOARDDAT|{board_name}|{i}/{total}|{chunk}"

            success = False
            for attempt in range(max_retries):
                acked, error = await self.mesh.send_dm_wait_ack(boarddat, dest_node, timeout=30.0)
                if acked:
                    logger.debug(f"BOARDDAT {board_name} chunk {i}/{total} ACKed")
                    success = True
                    break
                else:
                    logger.warning(f"BOARDDAT {board_name} chunk {i}/{total} failed: {error} (attempt {attempt + 1}/{max_retries})")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(random.uniform(3.0, 5.0))

            if not success:
                logger.error(f"BOARDDAT {board_name} chunk {i}/{total} failed after {max_retries} attempts to {dest_node}")
                return  # Abort — peer will time out and clean up

            if i < total:
                await asyncio.sleep(random.uniform(2.2, 2.6))

        logger.info(f"Sent {total} board chunks for {board_name} to {dest_node}, awaiting BOARDDLV")

    async def _deliver_board_posts(self, key: tuple, incoming: dict):
        """Reassemble and store board posts received from a peer.

        Multi-post payload: records joined by \\x1F, fields by \\x1E:
        uuid\\x1Eauthor\\x1Eorigin_bbs\\x1Etimestamp_us\\x1Esubject\\x1Ebody
        """
        try:
            # Reassemble data from chunks
            body_parts = []
            for i in range(1, incoming["num_parts"] + 1):
                if i in incoming["received_parts"]:
                    body_parts.append(incoming["received_parts"][i])
            raw_data = "".join(body_parts)

            board_name = incoming["board_name"]
            board_id = incoming["board_id"]
            sender_node_id = incoming["sender_node"]

            # Split into individual records
            records = raw_data.split("\x1f")

            from ..db.messages import MessageRepository
            from ..db.users import NodeRepository
            from ..db.models import MessageType

            msg_repo = MessageRepository(self.db)
            node_repo = NodeRepository(self.db)
            sender_node = node_repo.get_or_create_node(sender_node_id)

            stored = 0
            skipped = 0

            for record in records:
                fields = record.split("\x1e")
                if len(fields) < 6:
                    logger.warning(f"Skipping malformed board sync record ({len(fields)} fields)")
                    continue

                msg_uuid, author, origin_bbs, timestamp_us_str, subject, body = fields[:6]

                # Dedup check
                if msg_repo.message_exists(msg_uuid):
                    skipped += 1
                    continue

                # Encrypt with our master key
                board_key = self.bbs.master_key.key
                subject_enc = self.bbs.crypto.encrypt_string(subject, board_key) if subject else None
                body_enc = self.bbs.crypto.encrypt_string(body, board_key)

                expires_at_us = int((time.time() + 90 * 86400) * 1_000_000)

                msg_repo.create_message(
                    msg_type=MessageType.BULLETIN,
                    sender_node_id=sender_node.id,
                    board_id=board_id,
                    subject_enc=subject_enc,
                    body_enc=body_enc,
                    origin_bbs=origin_bbs,
                    message_uuid=msg_uuid,
                    expires_at_us=expires_at_us,
                    forwarded_to=f"{author}@{origin_bbs}",
                )
                stored += 1

            logger.info(f"Board sync delivery for {board_name}: stored={stored}, skipped={skipped}")

            # Send delivery confirmation
            dlv = f"BOARDDLV|{board_name}|OK"
            await self._send_protocol_dm(dlv, sender_node_id)
            logger.info(f"Sent BOARDDLV for {board_name} to {sender_node_id}")

            # Clean up
            if key in self._incoming_board_sync:
                del self._incoming_board_sync[key]

        except Exception as e:
            logger.error(f"Error delivering board posts for {key}: {e}")
            logger.error(traceback.format_exc())

    def notify_new_local_post(self, board_name: str):
        """Increment board sync counter. Actual sync happens in tick()."""
        self._board_sync_counters[board_name] = self._board_sync_counters.get(board_name, 0) + 1
        logger.info(f"Board sync counter: {board_name} = {self._board_sync_counters[board_name]}")

    async def _sync_boards_to_peers(self):
        """Flush batched board sync counters — send posts to all enabled peers."""
        if not self.config.enabled or not self._peers:
            self._board_sync_counters = {}
            self._last_board_sync_time = time.time()
            return

        from ..core.boards import BoardRepository
        from ..db.messages import MessageRepository
        from ..db.users import UserRepository

        board_repo = BoardRepository(self.db)
        msg_repo = MessageRepository(self.db)
        user_repo = UserRepository(self.db)
        my_callsign = self.bbs.config.bbs.callsign

        synced_boards = board_repo.get_synced_boards()
        boards_by_name = {b.name: b for b in synced_boards}

        for board_name, count in self._board_sync_counters.items():
            if count <= 0:
                continue

            board = boards_by_name.get(board_name)
            if not board:
                continue

            for node_id, peer in self._peers.items():
                if not peer.enabled:
                    continue

                # Get last_board_sync_us for this peer
                peer_row = self.db.fetchone(
                    "SELECT last_board_sync_us FROM bbs_peers WHERE node_id = ?",
                    (node_id,)
                )
                since_us = (peer_row[0] or 0) if peer_row else 0

                # Get our local posts since last sync
                messages = msg_repo.get_board_messages(
                    board.id, since_us=since_us, limit=1000
                )
                # Filter to only our local posts
                our_posts = [m for m in messages if m.origin_bbs == my_callsign]

                if not our_posts:
                    continue

                # Build multi-post payload
                records = []
                max_ts = since_us
                for msg in our_posts:
                    # Decrypt subject/body for transit
                    board_key = self.bbs.master_key.key
                    subject = self.bbs.crypto.decrypt_string(msg.subject_enc, board_key) if msg.subject_enc else ""
                    body = self.bbs.crypto.decrypt_string(msg.body_enc, board_key) if msg.body_enc else ""

                    author = user_repo.get_user_by_id(msg.sender_user_id)
                    author_name = author.username if author else "unknown"

                    # Record: uuid\x1Eauthor\x1Eorigin_bbs\x1Etimestamp_us\x1Esubject\x1Ebody
                    record = f"{msg.uuid}\x1e{author_name}\x1e{my_callsign}\x1e{msg.created_at_us}\x1e{subject}\x1e{body}"
                    records.append(record)

                    if msg.created_at_us > max_ts:
                        max_ts = msg.created_at_us

                payload = "\x1f".join(records)
                chunks = self._chunk_message(payload, 150)
                num_parts = len(chunks)

                # Store pending state
                key = (board_name, node_id)
                self._pending_board_sync[key] = {
                    "chunks": chunks,
                    "state": "pending",
                    "timestamp": time.time(),
                    "max_ts": max_ts,
                }

                # Send BOARDREQ|board_name|count|since_us
                boardreq = f"BOARDREQ|{board_name}|{len(our_posts)}|{since_us}"
                self._schedule_async(self._send_protocol_dm(boardreq, node_id))

                logger.info(f"Board sync: {board_name} sending {len(our_posts)} post(s) to {node_id}")

                # Update last_board_sync_us for this peer
                self.db.execute(
                    "UPDATE bbs_peers SET last_board_sync_us = ? WHERE node_id = ?",
                    (max_ts, node_id)
                )

        self._board_sync_counters = {}
        self._last_board_sync_time = time.time()
