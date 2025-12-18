# Migrating from fq51bbs to advBBS

This guide covers migrating from fq51bbs to advBBS. The database and config format are compatible - only the package name and wire protocol prefix changed.

## What Changed

| Item | fq51bbs | advBBS |
|------|---------|--------|
| Package name | `fq51bbs` | `advbbs` |
| CLI command | `fq51bbs` | `advbbs` |
| Wire protocol | `FQ51\|1\|...` | `advBBS\|1\|...` |
| Peer protocol | `protocol = "fq51"` | `protocol = "advbbs"` |

## What Stays the Same

- Database schema (all tables, columns)
- Config file format (all TOML fields)
- User data, mail, boards, RAP routes
- All `!commands` users send

## Federation Note

**Important:** fq51bbs and advBBS nodes cannot federate with each other. The wire protocol prefix changed, so messages are ignored.

**Solution:** Upgrade all federated BBS nodes at the same time.

---

## Docker Migration

### Step 1: Stop the container

```bash
cd /path/to/your/fq51bbs
docker compose down
```

### Step 2: Backup your data

```bash
cp -r ./data ./data-backup
cp config.toml config.toml.backup
```

### Step 3: Update config.toml

Change any peer protocol lines from `fq51` to `advbbs`:

```bash
sed -i s/protocol = fq51/protocol = advbbs/g config.toml
```

Or manually edit:

```toml
# Change this:
[[sync.peers]]
protocol = "fq51"

# To this:
[[sync.peers]]
protocol = "advbbs"
```

### Step 4: Update Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN pip install git+https://github.com/zvx-echo6/advbbs.git

CMD ["advbbs", "--config", "/config/config.toml"]
```

### Step 5: Update docker-compose.yml (if needed)

Ensure volumes are mapped correctly:

```yaml
services:
  bbs:
    build: .
    volumes:
      - ./data:/app/data
      - ./config.toml:/config/config.toml
    restart: unless-stopped
```

### Step 6: Rebuild the image

```bash
docker compose build --no-cache
```

### Step 7: Start the container

```bash
docker compose up -d
```

### Step 8: Verify it works

```bash
docker compose logs -f
```

Look for:
- `advBBS` in startup messages
- `Connected to Meshtastic`
- No database or config errors

### Step 9: Test

Send `!help` from a Meshtastic device to confirm it responds.

---

## Native (Non-Docker) Migration

### Step 1: Stop the BBS

```bash
# Find and kill the process
pkill -f fq51bbs
```

### Step 2: Backup your data

```bash
cp -r ./data ./data-backup
cp config.toml config.toml.backup
```

### Step 3: Update config.toml

```bash
sed -i s/protocol = fq51/protocol = advbbs/g config.toml
```

### Step 4: Install advBBS

```bash
source venv/bin/activate
pip uninstall fq51bbs
pip install git+https://github.com/zvx-echo6/advbbs.git
```

### Step 5: Start with new command

```bash
advbbs --config config.toml
```

---

## Rollback

If something goes wrong:

```bash
# Stop the new version
docker compose down  # or pkill -f advbbs

# Restore backup
cp config.toml.backup config.toml
rm -rf ./data && mv ./data-backup ./data

# For Docker: revert Dockerfile and rebuild
# For native: pip uninstall advbbs && pip install git+https://github.com/zvx-echo6/fq51bbs.git

# Restart old version
```

---

## Verification Checklist

- [ ] Container/process starts without errors
- [ ] Logs show `advBBS` (not `fq51bbs`)
- [ ] `!help` command responds
- [ ] Existing users can `!login`
- [ ] Old mail is visible with `!mail`
- [ ] Old board posts visible with `!read <board>`
- [ ] Peers show as connected (if federated)
