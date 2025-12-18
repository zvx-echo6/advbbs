"""
advBBS Configuration Module

Handles loading, validation, and management of configuration settings.
"""

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Use tomllib for Python 3.11+, tomli for earlier versions
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError:
        raise ImportError("Please install tomli: pip install tomli")


@dataclass
class BBSConfig:
    """BBS general settings."""
    name: str = "advBBS"
    callsign: str = "ADV"
    admin_password: str = "changeme"
    motd: str = "Welcome to advBBS!"
    timezone: str = "America/Boise"  # Timezone for display (e.g., America/New_York, UTC)
    max_message_age_days: int = 30
    announcement_interval_hours: int = 12
    announcements_enabled: bool = True  # Enable/disable periodic announcements
    announcement_message: str = ""  # Custom announcement (empty = default with stats)
    session_timeout_minutes: int = 30  # Auto-logout after inactivity
    reply_to_unknown_commands: bool = True  # Reply with "Unknown cmd" for unrecognized messages


@dataclass
class DatabaseConfig:
    """Database settings."""
    path: str = "/data/advbbs.db"
    backup_path: str = "/data/backups"
    backup_interval_hours: int = 24


@dataclass
class MeshtasticConfig:
    """Meshtastic connection settings."""
    connection_type: str = "serial"  # serial | tcp | ble
    serial_port: str = "/dev/ttyUSB0"
    tcp_host: str = "localhost"
    tcp_port: int = 4403
    # Channel settings
    public_channel: int = 0  # Default public channel (e.g., LongFast)
    respond_channel: int = -1  # Channel to respond on (-1 = DM only, 0+ = specific channel)
    ignore_channels: list[int] = field(default_factory=list)  # Channels to ignore
    dm_only: bool = True  # Only respond to direct messages (not channel broadcasts)


@dataclass
class CryptoConfig:
    """Cryptography settings."""
    argon2_time_cost: int = 3
    argon2_memory_kb: int = 32768  # 32MB - RPi friendly
    argon2_parallelism: int = 1


@dataclass
class FeaturesConfig:
    """Feature toggles."""
    mail_enabled: bool = True
    boards_enabled: bool = True
    sync_enabled: bool = True
    registration_enabled: bool = True


@dataclass
class OperatingModeConfig:
    """Operating mode settings."""
    mode: str = "full"  # full | mail_only | boards_only | repeater


@dataclass
class RepeaterConfig:
    """Repeater mode settings."""
    forward_mail: bool = True
    forward_bulletins: bool = True
    forward_to_peers: list[str] = field(default_factory=list)
    announce_enabled: bool = True
    announce_message: str = "advBBS Relay active. DM !send <user> <msg> to send mail."
    announce_interval_hours: int = 12
    announce_channel: int = 0


@dataclass
class SyncPeer:
    """Single sync peer configuration."""
    node_id: str
    name: str
    protocol: str  # advbbs
    enabled: bool = True  # Enable/disable this peer
    # Protocol-specific settings
    use_channel: bool = False  # If True, use channel broadcast instead of DM
    channel: int = 2  # Channel to use if use_channel=True


@dataclass
class SyncConfig:
    """Sync settings."""
    enabled: bool = True
    mail_delivery_mode: str = "instant"  # instant | batched
    mail_batch_interval_minutes: int = 5
    mail_retry_attempts: int = 3
    mail_ack_timeout_seconds: int = 30
    mail_retry_backoff_base: int = 60
    mail_max_hops: int = 3
    # MAILDLV retry settings (for delivery confirmation)
    maildlv_retry_interval_seconds: int = 45
    maildlv_max_attempts: int = 3
    maildlv_timeout_seconds: int = 300
    participate_in_mail_relay: bool = True
    peers: list[SyncPeer] = field(default_factory=list)
    # RAP (Route Announcement Protocol) settings
    rap_enabled: bool = True
    rap_heartbeat_interval_seconds: int = 43200  # 12 hours
    rap_heartbeat_timeout_seconds: int = 60  # Wait for PONG
    rap_unreachable_threshold: int = 2  # Failed pings before UNREACHABLE
    rap_dead_threshold: int = 5  # Total failed pings before DEAD
    rap_route_expiry_seconds: int = 129600  # Routes expire after 36 hours (3 missed heartbeats)
    rap_route_share_interval_seconds: int = 86400  # Share full route table every 24 hours
    rap_pending_mail_expiry_seconds: int = 86400  # Pending mail expires after 24 hours
    rap_pending_mail_max_retries: int = 10  # Max retry attempts


@dataclass
class AdminChannelConfig:
    """Admin channel settings."""
    enabled: bool = True
    channel_index: int = 7
    sync_bans: bool = True
    sync_peer_status: bool = True
    trusted_peers: list[str] = field(default_factory=list)
    require_mutual_trust: bool = True


@dataclass
class RateLimitsConfig:
    """Rate limiting settings."""
    messages_per_minute: int = 10
    sync_messages_per_minute: int = 20
    commands_per_minute: int = 30


@dataclass
class WebReaderConfig:
    """Web reader interface settings."""
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8080
    use_bbs_auth: bool = True
    session_timeout_minutes: int = 30
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    requests_per_minute: int = 60
    login_attempts_per_minute: int = 5
    allow_board_browsing: bool = True
    allow_mail_reading: bool = True
    allow_user_list: bool = False
    show_node_status: bool = True
    terminal_style: bool = True
    motd_on_login: bool = True


@dataclass
class CLIConfigSettings:
    """CLI configuration interface settings."""
    enabled: bool = True
    require_admin: bool = True
    auto_apply: bool = False
    backup_on_change: bool = True
    color_output: bool = True
    menu_timeout_minutes: int = 30


