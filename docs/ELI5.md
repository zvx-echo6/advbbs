# advBBS — What It Is and How It Works

## Already comfortable with the tech?

Jump to the [User Quickstart](https://github.com/zvx-echo6/advbbs/blob/main/docs/USER-QUICKSTART.md) or the [Operator Quickstart](https://github.com/zvx-echo6/advbbs/blob/main/docs/quickstart.md).

## The Short Version

advBBS is a bulletin board system that runs over Meshtastic. It adds persistent mail, message boards, user accounts, and multi-hop federation on top of the mesh — things Meshtastic's built-in messaging doesn't do.

You interact with it by DMing the BBS node from your Meshtastic app. Commands start with `!`.

## What It Adds to Meshtastic

Meshtastic gives you real-time chat — great when everyone's online at the same time. advBBS fills the gaps:

**Persistent mail.** Send a message to someone who's offline. The BBS stores it. They pick it up later when they check in. No more "sorry, I wasn't on the mesh when you sent that."

**Bulletin boards.** Post to shared boards (`general`, `local`, or custom boards) that anyone on the BBS can read. Boards on federated nodes sync automatically — post on your BBS, it shows up on your peer's BBS.

**User accounts.** Register once, log in from any of your Meshtastic nodes. Your identity follows you across devices. Node-based 2FA means someone can't impersonate you even if they know your password — they also need one of your registered radios.

**Encryption at rest.** Messages aren't stored in plaintext on the Pi. Everything is encrypted with keys derived from user passwords (Argon2id + ChaCha20-Poly1305). The BBS operator can't read your mail.

## Federation — BBS to BBS

This is where advBBS gets interesting. Multiple BBS nodes can peer with each other over the mesh and exchange mail and board posts.

Addressing works like email: `!send alice@REMOTE1 Hey, are you coming Saturday?`

If REMOTE1 isn't a direct peer, advBBS routes through intermediate nodes automatically. RAP (Route Announcement Protocol) handles discovery — each BBS periodically shares its route table with peers, and the network converges on a map of who can reach whom. A four-node chain like this works without any manual route configuration:

```
Your BBS ←→ BBS-A ←→ BBS-B ←→ Destination BBS
```

You type one command. The routing, chunking, acknowledgment, and delivery confirmation all happen behind the scenes.

### Board Sync

Sync-enabled boards (like `general`) automatically exchange posts between peers. When enough new posts accumulate (10 posts, or at least 1 post after an hour), the BBS batches them up and sends them to each peer. Posts from other BBSes show up with federated identity — `alice@REMOTE1` instead of just `alice` — so you know where they came from.

Admins can enable sync on up to 2 custom boards beyond `general` (max 3 synced total). The `local` board never syncs by design, giving each BBS a space for community-specific content.

## For Operators

advBBS runs on anything that can run Python 3.10+ and connect to a Meshtastic node — a Raspberry Pi Zero 2 W is the typical deployment. Docker is the easiest path:

```bash
mkdir -p ./advbbs/data && cd advbbs
curl -O https://raw.githubusercontent.com/zvx-echo6/advbbs/refs/heads/main/docker-compose.yml
nano docker-compose.yml   # set your connection type, serial port or TCP host
docker compose up -d
```

Configuration is done through a web-based TUI at `http://<your-ip>:7681`. Federation requires both operators to add each other as peers in their config — it's a mutual whitelist, not open peering.

## Mesh Etiquette

advBBS is designed to be a good neighbor on the mesh. Federation traffic uses DMs (not channel broadcasts), messages are chunked to fit within Meshtastic's 237-byte payload limit, and rate limiting enforces minimum spacing between transmissions. Board sync batches posts rather than sending them one at a time. RAP uses conservative intervals (12-hour heartbeats, 24-hour route shares) to minimize airtime.

The health of the mesh comes first. advBBS is built around that principle.

## Common Questions

**Does this replace Meshtastic's built-in messaging?**
No. Use channel chat for real-time group conversation. Use advBBS for persistent mail, boards, and anything that needs to survive someone being offline.

**Can someone sniff my password over the air?**
Registration and login commands go via Meshtastic DM. If your mesh channel has PSK encryption enabled (and it should), those DMs are encrypted in transit with AES-256. Without PSK, they're plaintext over the air. Always enable PSK.

**What happens if a relay node goes down?**
RAP detects it within a few missed heartbeat cycles and marks the peer as unreachable, then dead. Routes through that node are removed. If an alternate path exists, the network reconverges on it automatically.

**Can I run multiple BBS nodes on the same mesh?**
Yes — that's the entire point of federation. Each BBS has its own callsign, user base, and storage. They peer with each other to exchange mail and board posts.
