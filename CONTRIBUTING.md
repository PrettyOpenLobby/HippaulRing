# Contributing to CrystalRing

CrystalRing is the Fantasy Earth server: the lobby balancer, the lobby, the
world and the optional map page. It runs beside the OpenLobby core, which
handles PlayOnline login. This page says where things are, how to run the
checks, and what a pull request needs.

## Where things are

```
services/
  fellb.py          lobby load balancer (54848): tells the client where the lobby is
  felobby.py        the lobby (54849): character list, creation, the jump to the world
  feworld.py        entry point for the world server (54850) and a facade over world/
  world/            the world server, one module per concern (listed below)
  fe<feature>.py    extension modules loaded by the world: party, mail, trade,
                    unit, war, items, force, gm, campaign, prog, presence, pvp, map
  fenet.py          the transport shared by lobby and world: framing and cipher
  feblowfish.py     FE's Blowfish with its own tables (generated, see README)
  feident.py        which PlayOnline member is on the other end of a socket
  festore.py        the player database (the fe_character table in PostgreSQL,
                    or JSON when FE_DB is empty)
  festate.py        the world stores, one row each of fe_world_state
  fedb.py           reaches the core's polcore; migrate, status and import
  fe_migrations/    CrystalRing's migrations, numbered from 1001
  fegamedata.py     the client's shipped tables, read from services/fedata/
  fedevtool.py      the world-building web panel (--devtool-port)
  fetitle.py        the title plugin that runs inside the OpenLobby core
tools/              self-tests (fe_*_test.py), data builders, operator tools
```

### The world package

`feworld.py` used to be one file of about 21,800 lines. Its code is now in
`services/world/`, and each module's docstring says what it holds:

```
deps.py           imports shared by the modules; services/ on sys.path
sess.py           per-connection state: the thread-local _SESSION and the sequence slots
wire.py           the transport: key exchange, inner message framing, send()
messages.py       message id tables: log names, the client's NG table, header-only OK replies
ext.py            the extension seam: the tables extensions register into, load_extensions()
chat.py           the shared chat room, relaying a line to other sessions, speech bubbles
extrun.py         running extensions: Ctx, dispatch, relay posts between sessions, pumps
character.py      account resolution, stored character fields, the blacklist
territory.py      nations and the territory map: the 0x3027 force record, area owners
zones.py          groups, capitals and rooms: the 0x3031 group record, area id meanings
arrival.py        entering an area: enter_area, the field-ready batch, the first position
clock.py          the client's clock read off its telemetry, and the clock pushes
war.py            the war cycle on a field: start, notify, truce, peace, the deadline pump
warresult.py      0x1100, the war result window
campaignview.py   what the campaign extension says about a field; the per-field head count
entities.py       drawing units: the player's avatar, other players, NPCs, monsters (0x1006)
movement.py       who owns a position, unit speed, the move rows, jump physics
buildings.py      buildings and keeps: the type-1 record, build timers, keep HP and hits
spawns.py         where a player arrives: the spawn store and derived spawn points
mapcal.py         minimap calibration per capital half: anchors and the fitted projection
doors.py          where a door puts you down, and the shipped portal table
town.py           the town store (fe_world_state): placed NPCs, door links, the town push
populate.py       filling a field with monster groups: spawn points, scatter, population
monsters.py       shared monsters: one copy per field, relayed between sessions, their AI
combat.py         registering a target, hit and kill pushes, the battle tally
damage.py         damage both ways, resistance, item use effects
drops.py          EXP and gold per kill, treasure chests on the ground
progression.py    EXP, class level, skill points and Pw, and the pushes that show them
death.py          player HP, death, the return to base, respawn waits, spawn protection
unitstate.py      the unit state word [unit+0x2b4] and its 0x2024 pushes
events.py         the event VM: NPC talk, windows, quests, event steps, goto, room exits
shops.py          shop stock, pages and sell values
itemrecords.py    item rows: the bag and equip records and their fields
equipment.py      equip and unequip mid-session, the worn-slot map, self redress
inventory.py      the bag: size, sort, stack, capacity, new item uids
bank.py           40 item slots and a gold balance per character
staff.py          capital staff: role lines, the free weapon, the inn fee
wallet.py         gold, rings and crystal: the pushes and the ledger that spends them
skilllist.py      the acquired skill list (0x1075), class level rows, the skill palette
charsheet.py      the character record (0x1003) and the status record (0x303E)
probes.py         stat probes used to decode 0x2024/0x2025 fields
devtool.py        the state and edits behind the world-building panel
gm.py             GM commands (!verbs) and the pump that runs them
readloop.py       the per-session read loop: every inbound message id and its answer
launch.py         main(): the command line, the knobs, starting the listener
```

Reading order for a first visit:

1. `feworld.py`'s docstring, then `world/launch.py`: which knobs exist and how
   the listener starts. `RELEASE_DEFAULTS` there is what the live world runs.
2. `world/wire.py` and `world/sess.py`: how one message goes out, and the
   per-connection state every handler reads.
3. `world/readloop.py`: `_serve_loop` takes each inbound message id to its
   answer. Follow a call from there into the module that owns it.
4. `world/ext.py` and `world/extrun.py`, then any `fe<feature>.py`: how an
   extension claims a message id, a `!verb`, a knob or a timer.
5. The concern you came for, from the list above.

