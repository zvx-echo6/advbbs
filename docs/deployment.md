# advBBS Deployment

## Quick Start (Docker)

```bash
# Clone repository
git clone https://forge.echo6.co/advbbs/advbbs.git
cd advbbs

# Create configuration
cp config.example.toml config.toml
# Edit config.toml - CHANGE admin_password!

# Build and run
docker-compose up -d

# View logs
docker-compose logs -f
```

## Quick Start (Raspberry Pi)

```bash
# On RPi Zero 2 W
git clone https://forge.echo6.co/advbbs/advbbs.git
cd advbbs

cp config.example.toml config.toml
vim config.toml  # Set admin_password, serial_port

# Build RPi-optimized image
docker-compose -f docker-compose.rpi.yml build

# Run
docker-compose -f docker-compose.rpi.yml up -d
```

---

## Container Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Docker Host                             │
│  (RPi Zero 2 W / x86_64 / ARM64)                            │
│                                                             │
│  ┌─────────────────┐         ┌─────────────────┐           │
│  │   advbbs       │         │  advbbs-web    │           │
│  │   (main BBS)    │         │  (web reader)   │           │
│  │                 │         │  [optional]     │           │
│  │  - Mesh I/O     │         │  - Flask        │           │
│  │  - Commands     │         │  - Read-only    │           │
│  │  - Sync         │         │  - Port 8080    │           │
│  └────────┬────────┘         └────────┬────────┘           │
│           │                           │                     │
│           └───────────┬───────────────┘                     │
│                       │                                     │
│              ┌────────▼────────┐                           │
│              │  advbbs_data   │                           │
│              │    (volume)     │                           │
│              │  - SQLite DB    │                           │
│              │  - Backups      │                           │
│              └─────────────────┘                           │
│                       │                                     │
│              ┌────────▼────────┐                           │
│              │  /dev/ttyUSB0   │                           │
│              │  (Meshtastic)   │                           │
│              └─────────────────┘                           │
└─────────────────────────────────────────────────────────────┘
```

---

## Docker Files

### Dockerfiles

| File | Target | Use Case |
|------|--------|----------|
| `Dockerfile` | General x86_64/ARM64 | Desktop, server |
| `Dockerfile.rpi` | Raspberry Pi | RPi Zero 2 W (memory optimized) |

### Docker Compose Files

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Standard deployment |
| `docker-compose.rpi.yml` | RPi optimized |

---

## Volume Mounts

| Volume | Container Path | Purpose |
|--------|---------------|---------|
| `advbbs_data` | `/data` | SQLite database, backups |
| `advbbs_logs` | `/var/log` | Application logs |
| `config.toml` | `/app/config.toml` | Configuration (bind mount) |

---

## Device Access

The BBS needs access to your Meshtastic radio:

### Serial Connection

```yaml
# docker-compose.yml
services:
  advbbs:
    devices:
      - /dev/ttyUSB0:/dev/ttyUSB0
```

Make sure your user has permission to access the serial port:

```bash
sudo usermod -a -G dialout $USER
# Log out and back in
```

### TCP Connection

If using Meshtastic's TCP interface (e.g., via meshtasticd):

```toml
# config.toml
[meshtastic]
connection_type = "tcp"
tcp_host = "192.168.1.100"
tcp_port = 4403
```

No device mapping needed in docker-compose.

---

## Running with Web Reader

The web reader is an optional read-only web interface:

```bash
# Start with web profile
docker-compose --profile web up -d
```

Access at `http://localhost:8080` (or your configured port).

---

## Configuration

### Minimal config.toml

```toml
[bbs]
name = "My BBS"
callsign = "MYBBS"
admin_password = "CHANGE_THIS"

[meshtastic]
connection_type = "serial"
serial_port = "/dev/ttyUSB0"
```

### Adding Sync Peers

```toml
[sync]
enabled = true

[[sync.peers]]
node_id = "!abc12345"
name = "REMOTE1"
protocol = "fq51"
enabled = true
```

See [configuration.md](configuration.md) for full reference.

---

## Updating

```bash
# Pull latest
git pull

# Rebuild and restart
docker-compose build
docker-compose up -d
```

---

## Logs and Debugging

```bash
# View logs
docker-compose logs -f

# Enter container shell
docker-compose exec advbbs /bin/sh

# Check database
docker-compose exec advbbs sqlite3 /data/advbbs.db ".tables"
```

---

## Backup

The database is stored in the `advbbs_data` volume:

```bash
# Manual backup
docker-compose exec advbbs cp /data/advbbs.db /data/backups/manual-$(date +%Y%m%d).db

# Copy backup out of container
docker cp advbbs_advbbs_1:/data/backups ./backups/
```

Automatic backups are configured in `config.toml`:

```toml
[database]
backup_path = "/data/backups"
backup_interval_hours = 24
```

---

## Troubleshooting

### Can't access serial port

```bash
# Check permissions
ls -la /dev/ttyUSB0

# Add user to dialout group
sudo usermod -a -G dialout $USER

# Restart docker
sudo systemctl restart docker
```

### Container won't start

```bash
# Check logs
docker-compose logs advbbs

# Common issues:
# - Serial port not available
# - Invalid config.toml syntax
# - Port already in use
```

### Database locked

SQLite can only have one writer. If you see "database is locked" errors:
- Make sure only one BBS instance is running
- Check for zombie processes
- Restart the container
