#!/bin/bash
# ============================================================
#  Factorio <-> Claude Code Chat Bridge - Start
# ============================================================
# Launches the bridge script. Factorio must already be running
# and hosting a multiplayer game (for RCON).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Starting Factorio <-> Claude Code bridge ..."
echo "(Make sure Factorio is hosting a game via Multiplayer -> Host saved game)"
echo

exec python3 "$SCRIPT_DIR/bridge.py"
