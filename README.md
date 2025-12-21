# advBBS - Advanced Bulletin Board System

> **advBBS** - A federated, encryption-first BBS designed for Meshtastic mesh networks. Multi-hop mail routing, multi-node identity, and lightweight enough for Raspberry Pi Zero 2 W.

> **Note**: This is a vibe-coded project built with AI assistance (Claude). While functional, it may contain bugs, unconventional patterns, or rough edges. Contributions and feedback welcome!

## Features

- **Encryption-first**: All messages encrypted at rest using password-derived keys (Argon2id + ChaCha20-Poly1305)
- **Multi-node identity**: Users can associate multiple Meshtastic nodes with their account
- **Node-based 2FA**: Login requires both password and a registered node
- **Inter-BBS federation**: Send mail to users on other BBS nodes (`SEND user@remotebbs message`)
- **Multi-hop routing**: Messages can relay through intermediate BBS nodes to reach destination
- **Lightweight**: Designed to run on Raspberry Pi Zero 2 W (~100MB RAM)
- **Operating modes**: Full, mail-only, boards-only, or repeater mode
- **Docker ready**: Includes Dockerfile and docker-compose for easy deployment

## Quick Start (Docker)

```bash
# Install Docker if it isn't already
curl -sSL https://get.docker.com/ | CHANNEL=stable bash
sudo systemctl enable --now docker
sudo usermod -aG docker $USER

# Create working directory for project
mkdir -p ./advbbs/data && cd advbbs

# Copy the latest docker-compose file from the GitHub
curl -O https://raw.githubusercontent.com/zvx-echo6/advbbs/refs/heads/main/docker-compose.yml

# or use wget if you don't have cURL installed
wget https://raw.githubusercontent.com/zvx-echo6/advbbs/refs/heads/main/docker-compose.yml

# Edit the docker-compose file to your liking
# You will need to comment out the "devices:" section if you are connecting over TCP
nano docker-compose.yml

# Start BBS
docker compose up -d

# View logs
docker compose logs -f
```

**Note**: The docker image supports 64-bit RISC-V, this repo does not build images for it as python libraries must be built for it which takes too long.

### Configuration

Open **http://localhost:7681** (or `http://<pi-ip>:7681`) in your browser for the web-based config interface.

Config is stored in the Docker volume and persists across restarts.

### Manual Installation

```bash
pip install -r requirements.txt
cp config.example.toml config.toml
# Edit config.toml
python -m advbbs
```

## Commands

**All commands require a `!` prefix** (e.g., `!help`, `!mail`, `!send user msg`).

Short aliases are shown in parentheses for quick typing on mobile.

**Case sensitivity:**
- Commands are case-insensitive (`!MAIL` = `!mail` = `!Mail`)
- Usernames are case-insensitive (`alice` = `Alice`)
- BBS peer names are case-insensitive (`!send user@MV51` = `!send user@mv51`)
- Passwords are case-sensitive

### General
| Command | Description |
|---------|-------------|
| `!bbs` / `!?` / `!help` | Show help (3 pages) |
| `!? admin` | Admin command help |
| `!info` (`!i`) | BBS information |

### Authentication
| Command | Description |
|---------|-------------|
| `!register <user> <pass>` | Create account (auto-registers current node) |
| `!login <user> <pass>` | Login (requires registered node) |
| `!logout` | Log out |
| `!passwd <old> <new>` | Change password |