A new feature goes into an extension module of its own when it can
(`register(fw)`, see the seam's comment in `world/ext.py`), so that it does not
grow the read loop.

### The facade

`services/feworld.py` is a thin module now. It imports the `world` package and
forwards `feworld.<name>` reads and writes to the module that owns the name,
so the extension modules (which receive it in `register(fw)`), the tools and
the tests work as they did when everything was in one file. A test that
rebinds `feworld.send` to capture output changes the function every module
calls. `tools/facade_rebind_check.py` proves that for every rebinding in
`tools/` and `services/`.

Inside the package a module reaches another one as `<module>.<name>`
(`sess._SESSION`, `wire.send(...)`), never with `from .x import name`, so a
rebinding through the facade is seen everywhere.

### Changing the package

The package was generated once from the single-file `feworld.py`, in commit
c94ced3. It is the source now and is edited directly; nothing regenerates
it. A change written against the single file elsewhere is carried over by
hand into the module that owns that code today.

`feworld.py` stays as the entry point and as the facade described above. It
forwards only the names in its `_OWNERS` table, which maps every name to the
module that owns it, so a new top-level name is not reachable as
`feworld.NAME` (or `fw.NAME` in an extension) until it has a line there.
Code inside the package does not need one, since it uses `<module>.<name>`.
An extension, a tool or a test that reads or rebinds the name through the
facade does, and `tools/facade_rebind_check.py` fails on a rebinding of a
name the table does not list. A new module is imported at the top of the
facade and added to `_MODULES`.

## Running the checks

```
python check.py --selftest     # the hygiene scanner can fail (positive controls)
python check.py                # nothing private or proprietary in the tree
python tools/fe_run_all.py     # every self-test; -k <substring> picks a few
```

Every suite is expected to pass on a clean checkout. Suites that need the
generated `services/fedata/` skip themselves until `tools/fedata_build.py`
has been run. `fe_run_all.py` finds `tools/fe_*_test.py` by name; a suite
with another name is added to its list by hand.

The suites that touch the database need the OpenLobby core checked out
beside this repository (or `OPENLOBBY_DIR` pointing at it), the drivers
(`pip install "psycopg[binary]" psycopg-pool valkey`), and Docker or
`POL_TEST_DATABASE_URL`. `fe_run_all.py` gives each suite a throwaway
database of its own; without a server those suites report SKIP, and
`POL_TEST_REQUIRE_DB=1` makes that a failure. `tools/fe_import_test.py`
rebuilds the old files from git at a pinned commit, so a shallow clone needs
`git fetch --unshallow` first.

No suite writes into the working tree: `git status` is clean after
`python tools/fe_run_all.py`, and `tools/fe_worldstate_test.py` fails if a
store writes a file under `services/`.

## Where state lives

Anything that must survive a restart is a table in the core's PostgreSQL:
the characters (`fe_character`), the mail (`fe_mail`), the world stores
(`fe_world_state`, through `festate.py`) and the blob record
(`fe_blob_observation`). State another container needs while players are
online goes in Valkey through the core's `polcore.kv`, always with an
expiry: the lobby-to-world handoff (`fe:handoff:*`), the member remembered
for an address (`fe:ipmember:*`) and the board snapshot (`fe:map:board`).
Nothing durable goes in Valkey. A file is only for what the operator edits,
such as the `--gmcmd-file` inbox. The member lookup behind the handoff reads
the core's account tables and is turned off with `--member-lookup off` or
`FE_MEMBER_LOOKUP=0`.

A schema change is a new file in `services/fe_migrations/` with the next
number. A shipped migration is never edited. The core's `schema_migrations`
table is keyed by the number alone and shared with the core and the other
titles, so CrystalRing keeps to 1001-1999 and a table name that starts with
`fe_`; a reused number is silently skipped.

Moving a file into the database comes with an importer in `fedb.py`
(`python fedb.py import fe_db|fe_mail_db|world ...`). It only reads its
source, runs in one transaction, refuses a table that already holds rows
unless given `--merge`, writes nothing with `--dry-run`, and changes nothing
on a second run. Its command is added to `TITLES` in OpenLobby's
`tools/db_import.py` and to its `docs/database.md`.

`live_sessions.py` is the core's. The lobby and the world publish their
counts with `live_sessions.start_heartbeat`; a copy of the module must not
be added here. `services/.dockerignore` keeps one out of the image and the
build refuses an image whose `live_sessions` is not the core's. A deploy
script asks a running container, for example
`docker compose exec -T feworld python live_sessions.py count feworld`.

## What a pull request needs

- One topic per pull request, with a subject line in the form the history
  uses ("Fantasy Earth: a built object gets HP only when --build-hp sets it").
- The checks above green, and a self-test for behaviour that can be pinned
  offline. A change to what a suite pins updates the suite in the same pull
  request.
- No Square Enix content: no client tables, captured server blobs, art or
  fonts, and no captured packets in tests. Code that reads such data from
  the user's own install is fine.
- Nothing private: no real addresses or hostnames, no member names or ids.
- Behaviour that exists because the client needs it keeps its comment: which
  client function reads the field and what happens without it. Most of this
  server is shaped by what FE_Client reads, and a reader cannot tell a client
  requirement from a mistake without that note.

## Reporting a bug

Open an issue with the client build, the knobs the world ran with, the log
lines around the failure (`/logs` in the containers, or `docker logs`), and
what the client showed.
