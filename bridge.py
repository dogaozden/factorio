#!/usr/bin/env python3
"""
Factorio <-> Claude Code Chat Bridge

Monitors the Factorio mod's output file for player messages,
sends them to Claude Code CLI, and returns responses via RCON.

Features:
  - Persistent conversation history across sessions
  - Live game state context (production, research, evolution)
  - Optional transcript loading for background context

Zero external dependencies - uses only Python stdlib + the `claude` CLI.
"""

import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

# ============================================================
# Configuration
# ============================================================

RCON_HOST = "127.0.0.1"
RCON_PORT = 27015
RCON_PASSWORD = "claude-chat-bridge"

FACTORIO_DIR = Path.home() / "Library" / "Application Support" / "factorio"
SCRIPT_OUTPUT = FACTORIO_DIR / "script-output"
INPUT_FILE = SCRIPT_OUTPUT / "claude_chat.jsonl"

PERSIST_DIR = Path.home() / ".claude-factorio"
HISTORY_FILE = PERSIST_DIR / "history.jsonl"
TRANSCRIPT_FILE = PERSIST_DIR / "transcript.txt"

CLAUDE_BIN = "/opt/homebrew/bin/claude"

POLL_INTERVAL = 0.5          # seconds between file checks
MAX_RESPONSE_LENGTH = 500    # chars per chat message chunk
PROMPT_HISTORY = 20          # conversation turns to include in prompt
CLAUDE_TIMEOUT = 120         # seconds to wait for claude response
RCON_CONNECT_RETRIES = 60    # how many times to retry RCON connection
RCON_RETRY_DELAY = 2         # seconds between retries
MAX_TRANSCRIPT_CHARS = 4000  # max transcript chars to include in prompt


# ============================================================
# RCON Client (Source RCON protocol)
# ============================================================

class RCONError(Exception):
    pass


class RCONClient:
    """Minimal Valve Source RCON protocol client."""

    SERVERDATA_AUTH = 3
    SERVERDATA_EXECCOMMAND = 2

    def __init__(self, host: str, port: int, password: str):
        self.host = host
        self.port = port
        self.password = password
        self.sock = None
        self.request_id = 0

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(10)
        self.sock.connect((self.host, self.port))
        self._authenticate()

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def send_command(self, command: str) -> str:
        self._send_packet(self.SERVERDATA_EXECCOMMAND, command)
        _, _, body = self._receive_packet()
        return body

    def _authenticate(self):
        self._send_packet(self.SERVERDATA_AUTH, self.password)
        req_id, _, _ = self._receive_packet()
        if req_id == -1:
            raise RCONError("RCON authentication failed - check password")

    def _send_packet(self, packet_type: int, body: str) -> int:
        self.request_id += 1
        body_encoded = body.encode("utf-8") + b"\x00"
        padding = b"\x00"
        size = 4 + 4 + len(body_encoded) + len(padding)
        packet = struct.pack("<iii", size, self.request_id, packet_type)
        packet += body_encoded + padding
        self.sock.sendall(packet)
        return self.request_id

    def _receive_packet(self):
        raw_size = self._recv_exact(4)
        size = struct.unpack("<i", raw_size)[0]
        data = self._recv_exact(size)
        req_id = struct.unpack("<i", data[0:4])[0]
        ptype = struct.unpack("<i", data[4:8])[0]
        body = data[8:-2].decode("utf-8", errors="replace")
        return req_id, ptype, body

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RCONError("RCON connection closed unexpectedly")
            buf += chunk
        return buf


# ============================================================
# Message splitting for Factorio chat
# ============================================================

def split_message(text: str, limit: int = MAX_RESPONSE_LENGTH) -> list[str]:
    """Split text into chunks that fit in Factorio's chat window."""
    text = text.replace("\n", " ").strip()
    if len(text) <= limit:
        return [text]

    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break

        cut = limit
        for sep in [". ", "! ", "? ", "; ", ", ", " "]:
            idx = text.rfind(sep, 0, limit)
            if idx > limit // 3:
                cut = idx + len(sep)
                break

        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()

    return chunks


