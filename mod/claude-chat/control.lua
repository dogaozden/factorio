-- Claude Chat Bridge - Factorio Mod
-- Captures player chat messages and provides RCON commands for the bridge.
--
-- Usage:
--   !<message>         Send a message to Claude via regular chat
--   /claude <message>  Send a message to Claude via command
--
-- The bridge reads messages from script-output/claude_chat.jsonl
-- and sends responses back via the /claude-respond RCON command.

local OUTPUT_FILE = "claude_chat.jsonl"
-- ============================================================
-- Helper: write a message to the bridge input file
-- ============================================================
local function write_message(player_name, message)
    local data = {
        tick = game.tick,
        player = player_name,
        message = message
    }
    helpers.write_file(OUTPUT_FILE, helpers.table_to_json(data) .. "\n", true)
end

-- ============================================================
-- Event: capture ALL chat messages and send to Claude
-- ============================================================
script.on_event(defines.events.on_console_chat, function(event)
    if not event.player_index then return end

    local player = game.get_player(event.player_index)
    if not player then return end

    local msg = string.match(event.message, "^%s*(.-)%s*$")
    if msg == "" then return end

    write_message(player.name, msg)
    player.print({"", "[color=0,191,255][Claude][/color] ", "Thinking..."})
end)

-- ============================================================
-- Command: /claude <message>
-- ============================================================
commands.add_command("claude", "Send a message to Claude AI", function(cmd)
    if not cmd.parameter or cmd.parameter == "" then
        if cmd.player_index then
            game.get_player(cmd.player_index).print("[color=255,100,100]Usage: /claude <your message>[/color]")
        end
        return
    end

    local player_name = "Server"
    if cmd.player_index then
        local player = game.get_player(cmd.player_index)
        if player then
            player_name = player.name
            player.print({"", "[color=0,191,255][Claude][/color] ", "Thinking..."})
        end
    end

    write_message(player_name, cmd.parameter)
end)

-- ============================================================
-- Command: /ss  (take a screenshot for Claude to analyze)
-- ============================================================
commands.add_command("ss", "Take a screenshot for Claude to analyze", function(cmd)
    local player_name = "Server"
    if cmd.player_index then
        local player = game.get_player(cmd.player_index)
        if player then
            player_name = player.name
            player.print({"", "[color=0,191,255][Claude][/color] ", "Taking screenshot..."})
        end
    end
    local msg = "/ss"
    if cmd.parameter and cmd.parameter ~= "" then
        msg = "/ss " .. cmd.parameter
    end
    write_message(player_name, msg)
end)

-- ============================================================
-- Command: /claude-respond <text>  (called by bridge via RCON)
-- ============================================================
commands.add_command("claude-respond", nil, function(cmd)
    if cmd.parameter and cmd.parameter ~= "" then
        game.print({"", "[color=0,191,255][Claude] [/color]", cmd.parameter})
    end
end)

-- ============================================================
-- Command: /claude-gamestate  (called by bridge via RCON)
-- Returns JSON snapshot of game state for Claude context.
-- ============================================================
commands.add_command("claude-gamestate", nil, function(cmd)
    local ok, result = pcall(function()
        local force = game.forces["player"]
        local surface = game.surfaces[1]

        -- Game time
        local ticks = game.ticks_played
        local hours = math.floor(ticks / (60 * 60 * 60))
        local minutes = math.floor((ticks % (60 * 60 * 60)) / (60 * 60))

        -- Research
        local current_research = "none"
        local research_progress = 0
        if force.current_research then
            current_research = force.current_research.name
            research_progress = math.floor(force.research_progress * 100)
        end

        -- Count researched techs
        local researched_count = 0
        for _, tech in pairs(force.technologies) do
            if tech.researched then
                researched_count = researched_count + 1
            end
        end

        -- Evolution
        local evo = 0
        local enemy = game.forces["enemy"]
        if enemy and enemy.get_evolution_factor then
            evo = enemy.get_evolution_factor(surface)
        end

        -- Production stats for key items
        local stats = force.get_item_production_statistics(surface.name)
        local key_items = {
            "iron-plate", "copper-plate", "steel-plate",
            "electronic-circuit", "advanced-circuit", "processing-unit",
            "automation-science-pack", "logistic-science-pack",
            "military-science-pack", "chemical-science-pack",
            "production-science-pack", "utility-science-pack",
            "iron-ore", "copper-ore", "coal", "stone",
            "plastic-bar", "sulfur", "battery",
            "engine-unit", "electric-engine-unit",
            "firearm-magazine", "piercing-rounds-magazine",
            "grenade", "stone-wall", "gun-turret",
        }

        local production = {}
        for _, name in pairs(key_items) do
            local produced = stats.get_input_count(name)
            local consumed = stats.get_output_count(name)
            if produced > 0 or consumed > 0 then
                production[name] = {produced = produced, consumed = consumed}
            end
        end

        -- Player info
        local player_info = {}
        for _, p in pairs(game.connected_players) do
            table.insert(player_info, {
                name = p.name,
                position = {x = math.floor(p.position.x), y = math.floor(p.position.y)},
                surface = p.surface.name,
            })
        end

        return helpers.table_to_json({
            game_time = string.format("%dh %dm", hours, minutes),
            current_research = current_research,
            research_progress_pct = research_progress,
            technologies_researched = researched_count,
            evolution_factor = math.floor(evo * 1000) / 1000,
            production = production,
            players = player_info,
        })
    end)

    if ok then
        rcon.print(result)
    else
        rcon.print('{"error":"' .. tostring(result):gsub('"', '\\"') .. '"}')
    end
end)

-- ============================================================
-- Command: /claude-status  (check if bridge is connected)
-- ============================================================
commands.add_command("claude-status", "Check if the Claude bridge is connected", function(cmd)
    if cmd.player_index then
        game.get_player(cmd.player_index).print(
            "[color=200,200,200]Claude Chat mod is loaded. "
            .. "If you see responses to !messages, the bridge is connected.[/color]"
        )
    end
end)

-- ============================================================
-- Command: /claude-clear  (clear the message file)
-- ============================================================
commands.add_command("claude-clear", "Clear Claude chat history file", function(cmd)
    helpers.write_file(OUTPUT_FILE, "", false)
    if cmd.player_index then
        game.get_player(cmd.player_index).print("[color=0,191,255]Claude chat history cleared.[/color]")
    end
end)
