# advBBS Bulletin Boards

Public message boards for community discussion.

## Commands

| Command | Format | Description | Access |
|---------|--------|-------------|--------|
| `!board` | `!board` | List all boards | Always |
| `!board` | `!board <name>` | Enter a board | Always |
| `!list` | `!list [start]` | List posts (5 at a time) | Sync boards or authenticated |
| `!read` | `!read <n>` | Read post #n | Sync boards or authenticated |
| `!post` | `!post <subject> <body>` | Create new post | Authenticated |
| `!quit` | `!quit` | Exit current board | Always |

## Board Types

### Sync Boards (Public)

These boards sync between BBS nodes and can be read without logging in:

| Board | Description |
|-------|-------------|
| `general` | General discussion |
| `help` | Help and support |

**Access:**
- Anyone can `!list` and `!read` posts
- Must be logged in to `!post`

### Local Boards

Admin-created boards that don't sync between BBS nodes:

- Require login to read and post
- Created with `!mkboard <name> [description]` (admin only)
- Deleted with `!rmboard <name>` (admin only)

### Restricted Boards

Private boards with per-board encryption:

- Require explicit access grants
- Never synced (recipients on other BBS nodes don't have keys)
- Content encrypted with board-specific key

## Listing Posts

Posts are numbered sequentially, starting with **#1 for the oldest post**.

```
!list        - Shows last 5 posts (e.g., posts 18-22 of 22)
!list 13     - Shows posts 13-17
!list 1      - Shows first 5 posts (oldest)
```

Example output:
```
Posts 18-22 of 22:
#    Date   Author       Subject
----------------------------------------
 18. 12/10 alice        Anyone using solar?
 19. 12/11 bob          Re: Solar setup tips
 20. 12/12 charlie      New repeater online
 21. 12/14 alice        Weather station data
 22. 12/15 dave         Testing from mobile
```

## Reading Posts

```
!read 18
```

Output:
```
=== general#18 ===
Subject: Anyone using solar?
From: alice
Date: 2025-12-10 14:32

Has anyone had success running their node on solar power?
Looking for panel and battery recommendations for off-grid use.
```

## Creating Posts

Must be logged in and inside a board:

```
!board general
!post Solar setup My solar config: 20W panel, 12Ah LiFePO4 battery...
```

**Limits:**
- Subject: 64 characters max
- Body: 2000 characters max
- Posts expire after 90 days

## Board Sync

Only `general` and `help` boards sync between BBS nodes.

```python
# Defined in advbbs/core/boards.py
SYNC_BOARDS = ["general", "help"]
```

### What Syncs

| Content | Syncs? |
|---------|--------|
| Posts on `general` | ✅ Yes |
| Posts on `help` | ✅ Yes |
| Admin-created boards | ❌ No |
| Restricted boards | ❌ No |

### How Sync Works

1. BBS nodes periodically exchange bulletins via FQ51 protocol
2. Each post has a UUID for deduplication
3. Content is decrypted, transmitted, and re-encrypted at destination
4. Posts from unknown boards are mapped to `general`

### Sync Interval

Default: Every 60 minutes (configurable in `config.toml`)

```toml
[sync]
bulletin_sync_interval_minutes = 60
```

## Admin Commands

| Command | Description |
|---------|-------------|
| `!mkboard <name> [desc]` | Create a new board |
| `!rmboard <name>` | Delete a board |

Example:
```
!mkboard local-events Community events and meetups
```

Note: Admin-created boards are local-only and don't sync.
