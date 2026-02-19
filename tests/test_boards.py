"""
Tests for advBBS Bulletin Board System
"""

import pytest
from unittest.mock import MagicMock

from advbbs.core.crypto import CryptoManager, MasterKeyManager
from advbbs.core.boards import BoardService, BoardRepository
from advbbs.db.connection import Database
from advbbs.db.users import UserRepository, NodeRepository
from advbbs.db.messages import MessageRepository


class MockBBS:
    """Mock BBS for testing board service."""

    def __init__(self, db_path=":memory:"):
        self.crypto = CryptoManager(
            time_cost=1,
            memory_cost_kb=8192,
            parallelism=1
        )
        self.master_key = MasterKeyManager(self.crypto)
        self.master_key.initialize("test_admin_password")

        self.db = Database(db_path)
        self.db.initialize()

        self.config = MagicMock()
        self.config.bbs.callsign = "TEST"


class TestBoardListing:
    """Tests for board listing."""

    def setup_method(self):
        """Set up test fixtures."""
        self.bbs = MockBBS()
        self.board_service = BoardService(self.bbs)

    def test_list_default_boards(self):
        """Test listing default boards."""
        boards = self.board_service.list_boards()

        # Default boards from migrations (general, help from 001; local from 004)
        assert len(boards) >= 3
        board_names = [b["name"] for b in boards]
        assert "general" in board_names
        assert "help" in board_names
        assert "local" in board_names

    def test_board_list_includes_counts(self):
        """Test that board list includes post counts."""
        boards = self.board_service.list_boards()

        for board in boards:
            assert "name" in board
            assert "posts" in board
            assert "description" in board


class TestBoardEntry:
    """Tests for entering boards."""

    def setup_method(self):
        """Set up test fixtures."""
        self.bbs = MockBBS()
        self.board_service = BoardService(self.bbs)

    def test_enter_existing_board(self):
        """Test entering an existing board."""
        board, error = self.board_service.enter_board("general")

        assert error == ""
        assert board is not None
        assert board.name == "general"

    def test_enter_nonexistent_board(self):
        """Test entering a nonexistent board fails."""
        board, error = self.board_service.enter_board("doesnotexist")

        assert board is None
        assert "not found" in error.lower()

    def test_enter_board_case_insensitive(self):
        """Test board names are case insensitive."""
        board1, _ = self.board_service.enter_board("general")
        board2, _ = self.board_service.enter_board("GENERAL")
        board3, _ = self.board_service.enter_board("General")

        assert board1.id == board2.id == board3.id


class TestPosting:
    """Tests for posting to boards."""

    def setup_method(self):
        """Set up test fixtures."""
        self.bbs = MockBBS()
        self.board_service = BoardService(self.bbs)
        self.user_repo = UserRepository(self.bbs.db)
        self.node_repo = NodeRepository(self.bbs.db)
        self.board_repo = BoardRepository(self.bbs.db)

        # Create test user
        self._create_test_user()

        # Get general board
        self.board = self.board_repo.get_board_by_name("general")

    def _create_test_user(self):
        """Create a test user."""
        salt = self.bbs.crypto.generate_salt()
        key = self.bbs.crypto.derive_key("test_pass", salt)
        key_enc = self.bbs.master_key.encrypt_user_key(key)

        self.user = self.user_repo.create_user(
            username="testuser",
            password_hash=self.bbs.crypto.hash_password("test_pass").encode(),
            salt=salt,
            encryption_key=key_enc
        )

        self.node = self.node_repo.get_or_create_node("!testnode")

    def test_create_post(self):
        """Test creating a post."""
        message, error = self.board_service.create_post(
            board_id=self.board.id,
            user_id=self.user.id,
            sender_node_id="!testnode",
            subject="Test Post",
            body="This is a test post body."
        )

        assert error == ""
        assert message is not None
        assert message.board_id == self.board.id

    def test_create_post_empty_body(self):
        """Test creating post with empty body fails."""
        message, error = self.board_service.create_post(
            board_id=self.board.id,
            user_id=self.user.id,
            sender_node_id="!testnode",
            subject="Empty",
            body=""
        )

        assert message is None
        assert "empty" in error.lower()

    def test_create_post_subject_too_long(self):
        """Test creating post with too-long subject fails."""
        message, error = self.board_service.create_post(
            board_id=self.board.id,
            user_id=self.user.id,
            sender_node_id="!testnode",
            subject="x" * 100,  # Over 64 char limit
            body="Body"
        )

        assert message is None
        assert "subject" in error.lower()