@dataclass
class LoggingConfig:
    """Logging settings."""
    level: str = "INFO"
    file: str = "/var/log/advbbs.log"
    max_size_mb: int = 10
    backup_count: int = 3
    enabled: bool = True  # Added for compatibility


@dataclass
class Config:
    """Main configuration container."""
    bbs: BBSConfig = field(default_factory=BBSConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    meshtastic: MeshtasticConfig = field(default_factory=MeshtasticConfig)
    crypto: CryptoConfig = field(default_factory=CryptoConfig)
    features: FeaturesConfig = field(default_factory=FeaturesConfig)
    operating_mode: OperatingModeConfig = field(default_factory=OperatingModeConfig)
    repeater: RepeaterConfig = field(default_factory=RepeaterConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)
    admin_channel: AdminChannelConfig = field(default_factory=AdminChannelConfig)
    rate_limits: RateLimitsConfig = field(default_factory=RateLimitsConfig)
    web_reader: WebReaderConfig = field(default_factory=WebReaderConfig)
    cli_config: CLIConfigSettings = field(default_factory=CLIConfigSettings)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    def validate(self) -> list[str]:
        """Validate configuration and return list of errors."""
        errors = []

        # BBS validation
        if not self.bbs.name:
            errors.append("bbs.name cannot be empty")
        if self.bbs.admin_password == "changeme":
            errors.append("bbs.admin_password must be changed from default")

        # Operating mode validation
        valid_modes = ["full", "mail_only", "boards_only", "repeater"]
        if self.operating_mode.mode not in valid_modes:
            errors.append(f"operating_mode.mode must be one of: {valid_modes}")

        # Meshtastic validation
        valid_connections = ["serial", "tcp", "ble"]
        if self.meshtastic.connection_type not in valid_connections:
            errors.append(f"meshtastic.connection_type must be one of: {valid_connections}")

        # Crypto validation (RPi constraints)
        if self.crypto.argon2_memory_kb > 65536:  # 64MB max for RPi
            errors.append("crypto.argon2_memory_kb should not exceed 65536 (64MB) for RPi compatibility")

        # Sync peer validation
        for peer in self.sync.peers:
            if peer.protocol not in ["tc2", "meshing-around", "advbbs"]:
                errors.append(f"Invalid protocol '{peer.protocol}' for peer {peer.name}")

        return errors

    def save(self, path: Path):
        """Save configuration to TOML file."""
        import toml  # For writing

        # Convert dataclasses to dict
        data = self._to_dict()

        with open(path, "w") as f:
            toml.dump(data, f)

    def _to_dict(self) -> dict[str, Any]:
        """Convert config to dictionary for serialization."""
        from dataclasses import asdict
        return asdict(self)


def _coerce_types(data: dict, dataclass_type) -> dict:
    """Coerce string values to proper types based on dataclass field annotations."""
    from dataclasses import fields
    result = {}
    field_types = {f.name: f.type for f in fields(dataclass_type)}

    for key, value in data.items():
        if key not in field_types:
            continue  # Skip unknown fields
        expected_type = field_types[key]

        # Handle string-to-bool conversion
        if expected_type == bool and isinstance(value, str):
            result[key] = value.lower() in ('true', '1', 'yes')
        # Handle string-to-int conversion
        elif expected_type == int and isinstance(value, str):
            try:
                result[key] = int(value)
            except ValueError:
                result[key] = value
        else:
            result[key] = value

    return result


def load_config(path: Path) -> Config:
    """Load configuration from TOML file."""
    config = Config()

    if not path.exists():
        return config

    with open(path, "rb") as f:
        data = tomllib.load(f)

    # Map TOML sections to config dataclasses
    if "bbs" in data:
        config.bbs = BBSConfig(**_coerce_types(data["bbs"], BBSConfig))

    if "database" in data:
        config.database = DatabaseConfig(**_coerce_types(data["database"], DatabaseConfig))

    if "meshtastic" in data:
        config.meshtastic = MeshtasticConfig(**_coerce_types(data["meshtastic"], MeshtasticConfig))

    if "crypto" in data:
        config.crypto = CryptoConfig(**_coerce_types(data["crypto"], CryptoConfig))

    if "features" in data:
        config.features = FeaturesConfig(**_coerce_types(data["features"], FeaturesConfig))

    if "operating_mode" in data:
        config.operating_mode = OperatingModeConfig(**_coerce_types(data["operating_mode"], OperatingModeConfig))

    if "repeater" in data:
        config.repeater = RepeaterConfig(**_coerce_types(data["repeater"], RepeaterConfig))

    if "sync" in data:
        sync_data = data["sync"].copy()
        peers = []
        for peer_data in sync_data.pop("peers", []):
            peers.append(SyncPeer(**peer_data))
        config.sync = SyncConfig(**_coerce_types(sync_data, SyncConfig), peers=peers)

    if "admin_channel" in data:
        config.admin_channel = AdminChannelConfig(**_coerce_types(data["admin_channel"], AdminChannelConfig))

    if "rate_limits" in data:
        config.rate_limits = RateLimitsConfig(**_coerce_types(data["rate_limits"], RateLimitsConfig))

    if "web_reader" in data:
        config.web_reader = WebReaderConfig(**_coerce_types(data["web_reader"], WebReaderConfig))

    if "cli_config" in data:
        config.cli_config = CLIConfigSettings(**_coerce_types(data["cli_config"], CLIConfigSettings))

    if "logging" in data:
        config.logging = LoggingConfig(**_coerce_types(data["logging"], LoggingConfig))

    return config


def create_default_config(path: Path):
    """Create a default configuration file."""
    config = Config()
    config.save(path)
