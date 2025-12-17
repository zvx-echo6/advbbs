"""
advBBS Database Connection Manager

SQLite database with WAL mode for concurrent reads.
"""

import sqlite3
import logging
import time
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, Generator

logger = logging.getLogger(__name__)


class Database:
    """
    SQLite database manager for advBBS.

    Uses WAL mode for concurrent reads (web reader can read while BBS writes).
    """

    def __init__(self, path: str):
        """
        Initialize database connection.

        Args:
            path: Path to SQLite database file
        """
        self.path = Path(path)
        self._conn: Optional[sqlite3.Connection] = None
        self._initialized = False

    def initialize(self):
        """Initialize database connection and schema."""
        # Ensure directory exists
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # Connect with WAL mode
        self._conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            isolation_level=None  # Autocommit mode
        )

        # Enable WAL mode for concurrent reads
        self._conn.execute("PRAGMA journal_mode=WAL")

        # Enable foreign keys
        self._conn.execute("PRAGMA foreign_keys=ON")

        # Use Row factory for dict-like access
        self._conn.row_factory = sqlite3.Row

        # Run migrations
        self._run_migrations()

        self._initialized = True
        logger.info(f"Database initialized: {self.path}")

    def _run_migrations(self):
        """Run database migrations."""
        # Create migrations tracking table
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                applied_at INTEGER NOT NULL
            )
        """)

        # Get applied migrations
        applied = {
            row[0] for row in
            self._conn.execute("SELECT name FROM _migrations").fetchall()
        }

        # Run pending migrations
        migrations = [
            ("001_initial", self._migration_001_initial),
            ("002_settings_and_maintenance", self._migration_002_settings),
        ]

        for name, func in migrations:
            if name not in applied:
                logger.info(f"Running migration: {name}")
                func()
                self._conn.execute(
                    "INSERT INTO _migrations (name, applied_at) VALUES (?, ?)",
                    (name, int(time.time() * 1_000_000))
                )

    def _migration_001_initial(self):
        """Initial database schema."""
        self._conn.executescript("""
            -- Users table
            CREATE TABLE IF NOT EXISTS users (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                username        TEXT UNIQUE NOT NULL,
                password_hash   BLOB NOT NULL,
                salt            BLOB NOT NULL,
                encryption_key  BLOB NOT NULL,
                recovery_key_enc BLOB,
                created_at_us   INTEGER NOT NULL,
                last_seen_at_us INTEGER,
                is_admin        INTEGER DEFAULT 0,
                is_banned       INTEGER DEFAULT 0,
                banned_by       TEXT,
                ban_reason      TEXT,
                ban_origin      TEXT,
                banned_at_us    INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_users_username ON users(username COLLATE NOCASE);

            -- Nodes table
            CREATE TABLE IF NOT EXISTS nodes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id         TEXT UNIQUE NOT NULL,
                short_name      TEXT,
                long_name       TEXT,
                first_seen_us   INTEGER NOT NULL,
                last_seen_us    INTEGER NOT NULL,
                last_snr        REAL,
                last_rssi       INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_nodes_node_id ON nodes(node_id);

            -- UserNodes table (multi-node identity)
            CREATE TABLE IF NOT EXISTS user_nodes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                node_id         INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
                registered_at_us INTEGER NOT NULL,
                is_primary      INTEGER DEFAULT 0,
                UNIQUE(user_id, node_id)
            );
            CREATE INDEX IF NOT EXISTS idx_user_nodes_user ON user_nodes(user_id);
            CREATE INDEX IF NOT EXISTS idx_user_nodes_node ON user_nodes(node_id);

            -- Boards table
            CREATE TABLE IF NOT EXISTS boards (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT UNIQUE NOT NULL,
                description     TEXT,
                created_at_us   INTEGER NOT NULL,
                is_restricted   INTEGER DEFAULT 0,
                board_type      TEXT DEFAULT 'public',
                board_key_enc   BLOB
            );

            -- Board access (for restricted boards)
            CREATE TABLE IF NOT EXISTS board_access (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                board_id        INTEGER NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
                user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                board_key_enc   BLOB NOT NULL,
                granted_at_us   INTEGER NOT NULL,
                granted_by      INTEGER REFERENCES users(id),
                UNIQUE(board_id, user_id)
            );

            -- Board states (reading position)
            CREATE TABLE IF NOT EXISTS board_states (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                board_id        INTEGER NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
                last_read_us    INTEGER NOT NULL,
                UNIQUE(user_id, board_id)
            );

            -- Messages table
            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                uuid            TEXT UNIQUE NOT NULL,
                msg_type        TEXT NOT NULL CHECK (msg_type IN ('mail', 'bulletin', 'system')),
                board_id        INTEGER REFERENCES boards(id),
                sender_user_id  INTEGER REFERENCES users(id),
                sender_node_id  INTEGER REFERENCES nodes(id),  -- NULL for remote mail
                recipient_user_id INTEGER REFERENCES users(id),
                recipient_node_id INTEGER REFERENCES nodes(id),
                subject_enc     BLOB,
                body_enc        BLOB NOT NULL,
                created_at_us   INTEGER NOT NULL,
                delivered_at_us INTEGER,
                read_at_us      INTEGER,
                expires_at_us   INTEGER,
                origin_bbs      TEXT,
                delivery_attempts INTEGER DEFAULT 0,
                last_attempt_us INTEGER,
                forwarded_to    TEXT,
                hop_count       INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_messages_uuid ON messages(uuid);
            CREATE INDEX IF NOT EXISTS idx_messages_recipient_user ON messages(recipient_user_id);
            CREATE INDEX IF NOT EXISTS idx_messages_recipient_node ON messages(recipient_node_id);
            CREATE INDEX IF NOT EXISTS idx_messages_board ON messages(board_id);
            CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at_us);

            -- BBS Peers table
            CREATE TABLE IF NOT EXISTS bbs_peers (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                node_id         TEXT UNIQUE NOT NULL,
                bbs_name        TEXT,
                protocol        TEXT DEFAULT 'fq51',
                last_sync_us    INTEGER,
                sync_enabled    INTEGER DEFAULT 1,
                trust_level     INTEGER DEFAULT 0
            );

            -- Sync log table
            CREATE TABLE IF NOT EXISTS sync_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                message_uuid    TEXT NOT NULL,
                peer_id         INTEGER NOT NULL REFERENCES bbs_peers(id),
                direction       TEXT NOT NULL CHECK (direction IN ('sent', 'received')),
                status          TEXT NOT NULL CHECK (status IN ('pending', 'acked', 'failed')),
                attempts        INTEGER DEFAULT 0,
                last_attempt_us INTEGER,
                UNIQUE(message_uuid, peer_id, direction)
            );

            -- Create default boards (these are the only boards that sync)
            INSERT OR IGNORE INTO boards (name, description, created_at_us, board_type)
            VALUES
                ('general', 'General discussion (synced)', strftime('%s', 'now') * 1000000, 'public'),
                ('help', 'Help and Support (synced)', strftime('%s', 'now') * 1000000, 'public');
        """)

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager for database transactions."""
        self._conn.execute("BEGIN")
        try:
            yield self._conn
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute SQL query."""
        return self._conn.execute(sql, params)

    def executemany(self, sql: str, params_list: list) -> sqlite3.Cursor:
        """Execute SQL query with multiple parameter sets."""
        return self._conn.executemany(sql, params_list)

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        """Execute query and fetch one result."""
        return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """Execute query and fetch all results."""
        return self._conn.execute(sql, params).fetchall()

    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
            logger.info("Database connection closed")

    # === Utility Methods ===

    def count_users(self) -> int:
        """Count total registered users."""
        row = self.fetchone("SELECT COUNT(*) FROM users")
        return row[0] if row else 0

    def count_banned_users(self) -> int:
        """Count banned users."""
        row = self.fetchone("SELECT COUNT(*) FROM users WHERE is_banned = 1")
        return row[0] if row else 0

    def count_messages(self) -> int:
        """Count total messages."""
        row = self.fetchone("SELECT COUNT(*) FROM messages")
        return row[0] if row else 0

    def get_last_sync_time(self, peer_id: str) -> int:
        """Get last sync timestamp for a peer."""
        row = self.fetchone(
            "SELECT last_sync_us FROM bbs_peers WHERE node_id = ?",
            (peer_id,)
        )
        return row[0] if row and row[0] else 0

    def _migration_002_settings(self):
        """Add settings and maintenance support tables."""
        # First, add columns to existing tables (ignore errors if already exist)
        alter_statements = [
            "ALTER TABLE messages ADD COLUMN deleted_at_us INTEGER",
            "ALTER TABLE bbs_peers ADD COLUMN callsign TEXT",
            "ALTER TABLE bbs_peers ADD COLUMN name TEXT",
            "ALTER TABLE bbs_peers ADD COLUMN capabilities TEXT",
            "ALTER TABLE bbs_peers ADD COLUMN last_seen_us INTEGER",
        ]

        for stmt in alter_statements:
            try:
                self._conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # Column already exists

        # Now create tables and indexes
        self._conn.executescript("""
            -- BBS Settings table (key-value store)
            CREATE TABLE IF NOT EXISTS bbs_settings (
                key             TEXT PRIMARY KEY,
                value           TEXT NOT NULL,
                updated_at_us   INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000000)
            );

            -- Board read positions table (for tracking reading position)
            CREATE TABLE IF NOT EXISTS board_read_positions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                board_id        INTEGER NOT NULL REFERENCES boards(id) ON DELETE CASCADE,
                last_read_msg_id INTEGER NOT NULL,
                updated_at_us   INTEGER NOT NULL,
                UNIQUE(user_id, board_id)
            );

            -- Add indexes for maintenance queries
            CREATE INDEX IF NOT EXISTS idx_messages_deleted ON messages(deleted_at_us);
            CREATE INDEX IF NOT EXISTS idx_messages_type ON messages(msg_type);
            CREATE INDEX IF NOT EXISTS idx_sync_log_attempt ON sync_log(last_attempt_us);
        """)
