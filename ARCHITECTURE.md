# How the Factorio Claude Chat Bridge Works

## The Big Picture

You type in Factorio's chat. Claude reads your message, looks at your entire factory's state, and responds in chat. It also watches your factory in the background and alerts you if something goes wrong.

There are three pieces: a **mod** inside Factorio, a **bridge** script running in your terminal, and the **Claude CLI**.

## The Three Pieces

### 1. The Mod (lives inside Factorio)

A small Lua script that runs inside Factorio. It does two things:

- **Captures your chat messages** and writes them to a file on disk. Every time you type in chat, a line gets appended to a file.
- **Displays Claude's responses** in chat when told to by the bridge. The bridge sends a command over the network, and the mod prints it as a `[Claude]` message.

The mod can't talk to the internet or to Claude directly. It can only read/write files and respond to network commands.

### 2. The Bridge (runs in your terminal)

This is the brain. A Python script (`bridge.py`) that connects everything together. It does several things at once:

**Watching for your messages:** Checks the mod's output file twice per second. When a new line appears (you typed something), it picks it up.

**Knowing your factory:** Every 30 seconds, the bridge sends 15 small Lua commands to Factorio over the network and gets back a complete snapshot of your factory:

- What you're researching and how far along it is
- Every item you're producing and how fast (iron plates per minute, etc.)
- All your fluids (oil, petroleum, water, etc.)
- Your power grid (generation vs consumption, broken down by building type)
- Every building you have and how many (167 mining drills, 105 turrets, etc.)
- Which machines are broken (starved for ingredients, output backed up, no power)
- Where the problems are (coordinates, clustered by area)
- Your turrets and walls (ammo levels, damage)
- Your trains (where they are, what route, are they stuck)
- What's in your inventory
- Pollution sources and amounts
- Resources remaining on the map
- What technologies you can research next

This snapshot is cached so when you send a message, there's no delay waiting for factory data.

**Alert monitoring:** Every 5 seconds, the bridge runs 3 very lightweight checks:

- Did any walls or turrets get destroyed? (just counts, no scanning)
- Are any trains stuck?
- Is power generation about to run out?

If something critical is detected, it immediately sends an `[ALERT]` message to your chat. No Claude involved, no delay, no tokens spent.

**Talking to Claude:** When you send a message, the bridge calls the Claude CLI with:
- Your message
- A system prompt containing your full factory snapshot
- Background context (the transcript from your Claude Desktop conversation)

Claude sees everything about your factory and responds with context-aware advice. The bridge sends the response back to Factorio over the network, and it appears in your chat.

**Remembering conversations:** The bridge uses a persistent Claude session (same session ID across restarts) so Claude remembers what you've talked about. It also saves all messages to disk as a backup.

**Auto-pause:** When you cmd-tab away from Factorio, the bridge pauses the game. When you tab back, it resumes. This is checked every half second by asking macOS which app is in front.

**Check-ins:** Every 5 minutes, the bridge compares the current factory snapshot to the one from 5 minutes ago, summarizes what changed, and has Claude post a brief observation. Skipped if the game is paused.

**Blueprints:** When you ask Claude for a blueprint, it generates a Factorio blueprint string. The bridge detects it, validates it, saves it to a file, and tells you where to find it so you can import it.

### 3. The Network Connection (RCON)

Factorio has a built-in remote control protocol called RCON (borrowed from game server hosting). It's a simple TCP connection on port 27015 with a password.

The bridge uses RCON for two things:
- **Sending commands to Factorio** (display a message in chat, run a Lua snippet to get factory data)
- **Running Lua code** inside Factorio to query production stats, machine status, etc.

RCON only works when Factorio is hosting a multiplayer game, which is why you load your save through "Multiplayer > Host saved game" instead of single-player. You play alone — it's the same game, just with the network port open.

## Data Flow

```
You type "how's my iron?"
        |
        v
Mod writes {"player":"doga","message":"how's my iron?"} to file
        |
        v
Bridge picks up the new line (checks every 0.5s)
        |
        v
Bridge grabs cached factory snapshot (refreshed every 30s)
        |
        v
Bridge calls: claude -p -r SESSION_ID --append-system-prompt "factory data..." "doga: how's my iron?"
        |
        v
Claude sees your message + full factory state, responds
        |
        v
Bridge sends response to Factorio via RCON
        |
        v
[Claude] You're producing 1,040 iron plates/min but consuming 1,142/min — you're in deficit.
         15 steel furnaces near (-20,-80) have no recipe set, put them to work on iron!
```

## Background Loops (all running simultaneously)

| Loop | Frequency | What it does | Game impact |
|------|-----------|-------------|-------------|
| Message watcher | Every 0.5s | Checks file for new chat messages | None (reads a file) |
| Alert poller | Every 5s | Counts walls/turrets, checks trains and power | Negligible (O(1) lookups) |
| State poller | Every 30s | Full 15-query factory snapshot | Light (scans entities) |
| Focus watcher | Every 0.5s | Checks if Factorio is frontmost app | None (asks macOS) |
| Check-in | Every 5 min | Has Claude comment on changes | None (uses cached data) |

## Files

| File | Where | What |
|------|-------|------|
| `mod/claude-chat/control.lua` | This project | The Factorio mod |
| `bridge.py` | This project | The bridge script |
| `~/.claude-factorio/history.jsonl` | Home dir | All conversation history (persists forever) |
| `~/.claude-factorio/transcript.txt` | Home dir | Claude Desktop conversation context |
| `~/.claude-factorio/blueprints/` | Home dir | Saved blueprint strings |
| `~/Library/Application Support/factorio/script-output/claude_chat.jsonl` | Factorio dir | Mod's message output (runtime only) |