class TestReadingPosts:
    """Tests for reading posts."""

    def setup_method(self):
        """Set up test fixtures."""
        self.bbs = MockBBS()
        self.board_service = BoardService(self.bbs)
        self.user_repo = UserRepository(self.bbs.db)
        self.node_repo = NodeRepository(self.bbs.db)
        self.board_repo = BoardRepository(self.bbs.db)

        self._create_test_user()
        self.board = self.board_repo.get_board_by_name("general")
        self._create_test_posts()

    def _create_test_user(self):
        """Create a test user."""
        salt = self.bbs.crypto.generate_salt()
        key = self.bbs.crypto.derive_key("test_pass", salt)
        key_enc = self.bbs.master_key.encrypt_user_key(key)

        self.user = self.user_repo.create_user(
            username="testuser",
            password_hash=b"hash",
            salt=salt,
            encryption_key=key_enc
        )

        self.node = self.node_repo.get_or_create_node("!testnode")

    def _create_test_posts(self):
        """Create test posts."""
        for i in range(3):
            self.board_service.create_post(
                board_id=self.board.id,
                user_id=self.user.id,
                sender_node_id="!testnode",
                subject=f"Post {i+1}",
                body=f"Body of post {i+1}"
            )

    def test_list_posts(self):
        """Test listing posts."""
        posts = self.board_service.list_posts(self.board.id, self.user.id)

        assert len(posts) == 3

    def test_read_post_by_number(self):
        """Test reading post by number."""
        post, error = self.board_service.read_post(
            self.board.id,
            post_number=1,
            user_id=self.user.id
        )

        assert error == ""
        assert post is not None
        assert post.number == 1

    def test_read_nonexistent_post(self):
        """Test reading nonexistent post fails."""
        post, error = self.board_service.read_post(
            self.board.id,
            post_number=999,
            user_id=self.user.id
        )

        assert post is None
        assert "not found" in error.lower()


class TestBoardCreation:
    """Tests for board creation."""

    def setup_method(self):
        """Set up test fixtures."""
        self.bbs = MockBBS()
        self.board_service = BoardService(self.bbs)
        self.user_repo = UserRepository(self.bbs.db)

        self._create_test_user()

    def _create_test_user(self):
        """Create a test admin user."""
        salt = self.bbs.crypto.generate_salt()
        key = self.bbs.crypto.derive_key("admin_pass", salt)
        key_enc = self.bbs.master_key.encrypt_user_key(key)

        self.admin = self.user_repo.create_user(
            username="admin",
            password_hash=b"hash",
            salt=salt,
            encryption_key=key_enc,
            is_admin=True
        )

    def test_create_public_board(self):
        """Test creating a public board."""
        board, error = self.board_service.create_board(
            name="newboard",
            description="A new test board"
        )

        assert error == ""
        assert board is not None
        assert board.name == "newboard"
        assert not board.is_restricted

    def test_create_restricted_board(self):
        """Test creating a restricted board."""
        board, error = self.board_service.create_board(
            name="private",
            description="A private board",
            is_restricted=True,
            creator_id=self.admin.id
        )

        assert error == ""
        assert board is not None
        assert board.is_restricted

    def test_create_board_duplicate_name(self):
        """Test creating board with duplicate name fails."""
        self.board_service.create_board(name="myboard")
        board, error = self.board_service.create_board(name="myboard")

        assert board is None
        assert "exists" in error.lower()

    def test_create_board_invalid_name(self):
        """Test creating board with invalid name fails."""
        board, error = self.board_service.create_board(name="a")  # Too short

        assert board is None

        board, error = self.board_service.create_board(name="with spaces")

        assert board is None


