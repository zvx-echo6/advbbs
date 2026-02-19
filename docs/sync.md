# advBBS Inter-BBS Sync

How advBBS nodes synchronize and route mail between each other.

## Overview

advBBS uses a peer-to-peer sync model where configured BBS nodes exchange:
- **Remote mail** using `user@BBS` addressing
- **Board posts** from sync-enabled boards (batched to reduce mesh traffic)

Only explicitly configured peers can participate in sync - this is a security whitelist.

## Peer Configuration

Peers are configured in `config.toml`:

```toml
[sync]
enabled = true

# Add peer BBS nodes
[[sync.peers]]
node_id = "!abc12345"                  # Meshtastic node ID
name = "REMOTE1"                       # BBS name (used in user@BBS addressing)
protocol = "advbbs"                      # Protocol type
enabled = true                         # Enable/disable this peer

[[sync.peers]]
node_id = "!def67890"
name = "REMOTE2"
protocol = "advbbs"
enabled = true
```

**Important fields:**
- `node_id` - The Meshtastic node ID (e.g., `!abc12345`)
- `name` - The BBS callsign used in `user@BBS` mail addressing
- `protocol` - Currently only `advbbs` is supported

## What Syncs

| Content | Syncs? | Notes |
|---------|--------|-------|
| Remote mail (`user@BBS`) | ✅ Yes | Routed through peers |
| Local mail (`user`) | ❌ No | Stays on local BBS |
| Sync-enabled board posts | ✅ Yes | Batched, `general` synced by default |
| Local-only board posts | ❌ No | `local` board and restricted boards |

## Sync Triggers

| Trigger | What Happens |
|---------|--------------|
| Remote mail | `!send user@BBS` routes immediately through peers |
| Board posts | Batched: syncs at 10 new local posts OR hourly with pending content |

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

## advBBS Native Protocol

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
advBBS|<version>|<msg_type>|<payload>
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

### Board Sync Protocol Messages

Board sync uses a dedicated protocol for batched post exchange:

| Message | Purpose |
|---------|---------|
| `BOARDREQ` | Request to sync board posts (includes board name, count, since timestamp) |
| `BOARDACK` | Destination accepts, ready for chunks |
| `BOARDNAK` | Sync rejected (board not found, sync disabled, etc) |
| `BOARDDAT` | Post data chunk (max 150 chars each) |
| `BOARDDLV` | Delivery confirmation |

### Board Sync Flow

```
Sender BBS                              Destination BBS
    │                                        │
    │──── BOARDREQ (board:general, count:3, since:xxx) ─▶│
    │                                        │
    │◀─── BOARDACK (board:general) ──────────│  Ready for chunks
    │                                        │
    │──── BOARDDAT (general, 1/2) ──────────▶│  Chunk 1
    │──── BOARDDAT (general, 2/2) ──────────▶│  Chunk 2
    │                                        │
    │◀─── BOARDDLV (general) ───────────────│  Delivered!
    │                                        │
```

**Payload format:** Multiple posts are packed into a single payload. Records are separated by `\x1F` (RS), fields within each record by `\x1E` (GS): `uuid\x1Eauthor\x1Eorigin_bbs\x1Etimestamp_us\x1Esubject\x1Ebody`

**Batching triggers:**
- 10 new local posts on any synced board, OR
- 1+ pending posts and 1 hour elapsed since last sync

**Deduplication:** Each post has a UUID. Duplicate UUIDs are silently skipped on the receiving end.

**Federated identity:** Remote posts are displayed as `author@BBS` (e.g., `alice@REMOTE1`).

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

## Route Announcement Protocol (RAP)

RAP enables automatic route discovery between federated BBS nodes, similar to distance-vector routing protocols like RIP. This allows mail to be routed through intermediate nodes without manual configuration of the full mesh topology.

### Problem RAP Solves

Without RAP, if a user tries to send mail to `alice@TB3` but their local BBS (TB0) doesn't have TB3 as a direct peer, the system has no way to know that TB3 might be reachable through intermediate nodes (e.g., TB0 → TB1 → TB2 → TB3).

### How RAP Works