# ============================================================
# Persistence
# ============================================================

def init_persistence():
    """Create persistence directory if needed."""
    PERSIST_DIR.mkdir(parents=True, exist_ok=True)


def load_history() -> list[dict]:
    """Load full conversation history from disk."""
    history = []
    if not HISTORY_FILE.exists():
        return history
    for line in HISTORY_FILE.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            history.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return history


def append_history(entry: dict):
    """Append a single exchange to the persistent history file."""
    with open(HISTORY_FILE, "a") as fh:
        fh.write(json.dumps(entry) + "\n")


def load_transcript() -> str:
    """Load the Claude Desktop transcript if it exists."""
    if not TRANSCRIPT_FILE.exists():
        return ""
    text = TRANSCRIPT_FILE.read_text().strip()
    if not text:
        return ""
    # Truncate from the END (keep most recent context)
    if len(text) > MAX_TRANSCRIPT_CHARS:
        text = "...(earlier conversation truncated)...\n" + text[-MAX_TRANSCRIPT_CHARS:]
    return text


# ============================================================
# Bridge
# ============================================================

CHECKIN_INTERVAL = 300        # seconds between auto check-ins


class Bridge:
    def __init__(self):
        self.rcon: RCONClient | None = None
        self.file_pos = 0
        self.history: list[dict] = []
        self.transcript: str = ""
        self.last_state: dict | None = None
        self.last_checkin: float = 0

    def init(self):
        """Load persistent state."""
        init_persistence()
        self.history = load_history()
        self.transcript = load_transcript()

        n = len(self.history)
        print(f"  History     : {n} messages loaded from prior sessions")
        if self.transcript:
            print(f"  Transcript  : {len(self.transcript)} chars loaded")
        else:
            print(f"  Transcript  : none (place transcript in {TRANSCRIPT_FILE})")

    # -- RCON helpers -----------------------------------------------

    def connect_rcon(self):
        print(f"Connecting to Factorio RCON at {RCON_HOST}:{RCON_PORT} ...")
        self.rcon = RCONClient(RCON_HOST, RCON_PORT, RCON_PASSWORD)
        self.rcon.connect()
        print("Connected!")
        # Dismiss Factorio's "achievements will be disabled" gate
        # by sending a throwaway /sc command twice
        self.rcon.send_command("/sc rcon.print('ok')")
        self.rcon.send_command("/sc rcon.print('ok')")
        print("Lua console unlocked.")

    def reconnect_rcon(self):
        if self.rcon:
            self.rcon.close()
        try:
            self.connect_rcon()
        except Exception as exc:
            print(f"  Reconnect failed: {exc}")
            self.rcon = None

    def send_to_game(self, text: str):
        """Send a message that appears in Factorio chat as [Claude]."""
        if not self.rcon:
            print(f"  [no RCON] {text}")
            return

        for chunk in split_message(text):
            try:
                self.rcon.send_command(f"/claude-respond {chunk}")
            except Exception as exc:
                print(f"  RCON send error: {exc}")
                self.reconnect_rcon()
                return

    # -- Game state --------------------------------------------------
    # Multiple RCON queries to fit within the ~1446 byte command limit.

    # Query 1: Core stats (research, evolution, time, game speed)
    Q_CORE = (
        "/sc local f=game.forces.player; local su=game.surfaces[1];"
        " local r='none'; local p=0; local rq={}"
        " if f.current_research then r=f.current_research.name;"
        "   p=math.floor(f.research_progress*100) end;"
        " for _,v in pairs(f.research_queue or {}) do"
        "   rq[#rq+1]=v end;"
        " local t=0; for _,v in pairs(f.technologies) do"
        "   if v.researched then t=t+1 end end;"
        " local e=game.forces.enemy;"
        " rcon.print(helpers.table_to_json({"
        "   game_time=string.format('%dh %dm',"
        "     math.floor(game.ticks_played/216000),"
        "     math.floor(game.ticks_played%216000/3600)),"
        "   tick=game.ticks_played,"
        "   speed=game.speed,"
        "   current_research=r,"
        "   research_progress_pct=p,"
        "   research_queue=rq,"
        "   technologies_researched=t,"
        "   rockets_launched=f.rockets_launched,"
        "   evo_total=math.floor(e.get_evolution_factor(su)*1000)/1000,"
        "   evo_pollution=math.floor(e.get_evolution_factor_by_pollution(su)*1000)/1000,"
        "   evo_time=math.floor(e.get_evolution_factor_by_time(su)*1000)/1000,"
        "   evo_kills=math.floor(e.get_evolution_factor_by_killing_spawners(su)*1000)/1000,"
        "   daytime=math.floor(su.daytime*100)/100,"
        "   darkness=math.floor(su.darkness*100)/100,"
        "   peaceful=su.peaceful_mode"
        " }))"
    )

    # Query 2: Item production (lifetime totals for key items)
    Q_ITEMS = (
        "/sc local s=game.forces.player.get_item_production_statistics('nauvis');"
        " local d={};"
        " local items={"
        "   'iron-plate','copper-plate','steel-plate',"
        "   'electronic-circuit','advanced-circuit','processing-unit',"
        "   'iron-ore','copper-ore','coal','stone','uranium-ore',"
        "   'plastic-bar','sulfur','battery','explosives',"
        "   'engine-unit','electric-engine-unit','flying-robot-frame',"
        "   'low-density-structure','rocket-fuel',"
        "   'automation-science-pack','logistic-science-pack',"
        "   'military-science-pack','chemical-science-pack',"
        "   'production-science-pack','utility-science-pack','space-science-pack',"
        "   'firearm-magazine','piercing-rounds-magazine','uranium-rounds-magazine',"
        "   'stone-wall','gun-turret','laser-turret',"
        "   'transport-belt','fast-transport-belt','express-transport-belt',"
        "   'inserter','fast-inserter','bulk-inserter',"
        "   'pipe','rail','train-stop','locomotive','cargo-wagon',"
        "   'solar-panel','accumulator','medium-electric-pole','big-electric-pole'"
        " };"
        " for _,n in pairs(items) do"
        "   local i=s.get_input_count(n); local o=s.get_output_count(n);"
        "   if i>0 or o>0 then d[n]={produced=i,consumed=o} end"
        " end;"
        " rcon.print(helpers.table_to_json(d))"
    )

    # Query 3: Item production FLOW rates (per minute, last 10 minutes)
    Q_FLOWS = (
        "/sc local s=game.forces.player.get_item_production_statistics('nauvis');"
        " local p=defines.flow_precision_index.ten_minutes; local d={};"
        " local items={"
        "   'iron-plate','copper-plate','steel-plate',"
        "   'electronic-circuit','advanced-circuit','processing-unit',"
        "   'automation-science-pack','logistic-science-pack',"
        "   'military-science-pack','chemical-science-pack',"
        "   'production-science-pack','utility-science-pack',"
        "   'plastic-bar','sulfur','battery',"
        "   'engine-unit','electric-engine-unit',"
        "   'iron-ore','copper-ore','coal'"
        " };"
        " for _,n in pairs(items) do"
        "   local i=s.get_flow_count{name=n,category='input',precision_index=p};"
        "   local o=s.get_flow_count{name=n,category='output',precision_index=p};"
        "   if i>0 or o>0 then d[n]={inp=math.floor(i*10)/10,out=math.floor(o*10)/10} end"
        " end;"
        " rcon.print(helpers.table_to_json(d))"
    )

    # Query 4: Fluid production (totals + flow rates)
    Q_FLUIDS = (
        "/sc local s=game.forces.player.get_fluid_production_statistics('nauvis');"
        " local p=defines.flow_precision_index.ten_minutes; local d={};"
        " local fluids={'water','crude-oil','petroleum-gas','light-oil','heavy-oil',"
        "   'lubricant','sulfuric-acid','steam'};"
        " for _,n in pairs(fluids) do"
        "   local i=s.get_input_count(n); local o=s.get_output_count(n);"
        "   local fi=s.get_flow_count{name=n,category='input',precision_index=p};"
        "   local fo=s.get_flow_count{name=n,category='output',precision_index=p};"
        "   if i>0 or o>0 then d[n]={produced=i,consumed=o,"
        "     flow_in=math.floor(fi*10)/10,flow_out=math.floor(fo*10)/10} end"
        " end;"
        " rcon.print(helpers.table_to_json(d))"
    )

    # Query 5: Kill stats + pollution
    Q_COMBAT = (
        "/sc local f=game.forces.player; local su=game.surfaces[1];"
        " local k=f.get_kill_count_statistics(su);"
        " local d={kills={},pollution=math.floor(su.get_total_pollution())};"
        " local enemies={'small-biter','medium-biter','big-biter','behemoth-biter',"
        "   'small-spitter','medium-spitter','big-spitter','behemoth-spitter',"
        "   'small-worm-turret','medium-worm-turret','big-worm-turret',"
        "   'biter-spawner','spitter-spawner'};"
        " for _,n in pairs(enemies) do"
        "   local c=k.get_input_count(n);"
        "   if c>0 then d.kills[n]=c end"
        " end;"
        " rcon.print(helpers.table_to_json(d))"
    )

    # Query 6: Player info + entity counts
    Q_PLAYER = (
        "/sc local f=game.forces.player; local su=game.surfaces[1]; local d={players={},entities={}};"
        " for _,p in pairs(game.connected_players) do"
        "   d.players[#d.players+1]={name=p.name,"
        "     x=math.floor(p.position.x),y=math.floor(p.position.y)} end;"
        " local ents={'assembling-machine-1','assembling-machine-2','assembling-machine-3',"
        "   'stone-furnace','steel-furnace','electric-furnace',"
        "   'electric-mining-drill','pumpjack','oil-refinery','chemical-plant',"
        "   'lab','gun-turret','laser-turret','radar',"
        "   'roboport','solar-panel','accumulator',"
        "   'boiler','steam-engine','steam-turbine',"
        "   'locomotive','cargo-wagon','train-stop'};"
        " for _,n in pairs(ents) do"
        "   local c=f.get_entity_count(n);"
        "   if c>0 then d.entities[n]=c end"
        " end;"
        " rcon.print(helpers.table_to_json(d))"
    )

    # Query 7: Electric network stats (find a pole and read its network)
    Q_ELECTRIC = (
        "/sc local su=game.surfaces[1]; local f=game.forces.player;"
        " local poles=su.find_entities_filtered{type='electric-pole',force=f,limit=1};"
        " if #poles==0 then rcon.print('{}') return end;"
        " local s=poles[1].electric_network_statistics;"
        " local p=defines.flow_precision_index.one_minute;"
        " local d={production={},consumption={}};"
        " for n,_ in pairs(s.input_counts) do"
        "   local v=s.get_flow_count{name=n,category='input',precision_index=p};"
        "   if v>0 then d.production[n]=math.floor(v*1000)/1000 end end;"
        " for n,_ in pairs(s.output_counts) do"
        "   local v=s.get_flow_count{name=n,category='output',precision_index=p};"
        "   if v>0 then d.consumption[n]=math.floor(v*1000)/1000 end end;"
        " rcon.print(helpers.table_to_json(d))"
    )

    def _rcon_json(self, lua: str) -> dict | None:
        """Run a Lua snippet via RCON and parse the JSON result."""
        try:
            raw = self.rcon.send_command(lua)
            if raw and not raw.startswith("Cannot execute"):
                return json.loads(raw)
        except Exception:
            pass
        return None

    def query_game_state(self) -> dict | None:
        """Query full Factorio game state via multiple RCON commands."""
        if not self.rcon:
            return None
        try:
            state = {}
            core = self._rcon_json(self.Q_CORE)
            if not core:
                return None
            state.update(core)
            state["item_totals"] = self._rcon_json(self.Q_ITEMS) or {}
            state["item_flows"] = self._rcon_json(self.Q_FLOWS) or {}
            state["fluids"] = self._rcon_json(self.Q_FLUIDS) or {}
            state["combat"] = self._rcon_json(self.Q_COMBAT) or {}
            state["world"] = self._rcon_json(self.Q_PLAYER) or {}
            state["electric"] = self._rcon_json(self.Q_ELECTRIC) or {}
            # Keep legacy "production" key for diff_states compatibility
            state["production"] = state["item_totals"]
            return state
        except Exception as exc:
            print(f"  Game state query failed: {exc}")
        return None

    @staticmethod
    def format_game_context(state: dict) -> str:
        """Format full game state into a readable context string."""
        lines = []

        # Core
        lines.append(f"Game time: {state.get('game_time', '?')}")
        res = state.get("current_research", "none")
        prog = state.get("research_progress_pct", 0)
        if res != "none":
            lines.append(f"Researching: {res} ({prog}%)")
            rq = state.get("research_queue", [])
            if len(rq) > 1:
                lines.append(f"Research queue: {', '.join(str(r) for r in rq[1:])}")
        lines.append(f"Technologies researched: {state.get('technologies_researched', '?')}")
        if state.get("rockets_launched", 0) > 0:
            lines.append(f"Rockets launched: {state['rockets_launched']}")

        # Evolution breakdown
        lines.append(
            f"Evolution: {state.get('evo_total', '?')} "
            f"(time: {state.get('evo_time', '?')}, "
            f"pollution: {state.get('evo_pollution', '?')}, "
            f"spawner kills: {state.get('evo_kills', '?')})"
        )

        # Day/night
        dark = state.get("darkness", 0)
        if dark > 0.3:
            lines.append(f"Darkness: {dark} (night time)")

        # Pollution & combat
        combat = state.get("combat", {})
        pol = combat.get("pollution", 0)
        if pol:
            lines.append(f"Total pollution: {pol}")
        kills = combat.get("kills", {})
        if kills:
            kill_str = ", ".join(f"{k}: {v}" for k, v in sorted(kills.items()))
            lines.append(f"Kills: {kill_str}")

        # Item flow rates (per minute)
        flows = state.get("item_flows", {})
        if flows:
            lines.append("Item production rates (per minute, 10min avg):")
            for name, f in sorted(flows.items()):
                inp = f.get("inp", 0)
                out = f.get("out", 0)
                lines.append(f"  {name}: +{inp}/min produced, -{out}/min consumed")

        # Item lifetime totals
        totals = state.get("item_totals", {})
        if totals:
            lines.append("Item lifetime totals:")
            for name, c in sorted(totals.items()):
                p = c.get("produced", 0)
                u = c.get("consumed", 0)
                lines.append(f"  {name}: {p} produced / {u} consumed")

        # Fluids
        fluids = state.get("fluids", {})
        if fluids:
            lines.append("Fluids:")
            for name, f in sorted(fluids.items()):
                p = f.get("produced", 0)
                c = f.get("consumed", 0)
                fi = f.get("flow_in", 0)
                fo = f.get("flow_out", 0)
                lines.append(
                    f"  {name}: {p} produced / {c} consumed "
                    f"(rate: +{fi}/min, -{fo}/min)"
                )

        # Electric network
        elec = state.get("electric", {})
        eprod = elec.get("production", {})
        econs = elec.get("consumption", {})
        if eprod or econs:
            lines.append("Electric network:")
            if econs:
                total_gen = sum(econs.values())
                kw = round(total_gen * 60 / 1000, 1)
                lines.append(f"  Total generation: {kw} kW")
                for name, v in sorted(econs.items()):
                    kw = round(v * 60 / 1000, 1)
                    lines.append(f"    {name}: {kw} kW")
            if eprod:
                total_use = sum(eprod.values())
                kw = round(total_use * 60 / 1000, 1)
                lines.append(f"  Total consumption: {kw} kW")
                for name, v in sorted(eprod.items()):
                    kw = round(v * 60 / 1000, 1)
                    lines.append(f"    {name}: {kw} kW")

        # Entity counts
        world = state.get("world", {})
        entities = world.get("entities", {})
        if entities:
            lines.append("Entity counts:")
            for name, c in sorted(entities.items()):
                lines.append(f"  {name}: {c}")

        # Players
        players = world.get("players", [])
        if players:
            for p in players:
                lines.append(
                    f"Player: {p.get('name','?')} at ({p.get('x','?')}, {p.get('y','?')})"
                )

        return "\n".join(lines)

    # -- Claude CLI --------------------------------------------------

    def ask_claude(self, message: str, player: str) -> str:
        """Send a message to Claude Code CLI and return the response."""
        parts = [
            "You are Claude, chatting with a player inside Factorio through the game's chat.",
            "Keep responses concise (2-3 sentences max) — they appear in a small chat window.",
            "No markdown, no bullet points, no code blocks. Plain text only.",
            "Be helpful, friendly, and knowledgeable about Factorio and anything else.",
            "",
        ]

        # Include transcript context if available
        if self.transcript:
            parts.append(
                "Background context from a prior conversation with this player:"
            )
            parts.append(self.transcript)
            parts.append("")

        # Include live game state
        state = self.query_game_state()
        if state and "error" not in state:
            parts.append("Current game state:")
            parts.append(self.format_game_context(state))
            parts.append("")

        # Include recent conversation history
        recent = self.history[-PROMPT_HISTORY:]
        if recent:
            parts.append("Recent conversation:")
            for h in recent:
                parts.append(f"  {h['role']}: {h['text']}")
            parts.append("")

        parts.append(f"{player}: {message}")
        parts.append("")
        parts.append("Your response (concise, plain text):")

        prompt = "\n".join(parts)

        try:
            result = subprocess.run(
                [CLAUDE_BIN, "-p", prompt],
                capture_output=True,
                text=True,
                timeout=CLAUDE_TIMEOUT,
            )
            response = result.stdout.strip()
            if not response and result.returncode != 0:
                stderr = result.stderr.strip()[:200]
                return f"(Claude error: {stderr or 'empty response'})"
            return response or "(empty response)"

        except subprocess.TimeoutExpired:
            return "Sorry, I took too long thinking! Try a shorter question."
        except FileNotFoundError:
            return f"Error: claude CLI not found at {CLAUDE_BIN}"
        except Exception as exc:
            return f"Error: {str(exc)[:200]}"

    # -- File monitoring ---------------------------------------------

    def poll_messages(self) -> list[dict]:
        """Read new JSONL lines from the mod's output file."""
        if not INPUT_FILE.exists():
            return []

        messages = []
        try:
            with open(INPUT_FILE, "r") as fh:
                fh.seek(self.file_pos)
                new_data = fh.read()
                self.file_pos = fh.tell()

            for line in new_data.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        except OSError as exc:
            print(f"  File read error: {exc}")

        return messages

    # -- Processing --------------------------------------------------

    def handle_message(self, msg: dict):
        player = msg.get("player", "Player")
        text = msg.get("message", "").strip()
        if not text:
            return

        ts = datetime.now(timezone.utc).isoformat()
        print(f"\n>>> [{player}]: {text}")

        # Save player message to persistent history
        player_entry = {"role": player, "text": text, "ts": ts}
        self.history.append(player_entry)
        append_history(player_entry)

        # Get response
        response = self.ask_claude(text, player)

        # Save Claude response to persistent history
        claude_entry = {"role": "Claude", "text": response, "ts": ts}
        self.history.append(claude_entry)
        append_history(claude_entry)

        print(f"<<< [Claude]: {response}")
        self.send_to_game(response)

    # -- Periodic check-in -------------------------------------------

    @staticmethod
    def diff_states(old: dict, new: dict) -> str:
        """Summarize what changed between two game state snapshots."""
        lines = []

        old_time = old.get("game_time", "?")
        new_time = new.get("game_time", "?")
        lines.append(f"Time: {old_time} -> {new_time}")

        old_res = old.get("current_research", "none")
        new_res = new.get("current_research", "none")
        if old_res != new_res:
            lines.append(f"Research changed: {old_res} -> {new_res}")
        elif new_res != "none":
            lines.append(
                f"Research: {new_res} ({new.get('research_progress_pct', 0)}%)"
            )

        old_techs = old.get("technologies_researched", 0)
        new_techs = new.get("technologies_researched", 0)
        if new_techs > old_techs:
            lines.append(f"New techs researched: +{new_techs - old_techs} (total {new_techs})")

        old_evo = old.get("evolution_factor", 0)
        new_evo = new.get("evolution_factor", 0)
        if new_evo != old_evo:
            lines.append(f"Evolution: {old_evo} -> {new_evo}")

        old_prod = old.get("production", {})
        new_prod = new.get("production", {})
        all_items = set(list(old_prod.keys()) + list(new_prod.keys()))
        changes = []
        for item in sorted(all_items):
            old_p = old_prod.get(item, {}).get("produced", 0)
            new_p = new_prod.get(item, {}).get("produced", 0)
            delta = new_p - old_p
            if delta > 0:
                changes.append(f"  {item}: +{delta}")
        if changes:
            lines.append("Production in last 5 min:")
            lines.extend(changes)

        # Flag new items that weren't produced before
        for item in sorted(all_items):
            old_p = old_prod.get(item, {}).get("produced", 0)
            new_p = new_prod.get(item, {}).get("produced", 0)
            if old_p == 0 and new_p > 0:
                lines.append(f"NEW: started producing {item}!")

        return "\n".join(lines)

    def do_checkin(self):
        """Periodic auto-message with game state changes and commentary."""
        state = self.query_game_state()
        if not state or "error" in state:
            return

        if self.last_state is None:
            self.last_state = state
            return

        diff = self.diff_states(self.last_state, state)
        self.last_state = state

        print(f"\n--- [Auto check-in] ---\n{diff}")

        parts = [
            "You are Claude, an AI advisor embedded in a Factorio game.",
            "The game just hit a 5-minute check-in. Below is what changed.",
            "Give a SHORT (1-2 sentences) observation or suggestion.",
            "Focus on what's interesting: bottlenecks, new production,",
            "research changes, or strategy tips. Plain text, no markdown.",
            "If nothing notable changed, just say something brief and encouraging.",
            "",
            "Changes since last check-in:",
            diff,
            "",
            "Current full state:",
            self.format_game_context(state),
            "",
        ]

        if self.history:
            parts.append("Recent conversation for context:")
            for h in self.history[-6:]:
                parts.append(f"  {h['role']}: {h['text']}")
            parts.append("")

        parts.append("Your brief check-in message:")
        prompt = "\n".join(parts)

        try:
            result = subprocess.run(
                [CLAUDE_BIN, "-p", prompt],
                capture_output=True,
                text=True,
                timeout=CLAUDE_TIMEOUT,
            )
            response = result.stdout.strip()
            if response:
                ts = datetime.now(timezone.utc).isoformat()
                self.history.append({"role": "Claude", "text": response, "ts": ts})
                append_history({"role": "Claude", "text": response, "ts": ts})
                print(f"<<< [Claude check-in]: {response}")
                self.send_to_game(response)
        except Exception as exc:
            print(f"  Check-in failed: {exc}")

    # -- Main loop ---------------------------------------------------

    def run(self):
        print("=" * 60)
        print("  Factorio  <->  Claude Code  Chat Bridge")
        print("=" * 60)
        print()
        print(f"  RCON target : {RCON_HOST}:{RCON_PORT}")
        print(f"  Input file  : {INPUT_FILE}")
        print(f"  Claude CLI  : {CLAUDE_BIN}")
        print(f"  Persist dir : {PERSIST_DIR}")
        print()

        self.init()
        print()

        # wait for RCON
        for attempt in range(1, RCON_CONNECT_RETRIES + 1):
            try:
                self.connect_rcon()
                break
            except Exception as exc:
                if attempt == RCON_CONNECT_RETRIES:
                    print(
                        f"\nFailed to connect after {RCON_CONNECT_RETRIES} attempts."
                    )
                    print(
                        "Make sure Factorio is hosting a multiplayer game with RCON enabled."
                    )
                    sys.exit(1)
                print(f"  Attempt {attempt}/{RCON_CONNECT_RETRIES}: {exc}")
                time.sleep(RCON_RETRY_DELAY)

        # announce
        self.send_to_game("Bridge connected! Just type in chat to talk to Claude.")

        # skip any pre-existing messages in the file
        if INPUT_FILE.exists():
            self.file_pos = INPUT_FILE.stat().st_size

        print("\nWaiting for messages... (Ctrl-C to quit)\n")

        # snapshot initial game state for check-in diffs
        self.last_state = self.query_game_state()
        self.last_checkin = time.time()

        # start focus watcher (auto-pause on cmd-tab away)
        self._paused = False
        self._start_focus_watcher()

        try:
            while True:
                for msg in self.poll_messages():
                    self.handle_message(msg)

                # periodic check-in (skip if game is paused)
                now = time.time()
                if now - self.last_checkin >= CHECKIN_INTERVAL:
                    if self._paused:
                        # reset timer so it starts fresh when unpaused
                        self.last_checkin = now
                    else:
                        self.last_checkin = now
                        self.do_checkin()

                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            print("\nShutting down ...")
        finally:
            # unpause before exiting so the game doesn't stay frozen
            self._set_paused(False)
            if self.rcon:
                try:
                    self.send_to_game("Bridge disconnected. See you!")
                except Exception:
                    pass
                self.rcon.close()

    # -- Auto-pause on focus loss ------------------------------------

    def _set_paused(self, paused: bool):
        if paused == self._paused or not self.rcon:
            return
        self._paused = paused
        try:
            val = "true" if paused else "false"
            self.rcon.send_command(f"/sc game.tick_paused = {val}")
            state = "PAUSED" if paused else "RESUMED"
            print(f"  [{state}] (focus change)")
        except Exception as exc:
            print(f"  Pause toggle failed: {exc}")

    @staticmethod
    def _get_frontmost_app() -> str:
        try:
            r = subprocess.run(
                ["osascript", "-e",
                 'tell application "System Events" to get name of '
                 'first application process whose frontmost is true'],
                capture_output=True, text=True, timeout=2,
            )
            return r.stdout.strip().lower() if r.returncode == 0 else ""
        except Exception:
            return ""

    def _focus_poll_loop(self):
        last = ""
        while True:
            current = self._get_frontmost_app()
            if current and current != last:
                is_factorio = "factorio" in current
                self._set_paused(not is_factorio)
                last = current
            time.sleep(0.5)

    def _start_focus_watcher(self):
        t = threading.Thread(target=self._focus_poll_loop, daemon=True)
        t.start()
        print("Focus watcher started (auto-pause on cmd-tab away)")


# ============================================================
# Entry point
# ============================================================

if __name__ == "__main__":
    Bridge().run()