class TestRestrictedBoards:
    """Tests for restricted board access."""

    def setup_method(self):
        """Set up test fixtures."""
        self.bbs = MockBBS()
        self.board_service = BoardService(self.bbs)
        self.user_repo = UserRepository(self.bbs.db)
        self.board_repo = BoardRepository(self.bbs.db)

        self._create_test_users()
        self._create_restricted_board()

    def _create_test_users(self):
        """Create test users."""
        # Admin
        admin_salt = self.bbs.crypto.generate_salt()
        admin_key = self.bbs.crypto.derive_key("admin_pass", admin_salt)
        admin_key_enc = self.bbs.master_key.encrypt_user_key(admin_key)

        self.admin = self.user_repo.create_user(
            username="admin",
            password_hash=b"hash",
            salt=admin_salt,
            encryption_key=admin_key_enc,
            is_admin=True
        )

        # Regular user
        user_salt = self.bbs.crypto.generate_salt()
        user_key = self.bbs.crypto.derive_key("user_pass", user_salt)
        user_key_enc = self.bbs.master_key.encrypt_user_key(user_key)

        self.user = self.user_repo.create_user(
            username="regularuser",
            password_hash=b"hash",
            salt=user_salt,
            encryption_key=user_key_enc
        )

    def _create_restricted_board(self):
        """Create a restricted board."""
        self.restricted_board, _ = self.board_service.create_board(
            name="secret",
            description="Secret board",
            is_restricted=True,
            creator_id=self.admin.id
        )

    def test_enter_restricted_board_with_access(self):
        """Test entering restricted board with access."""
        # Admin should have access (creator)
        board, error = self.board_service.enter_board("secret", self.admin.id)

        assert error == ""
        assert board is not None

    def test_enter_restricted_board_without_access(self):
        """Test entering restricted board without access fails."""
        board, error = self.board_service.enter_board("secret", self.user.id)

        assert board is None
        assert "access" in error.lower()

    def test_grant_access(self):
        """Test granting access to restricted board."""
        success, error = self.board_service.grant_access(
            self.restricted_board.id,
            self.user.id,
            self.admin.id
        )

        assert success
        assert error == ""

        # Now user should be able to enter
        board, error = self.board_service.enter_board("secret", self.user.id)
        assert board is not None


class TestPostDeletion:
    """Tests for post deletion."""

    def setup_method(self):
        """Set up test fixtures."""
        self.bbs = MockBBS()
        self.board_service = BoardService(self.bbs)
        self.user_repo = UserRepository(self.bbs.db)
        self.node_repo = NodeRepository(self.bbs.db)
        self.board_repo = BoardRepository(self.bbs.db)

        self._create_test_users()
        self.board = self.board_repo.get_board_by_name("general")
        self._create_test_post()

    def _create_test_users(self):
        """Create test users."""
        salt = self.bbs.crypto.generate_salt()
        key = self.bbs.crypto.derive_key("user_pass", salt)
        key_enc = self.bbs.master_key.encrypt_user_key(key)

        self.user = self.user_repo.create_user(
            username="testuser",
            password_hash=b"hash",
            salt=salt,
            encryption_key=key_enc
        )

        other_salt = self.bbs.crypto.generate_salt()
        other_key = self.bbs.crypto.derive_key("other_pass", other_salt)
        other_key_enc = self.bbs.master_key.encrypt_user_key(other_key)

        self.other_user = self.user_repo.create_user(
            username="otheruser",
            password_hash=b"hash",
            salt=other_salt,
            encryption_key=other_key_enc
        )

        self.node = self.node_repo.get_or_create_node("!testnode")

    def _create_test_post(self):
        """Create a test post."""
        self.board_service.create_post(
            board_id=self.board.id,
            user_id=self.user.id,
            sender_node_id="!testnode",
            subject="Test",
            body="Test post"
        )

    def test_delete_own_post(self):
        """Test deleting own post succeeds."""
        success, error = self.board_service.delete_post(
            self.board.id,
            post_number=1,
            user_id=self.user.id
        )

        assert success
        assert error == ""

    def test_delete_others_post(self):
        """Test deleting another user's post fails."""
        success, error = self.board_service.delete_post(
            self.board.id,
            post_number=1,
            user_id=self.other_user.id
        )

        assert not success
        assert "own posts" in error.lower()

    def test_admin_can_delete_any_post(self):
        """Test admin can delete any post."""
        # Make other_user an admin
        self.other_user.is_admin = True

        success, error = self.board_service.delete_post(
            self.board.id,
            post_number=1,
            user_id=self.other_user.id,
            is_admin=True
        )

        assert success
