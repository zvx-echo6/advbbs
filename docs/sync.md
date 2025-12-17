# advBBS Inter-BBS Sync

How advBBS nodes synchronize and route mail between each other.

## Overview

advBBS uses a peer-to-peer sync model where configured BBS nodes exchange:
- **Remote mail** using `user@BBS` addressing

Only explicitly configured peers can participate in sync - this is a security whitelist.

> **Note:** Bulletin sync has been removed. Boards are local-only to each BBS. This simplifies the federation model and reduces mesh traffic.

## Peer Configuration

Peers are configured in `config.toml`:

```toml
[sync]
enabled = true

# Add peer BBS nodes
[[sync.peers]]
node_id = "!abc12345"                  # Meshtastic node ID
name = "REMOTE1"                       # BBS name (used in user@BBS addressing)
protocol = "fq51"                      # Protocol type
enabled = true                         # Enable/disable this peer

[[sync.peers]]
node_id = "!def67890"
name = "REMOTE2"
protocol = "fq51"
enabled = true
```

**Important fields:**
- `node_id` - The Meshtastic node ID (e.g., `!abc12345`)
- `name` - The BBS callsign used in `user@BBS` mail addressing
- `protocol` - Currently only `fq51` is supported

## What Syncs

| Content | Syncs? | Notes |
|---------|--------|-------|
| Remote mail (`user@BBS`) | ✅ Yes | Routed through peers |
| Local mail (`user`) | ❌ No | Stays on local BBS |
| Board posts | ❌ No | Local only |

## Sync Triggers

| Trigger | What Happens |
|---------|--------------|
| Remote mail | `!send user@BBS` routes immediately through peers |

## Security: Peer Whitelisting

**Only messages from configured peers are accepted.**

This prevents:
- Unauthorized nodes from injecting messages
- Abuse of the relay system
- Spam flooding from unknown sources

```python
# From manager.py - Security check
def handle_mail_protocol(self, message: str, sender: str) -> bool:
    if not self.is_peer(sender):
        logger.warning(f"Rejected BBS protocol message from non-peer: {sender}")
        return False
```

---

## FQ51 Native Protocol

The wire protocol for advBBS-to-advBBS communication.

### Why DM-Based?

| Aspect | Channel Broadcast | Targeted DM |
|--------|-------------------|-------------|
| Control | Anyone can see | Explicit peer only |
| Privacy | Public | Private |
| Reliability | Best-effort | ACK-confirmed |
| Bandwidth | Floods mesh | Point-to-point |

### Protocol Format

All messages use this format:

```
FQ51|<version>|<msg_type>|<payload>
```

- **Version**: Currently `1`
- **Payload**: JSON encoded, then base64 for binary safety

### Message Types

| Type | Purpose | Payload |
|------|---------|---------|
| `HELLO` | Handshake, announce capabilities | `callsign:name\|capabilities` |
| `SYNC_ACK` | Acknowledge receipt | `uuid` |

### Mail Protocol Messages

Remote mail uses a separate, dedicated protocol:

| Message | Purpose |
|---------|---------|
| `MAILREQ` | Request to send mail (includes route info) |
| `MAILACK` | Destination accepts, ready for chunks |
| `MAILNAK` | Delivery rejected (user not found, loop, etc) |
| `MAILDAT` | Message chunk (max 150 chars each) |
| `MAILDLV` | Delivery confirmation |

### Mail Delivery Flow

```
Sender BBS                              Destination BBS
    │                                        │
    │──── MAILREQ (to:alice, from:bob, msg_id:xxx) ─▶│
    │                                        │
    │◀─── MAILACK (msg_id:xxx) ──────────────│  Ready for chunks
    │                                        │
    │──── MAILDAT (msg_id:xxx, chunk:1/2) ──▶│  Chunk 1
    │──── MAILDAT (msg_id:xxx, chunk:2/2) ──▶│  Chunk 2
    │                                        │
    │◀─── MAILDLV (msg_id:xxx) ──────────────│  Delivered!
    │                                        │
```

### Multi-Hop Routing

If the destination BBS isn't a direct peer, messages can relay through intermediate nodes:

```
MV51 ──▶ TEST ──▶ J51B
```

Each hop:
1. Checks if it's the destination
2. If not, forwards to next hop based on route info in MAILREQ
3. Route tracking prevents infinite loops

### Rate Limiting

To avoid flooding the mesh network:
- 3 second delay between message chunks
- Configurable retry limits and backoff

---

## Database Tables

### bbs_peers

Tracks known peer BBS nodes:

```sql
CREATE TABLE bbs_peers (
    id              INTEGER PRIMARY KEY,
    node_id         TEXT UNIQUE NOT NULL,    -- Meshtastic node ID
    name            TEXT,                     -- BBS name
    callsign        TEXT,                     -- BBS callsign
    protocol        TEXT DEFAULT 'fq51',
    capabilities    TEXT,                     -- mail
    last_seen_us    INTEGER,
    last_sync_us    INTEGER,
    sync_enabled    INTEGER DEFAULT 1
);
```

### sync_log

Tracks message sync status for deduplication and retry:

```sql
CREATE TABLE sync_log (
    id              INTEGER PRIMARY KEY,
    message_uuid    TEXT NOT NULL,
    peer_id         INTEGER NOT NULL,
    direction       TEXT NOT NULL,            -- 'sent' | 'received'
    status          TEXT NOT NULL,            -- 'pending' | 'acked' | 'failed'
    attempts        INTEGER DEFAULT 0,
    last_attempt_us INTEGER,
    UNIQUE(message_uuid, peer_id, direction)
);
```

---

## Compatibility

### Current Status

| BBS System | Status |
|------------|--------|
| advBBS | ✅ Implemented |
| TC2-BBS-mesh | 🔜 Planned |
| meshing-around | 🔜 Planned |
| frozenbbs | ❌ No protocol exists |

### Design Philosophy

advBBS is designed as a **polyglot BBS** - it can speak each external system's native protocol. We don't try to change how other BBS systems work; we participate as a peer in their existing networks.

Currently only FQ51-to-FQ51 sync is implemented.
