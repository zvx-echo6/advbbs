# advBBS Explained Simply

## What is advBBS?

Think of advBBS like a **community bulletin board at a coffee shop**, but for radios.

People can:
- **Leave messages** for specific people (like putting a note in someones mailbox)
- **Post on boards** for everyone to see (like pinning a flyer to the board)
- **Read what others posted**

The difference? This works over **Meshtastic radios** - no internet, no cell towers, no monthly bills.

## How Does It Work?

### The Basics

1. You have a **small radio** (Meshtastic device) in your pocket or backpack
2. Somewhere nearby, someone is running **advBBS** on their radio
3. You send a text message to that radio, like `!help`
4. The BBS responds with a menu of things you can do

Its like texting, but the messages hop between radios instead of going through cell towers.

### Sending Mail

Want to send a message to your friend Alice?

```
!send alice Hey, meet at the park at 3pm?
```

The BBS stores the message. When Alice checks her mail with `!mail`, shell see your note.

### Posting to Boards

Want to tell everyone about a community event?

```
!post events Potluck dinner Saturday 6pm at the community center!
```

Anyone who runs `!read events` will see your post.

## What About Other Towns?

Heres where it gets cool.

Lets say:
- Your town has **BBS-North**
- The next town has **BBS-South**  
- Theres a BBS in between called **BBS-Middle**

Even though your radio cant reach BBS-South directly, advBBS figures out the path:

```
Your Radio -> BBS-North -> BBS-Middle -> BBS-South
```

This is called **federation**. The BBS systems talk to each other and pass messages along, like a chain of people passing a note across a room.

## The "RAP" Thing

RAP stands for **Route Announcement Protocol**. 

Think of it like this: each BBS occasionally shouts "Hey, I can reach these other BBS systems!" 

Other BBS nodes listen and build a mental map:
- "Oh, BBS-North can reach BBS-Middle in 1 hop"
- "And BBS-Middle can reach BBS-South in 1 hop"
- "So I can reach BBS-South in 2 hops through them!"

This happens automatically. You dont need to configure routes - the system figures it out.

## Why Would I Use This?

- **Camping/Hiking**: Stay in touch when theres no cell service
- **Emergencies**: When the power and internet are down, radios still work
- **Community**: Build a local communication network that doesnt depend on big companies
- **Privacy**: Messages stay local, not stored on some companys servers
- **Fun**: Its like ham radio but with a modern interface

## Real Example

**Setup**: 5 BBS nodes in a line, each only talks to its neighbors

```
BBS0 <-> BBS1 <-> BBS2 <-> BBS3 <-> BBS4
```

**What happens**:

1. User on BBS0 sends: `!send user4@B4 Hello from the other side!`
2. BBS0 thinks: "B4? I dont know B4 directly, but BBS1 might..."
3. Message hops: BBS0 -> BBS1 -> BBS2 -> BBS3 -> BBS4
4. BBS4 stores the mail for user4
5. Confirmation hops all the way back to BBS0: "Delivered!"

The user on BBS0 just typed one command. All the routing happened automatically.

## Common Questions

**Q: Do I need internet?**  
A: No. Everything works over radio.

**Q: How far can messages go?**  
A: Each radio hop can be several miles. Chain enough together and you can cover huge distances.

**Q: Is it private?**  
A: Messages are stored only on the BBS systems involved. No cloud, no company servers. But radio transmissions can be received by anyone in range, so dont send secrets unless youre using encryption.

**Q: Can I run my own BBS?**  
A: Yes! You need a Meshtastic radio and a computer (even a Raspberry Pi works). See the [Quick Start Guide](quickstart.md).

**Q: What if a BBS in the middle goes offline?**  
A: The system notices within a few hours and stops trying to route through it. If theres another path, itll find it.

## Summary

advBBS turns a mesh of radios into a communication network with:

- **Mailboxes** for private messages
- **Bulletin boards** for public posts
- **Automatic routing** so messages find their way
- **No internet required**

Its old-school BBS vibes with modern mesh networking.
