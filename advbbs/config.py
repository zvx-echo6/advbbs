"""
advBBS Configuration Module

Handles loading, validation, and management of configuration settings.

Environment variables can be used as fallbacks for missing config values:
  ADVBBS_NAME          - BBS name
  ADVBBS_CALLSIGN      - BBS callsign
  ADVBBS_ADMIN_PASS    - Admin password
  ADVBBS_MOTD          - Message of the day
  TZ                   - Timezone
  ADVBBS_CONNECTION    - Meshtastic connection type (serial, tcp, ble)
  ADVBBS_SERIAL_PORT   - Serial port
  ADVBBS_TCP_HOST      - TCP host for Meshtastic
  ADVBBS_TCP_PORT      - TCP port for Meshtastic
  ADVBBS_MODE          - Operating mode (full, mail_only, boards_only, repeater)
  ADVBBS_LOG_LEVEL     - Logging level (DEBUG, INFO, WARNING, ERROR)
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _is_running_in_docker() -> bool:
    """Detect if running inside a Docker container."""
    return os.path.exists("/.dockerenv") or os.environ.get("ADVBBS_DOCKER", "").lower() in ("1", "true", "yes")


def _get_default_data_path() -> str:
    """Return appropriate data path based on environment."""
    if _is_running_in_docker():
        return "/data"
    return "./data"


def _get_default_log_file() -> str:
    """Return appropriate log file path based on environment."""
    if _is_running_in_docker():
        return "/var/log/advbbs.log"
    return "./logs/advbbs.log"

# Use tomllib for Python 3.11+, tomli for earlier versions
if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomli as tomllib
    except ImportError:
        raise ImportError("Please install tomli: pip install tomli")


# Environment variable to config field mapping
# Format: (env_var, config_section, config_field, type_converter)
ENV_VAR_MAPPING = [
    ("ADVBBS_NAME", "bbs", "name", str),
    ("ADVBBS_CALLSIGN", "bbs", "callsign", str),
    ("ADVBBS_ADMIN_PASS", "bbs", "admin_password", str),
    ("ADVBBS_MOTD", "bbs", "motd", str),
    ("TZ", "bbs", "timezone", str),
    ("ADVBBS_CONNECTION", "meshtastic", "connection_type", str),
    ("ADVBBS_SERIAL_PORT", "meshtastic", "serial_port", str),
    ("ADVBBS_TCP_HOST", "meshtastic", "tcp_host", str),
    ("ADVBBS_TCP_PORT", "meshtastic", "tcp_port", int),
    ("ADVBBS_MODE", "operating_mode", "mode", str),
    ("ADVBBS_LOG_LEVEL", "logging", "level", str),
]


@dataclass
class BBSConfig:
    """BBS general settings."""
    name: str = "advBBS"
    callsign: str = "ADV"
    admin_password: str = "changeme"
    motd: str = "Welcome to advBBS!"
    timezone: str = "UTC"  # Timezone for display (e.g., America/New_York, UTC)
    max_message_age_days: int = 30
    announcement_interval_hours: int = 12
    announcements_enabled: bool = True  # Enable/disable periodic announcements
    announcement_channel: int = 0  # Channel to broadcast announcements on
    announcement_message: str = ""  # Custom announcement (empty = default with stats)
    session_timeout_minutes: int = 30  # Auto-logout after inactivity
    reply_to_unknown_commands: bool = False  # Reply with "Unknown cmd" for unrecognized messages


@dataclass
class DatabaseConfig:
    """Database settings."""
    path: str = field(default_factory=lambda: f"{_get_default_data_path()}/advbbs.db")
    backup_path: str = field(default_factory=lambda: f"{_get_default_data_path()}/backups")
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
    registration_mode: str = "open"  # open | closed | limited
    registration_whitelist: list[str] = field(default_factory=list)  # Node IDs allowed when mode=limited
    max_users: int = 0  # Maximum registered users (0 = unlimited)


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
    file: str = field(default_factory=_get_default_log_file)
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
            if peer.protocol != "advbbs":
                errors.append(f"Invalid protocol '{peer.protocol}' for peer {peer.name} (only 'advbbs' is supported)")

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


def _apply_env_fallbacks(config: "Config", loaded_sections: set[str]) -> None:
    """
    Apply environment variable fallbacks for missing config values.

    Only applies if the config section/field was not explicitly set in the config file.
    Config file values always take precedence.
    """
    for env_var, section, field_name, type_converter in ENV_VAR_MAPPING:
        env_value = os.environ.get(env_var)
        if env_value is None:
            continue

        # Get the config section object
        section_obj = getattr(config, section, None)
        if section_obj is None:
            continue

        # Only apply env var if this field uses the default value
        # (i.e., wasn't explicitly set in the config file)
        current_value = getattr(section_obj, field_name, None)
        default_obj = type(section_obj)()
        default_value = getattr(default_obj, field_name, None)

        # If current value equals default, apply env var
        if current_value == default_value:
            try:
                converted_value = type_converter(env_value)
                setattr(section_obj, field_name, converted_value)
            except (ValueError, TypeError):
                # If conversion fails, skip this env var
                pass


def load_config(path: Path) -> Config:
    """
    Load configuration from TOML file.

    Environment variables are used as fallbacks for any values not
    explicitly set in the config file. Config file always takes precedence.
    """
    config = Config()
    loaded_sections: set[str] = set()

    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)

        # Map TOML sections to config dataclasses
        if "bbs" in data:
            config.bbs = BBSConfig(**_coerce_types(data["bbs"], BBSConfig))
            loaded_sections.add("bbs")

        if "database" in data:
            config.database = DatabaseConfig(**_coerce_types(data["database"], DatabaseConfig))
            loaded_sections.add("database")

        if "meshtastic" in data:
            config.meshtastic = MeshtasticConfig(**_coerce_types(data["meshtastic"], MeshtasticConfig))
            loaded_sections.add("meshtastic")

        if "crypto" in data:
            config.crypto = CryptoConfig(**_coerce_types(data["crypto"], CryptoConfig))
            loaded_sections.add("crypto")

        if "features" in data:
            config.features = FeaturesConfig(**_coerce_types(data["features"], FeaturesConfig))
            loaded_sections.add("features")

        if "operating_mode" in data:
            config.operating_mode = OperatingModeConfig(**_coerce_types(data["operating_mode"], OperatingModeConfig))
            loaded_sections.add("operating_mode")

        if "repeater" in data:
            config.repeater = RepeaterConfig(**_coerce_types(data["repeater"], RepeaterConfig))
            loaded_sections.add("repeater")

        if "sync" in data:
            sync_data = data["sync"].copy()
            peers = []
            for peer_data in sync_data.pop("peers", []):
                peers.append(SyncPeer(**peer_data))
            config.sync = SyncConfig(**_coerce_types(sync_data, SyncConfig), peers=peers)
            loaded_sections.add("sync")

        if "admin_channel" in data:
            config.admin_channel = AdminChannelConfig(**_coerce_types(data["admin_channel"], AdminChannelConfig))
            loaded_sections.add("admin_channel")

        if "rate_limits" in data:
            config.rate_limits = RateLimitsConfig(**_coerce_types(data["rate_limits"], RateLimitsConfig))
            loaded_sections.add("rate_limits")

        if "web_reader" in data:
            config.web_reader = WebReaderConfig(**_coerce_types(data["web_reader"], WebReaderConfig))
            loaded_sections.add("web_reader")

        if "cli_config" in data:
            config.cli_config = CLIConfigSettings(**_coerce_types(data["cli_config"], CLIConfigSettings))
            loaded_sections.add("cli_config")

        if "logging" in data:
            config.logging = LoggingConfig(**_coerce_types(data["logging"], LoggingConfig))
            loaded_sections.add("logging")

    # Apply environment variable fallbacks for missing values
    _apply_env_fallbacks(config, loaded_sections)

    return config


def create_default_config(path: Path):
    """Create a default configuration file."""
    config = Config()
    config.save(path)
