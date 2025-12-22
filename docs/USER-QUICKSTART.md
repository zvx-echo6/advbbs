# advBBS User Quick Reference

```
    ___       __      ____  ____  _____
   /   | ____/ /   __/ __ )/ __ )/ ___/
  / /| |/ __  / | / / __  / __  /\__ \
 / ___ / /_/ /| |/ / /_/ / /_/ /___/ /
/_/  |_\__,_/ |___/_____/_____//____/
        Meshtastic Mesh BBS
```

All commands start with `!` and are sent via **Meshtastic DM** to the BBS node.

---

## Quick Start

```
!register myname mypass     Create your account
!login myname mypass        Log in
!help                       Show all commands
```

---

## Sending Mail

### Local Mail (same BBS)
```
!send alice Hello there!
```

### Remote Mail (federated BBS)
```
!send alice@TV51 Hello from our BBS!
       └────┬───┘
         user @ BBS callsign
```

Use `!peers` to see connected BBS systems you can reach.

### Mail Commands

| Command | Short | What it does |
|:--------|:-----:|:-------------|
| `!send <user> <msg>` | `!s` | Send mail (local) |
| `!send <user@BBS> <msg>` | `!s` | Send mail (remote) |
| `!mail` | `!m` | Check inbox |
| `!read 1` | `!r 1` | Read message #1 |
| `!reply Hello back!` | `!re` | Reply to last read |
| `!delete 1` | `!d 1` | Delete message #1 |
| `!sent` | | Check remote mail status |

**Tip:** After `!read`, use Meshtastic's reply button - no `!` needed!

---

## Bulletin Boards

```
!board              List all boards
!board general      Enter 'general' board
!list               See posts
!read 3             Read post #3
!post Title Body    Create a post
!quit               Leave board
```

---

## Account & Nodes

### Link Multiple Devices
Your account can be used from multiple Meshtastic devices:

```
!nodes                  List your linked devices
!addnode !abc12345      Add another device
!rmnode !abc12345       Remove a device
```

### Account Management
```
!passwd oldpass newpass    Change password
!logout                    Log out
!destruct CONFIRM          Delete account forever
```

---

## Federation: Sending Mail to Other BBS Systems

advBBS nodes can federate together, allowing mail between users on different systems.

```
          Your BBS                    Remote BBS
         ┌────────┐                  ┌────────┐
   you ──┤  MV51  ├── Meshtastic ──► │  TV51  ├── alice
         └────────┘     mesh         └────────┘

         !send alice@TV51 Hello!
```

### How it works:
1. **Check peers:** `!peers` shows connected BBS systems
2. **Address format:** `username@CALLSIGN` (e.g., `alice@TV51`)
3. **Multi-hop:** Mail can relay through intermediate BBS nodes
4. **Size limit:** Remote mail max ~450 characters

### Example
```
!peers                              # See: MV51, TV51, J51B
!send alice@TV51 Hello from MV51!   # Send to alice on TV51
!sent                               # Check delivery status
```

---

## Command Quick Reference

| Category | Commands |
|:---------|:---------|
| **Help** | `!help` `!info` |
| **Auth** | `!register` `!login` `!logout` `!passwd` |
| **Mail** | `!send` `!mail` `!read` `!reply` `!delete` `!forward` `!sent` |
| **Boards** | `!board` `!list` `!read` `!post` `!quit` |
| **Nodes** | `!nodes` `!addnode` `!rmnode` |
| **Network** | `!peers` |

---

## Tips

- **Shortcuts:** Most commands have short aliases (`!s`, `!m`, `!r`, `!b`, `!l`)
- **Native Reply:** After reading mail or entering a board, just reply - no `!` needed
- **Case:** Commands and usernames are case-insensitive; passwords are case-sensitive
- **Remote Limits:** Keep federated mail under 450 characters

---

## Example Session

```
> !register matt secretpass
Registered! Node linked.

> !peers
Connected peers: MV51, TV51, J51B

> !send alice Hey, are you there?
Mail sent to alice

> !send bob@TV51 Hello from MV51!
Remote mail queued to bob@TV51

> !mail
Inbox (2 new):
1. [NEW] alice: Re: Hey
2. [NEW] bob@TV51: Got your message!

> !read 1
From: alice
Hey yourself! How's the mesh?

> !reply Great, just testing federation!
Reply sent to alice

> !board
Boards: general, announcements, trading

> !board trading
Entered: trading (5 posts)

> !post WTS Solar panel 100W solar panel, $50 OBO

Posted to trading

> !logout
Logged out. 73!
```
