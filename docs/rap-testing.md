# RAP Testing Documentation

This document describes the test setup and results for the Route Announcement Protocol (RAP) implementation.

## Test Environment

**Location:** 192.168.1.250 (Test VM)
**Transport:** MQTT via Mosquitto broker
**Mesh Simulation:** meshtasticd Docker containers in `--sim` mode

### Test Topology

```
BBS0 (B0) <---> BBS1 (B1) <---> BBS2 (B2) <---> BBS3 (B3) <---> BBS4 (B4)
!000000c8      !00000190      !00000258      !00000320      !2d195e17

5-node linear topology where:
- Each BBS only has direct peers configured for adjacent nodes
- User nodes (node0-node4) connect to their respective BBS for testing
- RAP enables discovery of non-adjacent nodes through route propagation
```

### Node Configuration

| Node | Callsign | Mesh Port | Node ID | Direct Peers |
|------|----------|-----------|---------|--------------|
| BBS0 | B0 | 4401 | !000000c8 | BBS1 |
| BBS1 | B1 | 4403 | !00000190 | BBS0, BBS2 |
| BBS2 | B2 | 4405 | !00000258 | BBS1, BBS3 |
| BBS3 | B3 | 4407 | !00000320 | BBS2, BBS4 |
| BBS4 | B4 | 4409 | !2d195e17 | BBS3 |

### User Nodes (for testing)

| Node | Mesh Port | Node ID | Connected BBS |
|------|-----------|---------|---------------|
| node0 | 4400 | !00000064 | BBS0 |
| node1 | 4402 | !0000012c | BBS1 |
| node2 | 4404 | !000001f4 | BBS2 |
| node3 | 4406 | !000002bc | BBS3 |
| node4 | 4408 | !00000384 | BBS4 |

## RAP Configuration

Each BBS uses these RAP settings (test environment with faster intervals):

```toml
[sync]
enabled = true
rap_enabled = true
rap_heartbeat_interval_seconds = 30      # Fast for testing (default: 43200 = 12 hours)
rap_heartbeat_timeout_seconds = 15
rap_unreachable_threshold = 2
rap_dead_threshold = 5
rap_route_expiry_seconds = 180           # Fast for testing (default: 129600 = 36 hours)
rap_route_share_interval_seconds = 60    # Fast for testing (default: 86400 = 24 hours)
mail_max_hops = 5
```

**Note:** Production defaults use 12-hour heartbeats, 36-hour route expiry, and 24-hour route sharing. The values above are accelerated for testing purposes.

## Implementation Notes

### Rate Limiting (Critical)

The meshtasticd simulator (and real Meshtastic devices) rate limits TEXT_MESSAGE_APP messages. Without rate limiting, messages sent in rapid succession will be silently dropped.

**Solution:** The `MeshInterface` class includes rate limiting:

```python
# In __init__
self._last_send_time = 0
self._send_min_interval = 3.5  # seconds between consecutive sends

# In send_text, before sending
now = time.time()
elapsed = now - self._last_send_time
if elapsed < self._send_min_interval:
    wait_time = self._send_min_interval - elapsed
    time.sleep(wait_time)
self._last_send_time = time.time()
```

### send_dm_wait_ack Method

For reliable MAILDAT delivery, the `send_dm_wait_ack` method sends a message and waits for mesh-level ACK:

```python
async def send_dm_wait_ack(
    self,
    text: str,
    destination: str,
    timeout: float = 30.0
) -> tuple[bool, str]:
    """Send DM and wait for mesh-level ACK."""
```

This ensures MAILDAT chunks are acknowledged at the mesh layer before proceeding.

## Test Results

### Route Discovery

After RAP convergence (~60-90 seconds), each node discovered the full network topology:

**BBS0 Route Table:**
```
dest_bbs | hop_count
---------|----------
B1       | 1
B2       | 2
B3       | 3
B4       | 4
BBS1     | 3
BBS2     | 2
BBS3     | 3
BBS4     | 4
```

**Key observation:** BBS0 discovered BBS4/B4 at 4 hops, even though they have no direct peer relationship.

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

Example from BBS0:
```
B0:0:1.0;BBS1:1:1.00;B1:1:1.00;BBS0:2:1.00;BBS2:2:1.00;B2:2:1.00;BBS3:3:1.00;B3:3:1.00;BBS4:4:1.00;B4:4:1.00
```

### Sample Message Trace

