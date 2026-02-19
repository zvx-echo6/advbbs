"""
advBBS Private Mail System

Handles encrypted mail storage, delivery, and retry logic.
Per architecture: 3 attempts (30s ACK timeout), 60s/120s backoff, then forward.
"""

import time
import logging
import asyncio
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING
from zoneinfo import ZoneInfo

from ..db.models import Message, MessageType
from ..db.messages import MessageRepository
from ..db.users import UserRepository, NodeRepository, UserNodeRepository
from .crypto import CryptoManager, MasterKeyManager, EncryptedData

if TYPE_CHECKING:
    from .bbs import advBBS

logger = logging.getLogger(__name__)


# Delivery configuration (from architecture)
MAX_DELIVERY_ATTEMPTS = 3
ACK_TIMEOUT_SECS = 30
BACKOFF_MULTIPLIERS = [1, 2, 4]  # 30s, 60s, 120s
MAX_HOP_COUNT = 3
MESSAGE_EXPIRY_DAYS = 30


@dataclass
class DeliveryResult:
    """Result of a mail delivery attempt."""
    success: bool
    acked: bool = False
    forwarded_to: Optional[str] = None
    error: Optional[str] = None


class MailService:
    """
    Private mail service for advBBS.

    Features:
    - End-to-end encryption using recipient's key
    - Delivery retry with exponential backoff
    - Forwarding to other BBS nodes when delivery fails
    - Multi-node delivery (try all recipient's nodes)
    """

    def __init__(self, bbs: "advBBS"):
        self.bbs = bbs
        self.crypto = bbs.crypto
        self.master_key = bbs.master_key
        self._delivery_task: Optional[asyncio.Task] = None
        self._pending_acks: dict[str, asyncio.Event] = {}

    def start_delivery_worker(self):
        """Start the background delivery worker."""
        if self._delivery_task is None:
            self._delivery_task = asyncio.create_task(self._delivery_loop())
            logger.info("Mail delivery worker started")

    def stop_delivery_worker(self):
        """Stop the background delivery worker."""
        if self._delivery_task:
            self._delivery_task.cancel()
            self._delivery_task = None
            logger.info("Mail delivery worker stopped")

    async def _delivery_loop(self):
        """Background loop that processes pending deliveries."""
        while True:
            try:
                await self._process_pending_deliveries()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Delivery worker error: {e}")

            # Check every 10 seconds
            await asyncio.sleep(10)

    async def _process_pending_deliveries(self):
        """Process all pending message deliveries."""
        msg_repo = MessageRepository(self.bbs.db)
        pending = msg_repo.get_pending_deliveries(limit=10)

        for message in pending:
            # Check if enough time has passed since last attempt
            if message.last_attempt_us:
                elapsed_secs = (time.time() * 1_000_000 - message.last_attempt_us) / 1_000_000
                backoff_idx = min(message.delivery_attempts, len(BACKOFF_MULTIPLIERS) - 1)
                required_wait = ACK_TIMEOUT_SECS * BACKOFF_MULTIPLIERS[backoff_idx]

                if elapsed_secs < required_wait:
                    continue

            # Attempt delivery
            result = await self._attempt_delivery(message)

            if result.success:
                msg_repo.mark_as_delivered(message.id)
                logger.info(f"Mail {message.uuid[:8]} delivered")
            elif message.delivery_attempts >= MAX_DELIVERY_ATTEMPTS - 1:
                # Try forwarding to another BBS
                if message.hop_count < MAX_HOP_COUNT:
                    await self._forward_message(message)
                else:
                    logger.warning(f"Mail {message.uuid[:8]} exceeded max hops, giving up")
            else:
                msg_repo.update_delivery_attempt(message.id)

    async def _attempt_delivery(self, message: Message) -> DeliveryResult:
        """Attempt to deliver a message to the recipient."""
        try:
            # Check if this is remote mail (has addressing in forwarded_to)
            if message.forwarded_to and ">" in message.forwarded_to:
                # Remote mail - forward immediately via sync manager
                await self._forward_message(message)
                return DeliveryResult(success=True, acked=False)

            # Get recipient's nodes
            user_node_repo = UserNodeRepository(self.bbs.db)
            node_repo = NodeRepository(self.bbs.db)

            if message.recipient_user_id:
                # User-addressed mail: try all their nodes
                recipient_nodes = user_node_repo.get_user_nodes(message.recipient_user_id)
            elif message.recipient_node_id:
                # Node-addressed mail: direct delivery
                node = node_repo.get_node_by_id(message.recipient_node_id)
                recipient_nodes = [node.node_id] if node else []
            else:
                return DeliveryResult(success=False, error="No recipient")

            if not recipient_nodes:
                return DeliveryResult(success=False, error="No recipient nodes")

            # Try each node
            for node_id in recipient_nodes:
                result = await self._send_to_node(message, node_id)
                if result.success:
                    return result

            return DeliveryResult(success=False, error="All delivery attempts failed")

        except Exception as e:
            logger.error(f"Delivery attempt error: {e}")
            return DeliveryResult(success=False, error=str(e))

    async def _send_to_node(self, message: Message, node_id: str) -> DeliveryResult:
        """Send a message to a specific node."""
        try:
            # Build delivery notification
            # Format: MAIL|uuid|from_user|subject_preview
            user_repo = UserRepository(self.bbs.db)
            sender = user_repo.get_user_by_id(message.sender_user_id) if message.sender_user_id else None
            sender_name = sender.username if sender else "ERR"

            notification = f"[MAIL] From: {sender_name}. DM !mail to check."

            # Send via mesh (fire-and-forget, no ACK wait)
            if self.bbs.mesh:
                # Delay to avoid TX queue collision with command response
                # The response to sender goes out first, we need to wait for it to clear
                await asyncio.sleep(5.0)
                logger.info(f"Sending mail notification to {node_id}: {notification}")
                await self.bbs.mesh.send_dm(notification, node_id)
                # Mark as delivered immediately - user will see it when they check mail
                return DeliveryResult(success=True, acked=False)
            else:
                # No mesh connection, queue for later
                logger.warning("No mesh connection for mail delivery")
                return DeliveryResult(success=False, error="No mesh connection")

        except Exception as e:
            logger.error(f"Send to node error: {e}")
            return DeliveryResult(success=False, error=str(e))

    async def _forward_message(self, message: Message):
        """Forward message to another BBS node for delivery."""
        # Get peer BBS nodes from sync manager
        if not self.bbs.sync_manager:
            logger.warning(f"No sync manager available to forward mail {message.uuid[:8]}")
            return

        peers = self.bbs.sync_manager.list_peers()
        if not peers:
            logger.warning(f"No peers available to forward mail {message.uuid[:8]}")
            return

        msg_repo = MessageRepository(self.bbs.db)

        for peer in peers:
            peer_node = peer["node_id"]
            peer_name = peer["name"]

            if message.forwarded_to and peer_node in message.forwarded_to:
                continue  # Already tried this peer

            logger.info(f"Forwarding mail {message.uuid[:8]} to {peer_name}")

            # Update message tracking
            msg_repo.update_delivery_attempt(message.id, forwarded_to=peer_node)

            # Send forward request via sync protocol
            await self.bbs.sync_manager.forward_mail(message, peer_node)

            break  # Only try one peer at a time

    def handle_ack(self, message_uuid: str, from_node: str):
        """Handle incoming ACK for a delivered message."""
        ack_key = f"mail_ack_{message_uuid}_{from_node}"
        if ack_key in self._pending_acks:
            self._pending_acks[ack_key].set()

    def compose_mail(
        self,
        sender_user_id: int,
        sender_node_id: int,
        recipient_username: str,
        body: str,
        subject: Optional[str] = None
    ) -> tuple[Optional[Message], str]:
        """
        Compose and store an encrypted mail message.

        Returns:
            (Message, "") on success
            (None, error_message) on failure
        """
        user_repo = UserRepository(self.bbs.db)
        node_repo = NodeRepository(self.bbs.db)
        msg_repo = MessageRepository(self.bbs.db)

        # Look up recipient
        recipient = user_repo.get_user_by_username(recipient_username)
        if not recipient:
            return None, f"User '{recipient_username}' not found."

        if recipient.is_banned:
            return None, f"Cannot send mail to banned user."

        if recipient.id == sender_user_id:
            return None, "Cannot send mail to yourself."

        # Get sender for encryption
        sender = user_repo.get_user_by_id(sender_user_id)
        if not sender:
            return None, "Sender not found."

        try:
            # Decrypt recipient's encryption key using master key
            recipient_key = self.master_key.decrypt_user_key(recipient.encryption_key)

            # Encrypt body with recipient's key
            # AAD includes sender + timestamp for integrity
            aad = f"{sender.username}|{int(time.time())}".encode()
            body_enc = self.crypto.encrypt_string(body, recipient_key, aad)

            # Encrypt subject if provided
            subject_enc = None
            if subject:
                subject_enc = self.crypto.encrypt_string(subject, recipient_key, aad)

            # Calculate expiration (30 days)
            expires_at_us = int((time.time() + MESSAGE_EXPIRY_DAYS * 86400) * 1_000_000)

            # Get sender node DB ID
            sender_node = node_repo.get_node_by_id(sender_node_id)
            if not sender_node:
                sender_node = node_repo.get_or_create_node(sender_node_id)

            # Create message
            message = msg_repo.create_message(
                msg_type=MessageType.MAIL,
                sender_node_id=sender_node.id,
                sender_user_id=sender_user_id,
                recipient_user_id=recipient.id,
                body_enc=body_enc,
                subject_enc=subject_enc,
                origin_bbs=self.bbs.config.bbs.callsign,
                expires_at_us=expires_at_us
            )

            logger.info(f"Mail composed: {sender.username} -> {recipient.username}")
            return message, ""

        except Exception as e:
            logger.error(f"Mail compose error: {e}\n{traceback.format_exc()}")
            return None, "Failed to compose mail."

    def read_mail(
        self,
        user_id: int,
        message_id: int
    ) -> tuple[Optional[dict], str]:
        """
        Read and decrypt a mail message.

        Returns:
            (mail_dict, "") on success
            (None, error_message) on failure
        """
        user_repo = UserRepository(self.bbs.db)
        msg_repo = MessageRepository(self.bbs.db)

        # Get message
        message = msg_repo.get_message_by_id(message_id)
        if not message:
            return None, "Message not found."

        if message.recipient_user_id != user_id:
            return None, "This message is not addressed to you."

        # Get user for decryption
        user = user_repo.get_user_by_id(user_id)
        if not user:
            return None, "User not found."

        try:
            # Check if this is remote mail (stored as plaintext)
            is_remote = msg_repo.is_remote_mail(message_id)

            if is_remote:
                # Remote mail - body is plaintext, sender info in forwarded_to
                body = message.body_enc.decode('utf-8') if isinstance(message.body_enc, bytes) else message.body_enc
                # Parse sender from forwarded_to (format: user@bbs)
                sender_name = message.forwarded_to or "ERR"
                from_bbs = None
                if "@" in sender_name:
                    sender_name, from_bbs = sender_name.split("@", 1)
                subject = "(remote mail)"
            else:
                # Local mail - decrypt normally
                user_key = self.master_key.decrypt_user_key(user.encryption_key)

                # Get sender info for AAD reconstruction
                sender = user_repo.get_user_by_id(message.sender_user_id) if message.sender_user_id else None
                sender_name = sender.username if sender else "ERR"
                from_bbs = None

                # Use message creation time as the AAD timestamp
                # The encrypt used int(time.time()) which matches created_at_us / 1_000_000
                msg_timestamp = int(message.created_at_us / 1_000_000)
                body = self._try_decrypt(message.body_enc, user_key, sender_name, msg_timestamp)
                if body is None:
                    return None, "Failed to decrypt message."

                subject = None
                if message.subject_enc:
                    subject = self._try_decrypt(message.subject_enc, user_key, sender_name, msg_timestamp)

            # Mark as read
            msg_repo.mark_as_read(message.id)

            # Format timestamp with configured timezone
            try:
                tz = ZoneInfo(self.bbs.config.bbs.timezone)
            except Exception:
                tz = ZoneInfo("UTC")
            msg_datetime = datetime.fromtimestamp(message.created_at_us / 1_000_000, tz=tz)
            created_time = msg_datetime.strftime("%Y-%m-%d %H:%M")

            return {
                "id": message.id,
                "from": sender_name,
                "from_bbs": from_bbs,  # For remote mail, shows origin BBS
                "subject": subject or "(no subject)",
                "body": body,
                "date": created_time,
                "read": message.read_at_us is not None
            }, ""

        except Exception as e:
            logger.error(f"Mail read error: {e}")
            logger.error(traceback.format_exc())
            return None, "Failed to read message."

    def _try_decrypt(self, encrypted_data: bytes, key: bytes, sender_name: str, msg_timestamp: int = None) -> Optional[str]:
        """Try to decrypt data with various AAD possibilities."""
        # Try without AAD first (for legacy/external messages)
        try:
            return self.crypto.decrypt_string(encrypted_data, key, None)
        except Exception:
            pass

        # If we have the message timestamp, try that first (and nearby seconds)
        if msg_timestamp:
            # Try exact timestamp and +/- 2 seconds (for timing variance)
            for ts_offset in range(-2, 3):
                try:
                    aad = f"{sender_name}|{msg_timestamp + ts_offset}".encode()
                    return self.crypto.decrypt_string(encrypted_data, key, aad)
                except Exception:
                    continue

        # Fallback: try recent timestamps (within last hour) for legacy messages
        now = int(time.time())
        for ts_offset in range(3600):  # Try each second for an hour
            try:
                aad = f"{sender_name}|{now - ts_offset}".encode()
                return self.crypto.decrypt_string(encrypted_data, key, aad)
            except Exception:
                continue

        return None

    def list_mail(
        self,
        user_id: int,
        unread_only: bool = False,
        limit: int = 10,
        offset: int = 0
    ) -> list[dict]:
        """
        List mail messages for a user (headers only, decrypts subject).

        Returns list of mail summaries.
        """
        user_repo = UserRepository(self.bbs.db)
        msg_repo = MessageRepository(self.bbs.db)

        messages = msg_repo.get_user_mail(user_id, unread_only, limit, offset)

        # Get user key for decrypting subjects
        user = user_repo.get_user_by_id(user_id)
        user_key = None
        if user:
            try:
                user_key = self.master_key.decrypt_user_key(user.encryption_key)
            except Exception:
                pass

        # Get configured timezone
        try:
            tz = ZoneInfo(self.bbs.config.bbs.timezone)
        except Exception:
            tz = ZoneInfo("UTC")

        result = []
        for msg in messages:
            # Check if this is remote mail
            is_remote = msg_repo.is_remote_mail(msg.id)

            if is_remote:
                # Remote mail - parse sender from forwarded_to (format: user@bbs)
                sender_info = msg.forwarded_to or "ERR"
                if "@" in sender_info:
                    sender_name, from_bbs = sender_info.split("@", 1)
                    sender_name = f"{sender_name}@{from_bbs[:4]}"  # Truncate BBS name
                else:
                    sender_name = sender_info
                subject = None  # Remote mail doesn't have encrypted subject
            else:
                # Local mail - get sender from user table
                sender = user_repo.get_user_by_id(msg.sender_user_id) if msg.sender_user_id else None
                sender_name = sender.username if sender else "ERR"

                # Try to decrypt subject if present
                subject = None
                if msg.subject_enc and user_key:
                    msg_timestamp = int(msg.created_at_us / 1_000_000)
                    subject = self._try_decrypt(msg.subject_enc, user_key, sender_name, msg_timestamp)

            # Format timestamp with configured timezone
            msg_datetime = datetime.fromtimestamp(msg.created_at_us / 1_000_000, tz=tz)
            created_time = msg_datetime.strftime("%m/%d %H:%M")

            result.append({
                "id": msg.id,
                "from": sender_name,
                "date": created_time,
                "subject": subject,
                "read": msg.read_at_us is not None,
                "new": msg.read_at_us is None
            })

        return result

    def delete_mail(self, user_id: int, message_id: int) -> tuple[bool, str]:
        """
        Delete a mail message.

        Returns:
            (True, "") on success
            (False, error_message) on failure
        """
        msg_repo = MessageRepository(self.bbs.db)

        # Verify ownership
        message = msg_repo.get_message_by_id(message_id)
        if not message:
            return False, "Message not found."

        if message.recipient_user_id != user_id and message.sender_user_id != user_id:
            return False, "You don't have permission to delete this message."

        if msg_repo.delete_message(message_id):
            return True, ""
        return False, "Failed to delete message."

    def get_inbox_summary(self, user_id: int) -> dict:
        """Get inbox summary (counts)."""
        msg_repo = MessageRepository(self.bbs.db)

        unread = msg_repo.count_unread_mail(user_id)
        total = len(msg_repo.get_user_mail(user_id, limit=1000))

        return {
            "unread": unread,
            "total": total
        }
