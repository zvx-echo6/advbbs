# advBBS Future Ideas & Roadmap

## Planned Features

### Route Announcement Protocol (RAP)

**Status:** Proposed
**Priority:** Medium
**Complexity:** High

Automatic route discovery and mapping between federated BBS nodes, similar to distance-vector routing protocols like RIP.

#### Problem

Currently, if a user tries to send mail to `alice@J51B` but their local BBS doesn't have J51B as a direct peer, the system has no way to know that J51B might be reachable through an intermediate BBS (e.g., MV51 → TEST → J51B).

#### Proposed Solution

Each BBS periodically announces its route table to all peers:

```
RAP|1|ROUTES|MV51:0,TEST:1,J51B:2
```

Format: `BBS_NAME:HOP_COUNT` (0 = self, 1 = direct peer, 2 = two hops away, etc.)

#### How It Works

1. **Announcement**: Each BBS broadcasts its route table every N minutes
2. **Propagation**: When BBS-A receives routes from BBS-B:
   - For each route in BBS-B's table, add 1 to the hop count
   - If shorter than existing route, update local table
   - If new destination, add to table
3. **Auto-Discovery**: When J51B joins the network and peers with TEST:
   - J51B announces `J51B:0` to TEST
   - TEST updates table, announces `TEST:0,J51B:1` to MV51
   - MV51 updates table, now knows `J51B:2` (reachable via TEST)
4. **Mail Routing**: When sending to `alice@J51B`:
   - Look up J51B in route table
   - Find next hop (TEST in this example)
   - Forward MAILREQ to TEST

#### Message Format

```
RAP|<version>|<type>|<payload>

Types:
- ROUTES: Full route table announcement
- WITHDRAW: Remove routes (BBS going offline)
```

#### Scaling Considerations

- Works like distance-vector routing - can scale to arbitrary network sizes
- Each BBS only needs to know next hop, not full path
- Loop prevention via hop count limits (already implemented: max 5 hops)
- Compressed format using shortnames keeps messages small

#### Configuration Options

```toml
[sync]
# Route Announcement Protocol settings
rap_enabled = true
rap_announce_interval_minutes = 30
rap_max_hops = 5
rap_stale_route_minutes = 120  # Remove routes not seen in this time
```

---

## Completed

### Bulletin Sync Removal (v1.x)

- Removed bulletin synchronization between BBS nodes
- Mail-only federation now - simpler and more reliable
- Boards remain local-only to each BBS

---

## Ideas Under Consideration

### Web Reader Interface

Basic read-only web interface for viewing boards and mail (already partially implemented, disabled by default).

### TC2-BBS Compatibility

Protocol adapter to communicate with TC2-BBS-mesh nodes using their native protocol.

### meshing-around Compatibility

Protocol adapter to communicate with meshing-around BBS nodes.

---

## Contributing

Have an idea? Open an issue or submit a PR!
