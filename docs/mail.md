# advBBS Mail System

Private messaging between users, both local and across BBS nodes.

## Commands

| Command | Format | Description |
|---------|--------|-------------|
| `!send` | `!send <user> <message>` | Send local mail |
| `!send` | `!send <user>@<BBS> <message>` | Send remote mail |
| `!mail` | `!mail` | Check inbox (unread count) |
| `!read` | `!read [n]` | Read mail (or list if no number) |
| `!delete` | `!delete <n>` | Delete message |
| `!reply` | `!reply [n] <message>` | Reply to message |
| `!forward` | `!forward [n] <user[@BBS]>` | Forward message |

## Local Mail

When sending mail to a user on the **same BBS** (no `@` in the address):

```
!send johnny Hello from the local BBS!
```

**Flow:**
1. Sender composes mail
2. Mail encrypted and stored in local database
3. Recipient sees unread count on next `!mail` check

Local mail is stored encrypted and waits for the recipient to retrieve it.
There's no push delivery to nodes - users check their mail via `!mail` and `!read`.

## Remote Mail

When sending mail to a user on a **different BBS**, use `user@BBS` addressing:

```
!send johnny@TV51 Hello from AIDA!
```

**Important:** The `@TV51` is the **BBS callsign**, not a Meshtastic node ID.

### How It Works

```
malice@AIDA sends to johnny@TV51
         │
         ▼
    ┌─────────────────────────────────────┐
    │  AIDA BBS: Route to TV51            │
    │  - Is TV51 a direct peer? Use it    │
    │  - Otherwise, relay through peers   │
    └─────────────────────────────────────┘
         │
         ▼ MAILREQ routed through peer network
         │
    ┌─────────────────────────────────────┐
    │  Each BBS in chain:                 │
    │  - Am I TV51? → Deliver locally     │
    │  - Not TV51? → Forward to next peer │
    └─────────────────────────────────────┘
         │
         ▼ (reaches TV51)
    ┌─────────────────────────────────────┐
    │  TV51 BBS:                          │
    │  - johnny exists? → Accept & store  │
    │  - Send delivery confirmation       │
    └─────────────────────────────────────┘
         │
         ▼
    johnny sees "1 unread" on next !mail
```

### Key Points

- The `@BBS` suffix is the **BBS callsign** (e.g., `TV51`, `AIDA`, `FQ51`)
- It is NOT a Meshtastic node ID (like `!abc12345`)
- Mail routes immediately through the BBS peer network
- No direct node-to-node delivery - mail goes BBS to BBS
- Recipient retrieves mail from their home BBS via `!mail`

### BBS Callsign vs Node ID

| Type | Example | Used For |
|------|---------|----------|
| BBS Callsign | `TV51`, `AIDA` | Mail addressing (`johnny@TV51`) |
| Node ID | `!abc12345` | Meshtastic radio identification |

The BBS callsign is configured in each BBS's `config.toml`:
```toml
[bbs]
callsign = "TV51"
name = "TV51 BBS"
```

### Message Size Limits

- **Local mail:** 1000 characters max
- **Remote mail:** 450 characters max (3 chunks × 150 chars)

Remote mail has a smaller limit due to mesh network constraints.

## Loop Prevention

The remote mail protocol tracks the route to prevent infinite loops:

| Check | Result |
|-------|--------|
| My callsign already in route? | Reject with `LOOP` |
| Hop count > 5? | Reject with `MAXHOPS` |
| Destination BBS unknown? | Reject with `NOROUTE` |
| User not found at destination? | Reject with `NOUSER` |

Because peers are explicitly configured (whitelisted), mail only routes through
trusted BBS nodes and cannot loop back through the same node twice.

---

## Wire Protocol Reference

For developers implementing the remote mail protocol.

### Protocol Handshake

```
Sender BBS                          Recipient BBS
    │                                     │
    │─── MAILREQ|uuid|from|to|hop|... ───▶│  Route check
    │                                     │
    │◀── MAILACK|uuid|OK ─────────────────│  Accept
    │    or MAILNAK|uuid|REASON           │  Reject
    │                                     │
    │─── MAILDAT|uuid|1/3|chunk1 ────────▶│  Data chunk 1
    │─── MAILDAT|uuid|2/3|chunk2 ────────▶│  Data chunk 2
    │─── MAILDAT|uuid|3/3|chunk3 ────────▶│  Data chunk 3
    │                                     │
    │◀── MAILDLV|uuid|OK|user@bbs ────────│  Delivery confirm
    │                                     │
```

### Message Formats

#### MAILREQ - Request to Send

```
MAILREQ|<uuid>|<from_user>|<from_bbs>|<to_user>|<to_bbs>|<hop>|<num_parts>|<route>
```

| Field | Description |
|-------|-------------|
| `uuid` | Unique message identifier |
| `from_user` | Sender username |
| `from_bbs` | Sender's BBS callsign |
| `to_user` | Recipient username |
| `to_bbs` | Recipient's BBS callsign |
| `hop` | Current hop count (starts at 1) |
| `num_parts` | Number of data chunks to follow |
| `route` | Comma-separated list of BBS callsigns traversed |

#### MAILACK - Accept Delivery

```
MAILACK|<uuid>|OK
```

#### MAILNAK - Reject Delivery

```
MAILNAK|<uuid>|<reason>
```

| Reason | Meaning |
|--------|---------|
| `NOUSER` | Recipient not found on destination BBS |
| `NOROUTE` | No path to destination BBS |
| `LOOP` | Loop detected (BBS already in route) |
| `MAXHOPS` | Maximum hop count (5) exceeded |

#### MAILDAT - Message Chunk

```
MAILDAT|<uuid>|<part>/<total>|<data>
```

- Maximum chunk size: 150 characters
- Maximum 3 chunks = 450 character body limit

#### MAILDLV - Delivery Confirmation

```
MAILDLV|<uuid>|OK|<recipient>@<bbs>
```

### Example Flow

```
User on FQ51 sends: !send alice@REMOTE2 Hello!

1. FQ51 creates MAILREQ:
   MAILREQ|550e8400...|bob|FQ51|alice|REMOTE2|1|1|FQ51

2. FQ51 doesn't know REMOTE2 directly, but knows REMOTE1
   Forwards to REMOTE1

3. REMOTE1 receives MAILREQ, adds itself to route:
   MAILREQ|550e8400...|bob|FQ51|alice|REMOTE2|2|1|FQ51,REMOTE1
   Forwards to REMOTE2

4. REMOTE2 receives MAILREQ:
   - Checks: alice exists? Yes
   - Sends: MAILACK|550e8400...|OK

5. ACK relayed back through REMOTE1 to FQ51

6. FQ51 sends: MAILDAT|550e8400...|1/1|Hello!

7. Data relayed through REMOTE1 to REMOTE2

8. REMOTE2 stores message, sends: MAILDLV|550e8400...|OK|alice@REMOTE2

9. Delivery confirmation relayed back to FQ51
```
