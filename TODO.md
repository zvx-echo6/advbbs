# advBBS Roadmap & Status

## Completed (v0.4.0)

### Route Announcement Protocol (RAP)
Automatic route discovery between federated BBS nodes using distance-vector routing.
- RAP_PING/RAP_PONG heartbeats for peer health tracking
- RAP_ROUTES for route table propagation
- Automatic multi-hop path discovery (max 5 hops)
- Peer health states: unknown → alive → unreachable → dead
- Conservative defaults for low-bandwidth mesh (12h heartbeat, 36h route expiry)

### Board Sync
Federated bulletin board synchronization between peer BBS nodes.
- BOARDREQ/BOARDACK/BOARDDAT/BOARDDLV wire protocol
- Database-driven sync control (`sync_enabled` column)
- Default boards: `general` (synced), `local` (not synced)
- Up to 2 additional custom boards can be marked for sync (max 3 synced total)
- Batch sync: triggers at 10 new local posts OR hourly with pending content
- Federated identity: remote posts display as `user@BBS`
- Per-UUID dedup prevents duplicate posts across peers
- Admin can delete any post locally (never propagated)

### ACK Handling Fix
- Thread-safe ACK signaling between Meshtastic callback thread and asyncio event loop
- Eliminated 30-second phantom timeouts on every mesh DM
- Fixed rate limiting bypass in chunk retries

### Codebase Cleanup
- Removed TC2-BBS, meshing-around, and frozenbbs compatibility layers
- Protocol validation hardcoded to `advbbs`
- All 87 tests passing

## Ideas Under Consideration

### Restricted Boards
Private boards with per-board encryption. The backend implementation exists (board_access table, per-user encrypted board keys, access checks in BoardService) but no user-facing commands are wired up yet.

Needed:
- `!mkboard -r <name> [desc]` — create a restricted board
- `!grant <board> <user>` — grant a user access (encrypts board key with their user key)
- `!revoke <board> <user>` — revoke access
- Restricted boards never sync (encryption keys are per-BBS)

### Web Reader Interface
Basic read-only web interface for viewing boards and mail (partially implemented, disabled by default).

---

## Contributing

Have an idea? Open an issue or submit a PR at https://github.com/zvx-echo6/advbbs