### Node Management
| Command | Description |
|---------|-------------|
| `!nodes` (`!n`) | List your registered nodes |
| `!addnode <node_id>` (`!an`) | Add a new node (run from existing device) |
| `!rmnode <node_id>` (`!rn`) | Remove a node (can't remove last or current) |

### Mail
| Command | Description |
|---------|-------------|
| `!send <user> <msg>` (`!s`) | Send local mail |
| `!send <user@bbs> <msg>` | Send mail to remote BBS |
| `!mail` (`!m`) | Check inbox summary |
| `!mail [start]` | List 5 messages starting at #start |
| `!read <n>` (`!r`) | Read message #n |
| `!reply [n] <msg>` (`!re`) | Reply to message (n or last read) |
| `!forward [n] <user[@bbs]>` (`!fwd`) | Forward message |
| `!delete <n>` (`!del`, `!d`) | Delete message #n |
| *(native reply)* | Use Meshtastic reply button after reading mail |

### Boards
| Command | Description |
|---------|-------------|
| `!board` (`!b`) | List boards |
| `!board <name>` | Enter board |
| `!list` (`!l`) | List posts |
| `!read <n>` (`!r`) | Read post #n |
| `!post <subj> <body>` (`!p`) | Create post |
| `!quit` (`!q`) | Exit board |
| *(native reply)* | Use Meshtastic reply button to post |

### Federation
| Command | Description |
|---------|-------------|
| `!peers` | List connected BBS peers |

### Admin
| Command | Description |
|---------|-------------|
| `!ban <user> [reason]` | Ban user |
| `!unban <user>` | Unban user |
| `!mkboard <name> [desc]` (`!mb`) | Create board |
| `!rmboard <name>` (`!rb`) | Delete board |
| `!announce <msg>` (`!ann`) | Broadcast message |

### Account
| Command | Description |
|---------|-------------|
| `!destruct CONFIRM` | Delete all your data |

## Meshtastic Native Reply

advBBS supports using Meshtastic's built-in reply function instead of typing commands:

- **Mail**: After reading a message with `!read <n>`, use your Meshtastic app's reply button to send a reply. No `!` prefix needed - just type your message.
- **Boards**: After entering a board with `!board <name>` or listing posts with `!L`, use reply to post a new message.

Reply context expires after 5 minutes (mail) or 10 minutes (boards).

## Remote Mail Federation

advBBS supports sending mail between BBS nodes using `user@bbs` addressing:

```
!send alice@REMOTE1 Hello from another BBS!
```

### How it works

1. **Pre-flight check**: Message limited to 450 chars for remote delivery
2. **Route discovery**: Your BBS finds a path to the destination
3. **Chunked delivery**: Message split into 150-char chunks (max 3)
4. **Multi-hop relay**: If your BBS can't reach the destination directly, it can relay through intermediate nodes

### Protocol Messages

| Message | Purpose |
|---------|---------|
| `MAILREQ` | Request to send mail (includes route info) |
| `MAILACK` | Destination accepts, ready for chunks |
| `MAILNAK` | Delivery rejected (user not found, loop, etc) |
| `MAILDAT` | Message chunk |
| `MAILDLV` | Delivery confirmation |

## Configuration

See `config.example.toml` for all options.

Key settings:
- `bbs.admin_password` - **CHANGE THIS!**
- `bbs.timezone` - Timezone for timestamps (e.g., America/Boise, UTC)
- `meshtastic.connection_type` - serial, tcp, or ble
- `meshtastic.serial_port` - e.g., /dev/ttyUSB0
- `operating_mode.mode` - full, mail_only, boards_only, repeater

### Peer Configuration (Federation)

Federation traffic is **whitelisted by peer** - only nodes configured as peers can send/receive BBS protocol messages. This prevents unauthorized nodes from injecting messages or abusing the relay system.

```toml
[[sync.peers]]
name = "REMOTE1"
node_id = "!abcd1234"
enabled = true

[[sync.peers]]
name = "REMOTE2"
node_id = "!efgh5678"
enabled = true
```

To federate with another BBS:
1. Exchange node IDs with the other BBS operator
2. Both sides add each other as peers in their config
3. Set `enabled = true` to activate the peering

## Security

- **Encryption at rest**: All messages encrypted with user-derived keys (Argon2id + ChaCha20-Poly1305)
- **Node-based 2FA**: Login requires both password AND a pre-registered Meshtastic node
- **Peer whitelisting**: BBS protocol messages only accepted from configured peers
- **Loop prevention**: Remote mail includes route tracking to prevent infinite relay loops
- **Hop limiting**: Maximum 5 hops for relayed messages

### ⚠️ Important: Enable Meshtastic PSK Encryption

advBBS uses private/direct messages for authentication commands (`!register`, `!login`, `!passwd`). This is similar to how IRC networks use NickServ - commands are sent via private message to keep them hidden from public channels.

**However, without Meshtastic PSK (Pre-Shared Key) encryption enabled, these messages are transmitted in plaintext over radio.** Anyone with a Meshtastic receiver in range can intercept registration and login commands.

**Strongly recommended:** Enable PSK encryption on your Meshtastic channel:

```bash
# Set a strong encryption key on all mesh nodes
meshtastic --ch-set psk random --ch-index 0
# Or use a specific key
meshtastic --ch-set psk base64:YOUR_KEY_HERE --ch-index 0
```

With PSK enabled, all Meshtastic traffic (including BBS commands) is encrypted with AES-256, providing transport-layer security equivalent to IRC with TLS.

See [docs/security.md](docs/security.md) for more details.

## Troubleshooting

### Build fails on Raspberry Pi

The build can take 10-15 minutes on a Pi due to compiling native extensions. If it fails due to memory, try:

```bash
# Add swap temporarily
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile

# Then build
docker compose build
```

### Web config not loading

Make sure port 7681 is accessible and the container is running:

```bash
docker compose ps
docker compose logs advbbs
```

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for full design documentation.

## Acknowledgments

Special thanks to the **Freq51 community** for their patience and support during development and testing:

- **@JeepnJonny** - Testing and feedback
- **@Brownik** - Testing and feedback
- **@SidPatchy** - Testing and feedback
- **@MicroSeth** - Testing and feedback

...and everyone else in the Freq51 community who put up with the test spam during development! Your feedback and patience made this project possible.

## License

MIT License
