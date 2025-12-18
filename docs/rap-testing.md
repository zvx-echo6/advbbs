# RAP Testing Documentation

This document describes the test setup and results for the Route Announcement Protocol (RAP) implementation.

## Test Environment

**Location:** 192.168.1.250 (Test VM)
**Transport:** MQTT via Mosquitto broker
**Mesh Simulation:** meshtasticd Docker containers in `--sim` mode

### Test Topology

```
TB0 <---> TB1 <---> TB2 <---> TB3 <---> TB4

5-node linear topology where:
- Each node only has direct peers configured for adjacent nodes
- RAP enables discovery of non-adjacent nodes through route propagation
```

### Node Configuration

| Node | Callsign | Mesh Port | Node ID | Direct Peers |
|------|----------|-----------|---------|--------------|
| TB0 | TB0 | 4407 | !000007d1 | TB1 |
| TB1 | TB1 | 4408 | !000007d2 | TB0, TB2 |
| TB2 | TB2 | 4409 | !000007d3 | TB1, TB3 |
| TB3 | TB3 | 4410 | !00000007 | TB2, TB4 |
| TB4 | TB4 | 4411 | !00000008 | TB3 |

### Docker Containers

```bash
# Mesh containers (meshtasticd with MQTT)
mesh-bbs-a  # TB0 node (!000007d1)
mesh-bbs-b  # TB1 node (!000007d2)
mesh-bbs-c  # TB2 node (!000007d3)
mesh-bbs-d  # TB3 node (!00000007)
mesh-bbs-e  # TB4 node (!00000008)
```

## RAP Configuration

Each BBS uses these RAP settings:

```toml
[sync]
enabled = true
rap_enabled = true
rap_heartbeat_interval_seconds = 30
mail_max_hops = 5
```

## Test Results

### Route Discovery

After RAP convergence, each node discovered the full network topology:

| Node | Discovered Routes |
|------|-------------------|
| TB0 | TB0:0, TB1:1, TB2:2, TB3:3, TB4:4 |
| TB1 | TB1:0, TB0:1, TB2:1, TB3:2, TB4:3 |
| TB2 | TB2:0, TB1:1, TB3:1, TB0:2, TB4:2 |
| TB3 | TB3:0, TB2:1, TB4:1, TB1:2, TB0:3 |
| TB4 | TB4:0, TB3:1, TB2:2, TB1:3, TB0:4 |

**Key observation:** TB0 discovered TB4 at 4 hops and TB4 discovered TB0 at 4 hops, even though they have no direct peer relationship.

### RAP Message Flow

#### Message Types

| Message | Format | Purpose |
|---------|--------|---------|
| RAP_PING | `advBBS\|1\|RAP_PING\|<timestamp_us>` | Heartbeat check |
| RAP_PONG | `advBBS\|1\|RAP_PONG\|<timestamp_us>\|<routes>` | Response with route table |
| RAP_ROUTES | `advBBS\|1\|RAP_ROUTES\|<routes>` | Periodic route advertisement |

#### Route Table Format

```
bbs_name:hop_count:quality_score;bbs_name:hop_count:quality_score;...
```

Example from TB0:
```
TB0:0:1.0;TB1:1:1.00;TB2:2:1.00;TB3:3:1.00;TB4:4:1.00
```

### Sample Message Trace

**TB0 receiving routes from TB1:**
```
advBBS|1|RAP_ROUTES|TB1:0:1.0;TB0:1:1.00;TB2:1:1.00;TB3:2:1.00;TB4:3:1.00
```

TB0 processes this by adding +1 to each hop count:
- TB1:0 becomes TB1:1 (direct peer)
- TB2:1 becomes TB2:2
- TB3:2 becomes TB3:3
- TB4:3 becomes TB4:4

**TB0 advertising its routes:**
```
advBBS|1|RAP_ROUTES|TB0:0:1.0;TB1:1:1.00;TB2:2:1.00;TB3:3:1.00;TB4:4:1.00
```

## Convergence Time