Each BBS periodically:
1. **Sends PING messages** to direct peers to verify connectivity
2. **Receives PONG responses** containing the peer's route table
3. **Shares route tables** with peers via RAP_ROUTES messages
4. **Learns routes** to distant BBS nodes through peer advertisements

### RAP Message Types

| Message | Purpose | Payload Format |
|---------|---------|----------------|
| `RAP_PING` | Heartbeat/connectivity check | `timestamp_us` |
| `RAP_PONG` | Response with route table | `timestamp_us\|route_table` |
| `RAP_ROUTES` | Full route table announcement | `route_table` |

**Route Table Format:** `bbs1:hop:quality;bbs2:hop:quality;...`

Example: `TB0:0:1.0;TB1:1:1.00;TB2:2:1.00;TB3:3:1.00`
- `TB0:0:1.0` - Self (hop 0)
- `TB1:1:1.00` - Direct peer (hop 1)
- `TB2:2:1.00` - Learned via TB1 (hop 2)
- `TB3:3:1.00` - Learned via TB1→TB2 (hop 3)

### Configuration

```toml
[sync]
enabled = true

# RAP settings
rap_enabled = true
rap_heartbeat_interval_seconds = 43200   # Send PING every 12 hours
rap_heartbeat_timeout_seconds = 60       # Wait for PONG response
rap_unreachable_threshold = 2            # Failed pings before UNREACHABLE
rap_dead_threshold = 5                   # Failed pings before DEAD
rap_route_expiry_seconds = 129600        # Routes expire after 36 hours (3 missed heartbeats)
rap_route_share_interval_seconds = 86400 # Share routes every 24 hours
```

**Note:** These conservative defaults are designed for low-bandwidth mesh networks. Routes remain valid across 3 missed heartbeat cycles (36 hours) before expiring.

### Route Discovery Example

Given this topology where only adjacent nodes are configured as peers:

```
TB0 <---> TB1 <---> TB2 <---> TB3
```

**Peer Configuration:**
- TB0: peer = TB1
- TB1: peers = TB0, TB2
- TB2: peers = TB1, TB3
- TB3: peer = TB2

**After RAP convergence:**

| Node | Discovered Routes |
|------|-------------------|
| TB0  | TB0:0, TB1:1, TB2:2, TB3:3 |
| TB1  | TB1:0, TB0:1, TB2:1, TB3:2 |
| TB2  | TB2:0, TB1:1, TB3:1, TB0:2 |
| TB3  | TB3:0, TB2:1, TB1:2, TB0:3 |

Each node discovers the full network topology automatically.

### Peer Health Tracking

RAP maintains peer health status based on PING/PONG success:

| Status | Meaning |
|--------|---------|
| `unknown` | New peer, not yet contacted |
| `alive` | Responding to PINGs |
| `unreachable` | Failed `rap_unreachable_threshold` consecutive PINGs |
| `dead` | Failed `rap_dead_threshold` consecutive PINGs |

Dead peers are excluded from route tables until they respond again.

### Database Tables for RAP

```sql
-- Peer health tracking (extends bbs_peers)
ALTER TABLE bbs_peers ADD COLUMN health_status TEXT DEFAULT 'unknown';
ALTER TABLE bbs_peers ADD COLUMN quality_score REAL DEFAULT 1.0;

-- Learned routes from RAP
CREATE TABLE rap_routes (
    id              INTEGER PRIMARY KEY,
    dest_bbs        TEXT NOT NULL,           -- Destination BBS callsign
    next_hop_peer_id INTEGER NOT NULL,       -- Peer ID to route through
    hop_count       INTEGER NOT NULL,
    quality_score   REAL DEFAULT 1.0,
    learned_from_us INTEGER,                 -- When we learned this route
    expires_at_us   INTEGER,                 -- Route expiry time
    FOREIGN KEY (next_hop_peer_id) REFERENCES bbs_peers(id)
);
```

For detailed RAP testing documentation with multi-hop mail delivery examples, see [rap-testing.md](rap-testing.md).

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
    protocol        TEXT DEFAULT 'advbbs',
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

## Protocol

advBBS uses its own native DM-based protocol (`advbbs`) for all inter-BBS communication. Only advBBS-to-advBBS federation is supported.
