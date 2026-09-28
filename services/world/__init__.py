"""The world package: feworld.py split into one module per concern.

    deps.py            Imports shared by the world modules, and the services directory on sys.path.
    sess.py            Per-connection state: the thread-local session dict (_SESSION) and the sequence slots.
    wire.py            The world connection's transport: key exchange, inner message framing, send().
    messages.py        Message id tables: names for the log, the client's NG table, header-only OK replies.
    ext.py             The extension seam: the tables fe<feature>.py modules register into, and load_extensions().
    chat.py            Chat: the shared chat room, relaying a line to other sessions, speech bubbles.
    extrun.py          Running extensions: the Ctx handed to a handler, dispatch, relay posts between sessions, pumps.
    character.py       Whose character this is: account resolution, stored character fields, the blacklist.
    territory.py       Nations and the territory map: the 0x3027 force record, who holds each area.
    zones.py           Groups, capitals and rooms: the 0x3031 group record and which area id means what.
    arrival.py         Entering an area: enter_area, the field-ready batch, the first position.
    clock.py           The client's clock read off its telemetry, and the clock pushes a war timer needs.
    war.py             The war cycle on a field: start, notify, truce, peace, the deadline pump.
    warresult.py       0x1100, the war result window.
    campaignview.py    What the campaign extension says about a field, and the per-field head count.
    entities.py        Drawing units: the player's own avatar, other players, NPCs and monsters (0x1006).
    movement.py        Movement: who owns a position, unit speed, the move rows and jump physics.
    buildings.py       Buildings and keeps: the type-1 record, construction timers, keep HP and hits.
    spawns.py          Where a player arrives: the spawn store and the derived spawn points.
    mapcal.py          The minimap calibration per capital half: anchors and the fitted projection.
    doors.py           Doors: where a door puts you down (the arrivals store) and the shipped portal table.
    town.py            The town store (festate "town"): placed NPCs, door links, pushing the town.
    populate.py        Filling a field with monster groups: spawn points, scatter, population.
    monsters.py        Shared monsters: one copy per field, relayed between sessions, and their AI.
    combat.py          Combat: registering a target, hit and kill pushes, the battle tally.
    damage.py          Damage numbers both ways, resistance, item use effects.
    drops.py           Kill rewards: EXP and gold per kill, treasure chests on the ground.
    progression.py     EXP, class level, skill points and Pw, and the pushes that show them.
    death.py           Player HP, death, the return to base, respawn waits and spawn protection.
    unitstate.py       The unit state word [unit+0x2b4] and the 0x2024 stat pushes.
    events.py          The event VM: NPC talk, windows, quests, event scripts and steps, goto and room exits.
    shops.py           Shops: stock, pages, sell values.
    itemrecords.py     Item rows: the bag and equip records and their fields.
    equipment.py       Equip and unequip mid-session, the worn-slot map, self redress.
    inventory.py       The bag: size, sort, stack, capacity, new item uids.
    bank.py            The bank: 40 item slots and a gold balance per character.
    staff.py           Capital staff: role lines, the free weapon, the inn fee.
    wallet.py          Gold, rings and crystal: the wallet pushes and the ledger that spends it.
    skilllist.py       The acquired skill list (0x1075), class level rows and the skill palette.
    charsheet.py       The character record (0x1003) and the status record (0x303E).
    probes.py          Stat probes for decoding 0x2024/0x2025 fields.
    devtool.py         The world-building panel's state and edits (served by fedevtool.py).
    gm.py              GM commands (!verbs) and the pump that runs them.
    readloop.py        The per-session read loop: every inbound message id and its answer.
    launch.py          main(): the command line, the knobs, and starting the listener.

feworld.py (one directory up) is the entry point and the compatibility
facade over these modules.
"""
