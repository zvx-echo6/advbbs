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

### Default Boards

| Board | Description | Syncs? |
|-------|-------------|--------|
| `general` | General discussion | ✅ Yes |
| `help` | Help and support | ❌ No |
| `local` | Local-only discussion | ❌ No |

**Sync board access:**
- Anyone can `!list` and `!read` posts on sync-enabled boards
- Must be logged in to `!post`

**Non-sync board access:**
- Must be logged in to `!list`, `!read`, and `!post`

### Custom Boards

Admin-created boards using `!mkboard <name> [description]` (admin only). Custom boards are local-only by default. Admins can enable sync on up to 2 additional boards (max 3 synced total including `general`) via the database `sync_enabled` column.

Delete with `!rmboard <name>` (admin only).

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

Boards with `sync_enabled = 1` in the database are synced between peer BBS nodes. By default, only `general` is synced. Admins can enable sync on up to 2 additional boards (3 synced total).

### What Syncs

| Content | Syncs? |
|---------|--------|
| Posts on sync-enabled boards | ✅ Yes |
| Posts on local-only boards | ❌ No |
| Restricted boards | ❌ No |

### How Sync Works

1. New local posts increment a per-board counter
2. Sync triggers when 10 posts accumulate OR 1 hour elapses with pending content
3. Posts are batched into a single payload per board per peer
4. Each post has a UUID for deduplication
5. Content is decrypted, transmitted, and re-encrypted at destination
6. Remote posts display as `author@BBS` (e.g., `alice@REMOTE1`)

## Admin Commands

| Command | Description |
|---------|-------------|
| `!mkboard <name> [desc]` | Create a new board |
| `!rmboard <name>` | Delete a board |

Example:
```
!mkboard local-events Community events and meetups
```

Note: Admin-created boards are local-only by default. Use the database `sync_enabled` column to enable sync (max 3 synced boards total).
