"""
advBBS Message Chunking

Handles splitting messages into 150-byte chunks for LoRa transmission.
"""

import re
import time
import logging
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Chunk configuration
MAX_CHUNK_SIZE = 150  # bytes
HEADER_RESERVE = 8    # "[xx/xx] " = 8 bytes max
CONTENT_SIZE = MAX_CHUNK_SIZE - HEADER_RESERVE  # 142 bytes

# Timeout configuration
CHUNK_TIMEOUT = 120   # 2 minutes between chunks
TOTAL_TIMEOUT = 600   # 10 minutes absolute max


@dataclass
class PendingMessage:
    """Tracks a message being reassembled from chunks."""
    sender: str
    total: int
    chunks: list[str]
    received: set[int]
    created: float
    last_chunk: float


# Global pending chunks tracker
_pending_chunks: dict[str, PendingMessage] = {}


def chunk_message(message: str) -> list[str]:
    """
    Split message into 150-byte chunks with sequence headers.

    Args:
        message: Message to chunk

    Returns:
        List of chunks with [seq/total] headers if needed
    """
    encoded = message.encode('utf-8')

    # If it fits in one chunk, return as-is
    if len(encoded) <= MAX_CHUNK_SIZE:
        return [message]

    # Split into content-sized pieces
    chunks = []
    for i in range(0, len(encoded), CONTENT_SIZE):
        chunk_bytes = encoded[i:i + CONTENT_SIZE]
        chunks.append(chunk_bytes.decode('utf-8', errors='replace'))

    total = len(chunks)

    # Add headers
    result = []
    for i, chunk in enumerate(chunks, 1):
        header = f"[{i}/{total}] "
        result.append(header + chunk)

    return result


def reassemble_message(chunk: str, sender: str) -> Optional[str]:
    """
    Reassemble chunked messages.

    Args:
        chunk: Received chunk (may or may not have header)
        sender: Sender node ID

    Returns:
        Complete message if all chunks received, None otherwise
    """
    # Check for chunk header pattern
    match = re.match(r'\[(\d+)/(\d+)\] (.+)', chunk, re.DOTALL)

    if not match:
        # Not a chunked message, return as-is
        return chunk

    seq = int(match.group(1))
    total = int(match.group(2))
    content = match.group(3)

    key = f"{sender}:{total}"
    now = time.time()

    # Create or update pending message
    if key not in _pending_chunks:
        _pending_chunks[key] = PendingMessage(
            sender=sender,
            total=total,
            chunks=[''] * total,
            received=set(),
            created=now,
            last_chunk=now
        )

    pending = _pending_chunks[key]

    # Update timestamps and store chunk
    pending.last_chunk = now
    pending.chunks[seq - 1] = content
    pending.received.add(seq)

    # Check if complete
    if len(pending.received) == total:
        full_message = ''.join(pending.chunks)
        del _pending_chunks[key]
        logger.debug(f"Reassembled {total}-chunk message from {sender}")
        return full_message

    logger.debug(f"Chunk {seq}/{total} from {sender}, waiting for more")
    return None


def cleanup_expired_chunks():
    """
    Remove stale pending chunks.

    Should be called periodically (e.g., every minute).
    """
    now = time.time()
    expired = []

    for key, pending in _pending_chunks.items():
        # Expired if no chunk for 2 min OR total time > 10 min
        chunk_stale = (now - pending.last_chunk) > CHUNK_TIMEOUT
        total_exceeded = (now - pending.created) > TOTAL_TIMEOUT

        if chunk_stale or total_exceeded:
            expired.append(key)

    for key in expired:
        pending = _pending_chunks[key]
        logger.debug(
            f"Expired incomplete message from {pending.sender}: "
            f"{len(pending.received)}/{pending.total} chunks"
        )
        del _pending_chunks[key]

    if expired:
        logger.info(f"Cleaned up {len(expired)} expired chunk assemblies")


def get_pending_count() -> int:
    """Return number of pending message assemblies."""
    return len(_pending_chunks)
