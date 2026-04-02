#!/bin/bash
# ============================================================
#  Factorio <-> Claude Code Chat Bridge - Setup
# ============================================================
# Installs the mod, enables it, and configures RCON in config.ini.
# Run once, then use start.sh to launch the bridge.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FACTORIO_DIR="$HOME/Library/Application Support/factorio"
MOD_DIR="$FACTORIO_DIR/mods"
CONFIG="$FACTORIO_DIR/config/config.ini"
MOD_LIST="$MOD_DIR/mod-list.json"

RCON_PORT="27015"
RCON_PASSWORD="claude-chat-bridge"

echo "=========================================="
echo "  Factorio Claude Chat Bridge - Setup"
echo "=========================================="
echo

# ----------------------------------------------------------
# 1. Install the mod
# ----------------------------------------------------------
echo "[1/3] Installing claude-chat mod ..."

if [ ! -d "$MOD_DIR" ]; then
    echo "  ERROR: Mods directory not found at $MOD_DIR"
    exit 1
fi

# Remove old version if present
rm -rf "$MOD_DIR/claude-chat"
cp -r "$SCRIPT_DIR/mod/claude-chat" "$MOD_DIR/claude-chat"
echo "  -> Installed to $MOD_DIR/claude-chat"

# ----------------------------------------------------------
# 2. Enable the mod in mod-list.json
# ----------------------------------------------------------
echo "[2/3] Enabling mod in mod-list.json ..."

if [ -f "$MOD_LIST" ]; then
    if grep -q '"claude-chat"' "$MOD_LIST"; then
        echo "  -> Already present in mod-list.json"
    else
        python3 -c "
import json, sys
with open('$MOD_LIST') as f:
    data = json.load(f)
data['mods'].append({'name': 'claude-chat', 'enabled': True})
with open('$MOD_LIST', 'w') as f:
    json.dump(data, f, indent=2)
"
        echo "  -> Added claude-chat to mod-list.json"
    fi
else
    echo "  ERROR: mod-list.json not found"
    exit 1
fi

# ----------------------------------------------------------
# 3. Configure RCON in config.ini
# ----------------------------------------------------------
echo "[3/3] Configuring RCON in config.ini ..."

if [ ! -f "$CONFIG" ]; then
    echo "  ERROR: config.ini not found at $CONFIG"
    exit 1
fi

# Back up config (only if no backup exists yet)
if [ ! -f "$CONFIG.pre-claude-backup" ]; then
    cp "$CONFIG" "$CONFIG.pre-claude-backup"
    echo "  -> Original config backed up to config.ini.pre-claude-backup"
fi

# Replace the commented-out RCON lines with active ones
sed -i '' "s|^; local-rcon-socket=.*|local-rcon-socket=127.0.0.1:${RCON_PORT}|" "$CONFIG"
sed -i '' "s|^; local-rcon-password=.*|local-rcon-password=${RCON_PASSWORD}|" "$CONFIG"

# Also handle the case where they were already uncommented
sed -i '' "s|^local-rcon-socket=.*|local-rcon-socket=127.0.0.1:${RCON_PORT}|" "$CONFIG"
sed -i '' "s|^local-rcon-password=.*|local-rcon-password=${RCON_PASSWORD}|" "$CONFIG"

echo "  -> RCON configured: 127.0.0.1:${RCON_PORT}"

echo
echo "=========================================="
echo "  Setup complete!"
echo "=========================================="
echo
echo "Next steps:"
echo
echo "  1. Start (or restart) Factorio"
echo "  2. From the main menu, go to:"
echo "       Multiplayer -> Host saved game"
echo "     Select your save file and start hosting."
echo "     (You play alone - this just enables RCON.)"
echo
echo "  3. In another terminal, run the bridge:"
echo "       python3 $SCRIPT_DIR/bridge.py"
echo
echo "  4. In Factorio chat, type:"
echo "       !hello"
echo "     or:"
echo "       /claude what should I research next?"
echo
echo "  Enjoy chatting with Claude in Factorio!"
echo
