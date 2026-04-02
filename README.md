# Factorio Claude Chat Bridge

Chat with Claude AI directly through Factorio's in-game chat. Type a message in Factorio, get a response from Claude in your chat window.

## How It Works

```
You type in Factorio  -->  Mod captures message  -->  Bridge reads file
                                                          |
You see response      <--  RCON sends to game    <--  Claude CLI responds
```

1. A Factorio mod captures your chat messages and writes them to a file
2. A Python bridge script watches that file for new messages
3. The bridge sends each message to the `claude` CLI (Claude Code)
4. Claude's response is sent back to Factorio via RCON and appears in chat

## Quick Start

### 1. Run setup (one time)

```bash
cd ~/Documents/AI_Projects/factorio
./setup.sh
```

This installs the mod, enables it, and configures RCON in your Factorio config.

### 2. Start Factorio and host your save

1. Start (or restart) Factorio
2. From the main menu: **Multiplayer -> Host saved game**
3. Select your save file and click Host

> Hosting as multiplayer enables RCON. You play alone - it's the same experience as single-player.

### 3. Start the bridge

In a terminal:

```bash
./start.sh
```

Or directly:

```bash
python3 bridge.py
```

The bridge will wait for Factorio's RCON to become available, then connect automatically.

### 4. Chat!

In Factorio, press Enter to open chat, then type:

```
!hello
!what should I research next?
!how do I set up a train network?
```

Or use the command form:

```
/claude what's the best way to produce green circuits?
```

Claude's response appears in your chat as `[Claude] ...`

## Commands

| In Factorio | What it does |
|---|---|
| `!<message>` | Send a message to Claude |
| `/claude <message>` | Same thing, via command |
| `/claude-status` | Check if the mod is loaded |
| `/claude-clear` | Clear the message history file |

## Files

```
factorio/
  bridge.py           # Python bridge (zero external dependencies)
  setup.sh            # One-time setup script
  start.sh            # Launch the bridge
  mod/claude-chat/    # Factorio mod source
    info.json
    control.lua
```

## Requirements

- Factorio 2.0+
- Python 3.10+
- Claude Code CLI (`claude`) installed and authenticated
- macOS (paths are macOS-specific)

## Troubleshooting

**Bridge says "Waiting for RCON..."**
- Make sure you loaded your save via Multiplayer -> Host saved game (not single-player)
- Check that setup.sh configured RCON in config.ini

**No response appears in game**
- Check the bridge terminal for errors
- Make sure `claude -p "test"` works in your terminal
- Try `/claude-status` in Factorio to verify the mod is loaded

**"Command claude-respond not found"**
- The mod isn't loaded. Restart Factorio after running setup.sh

**Want to undo RCON config**
- Your original config was backed up to `config.ini.pre-claude-backup`
