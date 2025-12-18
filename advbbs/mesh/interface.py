"""
advBBS Meshtastic Interface

Handles connection to Meshtastic device and message pub/sub.
"""

import asyncio
import logging
import time
from typing import Callable, Optional

try:
    import meshtastic
    import meshtastic.serial_interface
    import meshtastic.tcp_interface
    from pubsub import pub
    MESHTASTIC_AVAILABLE = True
except ImportError:
    MESHTASTIC_AVAILABLE = False

from ..config import MeshtasticConfig

logger = logging.getLogger(__name__)


class MeshInterface:
    """
    Meshtastic connection interface.

    Supports Serial, TCP, and BLE connections.
    Uses pub/sub for async message handling.
    """

    # Broadcast address
    BROADCAST_ADDR = "^all"

    def __init__(self, config: MeshtasticConfig):
        """
        Initialize mesh interface with configuration.

        Args:
            config: Meshtastic connection settings
        """
        if not MESHTASTIC_AVAILABLE:
            raise ImportError(
                "Meshtastic library not installed. "
                "Install with: pip install meshtastic"
            )

        self.config = config
        self._interface = None
        self._connected = False
        self._message_handlers: list[Callable] = []
        self._node_id: Optional[str] = None

        # ACK tracking for diagnostics
        self._pending_acks: dict[int, dict] = {}  # requestId -> {destination, sent_at, text_preview}
        self._ack_stats = {"sent": 0, "acked": 0, "failed": 0}

        # Reply context tracking - maps message IDs to reply context
        # Used to handle Meshtastic native replies to BBS messages
        self._reply_contexts: dict[int, dict] = {}  # requestId -> {type, context_data, expires_at}

        # Send rate limiting - meshtasticd rate limits TEXT_MESSAGE_APP (portnum 1)
        # We need at least 3 seconds between sends to avoid drops
        self._last_send_time = 0
        self._send_min_interval = 3.5  # seconds between consecutive sends

    @property
    def connected(self) -> bool:
        """Check if connected to Meshtastic device."""
        return self._connected and self._interface is not None

    @property
    def node_id(self) -> Optional[str]:
        """Get our node ID."""
        return self._node_id

    def connect(self):
        """Establish connection to Meshtastic device."""
        logger.info(
            f"Connecting to Meshtastic via {self.config.connection_type}..."
        )

        try:
            if self.config.connection_type == "serial":
                self._interface = meshtastic.serial_interface.SerialInterface(
                    devPath=self.config.serial_port
                )
            elif self.config.connection_type == "tcp":
                self._interface = meshtastic.tcp_interface.TCPInterface(
                    hostname=self.config.tcp_host,
                    portNumber=self.config.tcp_port
                )
            elif self.config.connection_type == "ble":
                # BLE support requires additional setup
                raise NotImplementedError("BLE connection not yet implemented")
            else:
                raise ValueError(
                    f"Unknown connection type: {self.config.connection_type}"
                )

            # Get our node info
            my_info = self._interface.getMyNodeInfo()
            if my_info:
                self._node_id = my_info.get("user", {}).get("id")

            # Subscribe to message events
            pub.subscribe(self._on_receive, "meshtastic.receive.text")
            pub.subscribe(self._on_connection, "meshtastic.connection.established")
            pub.subscribe(self._on_disconnect, "meshtastic.connection.lost")

            # Subscribe to all packets to catch routing/ACK responses
            pub.subscribe(self._on_any_packet, "meshtastic.receive")

            self._connected = True
            logger.info(f"Connected to Meshtastic. Node ID: {self._node_id}")

        except Exception as e:
            logger.error(f"Failed to connect to Meshtastic: {e}")
            self._connected = False
            raise

    def disconnect(self):
        """Disconnect from Meshtastic device."""
        if self._interface:
            try:
                self._interface.close()
            except Exception as e:
                logger.warning(f"Error closing interface: {e}")
            finally:
                self._interface = None
                self._connected = False
                logger.info("Disconnected from Meshtastic")

    def on_message(self, handler: Callable[[dict], None]):
        """
        Register a message handler.

        Handler receives packet dict with keys:
        - fromId: Sender node ID
        - toId: Destination node ID
        - text: Message text
        - channel: Channel index
        """
        self._message_handlers.append(handler)

    def _on_receive(self, packet, interface):
        """Internal handler for received text messages."""
        logger.debug(f"Received packet: {packet}")

        if not packet:
            return

        # Extract relevant fields
        decoded = packet.get("decoded", {})
        message_data = {
            "fromId": packet.get("fromId", ""),
            "toId": packet.get("toId", ""),
            "text": decoded.get("text", ""),
            "channel": packet.get("channel", 0),
            "hopLimit": packet.get("hopLimit"),
            "rxTime": packet.get("rxTime"),
            "rxSnr": packet.get("rxSnr"),
            "rxRssi": packet.get("rxRssi"),
            "replyId": decoded.get("replyId"),  # Meshtastic reply message ID
        }

        # Redact sensitive commands (login, register, passwd) from logs
        log_text = message_data['text'][:50] if message_data['text'] else '(empty)'
        text_lower = message_data['text'].lower() if message_data['text'] else ''
        if text_lower.startswith(('!login ', '!register ', '!passwd ', '!password ')):
            # Only show command name, hide credentials
            cmd_end = text_lower.find(' ', 1)
            if cmd_end > 0:
                log_text = message_data['text'][:cmd_end] + ' [REDACTED]'
        logger.info(f"Message from {message_data['fromId']} (ch{message_data['channel']}): {log_text}")

        # DEBUG: Log full MAILREQ/sync protocol messages
        if message_data['text'] and message_data['text'].startswith(('MAILREQ|', 'MAILACK|', 'MAILNAK|', 'MAILDAT|', 'MAILDLV|', 'advBBS|')):
            logger.info(f"SYNC PROTOCOL FULL TEXT ({len(message_data['text'])} chars): {message_data['text']}")

        # Call all registered handlers
        for handler in self._message_handlers:
            try:
                handler(message_data)
            except Exception as e:
                logger.error(f"Error in message handler: {e}")

    def _on_connection(self, interface, topic=pub.AUTO_TOPIC):
        """Handle connection established event."""
        self._connected = True
        self._reconnect_attempts = 0
        logger.info("Meshtastic connection established")

    def _on_disconnect(self, interface, topic=pub.AUTO_TOPIC):
        """Handle disconnection event."""
        self._connected = False
        logger.warning("Meshtastic connection lost")
        # Trigger reconnect in background
        self._schedule_reconnect()

    def _on_any_packet(self, packet, interface):
        """Handle all incoming packets, looking for ACK/routing responses."""
        if not packet:
            return

        # Check if this is a routing packet (contains ACK/NAK info)
        decoded = packet.get("decoded", {})
        portnum = decoded.get("portnum")

        # Log all portnums to debug
        if portnum:
            logger.debug(f"Packet portnum: {portnum}, from: {packet.get('fromId')}")

        # Routing packets have portnum ROUTING_APP
        if portnum not in ("ROUTING_APP", "routing"):
            return

        request_id = packet.get("requestId")
        from_id = packet.get("fromId", "unknown")

        # Check if this is an ACK we're tracking
        if request_id and request_id in self._pending_acks:
            pending = self._pending_acks.pop(request_id)
            elapsed_ms = (time.time() - pending["sent_at"]) * 1000

            # Check if ACK or NAK (routing error)
            routing = decoded.get("routing", {})
            error_reason = routing.get("errorReason")

            if error_reason and error_reason != "NONE":
                # NAK - delivery failed
                self._ack_stats["failed"] += 1
                logger.warning(
                    f"MESH NAK to {pending['destination']}: {error_reason} ({elapsed_ms:.0f}ms)"
                )
            else:
                # ACK - delivery confirmed
                self._ack_stats["acked"] += 1
                logger.info(
                    f"MESH ACK from {pending['destination']} ({elapsed_ms:.0f}ms)"
                )
        elif request_id:
            # Log untracked ACKs for debugging
            routing = decoded.get("routing", {})
            error_reason = routing.get("errorReason")
            if error_reason and error_reason != "NONE":
                logger.debug(f"Untracked NAK from {from_id}: {error_reason}")
            else:
                logger.debug(f"Untracked ACK from {from_id}")

    def _schedule_reconnect(self):
        """Schedule a reconnection attempt."""
        import threading
        if hasattr(self, '_reconnecting') and self._reconnecting:
            return
        self._reconnecting = True
        thread = threading.Thread(target=self._reconnect_loop, daemon=True)
        thread.start()

    def _reconnect_loop(self):
        """Attempt to reconnect with exponential backoff."""
        max_attempts = 10
        base_delay = 5  # seconds

        for attempt in range(max_attempts):
            if self._connected:
                self._reconnecting = False
                return

            delay = min(base_delay * (2 ** attempt), 300)  # Cap at 5 minutes
            logger.info(f"Reconnect attempt {attempt + 1}/{max_attempts} in {delay}s...")
            time.sleep(delay)

            try:
                self._do_reconnect()
                if self._connected:
                    logger.info("Reconnected to Meshtastic successfully")
                    self._reconnecting = False
                    return
            except Exception as e:
                logger.error(f"Reconnect failed: {e}")

        logger.error("Max reconnect attempts reached. Manual restart required.")
        self._reconnecting = False

    def _do_reconnect(self):
        """Perform the actual reconnection."""
        # Close old interface if it exists
        if self._interface:
            try:
                self._interface.close()
            except Exception:
                pass
            self._interface = None

        # Reconnect based on connection type
        if self.config.connection_type == "serial":
            self._interface = meshtastic.serial_interface.SerialInterface(
                devPath=self.config.serial_port
            )
        elif self.config.connection_type == "tcp":
            self._interface = meshtastic.tcp_interface.TCPInterface(
                hostname=self.config.tcp_host,
                portNumber=self.config.tcp_port
            )

        # Get our node info
        my_info = self._interface.getMyNodeInfo()
        if my_info:
            self._node_id = my_info.get("user", {}).get("id")
            self._connected = True

    async def send_text(
        self,
        text: str,
        destination: str,
        channel: int = 0,
        want_ack: bool = False
    ) -> bool:
        """
        Send text message.

        Args:
            text: Message text
            destination: Destination node ID or "^all" for broadcast
            channel: Channel index
            want_ack: Request acknowledgment

        Returns:
            True if message was sent (not necessarily delivered)
        """
        if not self.connected:
            logger.error("Cannot send: not connected")
            return False

        try:
            # Rate limit sends to avoid meshtasticd drops
            now = time.time()
            elapsed = now - self._last_send_time
            if elapsed < self._send_min_interval:
                wait_time = self._send_min_interval - elapsed
                logger.debug(f"Rate limiting: waiting {wait_time:.1f}s before send")
                time.sleep(wait_time)
            self._last_send_time = time.time()

            logger.info(f"Sending to {destination} on channel {channel}: {text[:50]}...")
            # DEBUG: Log full sync protocol messages being sent
            if text.startswith(('MAILREQ|', 'MAILACK|', 'MAILNAK|', 'MAILDAT|', 'MAILDLV|', 'advBBS|')):
                logger.info(f"SYNC PROTOCOL SENDING ({len(text)} chars): {text}")
            result = self._interface.sendText(
                text=text,
                destinationId=destination,
                channelIndex=channel,
                wantAck=want_ack
            )

            # Track for ACK if requested
            if want_ack and result:
                request_id = getattr(result, 'id', None)
                logger.debug(f"sendText result: {result}, id: {request_id}")
                if request_id:
                    self._pending_acks[request_id] = {
                        "destination": destination,
                        "sent_at": time.time(),
                        "text_preview": text[:30]
                    }
                    self._ack_stats["sent"] += 1
                    logger.info(f"Tracking ACK for msg {request_id} to {destination}")

            logger.info(f"Sent successfully to {destination}")
            # Return the message ID so callers can set reply context
            return request_id if want_ack and request_id else True
        except Exception as e:
            logger.error(f"Failed to send message to {destination}: {e}")
            return False

    def set_reply_context(self, message_id: int, context_type: str, context_data: dict, ttl_seconds: int = 300):
        """
        Set reply context for a sent message.

        When a Meshtastic reply comes in with this message_id as replyId,
        the context will be used to handle the reply appropriately.

        Args:
            message_id: The message ID returned from send_text
            context_type: Type of context (e.g., "mail_read", "board_view")
            context_data: Data needed to handle the reply
            ttl_seconds: How long the context is valid (default 5 minutes)
        """
        if not message_id or not isinstance(message_id, int):
            return

        self._reply_contexts[message_id] = {
            "type": context_type,
            "data": context_data,
            "expires_at": time.time() + ttl_seconds
        }
        logger.debug(f"Set reply context for msg {message_id}: {context_type}")

        # Clean up expired contexts
        self._cleanup_expired_contexts()

    def get_reply_context(self, reply_id: int) -> Optional[dict]:
        """
        Get and consume reply context for a message ID.

        Args:
            reply_id: The replyId from an incoming message

        Returns:
            Context dict with 'type' and 'data', or None if not found/expired
        """
        if not reply_id or reply_id not in self._reply_contexts:
            return None

        context = self._reply_contexts.get(reply_id)
        if not context:
            return None

        # Check if expired
        if time.time() > context.get("expires_at", 0):
            del self._reply_contexts[reply_id]
            return None

        # Don't delete - allow multiple replies to same message
        return {"type": context["type"], "data": context["data"]}

    def _cleanup_expired_contexts(self):
        """Remove expired reply contexts."""
        now = time.time()
        expired = [mid for mid, ctx in self._reply_contexts.items()
                   if now > ctx.get("expires_at", 0)]
        for mid in expired:
            del self._reply_contexts[mid]

    async def send_dm(self, text: str, destination: str, want_ack: bool = True):
        """Send direct message to specific node."""
        return await self.send_text(
            text,
            destination,
            channel=0,  # DMs use channel 0
            want_ack=want_ack
        )

    async def send_dm_wait_ack(
        self,
        text: str,
        destination: str,
        timeout: float = 30.0
    ) -> tuple[bool, str]:
        """
        Send DM and wait for mesh-level ACK.

        Args:
            text: Message text
            destination: Destination node ID
            timeout: How long to wait for ACK (seconds)

        Returns:
            (success, error_reason) - success=True if ACKed, False if NAK/timeout
        """
        if not self.connected:
            return False, "NOT_CONNECTED"

        try:
            # Send with ACK request
            result = self._interface.sendText(
                text=text,
                destinationId=destination,
                channelIndex=0,
                wantAck=True
            )

            request_id = getattr(result, "id", None)
            if not request_id:
                return False, "NO_REQUEST_ID"

            # Log for sync protocol messages
            if text.startswith(("MAILREQ|", "MAILACK|", "MAILNAK|", "MAILDAT|", "MAILDLV|", "advBBS|")):
                logger.info(f"SYNC PROTOCOL SENDING ({len(text)} chars): {text}")

            logger.debug(f"Waiting for ACK on msg {request_id} to {destination}")

            # Create an event to wait on
            ack_event = asyncio.Event()
            ack_result = {"success": None, "error": None}

            # Store in pending with callback info
            self._pending_acks[request_id] = {
                "destination": destination,
                "sent_at": time.time(),
                "text_preview": text[:30],
                "event": ack_event,
                "result": ack_result,
            }
            self._ack_stats["sent"] += 1

            # Wait for ACK with timeout
            try:
                await asyncio.wait_for(ack_event.wait(), timeout=timeout)
                if ack_result["success"]:
                    return True, ""
                else:
                    return False, ack_result.get("error", "UNKNOWN")
            except asyncio.TimeoutError:
                # Clean up
                self._pending_acks.pop(request_id, None)
                logger.warning(f"ACK timeout for msg {request_id} to {destination}")
                return False, "TIMEOUT"

        except Exception as e:
            logger.error(f"Failed to send message to {destination}: {e}")
            return False, str(e)

    async def send_broadcast(self, text: str, channel: int = 0) -> bool:
        """Send broadcast message to channel."""
        return await self.send_text(
            text,
            self.BROADCAST_ADDR,
            channel=channel,
            want_ack=False
        )

    def get_node_info(self, node_id: str) -> Optional[dict]:
        """Get information about a specific node."""
        if not self.connected:
            return None

        try:
            nodes = self._interface.nodes
            return nodes.get(node_id)
        except Exception:
            return None

    def get_all_nodes(self) -> dict:
        """Get all known nodes."""
        if not self.connected:
            return {}

        try:
            return self._interface.nodes or {}
        except Exception:
            return {}

    def get_ack_stats(self) -> dict:
        """Get ACK statistics for diagnostics."""
        # Clean up old pending ACKs (older than 2 minutes)
        cutoff = time.time() - 120
        expired = [k for k, v in self._pending_acks.items() if v["sent_at"] < cutoff]
        for k in expired:
            self._pending_acks.pop(k, None)

        return {
            "sent": self._ack_stats["sent"],
            "acked": self._ack_stats["acked"],
            "failed": self._ack_stats["failed"],
            "pending": len(self._pending_acks),
            "ack_rate": (
                f"{self._ack_stats['acked'] / self._ack_stats['sent'] * 100:.1f}%"
                if self._ack_stats["sent"] > 0 else "N/A"
            )
        }


