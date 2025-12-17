# advBBS Configuration

Configuration is stored in `config.toml`.

## Quick Start

Minimal configuration to get started:

```toml
[bbs]
name = "My BBS"
callsign = "MYBBS"
admin_password = "changeme"

[meshtastic]
connection_type = "serial"
serial_port = "/dev/ttyUSB0"
```

---

## Full Configuration Reference

### [bbs] - Basic Settings

```toml
[bbs]
name = "advBBS"                     # Display name
callsign = "FQ51"                    # Short identifier (used in user@BBS addressing)
admin_password = "changeme"          # Required at startup
motd = "Welcome to advBBS!"         # Message of the day
max_message_age_days = 30            # Auto-expire old messages
announcement_interval_hours = 12     # Periodic announcement (0 = disabled)
announcement_message = ""            # Custom announcement (empty = default)
session_timeout_minutes = 30         # Auto-logout after inactivity
reply_to_unknown_commands = false    # Reply to unknown commands
```

**Announcement Message Variables:**
- `{callsign}` - BBS callsign
- `{name}` - BBS name
- `{users}` - Registered user count
- `{msgs}` - Total message count

Example: `"[{callsign}] {name} online. DM !bbs for commands."`

### [database] - Storage

```toml
[database]
path = "/var/lib/advbbs/advbbs.db"
backup_path = "/var/lib/advbbs/backups"
backup_interval_hours = 24
```

### [meshtastic] - Radio Connection

```toml
[meshtastic]
connection_type = "serial"           # serial | tcp | ble
serial_port = "/dev/ttyUSB0"         # For serial connection
tcp_host = "localhost"               # For TCP connection
tcp_port = 4403                      # For TCP connection
channel_index = 0                    # Primary channel for BBS
public_channel = 0                   # Channel for broadcasts
```

### [crypto] - Password Hashing

```toml
[crypto]
argon2_time_cost = 3
argon2_memory_kb = 32768             # 32MB
argon2_parallelism = 1
```

### [features] - Feature Flags

```toml
[features]
mail_enabled = true                  # Private mail system
boards_enabled = true                # Public bulletin boards
sync_enabled = true                  # Inter-BBS synchronization
registration_enabled = true          # Allow new user registration
```

### [operating_mode] - BBS Mode

```toml
[operating_mode]
mode = "full"                        # full | mail_only | boards_only | repeater
```

| Mode | Description |
|------|-------------|
| `full` | All features enabled |
| `mail_only` | Only private mail, no boards |
| `boards_only` | Only bulletin boards, no mail |
| `repeater` | Forward only, no local users |

### [repeater] - Repeater Mode Settings

Only used when `mode = "repeater"`:

```toml
[repeater]
forward_mail = true
forward_bulletins = true
forward_to_peers = []                # Empty = all peers
```

---

## Sync Configuration

### [sync] - General Sync Settings

```toml
[sync]
enabled = true
bulletin_sync_interval_minutes = 60  # How often to sync bulletins
mail_delivery_mode = "instant"       # instant | batched
mail_batch_interval_minutes = 5      # Only if batched
```

### [[sync.peers]] - Peer Configuration

Add peer BBS nodes:

```toml
[[sync.peers]]
node_id = "!abc12345"                # Meshtastic node ID
name = "REMOTE1"                     # BBS callsign (for user@BBS addressing)
protocol = "fq51"                    # Protocol: fq51
enabled = true                       # Enable/disable this peer
```

**Important:** The `name` field is what users type in `!send user@NAME`.

### Example: Multiple Peers

```toml
[[sync.peers]]
node_id = "!abc12345"
name = "TV51"
protocol = "fq51"
enabled = true

[[sync.peers]]
node_id = "!def67890"
name = "AIDA"
protocol = "fq51"
enabled = true
```

### Sync Participation

```toml
[sync]
participate_in_mail_relay = true     # Help deliver others' mail
participate_in_bulletin_sync = true  # Sync boards with peers
```

---

## Admin Channel

Optional dedicated channel for admin operations:

```toml
[admin_channel]
enabled = true
channel_index = 7                    # Must match Meshtastic channel config
sync_bans = true                     # Sync ban/unban across peers
sync_peer_status = true              # Share peer health info
trusted_peers = ["!abc123"]          # Only accept admin sync from these
require_mutual_trust = true          # Both sides must trust each other
```

---

## Rate Limiting

```toml
[rate_limits]
messages_per_minute = 10
sync_messages_per_minute = 20
commands_per_minute = 30
```

---

## Web Reader (Optional)

Read-only web interface:

```toml
[web_reader]
enabled = false
host = "127.0.0.1"
port = 8080

# Authentication
use_bbs_auth = true                  # Use BBS credentials
session_timeout_minutes = 30
max_failed_logins = 5
lockout_minutes = 15

# Rate limiting
requests_per_minute = 60
login_attempts_per_minute = 5

# Features (all read-only)
allow_board_browsing = true
allow_mail_reading = true
allow_user_list = false
show_node_status = true

# Appearance
terminal_style = true                # Green-on-black aesthetic
motd_on_login = true
```

---

## CLI Configuration Tool

Interactive configuration editor:

```toml
[cli_config]
enabled = true
require_admin = true
auto_apply = false                   # Apply changes immediately vs restart
backup_on_change = true
color_output = true
menu_timeout_minutes = 30
```

---

## Logging

```toml
[logging]
level = "INFO"                       # DEBUG | INFO | WARNING | ERROR
file = "/var/log/advbbs.log"
max_size_mb = 10
backup_count = 3
```

---

## Environment Variables

Some settings can be overridden via environment:

| Variable | Description |
|----------|-------------|
| `advBBS_CONFIG` | Path to config file |
| `advBBS_DB_PATH` | Database path |
| `advBBS_LOG_LEVEL` | Logging level |
