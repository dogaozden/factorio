#!/usr/bin/env python3
"""Look up a Factorio recipe via RCON. Used by in-game Claude as a tool."""

import json
import re
import socket
import struct
import sys

RCON_HOST = "127.0.0.1"
RCON_PORT = 27015
RCON_PASSWORD = "claude-chat-bridge"


def rcon_command(cmd: str) -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect((RCON_HOST, RCON_PORT))
    # Auth
    body = RCON_PASSWORD.encode("utf-8") + b"\x00\x00"
    size = 4 + 4 + len(body)
    sock.sendall(struct.pack("<iii", size, 1, 3) + body)
    sock.recv(4096)
    # Command
    body = cmd.encode("utf-8") + b"\x00\x00"
    size = 4 + 4 + len(body)
    sock.sendall(struct.pack("<iii", size, 2, 2) + body)
    data = sock.recv(4096)
    sock.close()
    return data[12:].rstrip(b"\x00").decode("utf-8", errors="replace")


def lookup(item_name: str) -> str:
    safe = re.sub(r"[^a-z0-9-]", "", item_name.lower().strip())
    if not safe:
        return "Error: invalid item name"

    lua = (
        f"/sc local r=prototypes.recipe['{safe}']"
        f" if not r then rcon.print('NOT_FOUND') return end"
        f" local d={{name=r.name,energy=r.energy,category=r.category,"
        f"ingredients={{}},products={{}}}}"
        f" for _,i in pairs(r.ingredients) do"
        f"   d.ingredients[#d.ingredients+1]={{name=i.name,amount=i.amount,type=i.type}}"
        f" end"
        f" for _,p in pairs(r.products) do"
        f"   d.products[#d.products+1]={{name=p.name,amount=p.amount,type=p.type}}"
        f" end"
        f" rcon.print(helpers.table_to_json(d))"
    )

    raw = rcon_command(lua)
    if raw.strip() == "NOT_FOUND":
        return f"No recipe found for '{safe}'"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return f"Error parsing recipe data: {raw[:200]}"

    ingredients = [f"{i['amount']}x {i['name']}" for i in data.get("ingredients", [])]
    products = [p["name"] for p in data.get("products", [])]
    energy = data.get("energy", "?")
    category = data.get("category", "?")
    name = data.get("name", safe)

    result = f"Recipe for {name}: {', '.join(ingredients)}."
    result += f" Craft time: {energy}s. Category: {category}."
    if len(products) > 1 or (len(products) == 1 and products[0] != name):
        result += f" Produces: {', '.join(products)}."
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: recipe_lookup.py <item-name> [item-name2] ...")
        print("Example: recipe_lookup.py locomotive fast-transport-belt substation")
        sys.exit(1)

    for item in sys.argv[1:]:
        print(lookup(item))
