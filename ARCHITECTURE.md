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

**Talking to Claude:** When you send a message, the bridge calls the `claude` CLI in print mode (`-p`). The first message uses `--session-id` with a deterministic UUID to start a session. Every subsequent message uses `-r SESSION_ID` to resume that same session, so Claude has the full conversation history in its context. The factory snapshot goes in `--append-system-prompt` (updated each call with fresh data), and the transcript from your Claude Desktop conversation is included on the first call via `--system-prompt`.

**Auto-pause:** A background thread polls `osascript` every 0.5s to check which app is frontmost. When Factorio loses focus, the bridge sends `/sc game.tick_paused = true` via RCON. When it regains focus, unpauses. Also unpauses on bridge exit so your game never gets stuck frozen.

**Check-ins:** Every 5 minutes, the bridge diffs the current cached factory snapshot against the one from 5 minutes ago (production deltas, new items, research changes), sends the diff to Claude, and posts a brief observation in chat. Skipped if the game is paused.

**Blueprints:** When you ask Claude for a blueprint, it outputs a Factorio blueprint string (the `0` + base64(zlib(json)) format). The bridge detects these via regex, validates by decoding, saves to `~/.claude-factorio/blueprints/latest.txt`, and replaces the raw string in the chat response with import instructions.

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
| State poller | Every 30s | Full 17-query factory snapshot + biter threat scan | Light (scans entities) |
| Focus watcher | Every 0.5s | Checks if Factorio is frontmost app | None (asks macOS) |
| Check-in | Every 5 min | Has Claude comment on changes | None (uses cached data) |
| Play-by-play | Every 10 min | Sports-commentator narration of last 10 min | None (uses cached data, replaces check-in) |

## Special Commands

| Command | What it does |
|---------|-------------|
| `/ss` or `/ss <question>` | Takes a screenshot, sends it to Claude for visual analysis |
| `goal: <description>` | Adds a goal (e.g. "goal: launch a rocket"). Claude tracks progress in check-ins. |
| `goal: done` | Clears all goals. `goal: done <name>` removes a specific one. |
| `rename my factory` | Claude assigns fun, unhinged names to your trains, areas, and infrastructure |

## Features

**Screenshot analysis:** `/ss` triggers `game.take_screenshot` via RCON, saves to `script-output/`, then passes the image path to Claude via the persistent session. Claude uses its `Read` tool to see the image. You can add a question: `/ss what's wrong with my smelting?`

**Goal tracking:** Goals persist in `~/.claude-factorio/goals.json`. Claude sees them in every system prompt and references progress in check-ins and narrations.

**Ratio calculator:** Claude has assembler/furnace speed formulas and your force's productivity bonuses in its system prompt. Ask "how many assemblers for 60 blue science/min?" and it calculates with your actual game modifiers.

**Biter threat radar:** Every 30s, scans for enemies within 200/400/600 tiles of the player and the 5 nearest biter nests. Alerts with compass direction: "12 enemies approaching from the northeast, 180 tiles out!"

**Factory naming:** Claude names your trains, production areas, and infrastructure with fun, unhinged names. Names persist across sessions and Claude uses them in all conversations.

**Play-by-play narration:** Every 10 minutes, Claude narrates what happened in a dramatic sports commentary style, referencing specific numbers and factory names.

**Voice input (SuperWhisper):** No code needed. Open Factorio chat, press SuperWhisper hotkey, speak. It auto-pastes the transcription. Run Factorio in borderless windowed mode.

## Files

| File | Where | What |
|------|-------|------|
| `mod/claude-chat/control.lua` | This project | The Factorio mod |
| `bridge.py` | This project | The bridge script |
| `~/.claude-factorio/history.jsonl` | Home dir | All conversation history (persists forever) |
| `~/.claude-factorio/transcript.txt` | Home dir | Claude Desktop conversation context |
| `~/.claude-factorio/blueprints/` | Home dir | Saved blueprint strings |
| `~/.claude-factorio/goals.json` | Home dir | Player's tracked goals |
| `~/.claude-factorio/factory_names.json` | Home dir | Fun names for trains/areas |
| `~/Library/Application Support/factorio/script-output/claude_chat.jsonl` | Factorio dir | Mod's message output (runtime only) |
| `~/Library/Application Support/factorio/script-output/claude_screenshot.png` | Factorio dir | Latest screenshot for analysis |