With `rap_heartbeat_interval_seconds = 30`:
- 4-node topology converges in ~60-90 seconds
- 5-node topology converges in ~90-120 seconds

Route propagation requires multiple RAP exchange cycles to reach distant nodes.

## Reliability Considerations

### Multi-Hop Delivery Risks

Each hop in a multi-hop mail delivery introduces potential failure points:

| Risk | Description | Mitigation |
|------|-------------|------------|
| Intermediate node offline | A relay BBS goes down between hops | RAP detects via missed PINGs, routes expire |
| RF propagation failure | Meshtastic packet doesn't reach next hop | Meshtastic's built-in ACK and retry |
| Message corruption | Data corrupted in transit | Protocol-level validation |
| Timeout | Relay node too slow to respond | Configurable timeouts and retry limits |

### Retry Configuration

Mail delivery includes configurable retry options in `config.toml`:

```toml
[sync]
mail_delivery_max_retries = 3        # Retry failed deliveries up to 3 times
mail_delivery_retry_delay = 60       # Wait 60 seconds between retries
mail_delivery_timeout = 120          # Timeout waiting for MAILACK/MAILDLV
```

### Route Expiry

Routes learned via RAP expire after `rap_route_expiry_seconds` (default: 3600). This ensures stale routes are automatically removed when:
- Intermediate nodes go offline
- Network topology changes
- Peer health degrades to "dead" status

### Scalability vs Reliability Trade-off

While RAP theoretically supports unlimited hop counts (bounded only by Meshtastic message size limits), practical deployments should consider:

1. **Probability of failure increases with hops**: If each hop has 95% success rate, a 4-hop path has ~81% success rate (0.95^4)
2. **Latency increases linearly**: Each hop adds ~3 seconds (chunk delay) plus RF propagation time
3. **Mesh congestion**: More hops = more RF traffic on the mesh

**Recommendation:** For production deployments, prefer direct peering where possible. Use multi-hop routing as a fallback for truly disconnected regions of the mesh.

## Configuration Notes

### Max Hops

The `mail_max_hops` setting affects RAP route storage. Routes with hop counts exceeding this value are not stored. For a 5-node linear topology, set `mail_max_hops = 5` or higher.

**Note:** There is no hard limit on hop count - set this based on your expected network diameter. However, higher hop counts increase delivery failure probability.

### MQTT Transport Setup

Each meshtasticd container requires:
```bash
meshtastic --host localhost:<port> \
  --set mqtt.enabled true \
  --set mqtt.address <broker_ip> \
  --set mqtt.json_enabled true \
  --set mqtt.encryption_enabled false

meshtastic --host localhost:<port> \
  --ch-index 0 \
  --ch-set uplink_enabled true \
  --ch-set downlink_enabled true
```

## Database Tables

### bbs_peers

Stores direct peers with health tracking:
```sql
SELECT node_id, name, callsign, health_status FROM bbs_peers;
```

### rap_routes

Stores learned routes from RAP:
```sql
SELECT dest_bbs, hop_count, quality_score FROM rap_routes;
```

## Reproducing the Test

### Prerequisites

1. Docker with meshtastic/meshtasticd:beta image
2. Mosquitto MQTT broker on port 1883
3. advBBS installed in a Python virtualenv

### Setup Script

```bash
# Create mesh containers
for port in 4407 4408 4409 4410 4411; do
  hwid=$(printf "%08x" $((port - 4400)))
  docker run -d --name mesh-bbs-$port --network host \
    -v ~/mesh-sim/node-$port:/data \
    meshtastic/meshtasticd:beta \
    meshtasticd --sim --hwid $hwid -p $port -d /data
done

# Configure MQTT on each
for port in 4407 4408 4409 4410 4411; do
  meshtastic --host localhost:$port \
    --set mqtt.enabled true \
    --set mqtt.address $(hostname -I | awk '{print $1}') \
    --set mqtt.json_enabled true \
    --set mqtt.encryption_enabled false
  docker restart mesh-bbs-$port
  meshtastic --host localhost:$port \
    --ch-index 0 --ch-set uplink_enabled true --ch-set downlink_enabled true
  docker restart mesh-bbs-$port
done
```

