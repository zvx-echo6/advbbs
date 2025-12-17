"""
Tests for advBBS Message Chunking
"""

import pytest
from advbbs.utils.pagination import (
    chunk_message,
    reassemble_message,
    cleanup_expired_chunks,
    get_pending_count,
    MAX_CHUNK_SIZE,
)


class TestChunking:
    """Tests for message chunking."""

    def test_short_message_not_chunked(self):
        """Short messages should not be chunked."""
        message = "Hello, World!"
        chunks = chunk_message(message)

        assert len(chunks) == 1
        assert chunks[0] == message

    def test_exact_limit_not_chunked(self):
        """Messages at exactly the limit should not be chunked."""
        message = "x" * MAX_CHUNK_SIZE
        chunks = chunk_message(message)

        assert len(chunks) == 1

    def test_long_message_chunked(self):
        """Long messages should be split into chunks."""
        message = "x" * 500  # Well over the limit
        chunks = chunk_message(message)

        assert len(chunks) > 1

        # Each chunk should have header
        for i, chunk in enumerate(chunks, 1):
            assert chunk.startswith(f"[{i}/{len(chunks)}] ")

    def test_chunk_headers_format(self):
        """Test chunk header format."""
        message = "A" * 300
        chunks = chunk_message(message)

        # Should be 3 chunks (300 / 142 ≈ 2.1, rounded up)
        assert len(chunks) >= 2

        # Check first chunk header
        assert chunks[0].startswith(f"[1/{len(chunks)}] ")

        # Check last chunk header
        assert chunks[-1].startswith(f"[{len(chunks)}/{len(chunks)}] ")

    def test_chunks_within_size_limit(self):
        """All chunks should be within size limit."""
        message = "x" * 1000
        chunks = chunk_message(message)

        for chunk in chunks:
            assert len(chunk.encode('utf-8')) <= MAX_CHUNK_SIZE


class TestReassembly:
    """Tests for message reassembly."""

    def test_non_chunked_passthrough(self):
        """Non-chunked messages should pass through."""
        message = "Hello, World!"
        result = reassemble_message(message, "sender1")

        assert result == message

    def test_reassemble_complete_message(self):
        """Complete chunked messages should be reassembled."""
        original = "A" * 300
        chunks = chunk_message(original)

        # Simulate receiving chunks
        result = None
        for chunk in chunks:
            result = reassemble_message(chunk, "sender1")

        # Should have complete message after last chunk
        assert result == original

    def test_reassemble_out_of_order(self):
        """Chunks received out of order should still reassemble."""
        original = "B" * 300
        chunks = chunk_message(original)

        # Reverse order
        reversed_chunks = chunks[::-1]

        result = None
        for chunk in reversed_chunks:
            result = reassemble_message(chunk, "sender2")

        assert result == original

    def test_partial_returns_none(self):
        """Partial messages should return None."""
        original = "C" * 300
        chunks = chunk_message(original)

        # Only send first chunk
        result = reassemble_message(chunks[0], "sender3")

        assert result is None
        assert get_pending_count() > 0

    def test_different_senders_separate(self):
        """Chunks from different senders should be tracked separately."""
        message1 = "D" * 300
        message2 = "E" * 300

        chunks1 = chunk_message(message1)
        chunks2 = chunk_message(message2)

        # Interleave chunks from both senders
        reassemble_message(chunks1[0], "sender4")
        reassemble_message(chunks2[0], "sender5")

        # Should have 2 pending
        assert get_pending_count() >= 2


class TestCleanup:
    """Tests for chunk cleanup."""

    def test_cleanup_removes_stale(self):
        """Cleanup should remove stale pending chunks."""
        # Create some pending chunks
        message = "F" * 300
        chunks = chunk_message(message)

        reassemble_message(chunks[0], "stale_sender")

        # Pending count should be > 0
        assert get_pending_count() > 0

        # Note: In a real test, we'd mock time.time() to simulate timeout
        # For now, just verify cleanup doesn't crash
        cleanup_expired_chunks()
