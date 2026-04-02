# Factorio Claude Chat Bridge

## What this is
A bridge that lets a player chat with Claude AI through Factorio's in-game chat. The player types in Factorio, a Python bridge reads the message via a mod's file output, sends it to `claude` CLI, and returns the response via RCON.

## Architecture

```
Player types in Factorio chat
  -> Mod (control.lua) writes to script-output/claude_chat.jsonl
  -> bridge.py polls the JSONL file
  -> bridge.py queries game state via 15 RCON Lua commands
  -> bridge.py calls `claude -p` CLI with game state as system prompt
  -> Response sent back via RCON /claude-respond command
  -> Appears as [Claude] message in Factorio chat
```

## Key files
- `bridge.py` — Main bridge script. RCON client, game state queries, Claude CLI integration, auto-pause, background polling, check-ins.
- `mod/claude-chat/control.lua` — Factorio mod. Captures chat, provides `/claude` command, `/claude-respond` for bridge replies, `/claude-gamestate` for state queries.
- `mod/claude-chat/info.json` — Mod metadata (Factorio 2.0).
- `setup.sh` — One-time setup: installs mod, enables it, configures RCON in config.ini.
- `start.sh` — Launches bridge.py.

## Persistence
- `~/.claude-factorio/history.jsonl` — All conversation history across sessions
- `~/.claude-factorio/transcript.txt` — Background context from Claude Desktop conversation

## Important technical details

### RCON
- Factorio RCON only works when hosting a multiplayer game (not single-player)
- Config: `local-rcon-socket=127.0.0.1:27015`, `local-rcon-password=claude-chat-bridge`
- Factorio sends ONE auth response packet (not two like Valve spec says)
- First `/sc` command triggers an achievement warning; bridge sends it twice to dismiss
- RCON command limit: ~1446 bytes. Responses up to 4096 bytes.

### Factorio 2.0 API
- Use `helpers.write_file()` and `helpers.table_to_json()` (NOT `game.write_file()`)
- Production stats are per-surface: `force.get_item_production_statistics('nauvis')`
- Evolution is per-surface: `force.get_evolution_factor(surface)`
- Trains: `game.train_manager.get_trains{force=f}` (not `surface.get_trains`)
- Wall health: `wall.get_health_ratio() < 1` (not `prototype.max_health`)
- Item names: `stone-wall` (not `wall`), no `rocket-control-unit` in base game

### Claude CLI session
- Uses `--session-id` with a deterministic UUID for conversation persistence
- First message: `claude -p --session-id UUID --system-prompt ...`
- Subsequent: `claude -p -r UUID --append-system-prompt ...`
- Game state goes in system prompt (updates each call), conversation in CLI memory

### Game state queries (15 total)
Queries are split to stay under 1446-byte command limit:
1. Core (research, evolution breakdown, time, speed)
2. Item production totals
3. Item flow rates (per minute, 10min avg)
4. Fluid production + flows
5. Kill stats + pollution
6. Player info + entity counts
7. Electric network
8. Machine problems with coordinates (20-tile grid clustering)
9. Defense alerts with coordinates
10. Train status with positions and routes
11. Player inventory
12. Logistic networks
13. Pollution sources by entity type
14. Resource patches remaining
15. Available technologies to research

Background thread refreshes every 30s; messages get cached state instantly.

## Development guidelines
- Always test RCON queries against live game before committing
- `python3 -c "import ast; ast.parse(open('bridge.py').read())"` to check syntax
- Commit after each feature
- When adding new RCON queries, check item/entity names exist in Factorio 2.0
- Mod changes require Factorio restart; bridge.py changes only need bridge restart
- After changing mod, run: `cp -r mod/claude-chat "$HOME/Library/Application Support/factorio/mods/claude-chat"`
