"""
advBBS Configuration Tool

Rich-based interactive configuration interface.
Inspired by meshtasticd-config-tool.
"""

import os
import sys
import shutil
import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Optional, Any

# Rich imports
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt, Confirm, IntPrompt
from rich import box

# TOML handling
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError:
        print("Error: Please install tomli: pip install tomli")
        sys.exit(1)

try:
    import toml
except ImportError:
    print("Error: Please install toml: pip install toml")
    sys.exit(1)


console = Console()


class ConfigTool:
    """Rich-based configuration tool for advBBS."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or self._find_config()
        self.config = self._load_config()
        self.modified = False
        self.running = True

    def _find_config(self) -> Path:
        """Find config file from environment or default locations."""
        # Check environment variable first
        env_path = os.environ.get("advBBS_CONFIG")
        if env_path:
            return Path(env_path)

        # Check common locations
        candidates = [
            Path("/data/config.toml"),
            Path("config.toml"),
            Path.home() / ".config" / "advbbs" / "config.toml",
        ]

        for path in candidates:
            if path.exists():
                return path

        # Default to /data/config.toml for Docker
        return Path("/data/config.toml")

    def _load_config(self) -> dict:
        """Load configuration from TOML file."""
        if not self.config_path.exists():
            return self._default_config()

        try:
            with open(self.config_path, "rb") as f:
                return tomllib.load(f)
        except Exception as e:
            console.print(f"[red]Error loading config: {e}[/red]")
            return self._default_config()

    def _get_db_path(self) -> Optional[Path]:
        """Get database path from config."""
        db_path = self._get("database", "path", "/data/advbbs.db")
        path = Path(db_path)
        if path.exists():
            return path
        return None

    def _get_db_connection(self) -> Optional[sqlite3.Connection]:
        """Get database connection if available."""
        db_path = self._get_db_path()
        if not db_path:
            return None
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            return conn
        except Exception:
            return None

    def _default_config(self) -> dict:
        """Return default configuration."""
        return {
            "bbs": {
                "name": "advBBS",
                "callsign": "FQ51",
                "admin_password": "changeme",
                "motd": "Welcome to advBBS!",
                "timezone": "America/Boise",
                "max_message_age_days": 30,
                "announcement_interval_hours": 12,
                "announcements_enabled": True,
                "announcement_message": "",
                "session_timeout_minutes": 30,
                "reply_to_unknown_commands": True,
            },
            "database": {
                "path": "/data/advbbs.db",
                "backup_path": "/data/backups",
                "backup_interval_hours": 24,
            },
            "meshtastic": {
                "connection_type": "serial",
                "serial_port": "/dev/ttyUSB0",
                "tcp_host": "localhost",
                "tcp_port": 4403,
                "public_channel": 0,
                "respond_channel": -1,
                "dm_only": True,
                "ignore_channels": [],
            },
            "features": {
                "mail_enabled": True,
                "boards_enabled": True,
                "sync_enabled": True,
                "registration_enabled": True,
            },
            "operating_mode": {
                "mode": "full",
            },
            "sync": {
                "enabled": True,
                "auto_sync_interval_minutes": 60,
                "peers": [],
            },
            "rate_limits": {
                "messages_per_minute": 10,
                "sync_messages_per_minute": 20,
                "commands_per_minute": 30,
            },
            "logging": {
                "level": "INFO",
                "enabled": True,
            },
        }

    def _save_config(self) -> bool:
        """Save configuration to TOML file."""
        try:
            # Ensure directory exists
            self.config_path.parent.mkdir(parents=True, exist_ok=True)

            with open(self.config_path, "w") as f:
                toml.dump(self.config, f)

            self.modified = False
            return True
        except Exception as e:
            console.print(f"[red]Error saving config: {e}[/red]")
            return False

    def _get(self, section: str, key: str, default: Any = None) -> Any:
        """Get a config value safely."""
        return self.config.get(section, {}).get(key, default)

    def _set(self, section: str, key: str, value: Any):
        """Set a config value."""
        if section not in self.config:
            self.config[section] = {}
        self.config[section][key] = value
        self.modified = True

    def _status_icon(self, value: bool) -> str:
        """Return status icon for boolean value."""
        return "[green]✓[/green]" if value else "[red]✗[/red]"

    def _show_status_dashboard(self):
        """Display status dashboard with database stats and recent activity."""
        conn = self._get_db_connection()
        if not conn:
            console.print("[dim]Database not available - start BBS first[/dim]")
            return

        try:
            # Get statistics (using correct column names: msg_type, read_at_us)
            user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            total_mail = conn.execute("SELECT COUNT(*) FROM messages WHERE msg_type = 'mail'").fetchone()[0]
            unread_mail = conn.execute("SELECT COUNT(*) FROM messages WHERE msg_type = 'mail' AND read_at_us IS NULL").fetchone()[0]
            board_posts = conn.execute("SELECT COUNT(*) FROM messages WHERE msg_type = 'bulletin'").fetchone()[0]
            node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

            # Get recent activity (last 5 messages)
            # Note: subject_enc is encrypted, so we can't display it - show sender/recipient instead
            recent = conn.execute("""
                SELECT m.created_at_us, m.msg_type,
                       COALESCE(sender.username, 'Unknown') as sender,
                       COALESCE(recipient.username, 'broadcast') as recipient
                FROM messages m
                LEFT JOIN users sender ON m.sender_user_id = sender.id
                LEFT JOIN users recipient ON m.recipient_user_id = recipient.id
                WHERE m.deleted_at_us IS NULL
                ORDER BY m.created_at_us DESC
                LIMIT 5
            """).fetchall()

            conn.close()

            # Build compact dashboard - single line stats
            stats = f"[cyan]Users:[/cyan] {user_count}  [cyan]Nodes:[/cyan] {node_count}  [cyan]Mail:[/cyan] {total_mail}"
            if unread_mail:
                stats += f" ([yellow]{unread_mail} new[/yellow])"
            stats += f"  [cyan]Posts:[/cyan] {board_posts}"
            console.print(Panel(stats, title="Status", border_style="dim", expand=False))

            # Recent activity
            if recent:
                console.print()
                console.print("[cyan]Recent Activity:[/cyan]")
                for msg in recent:
                    timestamp = datetime.fromtimestamp(msg["created_at_us"] / 1_000_000)
                    time_str = timestamp.strftime("%m/%d %H:%M")
                    if msg["msg_type"] == "mail":
                        msg_type = "[blue]MAIL[/blue]"
                        detail = f"{msg['sender']} → {msg['recipient']}"
                    else:
                        msg_type = "[green]POST[/green]"
                        detail = f"by {msg['sender']}"
                    console.print(f"  [dim]{time_str}[/dim] {msg_type} {detail}")

        except Exception as e:
            console.print(f"[dim]Dashboard unavailable: {e}[/dim]")

    def _clear(self):
        """Clear screen."""
        console.clear()

    def run(self):
        """Main entry point."""
        while self.running:
            self._clear()
            self._show_main_menu()

    def _show_main_menu(self):
        """Display main menu."""
        # Compact header
        console.print("[cyan bold]advBBS Configuration Tool[/cyan bold]")
        console.print()

        # Status Dashboard
        self._show_status_dashboard()
        console.print()

        # BBS Information
        info_table = Table(title="BBS Information", box=box.SIMPLE, show_header=True)
        info_table.add_column("Property", style="cyan")
        info_table.add_column("Value", style="green")

        info_table.add_row("BBS Name", self._get("bbs", "name", "advBBS"))
        info_table.add_row("Callsign", self._get("bbs", "callsign", "FQ51"))
        info_table.add_row("Mode", self._get("operating_mode", "mode", "full"))
        info_table.add_row("Config File", str(self.config_path))

        console.print(info_table)
        console.print()

        # Configuration Options
        config_table = Table(title="Configuration Options", box=box.SIMPLE, show_header=True)
        config_table.add_column("Option", style="white")
        config_table.add_column("Status", justify="right")

        # Meshtastic connection status
        conn_type = self._get("meshtastic", "connection_type", "serial")
        if conn_type == "tcp":
            conn_status = f"TCP {self._get('meshtastic', 'tcp_host')}:{self._get('meshtastic', 'tcp_port')}"
        else:
            conn_status = f"Serial {self._get('meshtastic', 'serial_port')}"

        # Features status
        features = []
        if self._get("features", "mail_enabled", True):
            features.append("[green]Mail[/green]")
        if self._get("features", "boards_enabled", True):
            features.append("[green]Boards[/green]")
        if self._get("features", "sync_enabled", True):
            features.append("[green]Sync[/green]")
        features_str = " ".join(features) if features else "[dim]None[/dim]"

        # Peer count
        peers = self._get("sync", "peers", [])
        peer_count = len(peers) if isinstance(peers, list) else 0

        # Get user count if DB available
        user_count = 0
        node_count = 0
        conn = self._get_db_connection()
        if conn:
            try:
                user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
                conn.close()
            except Exception:
                pass

        # Get board count if DB available
        board_count = 0
        post_count = 0
        if conn:
            conn = self._get_db_connection()
            try:
                board_count = conn.execute("SELECT COUNT(*) FROM boards").fetchone()[0]
                post_count = conn.execute("SELECT COUNT(*) FROM messages WHERE msg_type = 'bulletin'").fetchone()[0]
                conn.close()
            except Exception:
                pass

        config_table.add_row("1. BBS Settings", self._status_icon(True))
        config_table.add_row("2. Meshtastic Connection", f"[cyan]{conn_status}[/cyan]")
        config_table.add_row("3. Operating Mode", f"[cyan]{self._get('operating_mode', 'mode', 'full')}[/cyan]")
        config_table.add_row("4. Features", features_str)
        config_table.add_row("5. Sync & Peers", f"[cyan]{peer_count} peers[/cyan]")
        config_table.add_row("6. Rate Limits", self._status_icon(True))
        config_table.add_row("7. User & Node Admin", f"[cyan]{user_count} users, {node_count} nodes[/cyan]")
        config_table.add_row("8. Board & Post Admin", f"[cyan]{board_count} boards, {post_count} posts[/cyan]")

        console.print(config_table)
        console.print()

        # Actions
        action_table = Table(title="Actions", box=box.SIMPLE, show_header=True)
        action_table.add_column("Action", style="white")
        action_table.add_column("Status", justify="right")

        action_table.add_row("9. Setup Wizard", "[dim]First-time setup[/dim]")
        action_table.add_row("10. Validate Config", "[dim]Check for errors[/dim]")
        action_table.add_row("11. Backup Config", "[dim]Save backup copy[/dim]")
        action_table.add_row("12. View Raw Config", "[dim]Show TOML file[/dim]")

        console.print(action_table)
        console.print()

        # Modified indicator
        if self.modified:
            console.print("[yellow]* Unsaved changes[/yellow]")
            console.print()

        # Exit options
        console.print("[white]13. Save[/white]                 [dim]Save config, stay in menu[/dim]")
        console.print("[green]14. Save & Restart BBS[/green]   [dim]Apply changes now[/dim]")
        console.print("[white]15. Save & Exit[/white]          [dim]Save, restart BBS, exit config tool[/dim]")
        console.print("[white]16. Exit without Saving[/white]")
        console.print()

        # Get selection
        try:
            choice = IntPrompt.ask("Select option", default=12)
            self._handle_main_choice(choice)
        except KeyboardInterrupt:
            self._exit_handler()

    def _handle_main_choice(self, choice: int):
        """Handle main menu selection."""
        handlers = {
            1: self._bbs_settings,
            2: self._meshtastic_settings,
            3: self._operating_mode,
            4: self._features_settings,
            5: self._sync_settings,
            6: self._rate_limits,
            7: self._user_node_admin,
            8: self._board_admin,
            9: self._setup_wizard,
            10: self._validate_config,
            11: self._backup_config,
            12: self._view_config,
            13: self._save_only,
            14: self._save_and_restart,
            15: self._save_restart_exit,
            16: self._exit_no_save,
        }

        handler = handlers.get(choice)
        if handler:
            handler()

    def _bbs_settings(self):
        """BBS settings submenu."""
        while True:
            self._clear()

            table = Table(title="BBS Settings", box=box.ROUNDED)
            table.add_column("Option", style="white")
            table.add_column("Current Value", style="cyan")

            table.add_row("1. BBS Name", self._get("bbs", "name", "advBBS"))
            table.add_row("2. Callsign", self._get("bbs", "callsign", "FQ51"))
            table.add_row("3. Admin Password", "********")
            table.add_row("4. MOTD", self._get("bbs", "motd", "")[:40] + "...")
            table.add_row("5. Timezone", self._get("bbs", "timezone", "America/Boise"))
            table.add_row("6. Message Expiration", f"{self._get('bbs', 'max_message_age_days', 30)} days")
            table.add_row("7. Announcement Interval", f"{self._get('bbs', 'announcement_interval_hours', 12)} hours")
            table.add_row("8. Announcements Enabled", self._status_icon(self._get("bbs", "announcements_enabled", True)))
            ann_msg = self._get("bbs", "announcement_message", "") or "(default)"
            table.add_row("9. Announcement Message", ann_msg[:40] + ("..." if len(ann_msg) > 40 else ""))
            table.add_row("10. Session Timeout", f"{self._get('bbs', 'session_timeout_minutes', 30)} minutes")
            table.add_row("11. Reply to Unknown Cmds", self._status_icon(self._get("bbs", "reply_to_unknown_commands", True)))

            console.print(table)
            console.print()
            console.print("[white]0. Back to Main Menu[/white]")
            console.print()

            try:
                choice = IntPrompt.ask("Select option", default=0)
            except KeyboardInterrupt:
                return

            if choice == 0:
                return
            elif choice == 1:
                value = Prompt.ask("BBS Name", default=self._get("bbs", "name", "advBBS"))
                self._set("bbs", "name", value)
            elif choice == 2:
                value = Prompt.ask("Callsign", default=self._get("bbs", "callsign", "FQ51"))
                self._set("bbs", "callsign", value)
            elif choice == 3:
                self._change_password()
            elif choice == 4:
                value = Prompt.ask("MOTD", default=self._get("bbs", "motd", ""))
                self._set("bbs", "motd", value)
            elif choice == 5:
                console.print()
                console.print("[dim]Examples: America/Boise, America/New_York, America/Los_Angeles, UTC[/dim]")
                console.print("[dim]See: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones[/dim]")
                value = Prompt.ask("Timezone", default=self._get("bbs", "timezone", "America/Boise"))
                self._set("bbs", "timezone", value)
            elif choice == 6:
                value = IntPrompt.ask("Message expiration (days)", default=self._get("bbs", "max_message_age_days", 30))
                self._set("bbs", "max_message_age_days", value)
            elif choice == 7:
                value = IntPrompt.ask("Announcement interval (hours, 0 to disable)", default=self._get("bbs", "announcement_interval_hours", 12))
                self._set("bbs", "announcement_interval_hours", value)
            elif choice == 8:
                value = Confirm.ask("Enable announcements?", default=self._get("bbs", "announcements_enabled", True))
                self._set("bbs", "announcements_enabled", value)
            elif choice == 9:
                console.print()
                console.print("[dim]Variables: {callsign}, {name}, {users}, {msgs}[/dim]")
                console.print("[dim]Leave empty for default message[/dim]")
                value = Prompt.ask("Announcement message", default=self._get("bbs", "announcement_message", ""))
                self._set("bbs", "announcement_message", value)
            elif choice == 10:
                value = IntPrompt.ask("Session timeout (minutes)", default=self._get("bbs", "session_timeout_minutes", 30))
                self._set("bbs", "session_timeout_minutes", value)
            elif choice == 11:
                current = self._get("bbs", "reply_to_unknown_commands", True)
                self._set("bbs", "reply_to_unknown_commands", not current)

    def _change_password(self):
        """Change admin password with confirmation."""
        console.print()
        password = Prompt.ask("New admin password", password=True)
        if not password or len(password) < 6:
            console.print("[red]Password must be at least 6 characters[/red]")
            Prompt.ask("Press Enter to continue")
            return

        confirm = Prompt.ask("Confirm password", password=True)
        if password != confirm:
            console.print("[red]Passwords do not match[/red]")
            Prompt.ask("Press Enter to continue")
            return

        self._set("bbs", "admin_password", password)
        console.print("[green]Password updated[/green]")
        Prompt.ask("Press Enter to continue")

    def _meshtastic_settings(self):
        """Meshtastic connection settings."""
        while True:
            self._clear()

            conn_type = self._get("meshtastic", "connection_type", "serial")

            table = Table(title="Meshtastic Settings", box=box.ROUNDED)
            table.add_column("Option", style="white")
            table.add_column("Current Value", style="cyan")

            table.add_row("1. Connection Type", conn_type)
            table.add_row("2. Serial Port", self._get("meshtastic", "serial_port", "/dev/ttyUSB0"))
            table.add_row("3. TCP Host", self._get("meshtastic", "tcp_host", "localhost"))
            table.add_row("4. TCP Port", str(self._get("meshtastic", "tcp_port", 4403)))
            table.add_row("5. Public Channel", str(self._get("meshtastic", "public_channel", 0)))
            table.add_row("6. DM Only Mode", self._status_icon(self._get("meshtastic", "dm_only", True)))

            respond = self._get("meshtastic", "respond_channel", -1)
            respond_str = "DM only" if respond == -1 else f"Channel {respond}"
            table.add_row("7. Respond Channel", respond_str)

            ignore = self._get("meshtastic", "ignore_channels", [])
            ignore_str = ", ".join(map(str, ignore)) if ignore else "None"
            table.add_row("8. Ignore Channels", ignore_str)

            console.print(table)
            console.print()
            console.print("[white]0. Back to Main Menu[/white]")
            console.print()

            try:
                choice = IntPrompt.ask("Select option", default=0)
            except KeyboardInterrupt:
                return

            if choice == 0:
                return
            elif choice == 1:
                console.print("\n[cyan]1.[/cyan] serial - USB Serial connection")
                console.print("[cyan]2.[/cyan] tcp - TCP Network connection")
                sel = IntPrompt.ask("Select", default=1 if conn_type == "serial" else 2)
                self._set("meshtastic", "connection_type", "serial" if sel == 1 else "tcp")
            elif choice == 2:
                value = Prompt.ask("Serial port", default=self._get("meshtastic", "serial_port", "/dev/ttyUSB0"))
                self._set("meshtastic", "serial_port", value)
            elif choice == 3:
                value = Prompt.ask("TCP host", default=self._get("meshtastic", "tcp_host", "localhost"))
                self._set("meshtastic", "tcp_host", value)
            elif choice == 4:
                value = IntPrompt.ask("TCP port", default=self._get("meshtastic", "tcp_port", 4403))
                self._set("meshtastic", "tcp_port", value)
            elif choice == 5:
                value = IntPrompt.ask("Public channel", default=self._get("meshtastic", "public_channel", 0))
                self._set("meshtastic", "public_channel", value)
            elif choice == 6:
                value = Confirm.ask("DM only mode?", default=self._get("meshtastic", "dm_only", True))
                self._set("meshtastic", "dm_only", value)
            elif choice == 7:
                console.print("\nEnter -1 for DM only, or channel number to respond on")
                value = IntPrompt.ask("Respond channel", default=self._get("meshtastic", "respond_channel", -1))
                self._set("meshtastic", "respond_channel", value)
            elif choice == 8:
                current = self._get("meshtastic", "ignore_channels", [])
                current_str = ",".join(map(str, current)) if current else ""
                value = Prompt.ask("Ignore channels (comma-separated, e.g., 1,2,3)", default=current_str)
                if value:
                    channels = [int(x.strip()) for x in value.split(",") if x.strip().isdigit()]
                else:
                    channels = []
                self._set("meshtastic", "ignore_channels", channels)

    def _operating_mode(self):
        """Operating mode selection."""
        self._clear()

        current = self._get("operating_mode", "mode", "full")

        console.print(Panel("Operating Mode", style="cyan"))
        console.print()
        console.print("[cyan]1.[/cyan] full - Mail and bulletin boards")
        console.print("[cyan]2.[/cyan] mail_only - Private messages only")
        console.print("[cyan]3.[/cyan] boards_only - Public bulletins only")
        console.print("[cyan]4.[/cyan] repeater - Relay messages only")
        console.print()

        mode_map = {1: "full", 2: "mail_only", 3: "boards_only", 4: "repeater"}
        reverse_map = {v: k for k, v in mode_map.items()}

        try:
            choice = IntPrompt.ask("Select mode", default=reverse_map.get(current, 1))
            if choice in mode_map:
                self._set("operating_mode", "mode", mode_map[choice])
        except KeyboardInterrupt:
            pass

    def _features_settings(self):
        """Feature toggles."""
        while True:
            self._clear()

            table = Table(title="Features", box=box.ROUNDED)
            table.add_column("Option", style="white")
            table.add_column("Status", style="cyan")

            table.add_row("1. Mail", self._status_icon(self._get("features", "mail_enabled", True)))
            table.add_row("2. Bulletin Boards", self._status_icon(self._get("features", "boards_enabled", True)))
            table.add_row("3. Inter-BBS Sync", self._status_icon(self._get("features", "sync_enabled", True)))
            table.add_row("4. User Registration", self._status_icon(self._get("features", "registration_enabled", True)))

            console.print(table)
            console.print()
            console.print("[white]0. Back to Main Menu[/white]")
            console.print()

            try:
                choice = IntPrompt.ask("Select option to toggle", default=0)
            except KeyboardInterrupt:
                return

            if choice == 0:
                return
            elif choice == 1:
                current = self._get("features", "mail_enabled", True)
                self._set("features", "mail_enabled", not current)
            elif choice == 2:
                current = self._get("features", "boards_enabled", True)
                self._set("features", "boards_enabled", not current)
            elif choice == 3:
                current = self._get("features", "sync_enabled", True)
                self._set("features", "sync_enabled", not current)
                # Also update sync.enabled
                self._set("sync", "enabled", not current)
            elif choice == 4:
                current = self._get("features", "registration_enabled", True)
                self._set("features", "registration_enabled", not current)

    def _sync_settings(self):
        """Sync and peer configuration."""
        while True:
            self._clear()

            peers = self._get("sync", "peers", [])
            if not isinstance(peers, list):
                peers = []

            table = Table(title="Sync Settings", box=box.ROUNDED)
            table.add_column("Option", style="white")
            table.add_column("Value", style="cyan")

            table.add_row("1. Sync Enabled", self._status_icon(self._get("sync", "enabled", True)))
            table.add_row("2. Sync Interval", f"{self._get('sync', 'auto_sync_interval_minutes', 60)} minutes")
            table.add_row("3. Manage Peers", f"{len(peers)} configured")

            console.print(table)
            console.print()

            # Show peers if any
            if peers:
                peer_table = Table(title="Configured Peers", box=box.SIMPLE)
                peer_table.add_column("#", style="dim")
                peer_table.add_column("Name", style="cyan")
                peer_table.add_column("Node ID", style="green")
                peer_table.add_column("Protocol", style="yellow")

                for i, peer in enumerate(peers, 1):
                    peer_table.add_row(
                        str(i),
                        peer.get("name", "Unknown"),
                        peer.get("node_id", ""),
                        peer.get("protocol", "fq51")
                    )
                console.print(peer_table)
                console.print()

            console.print("[white]0. Back to Main Menu[/white]")
            console.print()

            try:
                choice = IntPrompt.ask("Select option", default=0)
            except KeyboardInterrupt:
                return

            if choice == 0:
                return
            elif choice == 1:
                current = self._get("sync", "enabled", True)
                self._set("sync", "enabled", not current)
                self._set("features", "sync_enabled", not current)
            elif choice == 2:
                value = IntPrompt.ask("Sync interval (minutes)", default=self._get("sync", "auto_sync_interval_minutes", 60))
                self._set("sync", "auto_sync_interval_minutes", value)
            elif choice == 3:
                self._manage_peers()

    def _manage_peers(self):
        """Peer management submenu."""
        while True:
            self._clear()

            peers = self._get("sync", "peers", [])
            if not isinstance(peers, list):
                peers = []

            console.print(Panel("Peer Management", style="cyan"))
            console.print()

            if peers:
                for i, peer in enumerate(peers, 1):
                    console.print(f"[cyan]{i}.[/cyan] {peer.get('name', 'Unknown')} ({peer.get('node_id', '')}) [{peer.get('protocol', 'fq51')}]")
                console.print()
            else:
                console.print("[dim]No peers configured[/dim]")
                console.print()

            console.print("[cyan]A.[/cyan] Add new peer")
            console.print("[cyan]D.[/cyan] Delete peer")
            console.print("[cyan]0.[/cyan] Back")
            console.print()

            try:
                choice = Prompt.ask("Select option", default="0")
            except KeyboardInterrupt:
                return

            if choice == "0":
                return
            elif choice.upper() == "A":
                self._add_peer()
            elif choice.upper() == "D":
                self._delete_peer()
            elif choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(peers):
                    self._edit_peer(idx)

    def _add_peer(self):
        """Add a new peer."""
        console.print()

        node_id = Prompt.ask("Node ID (e.g., !a1b2c3d4)")
        if not node_id:
            return

        name = Prompt.ask("Friendly name")
        if not name:
            return

        console.print("\n[cyan]1.[/cyan] fq51 - advBBS native")
        console.print("[cyan]2.[/cyan] tc2 - TC2-BBS")
        console.print("[cyan]3.[/cyan] meshing-around - Meshing-Around BBS")
        protocol_choice = IntPrompt.ask("Protocol", default=1)
        protocol_map = {1: "fq51", 2: "tc2", 3: "meshing-around"}
        protocol = protocol_map.get(protocol_choice, "fq51")

        peer = {
            "node_id": node_id,
            "name": name,
            "protocol": protocol,
            "enabled": True,
        }

        peers = self._get("sync", "peers", [])
        if not isinstance(peers, list):
            peers = []
        peers.append(peer)
        self._set("sync", "peers", peers)

        console.print(f"[green]Peer '{name}' added[/green]")
        Prompt.ask("Press Enter to continue")

    def _delete_peer(self):
        """Delete a peer."""
        peers = self._get("sync", "peers", [])
        if not peers:
            console.print("[yellow]No peers to delete[/yellow]")
            Prompt.ask("Press Enter to continue")
            return

        try:
            idx = IntPrompt.ask("Enter peer number to delete") - 1
            if 0 <= idx < len(peers):
                removed = peers.pop(idx)
                self._set("sync", "peers", peers)
                console.print(f"[green]Peer '{removed.get('name', 'Unknown')}' deleted[/green]")
            else:
                console.print("[red]Invalid peer number[/red]")
        except (ValueError, KeyboardInterrupt):
            pass

        Prompt.ask("Press Enter to continue")

    def _edit_peer(self, idx: int):
        """Edit an existing peer."""
        peers = self._get("sync", "peers", [])
        if not isinstance(peers, list) or idx >= len(peers):
            return

        peer = peers[idx]

        console.print(f"\n[cyan]Editing peer: {peer.get('name', 'Unknown')}[/cyan]\n")

        node_id = Prompt.ask("Node ID", default=peer.get("node_id", ""))
        name = Prompt.ask("Name", default=peer.get("name", ""))

        console.print("\n[cyan]1.[/cyan] fq51  [cyan]2.[/cyan] tc2  [cyan]3.[/cyan] meshing-around")
        protocol_map = {"fq51": 1, "tc2": 2, "meshing-around": 3}
        current_proto = protocol_map.get(peer.get("protocol", "fq51"), 1)
        protocol_choice = IntPrompt.ask("Protocol", default=current_proto)
        protocol_reverse = {1: "fq51", 2: "tc2", 3: "meshing-around"}

        peers[idx] = {
            "node_id": node_id,
            "name": name,
            "protocol": protocol_reverse.get(protocol_choice, "fq51"),
            "enabled": peer.get("enabled", True),
        }
        self._set("sync", "peers", peers)

        console.print("[green]Peer updated[/green]")
        Prompt.ask("Press Enter to continue")

    def _rate_limits(self):
        """Rate limit settings."""
        while True:
            self._clear()

            table = Table(title="Rate Limits", box=box.ROUNDED)
            table.add_column("Option", style="white")
            table.add_column("Value", style="cyan")

            table.add_row("1. Messages/minute", str(self._get("rate_limits", "messages_per_minute", 10)))
            table.add_row("2. Sync messages/minute", str(self._get("rate_limits", "sync_messages_per_minute", 20)))
            table.add_row("3. Commands/minute", str(self._get("rate_limits", "commands_per_minute", 30)))

            console.print(table)
            console.print()
            console.print("[white]0. Back to Main Menu[/white]")
            console.print()

            try:
                choice = IntPrompt.ask("Select option", default=0)
            except KeyboardInterrupt:
                return

            if choice == 0:
                return
            elif choice == 1:
                value = IntPrompt.ask("Messages per minute", default=self._get("rate_limits", "messages_per_minute", 10))
                self._set("rate_limits", "messages_per_minute", value)
            elif choice == 2:
                value = IntPrompt.ask("Sync messages per minute", default=self._get("rate_limits", "sync_messages_per_minute", 20))
                self._set("rate_limits", "sync_messages_per_minute", value)
            elif choice == 3:
                value = IntPrompt.ask("Commands per minute", default=self._get("rate_limits", "commands_per_minute", 30))
                self._set("rate_limits", "commands_per_minute", value)

    def _user_node_admin(self):
        """User and node administration menu."""
        conn = self._get_db_connection()
        if not conn:
            console.print("[red]Database not available[/red]")
            console.print("[dim]Make sure the BBS has been started at least once[/dim]")
            Prompt.ask("\nPress Enter to continue")
            return

        while True:
            self._clear()

            console.print(Panel("User & Node Administration", style="cyan"))
            console.print()

            # Get counts
            try:
                user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                banned_count = conn.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1").fetchone()[0]
                admin_count = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0]
                node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            except Exception:
                user_count = banned_count = admin_count = node_count = 0

            table = Table(title="Administration Options", box=box.ROUNDED)
            table.add_column("Option", style="white")
            table.add_column("Info", style="cyan")

            table.add_row("1. List Users", f"{user_count} total, {admin_count} admin, {banned_count} banned")
            table.add_row("2. Ban/Unban User", "[dim]Toggle user ban status[/dim]")
            table.add_row("3. Set/Remove Admin", "[dim]Toggle admin privileges[/dim]")
            table.add_row("4. Delete User", "[dim]Permanently delete user[/dim]")
            table.add_row("5. List Nodes", f"{node_count} known nodes")
            table.add_row("6. View Node Details", "[dim]Show node information[/dim]")

            console.print(table)
            console.print()
            console.print("[white]0. Back to Main Menu[/white]")
            console.print()

            try:
                choice = IntPrompt.ask("Select option", default=0)
            except KeyboardInterrupt:
                conn.close()
                return

            if choice == 0:
                conn.close()
                return
            elif choice == 1:
                self._list_users(conn)
            elif choice == 2:
                self._ban_user(conn)
            elif choice == 3:
                self._set_admin(conn)
            elif choice == 4:
                self._delete_user(conn)
            elif choice == 5:
                self._list_nodes(conn)
            elif choice == 6:
                self._view_node(conn)

    def _list_users(self, conn: sqlite3.Connection):
        """List all users."""
        self._clear()

        console.print(Panel("Registered Users", style="cyan"))
        console.print()

        try:
            users = conn.execute("""
                SELECT u.id, u.username, u.is_admin, u.is_banned,
                       u.created_at_us, u.last_seen_at_us,
                       (SELECT COUNT(*) FROM user_nodes WHERE user_id = u.id) as node_count
                FROM users u
                ORDER BY u.username
            """).fetchall()

            if not users:
                console.print("[dim]No users registered[/dim]")
            else:
                table = Table(box=box.SIMPLE)
                table.add_column("ID", style="dim")
                table.add_column("Username", style="cyan")
                table.add_column("Status", style="white")
                table.add_column("Nodes", justify="center")
                table.add_column("Created", style="dim")
                table.add_column("Last Seen", style="dim")

                for user in users:
                    status = []
                    if user["is_admin"]:
                        status.append("[yellow]ADMIN[/yellow]")
                    if user["is_banned"]:
                        status.append("[red]BANNED[/red]")
                    status_str = " ".join(status) if status else "[green]active[/green]"

                    created = datetime.fromtimestamp(user["created_at_us"] / 1_000_000).strftime("%Y-%m-%d")
                    last_seen = ""
                    if user["last_seen_at_us"]:
                        last_seen = datetime.fromtimestamp(user["last_seen_at_us"] / 1_000_000).strftime("%Y-%m-%d %H:%M")

                    table.add_row(
                        str(user["id"]),
                        user["username"],
                        status_str,
                        str(user["node_count"]),
                        created,
                        last_seen
                    )

                console.print(table)

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        console.print()
        Prompt.ask("Press Enter to continue")

    def _ban_user(self, conn: sqlite3.Connection):
        """Ban or unban a user."""
        console.print()

        username = Prompt.ask("Username to ban/unban")
        if not username:
            return

        try:
            user = conn.execute(
                "SELECT id, username, is_banned FROM users WHERE username = ?",
                (username,)
            ).fetchone()

            if not user:
                console.print(f"[red]User '{username}' not found[/red]")
                Prompt.ask("Press Enter to continue")
                return

            current_status = "banned" if user["is_banned"] else "active"
            new_status = not user["is_banned"]
            action = "ban" if new_status else "unban"

            if Confirm.ask(f"User '{username}' is currently {current_status}. {action.capitalize()}?"):
                conn.execute(
                    "UPDATE users SET is_banned = ? WHERE id = ?",
                    (1 if new_status else 0, user["id"])
                )
                conn.commit()
                console.print(f"[green]User '{username}' has been {'banned' if new_status else 'unbanned'}[/green]")
            else:
                console.print("[yellow]Cancelled[/yellow]")

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        Prompt.ask("Press Enter to continue")

    def _set_admin(self, conn: sqlite3.Connection):
        """Set or remove admin privileges."""
        console.print()

        username = Prompt.ask("Username to modify")
        if not username:
            return

        try:
            user = conn.execute(
                "SELECT id, username, is_admin FROM users WHERE username = ?",
                (username,)
            ).fetchone()

            if not user:
                console.print(f"[red]User '{username}' not found[/red]")
                Prompt.ask("Press Enter to continue")
                return

            current_status = "admin" if user["is_admin"] else "regular user"
            new_status = not user["is_admin"]
            action = "grant admin" if new_status else "remove admin"

            if Confirm.ask(f"User '{username}' is currently {current_status}. {action}?"):
                conn.execute(
                    "UPDATE users SET is_admin = ? WHERE id = ?",
                    (1 if new_status else 0, user["id"])
                )
                conn.commit()
                console.print(f"[green]User '{username}' is now {'an admin' if new_status else 'a regular user'}[/green]")
            else:
                console.print("[yellow]Cancelled[/yellow]")

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        Prompt.ask("Press Enter to continue")

    def _delete_user(self, conn: sqlite3.Connection):
        """Delete a user and their data."""
        console.print()

        username = Prompt.ask("Username to delete")
        if not username:
            return

        try:
            user = conn.execute(
                "SELECT id, username FROM users WHERE username = ?",
                (username,)
            ).fetchone()

            if not user:
                console.print(f"[red]User '{username}' not found[/red]")
                Prompt.ask("Press Enter to continue")
                return

            console.print(f"\n[red]WARNING: This will permanently delete user '{username}' and all their data![/red]")
            console.print("[dim]This includes: mail, board posts, node associations[/dim]")

            if Confirm.ask(f"Are you sure you want to delete '{username}'?", default=False):
                # Delete related data first
                conn.execute("DELETE FROM user_nodes WHERE user_id = ?", (user["id"],))
                conn.execute("DELETE FROM messages WHERE sender_user_id = ? OR recipient_user_id = ?",
                           (user["id"], user["id"]))
                conn.execute("DELETE FROM board_read_positions WHERE user_id = ?", (user["id"],))
                conn.execute("DELETE FROM users WHERE id = ?", (user["id"],))
                conn.commit()
                console.print(f"[green]User '{username}' has been deleted[/green]")
            else:
                console.print("[yellow]Cancelled[/yellow]")

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        Prompt.ask("Press Enter to continue")

    def _list_nodes(self, conn: sqlite3.Connection):
        """List all known nodes."""
        self._clear()

        console.print(Panel("Known Nodes", style="cyan"))
        console.print()

        try:
            nodes = conn.execute("""
                SELECT n.id, n.node_id, n.short_name, n.long_name,
                       n.first_seen_us, n.last_seen_us,
                       (SELECT COUNT(*) FROM user_nodes WHERE node_id = n.id) as user_count
                FROM nodes n
                ORDER BY n.last_seen_us DESC
                LIMIT 50
            """).fetchall()

            if not nodes:
                console.print("[dim]No nodes recorded[/dim]")
            else:
                table = Table(box=box.SIMPLE)
                table.add_column("ID", style="dim")
                table.add_column("Node ID", style="cyan")
                table.add_column("Short Name", style="white")
                table.add_column("Users", justify="center")
                table.add_column("First Seen", style="dim")
                table.add_column("Last Seen", style="dim")

                for node in nodes:
                    first_seen = ""
                    if node["first_seen_us"]:
                        first_seen = datetime.fromtimestamp(node["first_seen_us"] / 1_000_000).strftime("%Y-%m-%d")
                    last_seen = ""
                    if node["last_seen_us"]:
                        last_seen = datetime.fromtimestamp(node["last_seen_us"] / 1_000_000).strftime("%Y-%m-%d %H:%M")

                    table.add_row(
                        str(node["id"]),
                        node["node_id"] or "",
                        node["short_name"] or "",
                        str(node["user_count"]),
                        first_seen,
                        last_seen
                    )

                console.print(table)

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        console.print()
        Prompt.ask("Press Enter to continue")

    def _view_node(self, conn: sqlite3.Connection):
        """View details of a specific node."""
        console.print()

        node_id = Prompt.ask("Node ID (e.g., !a1b2c3d4)")
        if not node_id:
            return

        try:
            node = conn.execute("""
                SELECT n.*,
                       (SELECT GROUP_CONCAT(u.username) FROM users u
                        JOIN user_nodes un ON u.id = un.user_id
                        WHERE un.node_id = n.id) as users
                FROM nodes n
                WHERE n.node_id = ?
            """, (node_id,)).fetchone()

            if not node:
                console.print(f"[red]Node '{node_id}' not found[/red]")
                Prompt.ask("Press Enter to continue")
                return

            console.print()
            console.print(f"[cyan]Node Details: {node_id}[/cyan]")
            console.print("-" * 40)
            console.print(f"Database ID: {node['id']}")
            console.print(f"Short Name: {node['short_name'] or 'N/A'}")
            console.print(f"Long Name: {node['long_name'] or 'N/A'}")

            if node["first_seen_us"]:
                first_seen = datetime.fromtimestamp(node["first_seen_us"] / 1_000_000)
                console.print(f"First Seen: {first_seen}")
            if node["last_seen_us"]:
                last_seen = datetime.fromtimestamp(node["last_seen_us"] / 1_000_000)
                console.print(f"Last Seen: {last_seen}")

            if node["users"]:
                console.print(f"Associated Users: {node['users']}")
            else:
                console.print("Associated Users: None")

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        console.print()
        Prompt.ask("Press Enter to continue")

    def _board_admin(self):
        """Board and post administration menu."""
        conn = self._get_db_connection()
        if not conn:
            console.print("[red]Database not available[/red]")
            console.print("[dim]Make sure the BBS has been started at least once[/dim]")
            Prompt.ask("\nPress Enter to continue")
            return

        while True:
            self._clear()

            console.print(Panel("Board & Post Administration", style="cyan"))
            console.print()

            # Get counts
            try:
                board_count = conn.execute("SELECT COUNT(*) FROM boards").fetchone()[0]
                post_count = conn.execute("SELECT COUNT(*) FROM messages WHERE msg_type = 'bulletin'").fetchone()[0]
            except Exception:
                board_count = post_count = 0

            table = Table(title="Administration Options", box=box.ROUNDED)
            table.add_column("Option", style="white")
            table.add_column("Info", style="cyan")

            table.add_row("1. List Boards", f"{board_count} boards")
            table.add_row("2. Create Board", "[dim]Add new board[/dim]")
            table.add_row("3. Delete Board", "[dim]Remove board and posts[/dim]")
            table.add_row("4. View Board Posts", f"{post_count} total posts")
            table.add_row("5. Delete Post", "[dim]Remove specific post[/dim]")

            console.print(table)
            console.print()
            console.print("[white]0. Back to Main Menu[/white]")
            console.print()

            try:
                choice = IntPrompt.ask("Select option", default=0)
            except KeyboardInterrupt:
                conn.close()
                return

            if choice == 0:
                conn.close()
                return
            elif choice == 1:
                self._list_boards(conn)
            elif choice == 2:
                self._create_board(conn)
            elif choice == 3:
                self._delete_board(conn)
            elif choice == 4:
                self._view_board_posts(conn)
            elif choice == 5:
                self._delete_post(conn)

    def _list_boards(self, conn: sqlite3.Connection):
        """List all boards."""
        self._clear()

        console.print(Panel("Boards", style="cyan"))
        console.print()

        try:
            boards = conn.execute("""
                SELECT b.id, b.name, b.description, b.is_restricted, b.created_at_us,
                       (SELECT COUNT(*) FROM messages WHERE board_id = b.id AND msg_type = 'bulletin') as post_count
                FROM boards b
                ORDER BY b.name
            """).fetchall()

            if not boards:
                console.print("[dim]No boards created[/dim]")
            else:
                table = Table(box=box.SIMPLE)
                table.add_column("ID", style="dim")
                table.add_column("Name", style="cyan")
                table.add_column("Description", style="white")
                table.add_column("Posts", justify="center")
                table.add_column("Restricted", justify="center")

                for board in boards:
                    restricted = "[yellow]Yes[/yellow]" if board["is_restricted"] else "No"
                    desc = (board["description"] or "")[:30]
                    table.add_row(
                        str(board["id"]),
                        board["name"],
                        desc,
                        str(board["post_count"]),
                        restricted
                    )

                console.print(table)

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        console.print()
        Prompt.ask("Press Enter to continue")

    def _create_board(self, conn: sqlite3.Connection):
        """Create a new board."""
        console.print()

        name = Prompt.ask("Board name (short, no spaces)")
        if not name or " " in name:
            console.print("[red]Invalid board name[/red]")
            Prompt.ask("Press Enter to continue")
            return

        description = Prompt.ask("Description (optional)", default="")
        restricted = Confirm.ask("Restricted (require login)?", default=False)

        try:
            import time
            now_us = int(time.time() * 1_000_000)
            conn.execute(
                "INSERT INTO boards (name, description, is_restricted, created_at_us) VALUES (?, ?, ?, ?)",
                (name.upper(), description, 1 if restricted else 0, now_us)
            )
            conn.commit()
            console.print(f"[green]Board '{name.upper()}' created[/green]")
        except sqlite3.IntegrityError:
            console.print(f"[red]Board '{name}' already exists[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        Prompt.ask("Press Enter to continue")

    def _delete_board(self, conn: sqlite3.Connection):
        """Delete a board and all its posts."""
        console.print()

        name = Prompt.ask("Board name to delete")
        if not name:
            return

        try:
            board = conn.execute(
                "SELECT id, name FROM boards WHERE LOWER(name) = LOWER(?)",
                (name,)
            ).fetchone()

            if not board:
                console.print(f"[red]Board '{name}' not found[/red]")
                Prompt.ask("Press Enter to continue")
                return

            post_count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE board_id = ? AND msg_type = 'bulletin'",
                (board["id"],)
            ).fetchone()[0]

            console.print(f"\n[red]WARNING: This will delete board '{board['name']}' and {post_count} posts![/red]")

            if Confirm.ask(f"Are you sure?", default=False):
                conn.execute("DELETE FROM messages WHERE board_id = ?", (board["id"],))
                conn.execute("DELETE FROM board_read_positions WHERE board_id = ?", (board["id"],))
                conn.execute("DELETE FROM boards WHERE id = ?", (board["id"],))
                conn.commit()
                console.print(f"[green]Board '{board['name']}' deleted[/green]")
            else:
                console.print("[yellow]Cancelled[/yellow]")

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        Prompt.ask("Press Enter to continue")

    def _view_board_posts(self, conn: sqlite3.Connection):
        """View posts in a board."""
        console.print()

        # List boards first
        boards = conn.execute("SELECT id, name FROM boards ORDER BY name").fetchall()
        if not boards:
            console.print("[dim]No boards available[/dim]")
            Prompt.ask("Press Enter to continue")
            return

        console.print("[cyan]Available boards:[/cyan]")
        for board in boards:
            console.print(f"  {board['name']}")
        console.print()

        name = Prompt.ask("Board name to view")
        if not name:
            return

        try:
            board = conn.execute(
                "SELECT id, name FROM boards WHERE LOWER(name) = LOWER(?)",
                (name,)
            ).fetchone()

            if not board:
                console.print(f"[red]Board '{name}' not found[/red]")
                Prompt.ask("Press Enter to continue")
                return

            self._clear()
            console.print(Panel(f"Posts in {board['name']}", style="cyan"))
            console.print()

            posts = conn.execute("""
                SELECT m.id, m.uuid, m.created_at_us,
                       COALESCE(u.username, 'Unknown') as author
                FROM messages m
                LEFT JOIN users u ON m.sender_user_id = u.id
                WHERE m.board_id = ? AND m.msg_type = 'bulletin'
                ORDER BY m.created_at_us DESC
                LIMIT 20
            """, (board["id"],)).fetchall()

            if not posts:
                console.print("[dim]No posts in this board[/dim]")
            else:
                table = Table(box=box.SIMPLE)
                table.add_column("ID", style="dim")
                table.add_column("Date", style="dim")
                table.add_column("Author", style="cyan")
                table.add_column("UUID", style="dim")

                for post in posts:
                    post_date = datetime.fromtimestamp(post["created_at_us"] / 1_000_000).strftime("%Y-%m-%d %H:%M")
                    table.add_row(
                        str(post["id"]),
                        post_date,
                        post["author"],
                        post["uuid"][:8] + "..."
                    )

                console.print(table)
                console.print()
                console.print("[dim]Note: Post content is encrypted and cannot be displayed here[/dim]")

        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        console.print()
        Prompt.ask("Press Enter to continue")

    def _delete_post(self, conn: sqlite3.Connection):
        """Delete a specific post."""
        console.print()

        post_id = Prompt.ask("Post ID to delete")
        if not post_id:
            return

        try:
            post_id = int(post_id)
            post = conn.execute("""
                SELECT m.id, m.uuid, b.name as board_name,
                       COALESCE(u.username, 'Unknown') as author
                FROM messages m
                LEFT JOIN boards b ON m.board_id = b.id
                LEFT JOIN users u ON m.sender_user_id = u.id
                WHERE m.id = ? AND m.msg_type = 'bulletin'
            """, (post_id,)).fetchone()

            if not post:
                console.print(f"[red]Post #{post_id} not found[/red]")
                Prompt.ask("Press Enter to continue")
                return

            console.print(f"\nPost #{post['id']} by {post['author']} in {post['board_name']}")

            if Confirm.ask("Delete this post?", default=False):
                conn.execute("DELETE FROM messages WHERE id = ?", (post_id,))
                conn.commit()
                console.print("[green]Post deleted[/green]")
            else:
                console.print("[yellow]Cancelled[/yellow]")

        except ValueError:
            console.print("[red]Invalid post ID[/red]")
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")

        Prompt.ask("Press Enter to continue")

    def _setup_wizard(self):
        """First-time setup wizard."""
        self._clear()

        console.print(Panel(
            "Welcome to advBBS Setup Wizard!\n\n"
            "This wizard will help you configure your BBS.\n"
            "Press Ctrl+C at any time to cancel.",
            title="[yellow]Setup Wizard[/yellow]",
            border_style="green"
        ))
        console.print()

        try:
            # Step 1: BBS Identity
            console.print("[cyan]Step 1: BBS Identity[/cyan]")
            console.print("-" * 40)

            name = Prompt.ask("BBS Name", default=self._get("bbs", "name", "advBBS"))
            self._set("bbs", "name", name)

            callsign = Prompt.ask("Callsign (short identifier)", default=self._get("bbs", "callsign", "FQ51"))
            self._set("bbs", "callsign", callsign)

            motd = Prompt.ask("Welcome message", default=self._get("bbs", "motd", "Welcome to advBBS!"))
            self._set("bbs", "motd", motd)
            console.print()

            # Step 2: Admin Password
            console.print("[cyan]Step 2: Admin Password[/cyan]")
            console.print("-" * 40)

            current_pass = self._get("bbs", "admin_password", "changeme")
            if current_pass == "changeme":
                console.print("[yellow]Default password detected - please change it![/yellow]")

            while True:
                password = Prompt.ask("Admin password (min 6 chars)", password=True)
                if len(password) < 6:
                    console.print("[red]Password too short[/red]")
                    continue
                confirm = Prompt.ask("Confirm password", password=True)
                if password != confirm:
                    console.print("[red]Passwords don't match[/red]")
                    continue
                self._set("bbs", "admin_password", password)
                break
            console.print()

            # Step 3: Meshtastic Connection
            console.print("[cyan]Step 3: Meshtastic Connection[/cyan]")
            console.print("-" * 40)
            console.print("[cyan]1.[/cyan] USB Serial (e.g., /dev/ttyUSB0)")
            console.print("[cyan]2.[/cyan] TCP Network (e.g., meshtastic.local:4403)")
            console.print("[cyan]3.[/cyan] Skip (configure later)")

            conn_choice = IntPrompt.ask("Connection type", default=1)

            if conn_choice == 1:
                self._set("meshtastic", "connection_type", "serial")
                port = Prompt.ask("Serial port", default="/dev/ttyUSB0")
                self._set("meshtastic", "serial_port", port)
            elif conn_choice == 2:
                self._set("meshtastic", "connection_type", "tcp")
                host = Prompt.ask("TCP host", default="localhost")
                self._set("meshtastic", "tcp_host", host)
                port = IntPrompt.ask("TCP port", default=4403)
                self._set("meshtastic", "tcp_port", port)
            console.print()

            # Step 4: Operating Mode
            console.print("[cyan]Step 4: Operating Mode[/cyan]")
            console.print("-" * 40)
            console.print("[cyan]1.[/cyan] Full - Mail and bulletin boards")
            console.print("[cyan]2.[/cyan] Mail Only - Private messages only")
            console.print("[cyan]3.[/cyan] Boards Only - Public bulletins only")
            console.print("[cyan]4.[/cyan] Repeater - Relay messages only")

            mode_choice = IntPrompt.ask("Mode", default=1)
            mode_map = {1: "full", 2: "mail_only", 3: "boards_only", 4: "repeater"}
            self._set("operating_mode", "mode", mode_map.get(mode_choice, "full"))
            console.print()

            # Step 5: Features
            console.print("[cyan]Step 5: Features[/cyan]")
            console.print("-" * 40)

            reg = Confirm.ask("Allow user registration?", default=True)
            self._set("features", "registration_enabled", reg)

            sync = Confirm.ask("Enable inter-BBS sync?", default=True)
            self._set("features", "sync_enabled", sync)
            self._set("sync", "enabled", sync)
            console.print()

            # Summary
            console.print("[green]Setup Complete![/green]")
            console.print("-" * 40)
            console.print(f"BBS Name: {self._get('bbs', 'name')}")
            console.print(f"Callsign: {self._get('bbs', 'callsign')}")
            console.print(f"Connection: {self._get('meshtastic', 'connection_type')}")
            console.print(f"Mode: {self._get('operating_mode', 'mode')}")
            console.print()

            if Confirm.ask("Save configuration?", default=True):
                if self._save_config():
                    console.print("[green]Configuration saved![/green]")
                else:
                    console.print("[red]Failed to save configuration[/red]")

        except KeyboardInterrupt:
            console.print("\n[yellow]Setup cancelled[/yellow]")

        Prompt.ask("\nPress Enter to continue")

    def _validate_config(self):
        """Validate configuration."""
        self._clear()

        console.print(Panel("Configuration Validation", style="cyan"))
        console.print()

        errors = []
        warnings = []

        # Check required fields
        if not self._get("bbs", "name"):
            errors.append("BBS name is empty")

        if self._get("bbs", "admin_password") == "changeme":
            errors.append("Admin password is still set to default 'changeme'")

        if len(self._get("bbs", "admin_password", "")) < 6:
            errors.append("Admin password is too short (min 6 characters)")

        # Check meshtastic config
        conn_type = self._get("meshtastic", "connection_type", "serial")
        if conn_type not in ["serial", "tcp", "ble"]:
            errors.append(f"Invalid connection type: {conn_type}")

        if conn_type == "serial":
            port = self._get("meshtastic", "serial_port", "")
            if not port:
                warnings.append("Serial port not configured")

        # Check operating mode
        mode = self._get("operating_mode", "mode", "full")
        if mode not in ["full", "mail_only", "boards_only", "repeater"]:
            errors.append(f"Invalid operating mode: {mode}")

        # Check sync peers
        peers = self._get("sync", "peers", [])
        if self._get("sync", "enabled", True) and not peers:
            warnings.append("Sync enabled but no peers configured")

        # Display results
        if errors:
            console.print("[red]Errors:[/red]")
            for err in errors:
                console.print(f"  [red]✗[/red] {err}")
            console.print()

        if warnings:
            console.print("[yellow]Warnings:[/yellow]")
            for warn in warnings:
                console.print(f"  [yellow]![/yellow] {warn}")
            console.print()

        if not errors and not warnings:
            console.print("[green]✓ Configuration is valid![/green]")
        elif not errors:
            console.print("[green]✓ No critical errors found[/green]")
        else:
            console.print(f"[red]Found {len(errors)} error(s)[/red]")

        console.print()
        Prompt.ask("Press Enter to continue")

    def _backup_config(self):
        """Backup configuration file."""
        self._clear()

        if not self.config_path.exists():
            console.print("[yellow]No config file to backup[/yellow]")
            Prompt.ask("Press Enter to continue")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.config_path.with_suffix(f".{timestamp}.bak")

        try:
            shutil.copy(self.config_path, backup_path)
            console.print(f"[green]Backup saved to:[/green] {backup_path}")
        except Exception as e:
            console.print(f"[red]Backup failed: {e}[/red]")

        Prompt.ask("\nPress Enter to continue")

    def _view_config(self):
        """View raw configuration."""
        self._clear()

        console.print(Panel("Current Configuration (TOML)", style="cyan"))
        console.print()

        try:
            toml_str = toml.dumps(self.config)
            console.print(toml_str)
        except Exception as e:
            console.print(f"[red]Error displaying config: {e}[/red]")

        console.print()
        Prompt.ask("Press Enter to continue")

    def _save_only(self):
        """Save config and stay in menu."""
        if self._save_config():
            console.print("[green]Configuration saved![/green]")
        else:
            console.print("[red]Failed to save config[/red]")
        Prompt.ask("Press Enter to continue")

    def _save_and_restart(self):
        """Save config and signal BBS to restart, stay in menu."""
        self._clear()

        console.print("[cyan]Saving configuration...[/cyan]")
        if not self._save_config():
            console.print("[red]Failed to save config[/red]")
            Prompt.ask("Press Enter to continue")
            return

        console.print("[green]Configuration saved![/green]")
        console.print()

        # Write restart signal file
        restart_file = Path("/tmp/advbbs_restart")
        try:
            restart_file.touch()
            console.print("[cyan]BBS restart signal sent.[/cyan]")
            console.print()
            console.print("The BBS will restart momentarily to apply changes.")
        except Exception as e:
            console.print(f"[red]Failed to signal restart: {e}[/red]")

        Prompt.ask("\nPress Enter to continue")

    def _save_restart_exit(self):
        """Save config, signal BBS restart, and exit config tool."""
        console.print("[cyan]Saving configuration...[/cyan]")
        if not self._save_config():
            console.print("[red]Failed to save config[/red]")
            Prompt.ask("Press Enter to continue")
            return

        console.print("[green]Configuration saved![/green]")

        # Write restart signal file
        restart_file = Path("/tmp/advbbs_restart")
        try:
            restart_file.touch()
            console.print("[cyan]BBS restart signal sent.[/cyan]")
        except Exception as e:
            console.print(f"[red]Failed to signal restart: {e}[/red]")

        self.running = False

    def _exit_no_save(self):
        """Exit without saving, no prompt."""
        self.running = False

    def _exit_handler(self):
        """Handle Ctrl+C with unsaved changes check."""
        if self.modified:
            if Confirm.ask("[yellow]You have unsaved changes. Save before exit?[/yellow]", default=True):
                self._save_config()
        self.running = False


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="advBBS Configuration Tool")
    parser.add_argument("--config", "-c", type=Path, help="Path to config file")
    args = parser.parse_args()

    try:
        tool = ConfigTool(args.config)
        tool.run()
    except KeyboardInterrupt:
        console.print("\n[yellow]Exiting...[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