class MockMeshInterface(MeshInterface):
    """
    Mock Meshtastic interface for testing.

    Useful for development without actual hardware.
    """

    def __init__(self, config: MeshtasticConfig):
        self.config = config
        self._connected = False
        self._message_handlers: list[Callable] = []
        self._node_id = "!mock1234"
        self._sent_messages: list[dict] = []

    def connect(self):
        """Simulate connection."""
        self._connected = True
        logger.info("Mock Meshtastic interface connected")

    def disconnect(self):
        """Simulate disconnection."""
        self._connected = False
        logger.info("Mock Meshtastic interface disconnected")

    async def send_text(
        self,
        text: str,
        destination: str,
        channel: int = 0,
        want_ack: bool = False
    ) -> bool:
        """Record sent message for testing."""
        if not self._connected:
            return False

        self._sent_messages.append({
            "text": text,
            "destination": destination,
            "channel": channel,
            "want_ack": want_ack,
            "timestamp": time.time()
        })

        logger.debug(f"Mock sent to {destination}: {text}")
        return True

    def simulate_receive(self, from_id: str, text: str, channel: int = 0):
        """Simulate receiving a message (for testing)."""
        message_data = {
            "fromId": from_id,
            "toId": self._node_id,
            "text": text,
            "channel": channel,
        }

        for handler in self._message_handlers:
            handler(message_data)

    def get_sent_messages(self) -> list[dict]:
        """Get list of sent messages for verification."""
        return self._sent_messages

    def clear_sent_messages(self):
        """Clear sent message history."""
        self._sent_messages = []
