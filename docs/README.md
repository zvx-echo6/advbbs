# advBBS Documentation

advBBS is a lightweight BBS (Bulletin Board System) designed for Meshtastic mesh networks.

---

## Table of Contents

- [User Guide](#user-guide)
- [Operator Guide](#operator-guide)
- [Security & Troubleshooting](security.md) - **READ THIS**
- [Quick Start](#quick-start)
  - [As a User](#as-a-user)
  - [As an Operator](#as-an-operator)
- [Updating](#updating)
- [Key Concepts](#key-concepts)
  - [BBS Callsign vs Node ID](#bbs-callsign-vs-node-id)
  - [Sync Boards](#sync-boards)
  - [Remote Mail](#remote-mail)
- [Examples](#examples)
  - [Sending Local Mail](#sending-local-mail)
  - [Sending Remote Mail](#sending-remote-mail)
  - [Reading Bulletin Boards](#reading-bulletin-boards)
  - [Posting to a Board](#posting-to-a-board)
- [Architecture](#architecture)

---

## User Guide

| Document | Description |
|----------|-------------|
| [Commands](commands.md) | Complete command reference |
| [Mail](mail.md) | Sending and receiving mail (local and remote) |
| [Boards](boards.md) | Bulletin boards and posting |

## Operator Guide

| Document | Description |
|----------|-------------|
| [Deployment](deployment.md) | Docker setup and deployment |
| [Configuration](configuration.md) | Full config.toml reference |
| [Sync](sync.md) | Inter-BBS synchronization |
| [Security & Troubleshooting](security.md) | **IMPORTANT:** Encryption, backups, and common issues |

---

## Quick Start

### As a User

1. Connect to the mesh and find a BBS channel
2. Send `!bbs` to see available commands
3. Register: `!register myname mypassword`
4. Check boards: `!board`
5. Read posts: `!board general` then `!list`

### As an Operator

1. Clone the repo and configure `config.toml`
2. Set your BBS callsign (used in `user@CALLSIGN` addressing)
3. Run with Docker: `docker-compose up -d`
4. Add peer BBS nodes in config for sync

See [deployment.md](deployment.md) for detailed setup instructions.

---

## Updating

To update your advBBS installation to the latest version:

### Standard Update

```bash
cd advbbs

# Pull latest changes
git pull

# Rebuild and restart
docker-compose down
docker-compose build
docker-compose up -d
```

### Raspberry Pi Update

```bash
cd advbbs

# Pull latest changes
git pull

# Rebuild and restart (RPi-optimized)
docker-compose -f docker-compose.rpi.yml down
docker-compose -f docker-compose.rpi.yml build
docker-compose -f docker-compose.rpi.yml up -d
```

### Checking the Update

```bash
# View logs to confirm startup
docker-compose logs -f

# Check BBS info (from a mesh node)
!info
```

**Note:** Your `config.toml` and database are preserved during updates. The database is stored in the `advbbs_data` Docker volume.

### Rolling Back

If something goes wrong:

```bash
# Check recent commits
git log --oneline -10

# Roll back to previous version
git checkout <commit-hash>

# Rebuild
docker-compose down
docker-compose build
docker-compose up -d
```

---

## Key Concepts

### BBS Callsign vs Node ID

**Important distinction:**

| Term | Example | Purpose |
|------|---------|---------|
| BBS Callsign | `TV51`, `AIDA`, `FQ51` | Used in mail addressing (`user@TV51`) |
| Node ID | `!abc12345` | Meshtastic radio identifier |

The callsign is a human-friendly name you configure. The node ID is assigned by Meshtastic.

```
┌─────────────────────────────────────────────────────────┐
│  BBS: "advBBS"                                         │
│  Callsign: FQ51         ← Used in user@FQ51 addressing  │
│  Node ID: !def67890     ← Meshtastic identifier         │
└─────────────────────────────────────────────────────────┘
```

When you send mail to `johnny@TV51`:
- `TV51` is the **BBS callsign**, not a node ID
- Your BBS looks up which node ID corresponds to the `TV51` peer
- Mail routes through the BBS peer network

### Sync Boards

Only `general` and `help` boards sync between BBS nodes:

| Board | Syncs? | Anonymous Read? | Requires Login to Post? |
|-------|--------|-----------------|------------------------|
| `general` | Yes | Yes | Yes |
| `help` | Yes | Yes | Yes |
| Other boards | No | No | Yes |

This allows anyone on the mesh to read public announcements without registering.

### Remote Mail

Mail to users on other BBS nodes uses the `user@BBS` format:

- `!send alice@REMOTE1` routes through the peer network
- Maximum 450 characters (mesh constraint)
- Delivery confirmed via MAILDLV message

See [Examples: Sending Remote Mail](#sending-remote-mail) for a walkthrough.

---

## Examples

### Sending Local Mail

**Scenario:** You're `alice` on advBBS, sending mail to `bob` who is also on advBBS.

```
alice> !send bob Hey, want to join the net tonight?
BBS> Mail sent to bob
```

The mail stays local - no network routing needed.

```
┌──────────────────────────────────────┐
│           advBBS (local)            │
│                                      │
│   alice ──── mail ────► bob          │
│                                      │
│   Mail stored in local database      │
└──────────────────────────────────────┘
```

When bob logs in:

```
bob> !mail
BBS> You have 1 new message
BBS> 1. From alice: Hey, want to join...
```

### Sending Remote Mail

**Scenario:** `malice` on AIDA BBS wants to send mail to `johnny` on TV51 BBS.

```
malice@AIDA> !send johnny@TV51 Got your message, will QSL tomorrow
AIDA> Mail queued for johnny@TV51
```

**What happens:**

```
┌─────────────┐         ┌─────────────┐         ┌─────────────┐
│  AIDA BBS   │         │   advBBS   │         │  TV51 BBS   │
│  (origin)   │         │   (relay)   │         │ (destination)│
└──────┬──────┘         └──────┬──────┘         └──────┬──────┘
       │                       │                       │
       │ 1. MAILREQ            │                       │
       │ (johnny@TV51)         │                       │
       │──────────────────────►│                       │
       │                       │                       │
       │                       │ 2. MAILREQ            │
       │                       │ (johnny@TV51)         │
       │                       │──────────────────────►│
       │                       │                       │
       │                       │                       │ 3. TV51 finds
       │                       │                       │    johnny, sends
       │                       │       4. MAILACK      │    MAILACK
       │                       │◄──────────────────────│
       │       5. MAILACK      │                       │
       │◄──────────────────────│                       │
       │                       │                       │
       │ 6. MAILDAT            │                       │
       │ (encrypted payload)   │                       │
       │──────────────────────►│──────────────────────►│
       │                       │                       │
       │                       │       7. MAILDLV      │ 8. johnny
       │◄──────────────────────│◄──────────────────────│    notified
       │                       │                       │
       │ 9. malice sees        │                       │
       │    "Delivered"        │                       │
```

**Key points:**
- The `@TV51` tells AIDA to route to the TV51 **BBS**, not a node
- AIDA looks up TV51's node ID from its peer configuration
- Mail routes through peer BBS nodes until it reaches TV51
- TV51 delivers to johnny and confirms back to AIDA

### Reading Bulletin Boards

**Scenario:** You want to read the `general` board (no login required for sync boards).

```
you> !board
BBS> Available boards:
BBS> 1. general (12 posts) - synced
BBS> 2. help (5 posts) - synced
BBS> 3. local (8 posts) - local only

you> !board general
BBS> Entered board: general (12 posts)

you> !list
BBS> Posts in general:
BBS> 8. [2024-01-15] alice: Weekly net reminder
BBS> 9. [2024-01-16] bob: New repeater online
BBS> 10. [2024-01-17] charlie: Field day planning
BBS> 11. [2024-01-18] alice: Emergency prep checklist
BBS> 12. [2024-01-19] dave: Welcome new members

you> !read 12
BBS> Post #12 by dave (2024-01-19):
BBS> Subject: Welcome new members
BBS>
BBS> Welcome to all the new folks who joined this week!
BBS> Check out the help board for getting started tips.
```

**Note:** You can read `general` and `help` boards without logging in. Other boards require authentication.

### Posting to a Board

**Scenario:** You're logged in and want to post to the general board.

```
you> !login myuser mypass
BBS> Welcome back, myuser!

you> !board general
BBS> Entered board: general (12 posts)

you> !post Weekly Update Here's what happened this week on the mesh...
BBS> Post created: #13

you> !quit
BBS> Left board: general
```

**Post format:**
```
!post <subject> <body>
```
- Subject: First word(s) up to 64 characters
- Body: Everything after, up to 2000 characters

Since `general` is a sync board, your post will propagate to peer BBS nodes.

---

## Architecture

For the complete technical architecture, see [ARCHITECTURE.md](../ARCHITECTURE.md).

Key technical docs:
- [sync.md](sync.md) - Sync protocol and peer configuration
- [mail.md](mail.md) - Wire protocol for remote mail (MAILREQ/MAILACK/MAILDAT/MAILDLV)
