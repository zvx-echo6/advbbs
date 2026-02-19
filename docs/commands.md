# advBBS Command Reference

All commands require the `!` prefix (e.g., `!help`, `!mail`).

Short aliases are shown in parentheses for quick typing on mobile.

## Case Sensitivity

- Commands are case-insensitive (`!MAIL` = `!mail` = `!Mail`)
- Usernames are case-insensitive (`alice` = `Alice`)
- BBS peer names are case-insensitive (`!send user@MV51` = `!send user@mv51`)
- Passwords are case-sensitive

---

## Quick Reference

| Command | Alias | Description |
|---------|-------|-------------|
| `!bbs` or `!help` | `!?` | Show help |
| `!info` | `!i` | BBS information |
| `!register` | | Create account |
| `!login` | | Log in |
| `!logout` | | Log out |
| `!mail` | `!m` | Check inbox |
| `!send` | `!s` | Send mail |
| `!read` | `!r` | Read mail or post |
| `!delete` | `!d`, `!del` | Delete mail |
| `!reply` | `!re` | Reply to mail |
| `!forward` | `!fwd` | Forward mail |
| `!sent` | | Check sent mail status |
| `!board` | `!b` | List/enter boards |
| `!list` | `!l` | List posts |
| `!post` | `!p` | Create post |
| `!quit` | `!q` | Exit board |
| `!nodes` | `!n` | List your nodes |
| `!addnode` | `!an` | Add a node |
| `!rmnode` | `!rn` | Remove a node |
| `!peers` | | List BBS peers |

---

## Authentication

### Register

Create a new account:

```
!register <username> <password>
```

- Username: 3-16 characters, alphanumeric + underscore
- Password: 6+ characters
- Auto-logs you in after registration
- Associates your current Meshtastic node with the account

### Login

```
!login <username> <password>
```

- Must login from a registered node (2FA)
- Use `!addnode` to add additional devices

### Logout

```
!logout
```

### Change Password

```
!passwd <old_password> <new_password>
```

### Node Management

Associate multiple Meshtastic devices with your account:

```
!addnode <node_id>     # Add a node (e.g., !addnode !abc12345)
!rmnode <node_id>      # Remove a node
!nodes                 # List your associated nodes
```

Short aliases: `!an` (addnode), `!rn` (rmnode), `!n` (nodes)

---

## Mail

### Check Mail

```
!mail              # or !m
!mail <start>      # List 5 messages starting at #start
```

Shows unread count and recent messages.

### Read Mail

```
!read              # or !r - List inbox
!read <n>          # Read message #n
```

### Send Mail

**Local (same BBS):**
```
!send <username> <message>     # or !s
```

**Remote (different BBS):**
```
!send <username>@<BBS> <message>
```

The `@BBS` is the BBS callsign (e.g., `MV51`, `TEST`), not a node ID.

### Reply

```
!reply <message>           # or !re - Reply to last read message
!reply <n> <message>       # Reply to message #n
```

### Forward

```
!forward <user[@BBS]>      # or !fwd - Forward last read message
!forward <n> <user[@BBS]>  # Forward message #n
```

### Delete

```
!delete <n>     # or !del or !d
```

### Check Sent Mail

```
!sent
```

Shows status of remote mail you've sent (delivered, pending, failed).

---

## Bulletin Boards

### List Boards

```
!board     # or !b
```

### Enter Board

```
!board <name>
```

### List Posts

```
!list           # or !l - Last 5 posts
!list <n>       # 5 posts starting at #n
```

Posts are numbered oldest=#1, newest=#N.

### Read Post

```
!read <n>     # or !r
```

(Must be in a board - use `!board <name>` first)

### Create Post

```
!post <subject> <body>     # or !p
```

Requires login. Subject max 64 chars, body max 2000 chars.

### Exit Board

```
!quit     # or !q
```

---

## Meshtastic Native Reply

advBBS supports using Meshtastic's built-in reply function:

- **Mail**: After reading a message with `!read <n>`, use your Meshtastic app's reply button to send a reply. No `!` prefix needed.
- **Boards**: After entering a board or listing posts, use reply to post a new message.

Reply context expires after 5 minutes (mail) or 10 minutes (boards).

---

## Access Levels

| Level | Description |
|-------|-------------|
| Always | Anyone can use |
| Sync boards | Anonymous on sync-enabled boards (e.g., `general`) |
| Authenticated | Must be logged in |
| Admin | Must be admin user |

### Command Access Summary

| Command | Access |
|---------|--------|
| `!help`, `!bbs`, `!?` | Always |
| `!info` (`!i`) | Always |
| `!register` | Always |
| `!login` | Always |
| `!board` (`!b`) | Always |
| `!peers` | Always |
| `!list` (`!l`) | Sync boards* or authenticated |
| `!read` (`!r`) board | Sync boards* or authenticated |
| `!read` (`!r`) mail | Authenticated |
| `!logout` | Authenticated |
| `!passwd` | Authenticated |
| `!send` (`!s`) | Authenticated |
| `!mail` (`!m`) | Authenticated |
| `!sent` | Authenticated |
| `!delete` (`!d`) | Authenticated |
| `!reply` (`!re`) | Authenticated |
| `!forward` (`!fwd`) | Authenticated |
| `!post` (`!p`) | Authenticated |
| `!addnode` (`!an`) | Authenticated |
| `!rmnode` (`!rn`) | Authenticated |
| `!nodes` (`!n`) | Authenticated |
| `!destruct` | Authenticated |

*Sync-enabled boards (e.g., `general`) allow anonymous read access.

---

## Admin Commands

| Command | Alias | Description |
|---------|-------|-------------|
| `!ban <user> [reason]` | | Ban a user |
| `!unban <user>` | | Unban a user |
| `!mkboard <name> [desc]` | `!mb` | Create a board |
| `!rmboard <name>` | `!rb` | Delete a board |
| `!announce <message>` | `!ann` | Broadcast announcement |

Use `!? admin` to see admin help.

---

## Utility

### Help

```
!bbs
!help
!?
```

Shows command help in 3 short messages.

### BBS Info

```
!info     # or !i
```

Shows BBS name, callsign, mode, uptime, user count, message count.

### Peers

```
!peers
```

Lists connected BBS peers for remote mail.

### Self-Destruct

```
!destruct CONFIRM
```

Permanently deletes your account and all messages. Cannot be undone.
