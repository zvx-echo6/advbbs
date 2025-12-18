# advBBS Quick Start Guide

Get advBBS running in 5 minutes.

## What is advBBS?

advBBS is a bulletin board system that runs over Meshtastic mesh networks. It provides:

- **Mail**: Send private messages to users on any connected BBS
- **Boards**: Public message boards for community discussion  
- **Federation**: Multiple BBS instances share messages automatically
- **RAP Routing**: Automatically discovers paths to remote BBS nodes

## Requirements

- Python 3.10+
- Meshtastic device (radio, or meshtasticd simulator)
- SQLite (included with Python)

## Installation

```bash
# Clone the repository
git clone https://github.com/zvx-echo6/advbbs.git
cd advbbs

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install
pip install -e .
```

## Basic Configuration

Create `config.toml`:

```toml
[bbs]
name = "MyBBS"
callsign = "CALL"
admin_password = "your-secure-password"

[database]
path = "./data/bbs.db"

[meshtastic]
connection_type = "tcp"      # or "serial"
tcp_host = "127.0.0.1"
tcp_port = 4403              # meshtasticd port

[features]
mail_enabled = true
boards_enabled = true
sync_enabled = true

[sync]
enabled = true
rap_enabled = true
```

## Running

```bash
# Start the BBS
advbbs --config config.toml
```

## User Commands

Users interact with advBBS by sending text messages to your Meshtastic node:

| Command | Description |
|---------|-------------|
| `!help` | Show available commands |
| `!register <user> <pass>` | Create account |
| `!login <user> <pass>` | Log in |
| `!mail` | Check your mailbox |
| `!send <user@BBS> <msg>` | Send mail |
| `!boards` | List message boards |
| `!read <board>` | Read board messages |
| `!post <board> <msg>` | Post to board |

## Federation (Multi-BBS)

To connect multiple BBS instances, add peers to your config:

```toml
[[sync.peers]]
node_id = "!abcd1234"        # Other BBS Meshtastic node ID
name = "RemoteBBS"
protocol = "advbbs"
```

RAP (Route Announcement Protocol) automatically discovers paths between BBS nodes, even through intermediate hops.

## RAP Timings

Default production values (configurable in `[sync]`):

| Setting | Default | Description |
|---------|---------|-------------|
| `rap_heartbeat_interval_seconds` | 43200 (12h) | How often to ping peers |
| `rap_route_expiry_seconds` | 129600 (36h) | When routes become stale |
| `rap_route_share_interval_seconds` | 86400 (24h) | How often to broadcast routes |

For testing, use shorter intervals (30-60 seconds).

## Architecture

```
User Radio  <--mesh-->  BBS Node  <--mesh-->  Peer BBS  <--mesh-->  Remote BBS
    |                      |                      |                      |
    |    !send user@R3     |                      |                      |
    |--------------------->|                      |                      |
    |                      |------MAILREQ-------->|                      |
    |                      |                      |------MAILREQ-------->|
    |                      |                      |<-----MAILACK---------|
    |                      |<-----MAILACK---------|                      |
    |                      |------MAILDAT-------->|                      |
    |                      |                      |------MAILDAT-------->|
    |                      |                      |                      | (stored)
```

## Next Steps

- [Configuration Reference](configuration.md)
- [Commands Reference](commands.md)  
- [Federation & Sync](sync.md)
- [RAP Testing](rap-testing.md)
- [Security](security.md)
