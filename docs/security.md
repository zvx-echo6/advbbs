# advBBS Security & Troubleshooting

This document covers security architecture, critical operational concerns, and troubleshooting common issues.

---

## Table of Contents

- [Transport Security - Meshtastic PSK](#transport-security---meshtastic-psk)
- [Encryption Architecture](#encryption-architecture)
- [Master Key Salt - CRITICAL](#master-key-salt---critical)
  - [What is the Master Key Salt?](#what-is-the-master-key-salt)
  - [Why This Matters](#why-this-matters)
  - [Failure Scenarios](#failure-scenarios)
  - [Prevention & Recovery](#prevention--recovery)
- [Backup Strategy](#backup-strategy)
- [Troubleshooting](#troubleshooting)

---

## Transport Security - Meshtastic PSK

### ⚠️ Critical: Enable PSK Encryption

advBBS authentication commands (`!register`, `!login`, `!passwd`) are sent via Meshtastic direct messages. This approach is similar to IRC's NickServ model, where users send commands via private message to register and authenticate.

**The Problem:** Meshtastic direct messages are only "private" in that they're addressed to a specific node - they are **not encrypted by default**. Without PSK (Pre-Shared Key) encryption enabled, anyone with a Meshtastic receiver can intercept:

- Registration commands containing usernames and passwords
- Login attempts
- Password change requests

### Security Comparison

| Scenario | Protection Level |
|----------|-----------------|
| **No PSK** | ❌ Passwords visible to anyone with a receiver in radio range |
| **PSK enabled** | ✅ AES-256 encrypted, equivalent to IRC with TLS |

### How to Enable PSK Encryption

Enable PSK on **all nodes** in your mesh network:

```bash
# Generate and set a random encryption key
meshtastic --ch-set psk random --ch-index 0

# View the generated key (to share with other mesh members)
meshtastic --ch-get psk --ch-index 0

# Set a specific key on other nodes
meshtastic --ch-set psk base64:YOUR_KEY_HERE --ch-index 0
```

**Important notes:**
- All nodes on the mesh must use the same PSK to communicate
- Share the key securely with mesh members (not over unencrypted radio!)
- Different channels can have different keys

### Why This Matters

Unlike traditional internet services where traffic is naturally obscured by network infrastructure, **radio transmissions are inherently public**. Anyone within reception range (~1-10+ km depending on terrain and power) can passively receive all traffic.

With PSK enabled:
- All Meshtastic traffic is encrypted with AES-256
- Only nodes with the correct key can decrypt messages
- BBS commands become as secure as HTTPS/TLS traffic

### Additional Protections in advBBS

Even if an attacker intercepts credentials, advBBS provides defense-in-depth:

| Protection | Description |
|-----------|-------------|
| **Node-based 2FA** | Login requires both password AND a pre-registered node ID |
| **Password hashing** | Argon2id - passwords never stored in plaintext |
| **Rate limiting** | Brute-force attempts are throttled |
| **Encrypted storage** | All mail encrypted at rest with per-user keys |

The node-based 2FA means that even with a stolen password, an attacker cannot login from an unregistered node. They would need physical access to a victim's registered Meshtastic device.

### Threat Model Summary

| Threat | Without PSK | With PSK |
|--------|-------------|----------|
| Password interception | ❌ Vulnerable | ✅ Protected |
| Session hijacking | ⚠️ Partially (node ID required) | ✅ Protected |
| Mail interception | ⚠️ Headers visible, body encrypted | ✅ Fully protected |
| Replay attacks | ⚠️ Possible | ✅ Protected |

**Recommendation:** Always enable PSK encryption on meshes running advBBS.

---

## Encryption Architecture

advBBS uses a layered encryption system to protect user data:

```
┌─────────────────────────────────────────────────────────────┐
│                    Admin Password                            │
│                         +                                    │
│                    Master Key Salt (stored in DB)            │
│                         │                                    │
│                         ▼                                    │
│              ┌─────────────────────┐                        │
│              │  Argon2id KDF       │                        │
│              │  (time=3, mem=32MB) │                        │
│              └─────────────────────┘                        │
│                         │                                    │
│                         ▼                                    │
│                   Master Key                                 │
│                         │                                    │
│         ┌───────────────┼───────────────┐                   │
│         ▼               ▼               ▼                   │
│   User 1 Key      User 2 Key      User N Key                │
│   (encrypted)     (encrypted)     (encrypted)               │
│         │               │               │                   │
│         ▼               ▼               ▼                   │
│   User 1 Mail     User 2 Mail     User N Mail               │
│   (encrypted)     (encrypted)     (encrypted)               │
└─────────────────────────────────────────────────────────────┘
```

### Key Components

| Component | Storage | Purpose |
|-----------|---------|---------|
| Admin Password | config.toml | Input to master key derivation |
| Master Key Salt | bbs_settings table | Random salt for key derivation |
| Master Key | Memory only | Encrypts/decrypts user keys |
| User Keys | users table (encrypted) | Per-user encryption key |
| Mail Content | messages table (encrypted) | Encrypted with recipient's key |

---

## Master Key Salt - CRITICAL

### What is the Master Key Salt?

The master key salt is a **random 16-byte value** generated once when the BBS is first started. It is stored in the `bbs_settings` table:

```sql
SELECT * FROM bbs_settings WHERE key = 'master_key_salt';
```

The salt is combined with the admin password using Argon2id to derive the master key. This master key is used to encrypt all user encryption keys.

### Why This Matters

**The salt is CRITICAL to data recovery.** Without the exact same salt:

- The master key cannot be regenerated (even with correct admin password)
- User encryption keys cannot be decrypted
- All user mail becomes permanently unreadable
- The only recovery is to delete all users and start fresh

### Failure Scenarios

#### Scenario 1: Salt Lost, Users Exist

**Symptoms:**
```
CRITICAL: Master key salt missing but 3 users exist!
User encryption keys cannot be decrypted. Database may be corrupted.
Either restore the bbs_settings table from backup or delete all users.
```

**Cause:** The `bbs_settings` table was truncated, corrupted, or restored from a backup that didn't include the salt.

**Resolution:**
1. Restore the database from a backup that includes the correct salt
2. OR delete all users and let them re-register (data loss)

#### Scenario 2: Salt Regenerated (Pre-fix versions)

In versions before the safety check was added, if the salt was missing, the BBS would silently generate a new one. Users created before the regeneration would have encryption keys that could never be decrypted.

**Symptoms:**
- `InvalidTag` errors when composing/reading mail
- "Failed to decrypt message" errors
- Mail works for some users but not others

**Cause:** User was created with salt A, but the BBS is now using salt B.

**Resolution:**
- Delete affected users (those created before salt regeneration)
- Have them re-register

#### Scenario 3: Admin Password Changed

Changing the admin password in `config.toml` will generate a different master key, breaking all existing user encryption.

**DO NOT change the admin password after users have registered.**

If you must change it:
1. Export all user data (if possible)
2. Delete the database
3. Start fresh with new password

### Prevention & Recovery

#### Prevention

1. **Never delete or truncate the `bbs_settings` table**
2. **Never change the admin password after users exist**
3. **Always backup the full database** (includes salt automatically)
4. **Use Docker volumes** to persist `/data` across container recreations

#### Verifying Salt Integrity

Check that salt exists and matches your backup:

```bash
# Inside container or with access to DB
sqlite3 /data/advbbs.db "SELECT value FROM bbs_settings WHERE key = 'master_key_salt';"
```

The value should be a base64-encoded string like `Abc123XyZ...==`

#### Recovery from Backup

If salt is lost but you have a backup:

```bash
# Stop BBS
docker stop advbbs

# Restore bbs_settings from backup
sqlite3 /data/advbbs.db "DELETE FROM bbs_settings WHERE key = 'master_key_salt';"
sqlite3 /data/backup.db "SELECT * FROM bbs_settings WHERE key = 'master_key_salt';" | \
  sqlite3 /data/advbbs.db

# Restart BBS
docker start advbbs
```

---

## Backup Strategy

### What Gets Backed Up

The automatic backup (via `backup_interval_hours`) copies the entire SQLite database, including:

- `users` table (with encrypted keys)
- `messages` table (encrypted mail and posts)
- `bbs_settings` table (**including master_key_salt**)
- All other tables

### Backup Location

Configured in `config.toml`:

```toml
[database]
path = "/data/advbbs.db"
backup_path = "/data/backups"
backup_interval_hours = 24
```

### Manual Backup

```bash
# From host (Docker volume)
docker exec advbbs sqlite3 /data/advbbs.db ".backup /data/backups/manual_$(date +%Y%m%d).db"

# Or copy the volume directly
cp /var/lib/docker/volumes/advbbs_data/_data/advbbs.db ~/advbbs_backup_$(date +%Y%m%d).db
```

### Backup Retention

The BBS keeps the **last 7 backups** automatically. Older backups are deleted.

For critical deployments, consider:
- Copying backups off the host
- Using a separate backup solution (e.g., restic, borg)
- Storing at least one backup with the salt separately

---

## Troubleshooting

### BBS Won't Start

**Error:** `Master key salt missing but X users exist`

The safety check is preventing data corruption. See [Salt Lost, Users Exist](#scenario-1-salt-lost-users-exist).

---

**Error:** `Admin password must be changed from default`

Edit `config.toml` and change the admin password from `changeme`.

---

### Mail Errors

**Error:** `InvalidTag` or `Failed to decrypt message`

The user's encryption key cannot be decrypted. This usually means:
- User was created with a different salt
- User was created with a different admin password

**Resolution:** Delete the affected user via the config tool (option 7 → 4) and have them re-register.

---

### User Can't Login

**Error:** `Invalid username or password`

- User may be typing wrong password
- User may be banned (check in config tool option 7 → 1)
- Try `!logout` first, then `!login` again

---

### Messages Not Delivering

Check the logs for ACK status:

```bash
docker logs advbbs --tail 100 | grep -E "(ACK|NAK|deliver)"
```

- `ACK from !nodeId (Xms)` - Message was acknowledged
- `NAK from !nodeId: NO_ROUTE` - Recipient not reachable
- `Mail composed: X -> Y` - Mail was created
- `Mail delivered` - Notification was sent

---

### Database Locked

**Error:** `database is locked`

Another process is accessing the database. This can happen if:
- The config tool is open while BBS is running (usually OK)
- Backup is in progress
- Multiple BBS instances pointing to same database (don't do this)

Wait a moment and retry, or restart the BBS.