**BBS0 receiving routes from BBS1:**
```
advBBS|1|RAP_ROUTES|B1:0:1.0;BBS0:1:1.00;BBS2:1:1.00;B2:1:1.00;B0:1:1.00;BBS1:2:1.00;BBS3:2:1.00;B3:2:1.00;BBS4:3:1.00;B4:3:1.00
```

BBS0 processes this by adding +1 to each hop count (except itself):
- B1:0 becomes B1:1 (direct peer)
- BBS2:1 becomes BBS2:2
- BBS3:2 becomes BBS3:3
- BBS4:3 becomes BBS4:4

## 4-Hop Mail Delivery Test

### Test Scenario

Send mail from user0@B0 to user4@B4 (4 hops).

### Complete Message Flow

```
1. user0 sends "!send user4@B4 4-hop test from BBS0!" to BBS0

2. BBS0:
   - Creates mail: 0396da51-ad25-4c54-a6d7-9ff73c14c013
   - Looks up B4 in RAP routes: 4 hops via BBS1
   - Sends MAILREQ to BBS1

3. BBS1:
   - Receives MAILREQ (hop 1)
   - Looks up B4: 3 hops via BBS2
   - Relays MAILREQ to BBS2

4. BBS2:
   - Receives MAILREQ (hop 2)
   - Looks up B4: 2 hops via BBS3
   - Relays MAILREQ to BBS3

5. BBS3:
   - Receives MAILREQ (hop 3)
   - Looks up B4: 1 hop via BBS4
   - Relays MAILREQ to BBS4

6. BBS4:
   - Receives MAILREQ (hop 4)
   - user4 exists locally - accepts mail
   - Sends MAILACK|OK back to BBS3

7. MAILACK propagates back:
   BBS4 -> BBS3 -> BBS2 -> BBS1 -> BBS0

8. BBS0 receives MAILACK|OK:
   - Sends MAILDAT with message content

9. MAILDAT propagates forward:
   BBS0 -> BBS1 -> BBS2 -> BBS3 -> BBS4

10. BBS4:
    - Receives MAILDAT: "4-hop test from BBS0!"
    - Stores in database for user4
    - Sends MAILDLV confirmation back
```

### Actual Log Output

**BBS0 (sender):**
```
Created remote mail 0396da51: user0@B0 -> user4@B4
MAILREQ|0396da51-ad25-4c54-a6d7-9ff73c14c013|user0|B0|user4|B4|1|1|B0
Mail queued for user4@B4
```

**BBS1 (relay 1):**
```
MAILREQ received: 0396da51 from user0@B0 to user4@B4 (hop 1)
MAILREQ 0396da51: Using RAP route to B4 via !00000258 (3 hops)
MAILREQ 0396da51: Relaying to B4 via !00000258
```

**BBS2 (relay 2):**
```
MAILREQ received: 0396da51 from user0@B0 to user4@B4 (hop 2)
MAILREQ 0396da51: Using RAP route to B4 via !00000320 (2 hops)
MAILDAT 0396da51: Relaying part 1/1 to !00000320
```

**BBS3 (relay 3):**
```
MAILREQ received: 0396da51 from user0@B0 to user4@B4 (hop 3)
MAILREQ 0396da51: Using RAP route to B4 via !2d195e17 (1 hops)
MAILDAT 0396da51: Relaying part 1/1 to !2d195e17
```

**BBS4 (destination):**
```
MAILREQ received: 0396da51 from user0@B0 to user4@B4 (hop 4)
MAILREQ 0396da51: Accepted for user4, sending MAILACK
MAILDAT received: 0396da51 part 1/1 from !00000320
MAILDAT 0396da51: Stored part 1, have 1/1
MAILDAT 0396da51: All parts received, delivering
Delivering remote mail 0396da51: user0@B0 -> user4
Stored incoming remote mail 0396da51 from user0@B0
DELIVER 0396da51: Stored in database for user4
```

### Route Tracking (Loop Prevention)

Each hop appends itself to the route field:

```
BBS0 sends:   MAILREQ|uuid|user0|B0|user4|B4|1|1|B0
BBS1 relays:  MAILREQ|uuid|user0|B0|user4|B4|2|1|B0,B1
BBS2 relays:  MAILREQ|uuid|user0|B0|user4|B4|3|1|B0,B1,B2
BBS3 relays:  MAILREQ|uuid|user0|B0|user4|B4|4|1|B0,B1,B2,B3
```

## Reproducing the Test

### Prerequisites

