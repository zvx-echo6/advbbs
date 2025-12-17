# advBBS Concepts

## Inspiration and Ideas
advBBS is a custom built, low overhead, amalgamation of concepts pulled form many already established BBS systems for Meshtastic including, but not limited to:

- https://github.com/MeshEnvy/lodb
- https://github.com/MeshEnvy/lodb
- https://github.com/SpudGunMan/meshing-around
- https://github.com/TheCommsChannel/TC2-BBS-mesh
- https://github.com/kstrauser/frozenbbs


## Priority 1 Goals
- A fully functional BBS in its own right drawing on proven concepts from mesh projects and ham radio
- It's own linking network
- Capable of sending to and receiving from:
   - meshing-around
   - TC2-BBS-mesh
   - frozenbbs
- Fully enabled encryption at rest for all stored messages
- A fully encrypted registration system for users to login with and associate nodeIDs with and send and retrieve messages.
-Understand that this whole thing should be CLI

## Priority 2 Goals
- Password reset
- A callable user config
- A callable menu
- Auto management of the messages and user DB
- A way to administrate the system
- A customizable auto announcement that can alert the mesh to the BBS system
- A ? command with small readmes to help users learn to use the system

## Moonshots
- Ability to attach BBS nodes to userIDs.
   - Registered User A sends a BBS message to nodeID abc123
   - Meshtastic user registers as User B from nodeID zyx098
   
        - System Assigns nodeID zyx098 to User B because the registration happened FROM the node itself.
   - User B calls for their BBS Mail and receives 0 new messages
   - User B then logs in from nodeID abc123
   - User B then calls for BBS Mail and receives 0 new messages
   - User B requests nodeID abc123 to be assigned to them

        - System assigns nodeID abc123 to User B in addition to zyx098 (this allows User B to pull messages sent to nodeID abc123, nodeID zyx098, and User B account)
- User B then checks for BBS mail and receives the messages sent by User A

In this way, BBS Mail can be sent to A) a userID or B) a nodeID but never a long name or a short name. Additionally, a user can only register a node they are actively operating on. The messages are stored and able to be called dynamically so long as the connection between the userID and the nodeID exists.

- Implement a menu system similar to:
   - https://github.com/chrismyers2000/Meshtasticd-Configuration-Tool

- Implement a user chat system that has zero access to the BBS systems based on:
   - https://github.com/pdxlocations/contact

- Make the whole thing light enough that it an be run from a Raspberry Pi Zero 2 W without impacting the load and making the Z2W run under duress.

- A command to erase all messages sent to your userID or nodes associated to your userID. A sort of self destruct.

- A system that will search for the nodeIDs associated userID or nodeID when they have received mail and send them a DM letting them know. If no node is found or ACK received from a DM, the system will send the BBS mail on to the next advBBS node in the mesh.

### Inter-BBS Message Forwarding Protocol
BBS nodes communicate via DM to coordinate message delivery:
- Each BBS maintains a log of message origins and delivery confirmations
- When forwarding, the sending BBS waits for both a mesh ACK and a DM confirmation reply
- Retries are built in until confirmation is received
- BBS nodes track "already delivered" status to prevent duplicate sends
- Protocol: Send → Wait for ACK → Wait for DM confirm → Mark delivered (or retry)

### Encryption Key Derivation
User encryption keys are derived from their password using a key derivation function (e.g., Argon2, scrypt). Stronger passwords = stronger encryption keys. This keeps the system simple while maintaining security - users only need to remember their password.

## Final Notes
Above all else, the health of the mesh is paramount. We do not spam and flood the mesh.

###Notes for reference:

Meshtastic documentation (including API information) can be found here: https://meshtastic.org/docs/getting-started/

Include DEVA and Delta7 for assistance in their specialization.
Engage SERA to save and document every step.
Utilize UIX to assist with UI and UX development. This is CLI so here will be nuances.
Use MAVEN to plan and work through issues.

Remember, this should run well on a RPi Zero 2 W and should not put it under load.