### Start BBS Instances

```bash
for i in 0 1 2 3 4; do
  cd ~/bbs-test/bbs$i
  nohup advbbs --config ./config.toml > bbs$i.log 2>&1 &
done
```

### Monitor RAP Activity

```bash
# Watch RAP messages on a specific node
tail -f ~/bbs-test/bbs0/bbs0.log | grep -E "RAP_PING|RAP_PONG|RAP_ROUTES"

# Check route tables
for i in 0 1 2 3 4; do
  echo "=== TB$i ==="
  grep "SYNC PROTOCOL SENDING.*RAP_PONG" ~/bbs-test/bbs$i/bbs$i.log | tail -1
done
```

## Multi-Hop Mail Routing

RAP enables automatic route discovery which is used for multi-hop mail delivery. When a user sends mail to `user@TB4` from TB0, the system:

1. Looks up TB4 in the RAP route table
2. Finds route: TB4 is 4 hops away via TB1
3. Sends MAILREQ to TB1 (next hop)
4. TB1 relays to TB2, TB2 to TB3, TB3 to TB4
5. TB4 sends MAILACK back through the reverse path
6. Mail chunks (MAILDAT) follow the same route
7. MAILDLV confirmation returns to sender

### Mail Protocol Messages

| Message | Purpose | Flow Direction |
|---------|---------|----------------|
| MAILREQ | Request delivery, check route | Sender → Destination |
| MAILACK | Accept mail, ready for chunks | Destination → Sender |
| MAILNAK | Reject mail (user not found, loop, etc) | Destination → Sender |
| MAILDAT | Message chunk (max 150 chars) | Sender → Destination |
| MAILDLV | Delivery confirmation | Destination → Sender |

### Route Tracking

Each hop adds itself to the route field to prevent loops:

```
TB0 sends: MAILREQ|uuid|sender|TB0|testuser|TB4|1|1|TB0
TB1 relays: MAILREQ|uuid|sender|TB0|testuser|TB4|2|1|TB0,TB1
TB2 relays: MAILREQ|uuid|sender|TB0|testuser|TB4|3|1|TB0,TB1,TB2
TB3 relays: MAILREQ|uuid|sender|TB0|testuser|TB4|4|1|TB0,TB1,TB2,TB3
TB4 receives and processes (is destination)
```

### Testing Multi-Hop Mail

To test multi-hop mail delivery:

1. Create users on source and destination BBS
2. Send mail via `!send user@DEST_BBS message`
3. Monitor logs on all intermediate nodes for MAILREQ/MAILDAT relay
4. Verify delivery on destination BBS

**Note:** The test VM environment uses simulated mesh nodes where all nodes are BBS nodes. For realistic mail testing, a separate user node (not configured as a BBS peer) should send commands to the source BBS.

### Successful Multi-Hop Test Results

Mail sent from TB0 to testuser4@TB4:

**Route taken:** TB0 → TB1 → TB2 → TB3 → TB4

**Message trace:**
```
TB0: MAILREQ|3c232664|testuser0|TB0|testuser4|TB4|1|1|TB0
TB1: MAILREQ received (hop 1), relaying to TB2
TB2: MAILREQ received (hop 2), using RAP route to TB4 via TB3
TB3: MAILREQ received (hop 3), relaying to TB4
TB4: MAILREQ received (hop 4), accepted for testuser4
TB4 → TB3 → TB2 → TB1 → TB0: MAILACK|OK
TB0 → TB1 → TB2 → TB3 → TB4: MAILDAT|1/1|Multi-hop mail test via RAP routing!
TB4: MAILDAT all parts received, delivering
```

**Key findings:**
- RAP routing table successfully used for multi-hop relay
- MAILREQ hop counter increments correctly at each relay
- Route tracking (TB0,TB1,TB2,TB3) prevents loops
- MAILACK propagates back through relay chain
- MAILDAT chunks follow same route as MAILREQ
- Total delivery time: ~30 seconds for 4-hop delivery

## Test Date

December 18, 2025