1. Docker with meshtastic/meshtasticd:beta image
2. Mosquitto MQTT broker on port 1883
3. advBBS installed in a Python virtualenv

### Quick Setup Script

```bash
#!/bin/bash
# RAP Test Setup - 5 BBS nodes + 5 user nodes

HOST_IP=$(hostname -I | awk "{print \$1}")

# Install Mosquitto
sudo apt-get install -y mosquitto mosquitto-clients
sudo systemctl start mosquitto

# Create directories
mkdir -p ~/bbs-test ~/mesh-sim/data
cd ~/bbs-test

# Clone and install advBBS
git clone https://github.com/zvx-echo6/advbbs.git
python3 -m venv venv
source venv/bin/activate
pip install -e advbbs/

# Create mesh nodes (pairs: user node + BBS node)
for i in 0 1 2 3 4; do
    user_port=$((4400 + i*2))
    bbs_port=$((4401 + i*2))
    user_hwid=$(printf "%08x" $((100 + i*100)))
    bbs_hwid=$(printf "%08x" $((200 + i*100)))
    
    # User node
    docker run -d --name node$i --network host \
        -v ~/mesh-sim/data/node$i:/data \
        meshtastic/meshtasticd:beta \
        meshtasticd --sim --hwid $user_hwid -p $user_port -d /data
    
    # BBS node
    docker run -d --name bbs$i --network host \
        -v ~/mesh-sim/data/bbs$i:/data \
        meshtastic/meshtasticd:beta \
        meshtasticd --sim --hwid $bbs_hwid -p $bbs_port -d /data
done

sleep 5

# Configure MQTT on all nodes
source ~/bbs-test/venv/bin/activate
for port in 4400 4401 4402 4403 4404 4405 4406 4407 4408 4409; do
    meshtastic --host 127.0.0.1:$port \
        --set mqtt.enabled true \
        --set mqtt.address $HOST_IP \
        --set mqtt.json_enabled true \
        --set mqtt.encryption_enabled false
    docker restart $(docker ps -q --filter publish=$port) 2>/dev/null || true
    sleep 2
    meshtastic --host 127.0.0.1:$port \
        --ch-index 0 --ch-set uplink_enabled true --ch-set downlink_enabled true
done

echo "Setup complete! Create BBS configs and start instances."
```

### Start BBS Instances

```bash
source ~/bbs-test/venv/bin/activate
for i in 0 1 2 3 4; do
    cd ~/bbs-test/bbs$i
    nohup advbbs --config ./config.toml > bbs$i.out 2>&1 &
    sleep 2
done
```

### Monitor RAP Activity

```bash
# Watch RAP messages on BBS0
tail -f ~/bbs-test/bbs0/bbs0.out | grep -E "RAP_PING|RAP_PONG|RAP_ROUTES"

# Check route tables
sqlite3 ~/bbs-test/bbs0/data/bbs0.db \
    "SELECT dest_bbs, hop_count FROM rap_routes ORDER BY hop_count"
```

### Send Test Mail

```bash
source ~/bbs-test/venv/bin/activate
python3 << EOF
import meshtastic.tcp_interface
import time

iface = meshtastic.tcp_interface.TCPInterface("127.0.0.1", portNumber=4400)
time.sleep(3)

# Register user0 on BBS0
iface.sendText("!register user0 password0", destinationId="!000000c8", channelIndex=0)
time.sleep(10)

# Send mail to user4@B4
iface.sendText("!send user4@B4 Test message via 4 hops!", destinationId="!000000c8", channelIndex=0)
time.sleep(5)
iface.close()
EOF
```

## Troubleshooting

### Messages Not Being Delivered

1. **Check rate limiting**: Ensure `_send_min_interval` is at least 3.5 seconds
2. **Check MQTT connectivity**: All nodes must see each other via MQTT
3. **Check RAP routes**: `SELECT * FROM rap_routes` should show destination
4. **Check peer config**: Ensure peers are correctly configured in config.toml

### RAP Routes Not Propagating

1. **Check peer health**: Peers must be in "healthy" state
2. **Wait for convergence**: Routes take multiple heartbeat cycles to propagate
3. **Check hop count**: Routes exceeding `mail_max_hops` are dropped

### MAILREQ Sent But No MAILACK

1. **Check destination user exists**: User must be registered on destination BBS
2. **Check route is valid**: Intermediate nodes must have valid routes
3. **Check logs on all hops**: Look for MAILREQ/MAILNAK messages

## Test Date

December 18, 2025
