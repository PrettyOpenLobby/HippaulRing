"""main(): the command line, the knobs, and starting the listener."""
import argparse
import socket
import sys
import threading
import time
import fedevtool  # noqa: E402  -- the world-building panel (--devtool-port)
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import buildings, death, deps, devtool, doors, drops, ext, itemrecords, mapcal, probes, progression, readloop, spawns, staff, territory, zones

def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    # A copy of the console in /logs/feworld.log, which outlives the container
    # (docker-compose.yml mounts the fe-logs volume there). See filelog.py.
    import filelog
    filelog.tee("feworld")

    ext.load_extensions()
    ap = argparse.ArgumentParser(description=deps.facade().__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=54850)
    ap.add_argument("--master-key", default="fantasyearth")
    ap.add_argument("--master-mode", default="ecb/le",
                    choices=["ecb/be", "ecb/le", "cbc/be", "cbc/le"],
                    help="ecb/le is the measured one -- LITTLE-endian block "
                         "halves, which is unusual for Blowfish and easy to miss")
    ap.add_argument("--server-key", default="0123456789abcdef0123456789abcdef",
                    help="hex of OUR half of the exchange. MUST be non-empty.")
    ap.add_argument("--seq", type=int, default=1)
    ap.add_argument("--seq-mode", default="echo",
                    choices=["echo", "count"],
                    help="see felobby.py --seq-mode. The world door is the case\n"
                         "where the two DIFFER: 0x20 arrives first and goes\n"
                         "unanswered, so echo makes our first reply carry 2.")
    # THE NAMES AND THEIR ORDER ARE THE CLIENT'S OWN, not invented. It carries a
    # five-entry pointer table at 0x052ea81c and indexes it with `ForceID - 1`:
    #
    #     mov esi, [esi+0x3bc]          ; the force id
    #     dec esi                       ; id - 1
    #     js / cmp esi,5 / jge          ; bounds 1..5
    #     mov eax, [esi*4 + 0x52ea81c]  ; the name         (0x05130453)
    #
    # so 1=Netzawar 2=Cathedira 3=Elsord 4=Holdein 5=Gebrand. Those are this
    # PlayOnline-era build's romanisations of the five kingdoms later shipped in
    # Fantasy Earth ZERO as Netzavare, Cesedria, Yelsord, Hordaine and
    # Gevrandian -- the account holder supplied that list, and matching it
    # against the binary is what pinned both the spellings and the ID order.
    # (An earlier default here guessed "Hordaine, Netzmilt, Ordan, Cesell,
    # Geburand" in the wrong order; only Hordaine was close.)
    ap.add_argument("--forces",
                    default="1:Netzawar,2:Cathedira,3:Elsord,4:Holdein,"
                            "5:Gebrand",
                    help="the NATIONS offered at the nation-select screen, as "
                         "id:Name pairs. The client polls ForceID 1..5 and an "
                         "empty record leaves its list blank. The defaults are "
                         "the client's own names in its own ID order.")
    ap.add_argument("--devtool-port", type=int, default=0, metavar="PORT",
                    help="serve the WORLD-BUILDING PANEL on this port: where "
                         "you are standing, what the area is still missing, and "
                         "a button for each of it. 0 (the default) is off. "
                         "WARNING: the panel runs !goto, !npc and !land against the "
                         "live session -- it binds LOOPBACK unless told "
                         "otherwise, and refuses to bind wider without a token.")
    ap.add_argument("--devtool-bind", default="127.0.0.1", metavar="ADDR",
                    help="what the panel listens on. Leave it on loopback and "
                         "reach it through an ssh tunnel unless you have a "
                         "reason not to.")
    ap.add_argument("--devtool-token", default="", metavar="STR",
                    help="required for any non-loopback bind; appended to the "
                         "panel's URL as ?t=... Not a password -- it is the "
                         "difference between 'awkward to reach' and 'open'.")
    ap.add_argument("--spawn-file", default=None,
                    help="the SPAWN STORE (default data/fe_spawn.json): area id "
                         "-> the point a player arrives at in that area, "
                         "written by `!spawn` from where the player is actually "
                         "standing. An area with no row falls back to "
                         "--spawn-pos, which is ONE position for all 95 and is "
                         "therefore wrong for 94 of them.")
    ap.add_argument("--spawn-derive", default="castle", choices=["castle", "off"],
                    help="where a war field with no `!spawn` row puts a player: "
                         "'castle' = beside their side's keep (the castle for a "
                         "defender, the attacker's keep for an attacker; "
                         "fet_castle_info + keep_grids), five cells toward the "
                         "centre, at the map's ground height. Needs "
                         "--spawn-height dat. 'off' = the old one-point fallback.")
    ap.add_argument("--spawn-height", default="dat", choices=["dat", "off"],
                    help="what an area with NO spawn row falls back to. `dat` "
                         "uses that area's own battle-map height -- the median "
                         "of the Hmap's shipped sound emitters, which is the "
                         "only per-map height dat.pak gives us. Those medians "
                         "run 8 to 62 across the twelve maps that carry "
                         "samples, and --spawn-pos unset is y=0, which is under "
                         "ALL of them. WARNING: a sound emitter is not standable "
                         "ground; this stops a player falling through the "
                         "world, it does not replace standing somewhere and "
                         "saying `!spawn`. `off` uses --spawn-pos verbatim.")
    ap.add_argument("--spawn-area", default="", metavar="GID:X:Y:Z[:H[:TAG]],...",
                    help="arrival points by area, over the top of the store. "
                         "Repeat a GID with different TAGs to give a "
                         "battlefield more than one -- a war has two sides and "
                         "they do not arrive together. The heights matter more "
                         "than the horizontals: world (0,0,0) is the CENTRE of "
                         "the terrain grid at height zero, which on every "
                         "battle map that ships heights is underneath it.")
    ap.add_argument("--door-height", default="dest", metavar="dest|client|N",
                    help="where a shipped door's arrival HEIGHT comes from. "
                         "fet_area_portal carries (x, z) only -- FE takes "
                         "height off the heightmap. `dest` (the default) uses "
                         "the DESTINATION's own ground: its spawn row first, "
                         "then its battle map's height, then the client's "
                         "reported y if that is plausible. WARNING: `client` was the "
                         "default until 2026-09-10 and was WRONG: it used the "
                         "height at the doorway you left, but a capital's two "
                         "halves are different maps at different heights, so "
                         "the player arrived in the air, fell, and the falling "
                         "y (10041, then 10064) fed the next door -- a loop "
                         "they could not walk out of. A number pins a literal.")
    ap.add_argument("--world", default="synthetic", choices=["synthetic", "dat"],
                    help="WHICH WORLD TO SERVE. `dat` serves the one the client "
                         "ships: fet_area's 95 areas over SIX islands -- island "
                         "1 the shared frontier continent (15 areas, three per "
                         "nation), islands 2..6 one home continent per nation "
                         "with its capital -- each field carrying the table's "
                         "own name, owner, Hmap number, map pixel, flags and "
                         "ADJACENCY LIST. `synthetic` is the old behaviour: "
                         "--islands x --groups invented fields with ids from a "
                         "formula. dat overrides --field-coords, --field-hmaps, "
                         "the --field-nations DEFENDER and --islands; it leaves "
                         "--field-names, --field-status and the attacker alone.")
    ap.add_argument("--world-neighbours", default="on", choices=["on", "off"],
                    help="whether --world dat serves 0x3031 mask bit 12, the "
                         "field's adjacency list. THIS IS WHAT LETS A PLAYER "
                         "ENTER ENEMY LAND: the client's frontier test "
                         "0x05009870 walks it, and with no list the only "
                         "enterable fields are the ones your own nation already "
                         "holds -- which is how 'you cannot leave your country' "
                         "looked like a rule instead of our omission. `off` is "
                         "the A/B, not a setting to run.")
    ap.add_argument("--world-inner-capitals", default="on", choices=["on", "off"],
                    help="whether --world dat announces the capitals' INNER "
                         "halves (91..95). They are door-only -- nothing in the "
                         "graph points at them -- but a group the client was "
                         "never told about cannot be resolved when a door lands "
                         "the player in it. They draw at the same map pixel as "
                         "their outer half.")
    ap.add_argument("--territory", default="", metavar="AREA:NATION,...",
                    help="hand areas to nations at startup, over the top of "
                         "fet_area's shipped board and the territory store. "
                         "`--territory 18:2` gives nation 2 a field bordering "
                         "nation 1's capital, which is the smallest test that "
                         "the adjacency rule works: 21 should become enterable "
                         "for a nation-2 character and stay shut for nation 3.")
    ap.add_argument("--territory-file", default=None,
                    help="the TERRITORY STORE (default data/fe_territory.json): "
                         "area id -> holding nation, seeded from fet_area and "
                         "rewritten whenever a field changes hands.")
    ap.add_argument("--islands", type=int, default=5,
                    help="how many islands to announce in 0x3030. The client "
                         "bounds-checks each id as (id-1) < count, so the list is "
                         "always 1..count; the per-island 16-byte record is not "
                         "mapped yet, so only the ids are sent.")
    ap.add_argument("--groups", type=int, default=1,
                    help="groups announced per island in 0x3031. Each is sent as "
                         "an id with a ZERO field mask, which the parser reads as "
                         "'no optional fields'. 0 is a complete valid answer.")
    ap.add_argument("--force-targets", default=None, metavar="FID:FIELD,...",
                    help="m_TargetFieldID per nation -- which field it is "
                         "attacking. Unset, the ATTACKING nation from "
                         "--field-nations targets field 1. Every nation reported "
                         "0 before this existed, i.e. nobody was attacking "
                         "anything anywhere, which is not a world that can have a "
                         "war to prepare for.")
    ap.add_argument("--world-notice", default=None, metavar="TITLE|TEXT",
                    # default shown in the help text below rather than set here,
                    # so --world-notice-none can still tell "unset" from "empty"
                    help="answer 0x4015 with a 0x303C notice instead of 0x303D "
                         "'nothing to show'. The client caches the notice id at "
                         "0x52e4a48 and only reacts when it CHANGES, so re-sending "
                         "the same id is a no-op.")
    ap.add_argument("--world-notice-id", type=lambda v: int(v, 0), default=1,
                    help="the i32 the client caches at 0x52e4a48. It reacts only "
                         "when this CHANGES, so bump it between runs.")
    ap.add_argument("--world-notice-none", action="store_true",
                    help="answer 0x4015 with 0x303D 'nothing to show' instead. "
                         "0x303D sets [screen+0xec]=0xFF, which sends the "
                         "map-select screen to state 7 and (unless "
                         "[screen+0x5c]==4) straight back to state 0 -- the "
                         "black-screen loop. Kept only for an A/B run.")
    ap.add_argument("--island-names", default=None,
                    help="comma-separated, one per island, attached to 0x3030. "
                         "Each island's record is [u16 id][u16 nameCount] + that "
                         "many strings; omitting them sends nameCount 0, which is "
                         "aligned and valid.")
    ap.add_argument("--area-echo", default="-1",
                    help="what 0x1000 puts in the u32 that lands at "
                         "[scene+0x4e8]. Mode 0xa dummies the whole field entry "
                         "when that equals the group id at +0x4ec, and since the "
                         "client derives its 0x2000 area FROM the group id, "
                         "'echo' (the old behaviour) collides every time. -1 is "
                         "the client's own ctor value for the slot and makes the "
                         "test fail, taking the real load path instead. Any "
                         "integer, or the literal 'echo'.")
    ap.add_argument("--move-field", choices=("ok", "ng", "none"), default="ok",
                    help="how to answer 0x2017 MSG_MOVE_FIELD_REQUEST, which the "
                         "client sends from the dummy area to ask for the real "
                         "field. 'ok' sends 0x1012 with an EMPTY body (its arm "
                         "0x5054af3 reads nothing); 'ng' sends 0x1013 with a u32 "
                         "error code; 'none' leaves it unanswered, which is what "
                         "parked the client in the dummy area.")
    ap.add_argument("--move-field-err", type=lambda v: int(v, 0), default=0,
                    help="the u32 error code carried by a 0x1013 NG "
                         "(--move-field ng). Looked up in the client's error "
                         "string table by 0x5061290.")
    ap.add_argument("--set-position", dest="set_position",
                    action=argparse.BooleanOptionalAction, default=True,
                    help="send 0x1027 MSG_SET_POSITION after 0x1000. THIS IS "
                         "WHAT UNPARKS THE FIELD: mode 0xa substate 2 waits on "
                         "[scene+0x296]==1 and only 0x1027's handler sets it, so "
                         "without it the client loads the field assets and then "
                         "stops forever. --no-set-position restores the old "
                         "(parked) behaviour for an A/B run.")
    ap.add_argument("--spawn-pos", default="0:0:0", metavar="X:Y:Z",
                    help="the pos(...) triple in 0x1027, big-endian f32. The "
                         "coordinate space is UNMEASURED; zeros are served so the "
                         "value is recognisable in the client's own "
                         "MSG_SET_POSITION log line.")
    ap.add_argument("--spawn-dir", default="0:0:1", metavar="X:Y:Z",
                    help="the FACING triple -- dir(...) in 0x1027 and, when "
                         "--add-mask2 has bit 2, the three f32 the 0x1006 "
                         "record writes to unit+0x1e4..+0x1ec. WARNING: IT IS A "
                         "DIRECTION VECTOR AND IT MUST NOT BE ZERO: "
                         "0x05078e40 builds the look-at target as "
                         "position + facing, componentwise, so an all-zero "
                         "facing makes the target EQUAL the position and "
                         "the orientation basis degenerate. Default 0:0:1 "
                         "is the value the client itself carried before we "
                         "started writing this field (measured live "
                         "2026-08-24).")
    ap.add_argument("--unit-id", default="0",
                    help="the leading u32 of every world inner header -- the "
                         "message's TARGET UNIT ID, not padding (see "
                         "inner_msg()). MEASURED 2026-08-24: this, NOT the "
                         "0x302B payload, is what lands at [0x533998c]+0x9e0 "
                         "as the server unit's id -- WARNING: UNTESTED. What IS "
                         "measured is only that the 0x302B PAYLOAD is NOT the "
                         "slot (sent charid there, read 0 back live). Default "
                         "is 0, the value every working session has used. "
                         "'auto' sends the session charid on EVERY world header "
                         "including 0x302B's, so the header, 0x1027's target "
                         "and the entity id we spawn in 0x1000 all name one "
                         "character -- that is the experiment, and it is opt-in "
                         "because getting it wrong is a BLACK SCREEN: 0x1027 is "
                         "dropped unless it matches [0x533998c]+0x9e0, and a "
                         "dropped 0x1027 parks mode 0xa substate 2 forever.")
    ap.add_argument("--enter-room", type=lambda v: int(v, 0), default=-1,
                    help="the i16 at [scene+0x4e6] in 0x1000 -- the ROOM index. "
                         "-1 (the client's own default) means OUTDOORS and "
                         "routes field entry to the Hmap battlefield loader; any "
                         "other value is an indoor room id resolved through the "
                         r"name map 0x5192030 into Rooms\<name>.mdl. We sent 0 "
                         "until 2026-08-23, so every field entry was an indoor "
                         "room (room_test.pak) with no battlefield ground.")
    ap.add_argument("--field-rooms", default="", metavar="GID:INDEX[,...]",
                    help="per-GROUP override of --enter-room: which group id "
                         "is a DOOR into an indoor room, and which room. "
                         "Unlisted groups keep --enter-room (default -1, "
                         "outdoors). This exists because --enter-room is one "
                         "global value and the entry branch is ordered capital "
                         "-> outdoors -> room: a global room index leaves the "
                         "capital untouched and sends every WAR FIELD indoors "
                         "instead, which is the opposite of the intent. Named "
                         "indices: %s. Any other value falls to the switch's "
                         "default arm, which our own runs show is room_test -- "
                         "a wasted probe, not a wedge."
                         % ", ".join("%d=%s" % (i, zones.SHIPPED_ROOMS[i])
                                     for i in sorted(zones.SHIPPED_ROOMS)))
    ap.add_argument("--field-status", type=lambda v: int(v, 0), default=1,
                    help="the group/FIELD status byte (mask bit 0). Zero is what "
                         "an all-empty record produced, and the war map showed "
                         "nothing; 1 is the first value worth trying.")
    ap.add_argument("--field-flags", type=lambda v: int(v, 0), default=8,
                    help="the group flags word at [group+0x8c] (mask bit 15). "
                         "Bit 3 (value 8) is what the home-field resolve "
                         "0x05009240 requires; without it map-select state 7 "
                         "can never find the player's nation's field and the "
                         "island sweep restarts forever (the black window).")
    ap.add_argument("--field-coords",
                    default="300:200,500:250,420:380,260:430,600:410",
                    help="per-field marker coordinates on the continent map, "
                         "`x:y` comma-separated by group id (mask bit 17, the "
                         "[%%03d-%%03d] pair in the client's FIELD log line). "
                         "Unserved they default to 0,0 and every marker stacks "
                         "in the top-left corner. The coordinate SPACE is "
                         "unmeasured -- these defaults spread the markers so "
                         "one screenshot calibrates it. Empty disables.")
    ap.add_argument("--field-hmaps", default="1,2,3,4,5",
                    help=r"per-field Data\Hmap\mapNN.pak numbers (mask bit 18, "
                         "[group+0x94]), comma-separated and indexed by group "
                         "id. THE OUTDOOR FIELD'S TERRAIN. The client's table "
                         "0x52d30e0 knows 1..14 (all shipped) and dev maps "
                         "100..105 (not shipped); anything else -- including the "
                         "0 an unserved field leaves -- makes 0x5007e30 return "
                         "NULL and the caller deref it. Empty disables the "
                         "field, which is only safe with --enter-room != -1.")
    ap.add_argument("--field-names", default="", metavar="A,B,.. | dat",
                    help="comma-separated field names (mask bit 7), one per "
                         "group id. Empty sends no name at all. `dat` serves "
                         "dat.pak's OWN names for all 95 areas out of "
                         "fet_area (in Japanese, as shipped); `dat-en` "
                         "serves the ENGLISH names in "
                         "fedata/fe-area-names-en.tsv -- all 95 of them, ours "
                         "rather than SE's because dat.pak ships no English "
                         "for this table and its string harvest recovers 7 of "
                         "the 95 paired with the wrong strings. A field with "
                         "no row falls back to 'Field N', which is what "
                         "dat-en did for every field before 2026-09-09.")
    ap.add_argument("--exp-next", default="auto", metavar="auto|N|off",
                    help="the SECOND number of the Status screen's "
                         "'Exp %%d/%%d' -- [unit+0x135c], which is what decides "
                         "whether the HUD bar is full. WARNING: It was full because "
                         "we wrote the same running total into BOTH halves: "
                         "the numerator is the per-class dword at "
                         "[unit+0x9F8+class*4] and the denominator is a "
                         "different field (draw at 0x050E1388). `auto` "
                         "(default) is the next multiple of --exp-per-level "
                         "above the total, an obvious placeholder that makes "
                         "the bar fill and reset; a number pins it; `off` "
                         "restores the full bar. WARNING: NO exp curve ships in "
                         "dat.pak, so any denominator here is OURS.")
    ap.add_argument("--exp-per-level", type=int, default=1000, metavar="N",
                    help="the step --exp-next auto rounds up to. CHOSEN.")
    ap.add_argument("--monster-wander", default="on", choices=["on", "off"],
                    help="idle monsters STROLL (2026-09-12): each live monster "
                         "outside --monster-aggro walks to a random spot within "
                         "--monster-wander-radius of its HOME at its model's "
                         "own walk speed, then pauses --monster-wander-secs. "
                         "The chase's own 0x2023 action-0 record. 'off' = they "
                         "stand in their spawn formation, as before. CHOSEN.")
    ap.add_argument("--monster-wander-secs", default="6:15", metavar="LO:HI",
                    help="the random pause between strolls, seconds.")
    ap.add_argument("--monster-wander-radius", type=float, default=12.0,
                    metavar="U",
                    help="how far from home an idle monster strolls.")
    ap.add_argument("--monster-share", default="on", choices=["on", "off"],
                    help="ONE set of monsters per field for every player in "
                         "it: the first player in builds it, later ones are "
                         "shown it as it is (position, HP), one session "
                         "drives chase/wander/respawn toward the nearest "
                         "living player, and moves, hits, deaths and respawns "
                         "are relayed to everyone in the field. off = each "
                         "session its own copy (before 2026-09-13).")
    ap.add_argument("--monster-chase", default="on", choices=["on", "off"],
                    help="monsters WALK TOWARD the player (0x2023 action 0) "
                         "when inside --monster-aggro, stopping at three "
                         "quarters of --monster-range so they close and then "
                         "swing. VERIFIED: The arm takes them: its kind gate "
                         "(0x04FEA91E) branches on 2 for an avatar and on "
                         "0xBBB/0xBBC for an NPC, and 0xBBC is what a monster "
                         "is. WARNING: LIVE 2026-09-09: THE MONSTER DID NOT CHASE. "
                         "Two client gates can eat it silently -- bit 0x800000 "
                         "('no ground under the unit') and an unloaded model "
                         "([model+0x27c] == 0) -- and the log now says whether "
                         "we sent anything at all, which is what tells those "
                         "apart from a range problem.")
    ap.add_argument("--monster-aggro", type=float, default=15.0, metavar="U",
                    help="how close the player has to be before a monster "
                         "starts walking toward them, X/Z only. CHOSEN.")
    ap.add_argument("--monster-speed", default="run", metavar="run|walk|U",
                    help="how fast a monster chases. 'run' (the default) is "
                         "each monster's OWN run speed, NPC_ModelType +0x420 "
                         "(Duke_Orc 3.7, King_Griffon 4.8, Barbatos 10.9 u/s); "
                         "'walk' is +0x41c; a number pins every monster to it. "
                         "The client picks walk or run by comparing the speed "
                         "with the model's own pair and plays the motion at "
                         "speed/that, clamped to 0.4..2.0 -- so a flat speed "
                         "faster than a model's run slides it along the "
                         "ground (live 2026-09-12 at a flat 10). HISTORY -- "
                         "monster walk speed in world units per second while "
                         "chasing. CHOSEN -- fet_npc_type ships no speed "
                         "column (hp/attack/defence/exp/level/radius/skills/"
                         "resists and nothing else). WARNING: WAS 3.0, AND THE "
                         "REASON WAS A UNITS ERROR: this help used to say 'the "
                         "PLAYER's own base is 4.0 on a war field, so this is "
                         "deliberately slower', but move_row's 4.0 is a "
                         "MULTIPLIER of the engine's own speed, not world "
                         "units -- the same confusion that had the jump launch "
                         "at 4.7 when it is 16.5. So 3.0 was not 75%% of a "
                         "player's pace, it was about a fifth of it, and the "
                         "live-test verdict on 2026-09-12 was 'very very "
                         "slow'. 10.0 is still under a player's, so you can "
                         "walk away from one.")
    ap.add_argument("--monster-chase-interval", type=float, default=0.5,
                    metavar="S",
                    help="seconds between chase steps for one monster. Each "
                         "step is one 0x2023, so this is also the wire cost.")
    ap.add_argument("--war-trace", default="off", choices=["on", "off"],
                    help="say why the war cycle did NOT advance. Live testing "
                         "saw a full cycle and then 'at the end of the second "
                         "battle start timer, nothing happens' -- and the three "
                         "gates that produce that (not in a field, the phase is "
                         "not one that advances, the deadline is not due) look "
                         "identical from outside. This prints which, once per "
                         "gate per phase, plus a per-second countdown over the "
                         "last 10 s so the approach to zero is visible. Static "
                         "RE has already ruled out the two client-side "
                         "explanations -- see the note in war_deadline_pump.")
    ap.add_argument("--field-home", default="all", choices=["all", "capital"],
                    help="who gets bit 3 of --field-flags. That bit is what "
                         "the client calls a CAPITAL: its map-label builder "
                         "(0x0510A2A4) draws \"首都%s\" -- 'Capital <name>', "
                         "which our UI table renders \"Cap\" -- for any group "
                         "carrying it, which is why every marker on the world "
                         "map reads 'Cap <field>'. `all` (default) sets it "
                         "everywhere, as this has always done. `capital` sets "
                         "it only on --capital groups, which is what the bit "
                         "means. WARNING: NOT the default because the home-field "
                         "resolve 0x05009240 accepts a group only when BOTH "
                         "def_id == the player's nation AND this bit is set, "
                         "so narrowing it can return a nation to the "
                         "2026-08-27 black screen unless that nation's capital "
                         "is served on the island with a matching def_id. "
                         "PARTIAL: Neither setting has been tested since this was "
                         "found.")
    ap.add_argument("--field-color", default="owner", metavar="owner|off|N",
                    help="the map record's COLOUR byte (+0x96, mask bit 19, "
                         "which the client logs as 'COLOR[%%d]') -- a palette "
                         "index the map screen reads SIX times per field and "
                         "we had never sent at all. 'owner' = the nation "
                         "holding the field (a READING: it is the only "
                         "per-field quantity a five-nation palette would key "
                         "on); N pins one index for a probe; 'off' leaves the "
                         "field absent, which is what drew no colour")
    ap.add_argument("--force-color", default="id", metavar="id|off|N",
                    help="the NATION record's colour byte (force+0x1bc, "
                         "FORCE_FIELDS f1bc) -- the '<nation> (of Player)' "
                         "circle and the war-prep window's two sides. 'id' = "
                         "the force id, the same palette index the field "
                         "colour uses; 'off' sends 0, which draws BLACK (what "
                         "every nation showed until 2026-09-12)")
    ap.add_argument("--field-tail", default="on", choices=("on", "off"),
                    help="fill the map record's two ALWAYS-READ tail fields: "
                         "+0xC0 = the attacker's palette index (the war "
                         "panel's second circle) and +0xC4 = the holder's "
                         "FIELDS HELD (the field panel's row). 'off' sends "
                         "both as 0, the old behaviour")
    ap.add_argument("--field-census-probe", type=int, default=0, metavar="N",
                    help="report N players in EVERY field for one probe run, "
                         "instead of the real census. A tester looking at the "
                         "continent map has field-ed OUT, so the true count is "
                         "0 everywhere and 'the client ignores chars_all' and "
                         "'the client draws nothing for nobody' look the same. "
                         "120 draws the largest silhouette on every field if "
                         "chars_all is the marker source. 0 = off (the default "
                         "and the only correct setting for players)")
    ap.add_argument("--field-census", default="on", choices=("on", "off"),
                    help="serve each field's PLAYER COUNT in the map record "
                         "(chars_all, mask bit 2, which the client logs as "
                         "'(ALL%%d)'), and the defenders' sign-ups during a "
                         "war (def_chars, bit 11, 'D%%d'). KEY: this is what draws a "
                         "field's MARKER: mapselect.tex's `Mark2` sheet is four "
                         "person silhouettes labelled 100~ / 50~ / 10~ / ~9 "
                         "plus a crown for a capital, so the marker is a "
                         "POPULATION TIER. Both bits were clear until "
                         "2026-09-12 and the map drew no field markers at all")
    ap.add_argument("--field-owner", default="board",
                    choices=("board", "knob"),
                    help="where a field's OWNING NATION comes from in the map "
                         "record (group+0x78 def_id, which SE's continent-map "
                         "page says draws the field's 国アイコン). 'board' = "
                         "the live territory board, so the icon follows a "
                         "conquest; 'knob' = the pre-2026-09-12 behaviour, one "
                         "constant per island from --field-nations / "
                         "--field-defenders, which showed all fifteen frontier "
                         "fields as one nation's and never changed hands")
    ap.add_argument("--field-nations", default="",
                    help="DEF:ATK nation ids for every field (mask bits 10/9). "
                         "Empty leaves the contest unset.")
    ap.add_argument("--capital", default="", metavar="ISLAND:GID[,...]",
                    help="announce a CAPITAL (town) field on an island, as an "
                         "extra group whose id the client's own predicate "
                         "0x04ff7ef0 accepts: %s. The id is the whole mechanism "
                         "-- on entry the client tests [scene+0x4ec] against "
                         "that set and takes the capital loader (0x5001d70, "
                         "DATA/capital/mapNN_*.oct via the table at 0x052d1858) "
                         "instead of the Hmap battlefield arm. The capital takes "
                         "its island's defender so the home-field resolve "
                         "accepts it, and war_notify refuses to arm in one. "
                         "WARNING: 21's assets are incomplete in our mirror -- use 39, "
                         "57, 62 or 78. Empty = no capital, the pre-2026-09-04 "
                         "behaviour."
                         % ", ".join(str(g) for g in zones.CAPITAL_GROUP_IDS))
    ap.add_argument("--capital-name", default="Capital", metavar="NAME",
                    help="the name the map screen shows for a --capital group.")
    ap.add_argument("--capital-coords", default="450:300", metavar="X:Y",
                    help="the capital's marker position on the continent map "
                         "(mask bit 17). --field-coords is indexed gid-1 and a "
                         "capital id is past the end of it, so without this the "
                         "capital had NO marker and the client clamped it to the "
                         "corner (coords below 0x28 are forced to 0x28 at "
                         "0x0500fae6). WARNING: The coordinate SPACE is still "
                         "unmeasured -- the client scales it through a magic "
                         "divide at 0x0500faff -- so this is a position that "
                         "puts the marker ON the map, not a calibrated one.")
    ap.add_argument("--capital-attacker", default="off", choices=["on", "off"],
                    help="whether a --capital group carries atk_id/atk_name "
                         "like a war field does. THIS IS AN A/B, not a "
                         "preference. `off` was the first attempt and the map "
                         "screen CRASHED (2026-09-04, c0000005 reading address "
                         "0x1 in a strcmp at FE_Client+0xFA576). `on` makes the "
                         "capital record identical in SHAPE to a war field's -- "
                         "which is known not to crash that screen -- so the only "
                         "difference left is the group ID. If it still crashes "
                         "with `on`, the id is the cause and these fields are "
                         "innocent; if it stops, the client needs the pair.")
    ap.add_argument("--field-defenders", default="",
                    help="comma-separated DEFENDING nation id per island "
                         "(island i takes entry i-1, cycling). Overrides the DEF "
                         "half of --field-nations per field so EVERY nation owns "
                         "a home field -- the map-select home resolve 0x05009240 "
                         "gates field entry on [group+0x78]==the character's "
                         "`force`, so a single defender blacks the screen for "
                         "every OTHER nation (the Maria fix, 2026-08-27). Empty "
                         "keeps the single --field-nations pair on every field. "
                         "WARNING: THE COST, measured 2026-09-04: it also CONFINES each "
                         "character to its own nation's island. Before it, every "
                         "field was def_id=1 and a nation-1 character could enter "
                         "all five; now Netzawar (force 1) gets island 1 and the "
                         "other four refuse. Entities served on field entry "
                         "(--npc/--monster) follow the player into whatever field "
                         "they DO enter, so testing props are not lost with them.")
    ap.add_argument("--world-prefix", type=int, default=4,
                    help="bytes to insert between the length and the id on THIS "
                         "connection. The transport reads the id at cursor + "
                         "[conn+0x1ca], which the relay dial (connect mode 2) "
                         "sets to 4 where the lobby (mode 1) leaves it 0. Use 0 "
                         "to send lobby-shaped frames.")
    ap.add_argument("--push-enter-area", action="store_true",
                    help="restore the pre-2026-08-19 behaviour of PUSHING "
                         "0x1000 MSG_ENTER_AREA_OK right after the group detail, "
                         "instead of waiting for the client's 0x2000 "
                         "MSG_ENTER_AREA and answering it. The disassembly says "
                         "the push cannot work -- the 0x1000 handler requires "
                         "[player+0x295]==0xFF, which only the client's own "
                         "request driver (0x04ff6a30) sets -- so this exists "
                         "only for an A/B run against the old capture.")
    ap.add_argument("--war", default="offensive",
                    choices=["none", "offensive", "defensive"],
                    help="push a START_WAR notify after the area is set, to arm "
                         "the war-prepare window ([screen+0x62]). 'offensive' "
                         "(id 0x1018) arms without driving a scene transition and "
                         "is the safe default; 'defensive' (0x1019) acts on the "
                         "four --war-fields via 0x5111650/0x5111010 -- a war-timer "
                         "countdown, NOT a scene transition, so only use it with "
                         "real values; 'none' leaves the map as-is.")
    ap.add_argument("--war-fields", default="0,0,0,0",
                    help="the four big-endian u32s the START_WAR handler reads "
                         "(0x0511843b). u32_1/u32_2 -> the field manager's array; "
                         "u32_3/u32_4 -> the war-view object (and the defensive "
                         "transition args). Meanings unverified; arming does not "
                         "depend on them, so 0,0,0,0 is fine for OFFENSIVE.")
    ap.add_argument("--proclamation", default="on", choices=["on", "off"],
                    help="answer the war-prep window's 1-second 0x2084 poll "
                         "with 0x1127 MSG_GET_INFO_OF_PROCLAMATION_OF_WAR_OK. "
                         "'off' restores the pre-2026-08-24 silence for an A/B.")
    ap.add_argument("--item-heal", type=int, default=0, metavar="N",
                    help="with --item-effects flat: how much HP using ANY item "
                         "restores (CHOSEN, uniform). Ignored under the "
                         "default --item-effects table.")
    ap.add_argument("--item-effects", default="table", choices=["table", "flat"],
                    help="what using an item does. 'table' (2026-09-12): the "
                         "client's own EFFECT_DATA row behind the item's use "
                         "skill -- bread 50 HP, cheese 90, steak 150 at once; "
                         "regen potions 25/48/100 HP every 4 s for 32 s; power "
                         "pots 5/10/15 Pw every 4 s for 60 s; a weaker potion "
                         "is refused while a stronger one runs. 'flat' = "
                         "--item-heal for every item. HP needs --monster-attack "
                         "on, which owns it.")
    ap.add_argument("--exp-display", default="on", choices=["on", "off"],
                    help="write the character's EXP into the per-class dword "
                         "at [unit+0x9F8 + class*4], which is the FIRST number "
                         "the Status screen's 'Exp %%d/%%d' draws (0x050e139d). "
                         "It goes through the class-level byte array because "
                         "that is the only 0x1075 bit that reaches those "
                         "bytes. 'off' leaves the numerator whatever the unit "
                         "was built with.")
    ap.add_argument("--monster-attack", default="off", choices=["on", "off"],
                    help="let monsters hit back: any live monster within "
                         "--monster-range of the player's own reported "
                         "position swings every --monster-interval seconds for "
                         "--monster-damage. The wire is 0x2024 maskA bit 0x4, "
                         "which is a STORE of the NEW hp (0x04ff3d2a) -- the "
                         "client draws old-minus-new as the damage number. At "
                         "0 the player is marked DEAD (CONDITION 0x800000) and "
                         "revived --revive-secs later, unconditionally.")
    ap.add_argument("--monster-damage", type=int, default=60, metavar="N",
                    help="damage per swing from the WEAKEST monster in the "
                         "game (level 1, attack 92). CHOSEN: no damage formula "
                         "is traced. 60 against --player-hp 1000 is ~17 swings "
                         "-- the same lethality 12 had against the old 200. "
                         "With --damage-model table every other monster scales "
                         "up from this by its own shipped ATTACK column.")
    ap.add_argument("--damage-model", default="table", choices=["table", "flat"],
                    help="how hard a monster hits. `table` (default since "
                         "2026-09-09) scales --monster-damage by the monster's "
                         "own ATTACK out of dat.pak's fet_npc_type, so a level "
                         "53 Duke Orc (attack 630) hits about seven times as "
                         "hard as a level 1 Venomous (92) instead of exactly "
                         "as hard. `flat` is the old behaviour: one number for "
                         "every monster in the game. PARTIAL: STATIC ONLY -- the "
                         "RATIO is shipped, the SCALE is ours, and nothing has "
                         "been seen on a screen.")
    ap.add_argument("--armour-defence", default="on", choices=["on", "off"],
                    help="monster damage x (1 - 耐性/400), 耐性 = the sum of "
                         "the worn pieces' defence from fewiki's 2006 armour "
                         "tables (fe-armour-defence.tsv), capped at 400 "
                         "(2026-09-12). The formula is FEZ's (2009) -- no "
                         "RoD one survives; BEST EFFORT. 'off' = no armour.")
    ap.add_argument("--monster-timing", default="table", choices=["table", "flat"],
                    help="how far and how often a monster swings. 'table' "
                         "(2026-09-12): its own melee skill (fet_npc_type "
                         "slot 0) out of SKILL_DATA -- reach = +0xF8 (7.0 for "
                         "most), period = wind-up + active + recovery, the "
                         "client's own busy-until (0x0505F500; 2.2 s for most, "
                         "Duke Orc 2.7). 'flat' = --monster-range / "
                         "--monster-interval for every monster.")
    ap.add_argument("--monster-reach-y", type=float, default=8.0, metavar="U",
                    help="how far above or below a monster may be and still "
                         "reach the player, world units. KEY: --monster-range is "
                         "measured HORIZONTALLY (y dropped), so without this a "
                         "monster on a cliff swings at someone it is forty "
                         "units above while their own cast -- gated by the "
                         "CLIENT's real 3D reach -- cannot answer. Found live "
                         "2026-09-12. 0 = the old behaviour, no height limit")
    ap.add_argument("--monster-range", type=float, default=8.0, metavar="U",
                    help="how close a monster has to be to swing, in world "
                         "units, measured on X/Z only. CHOSEN.")
    ap.add_argument("--monster-interval", type=float, default=2.0, metavar="S",
                    help="seconds between one monster's swings. CHOSEN.")
    ap.add_argument("--player-hp", type=int, default=1000, metavar="N",
                    help="the player's maximum HP for the server's own "
                         "bookkeeping. 1000 (since 2026-09-11; was 200) is "
                         "2006's flat HP at every level and the class table's "
                         "own first base value. nothing here changes +0x49a "
                         "(felobby's --char-hp serves that), so keep the two "
                         "equal or the bar looks wrong.")
    ap.add_argument("--respawn-wait", default=death.DEFAULT_RESPAWN_WAIT,
                    metavar="BASE:STEP:CAP|off",
                    help="RoD's respawn wait, in seconds: a death sets the "
                         "client's own Return to Base countdown (0x2024 bit "
                         "0x1000000 -> [player+0x1384]) to BASE, +STEP for "
                         "each further death in the same area (a new area, "
                         "or 10 min without dying, starts over), at most CAP; "
                         "the press starts it and the revive follows it. "
                         "15 s is SOURCED (fewiki Guide/メモ: 約15秒, longer "
                         "for repeated deaths in one area); STEP and CAP are "
                         "CHOSEN. off = the press revives at once")
    ap.add_argument("--revive-secs", type=float, default=60.0, metavar="S",
                    help="the SAFETY NET for a dead player who never presses "
                         "Return to Base. Since 2026-09-12 a death waits for "
                         "that button (0x2015 -> return_to_base), which is "
                         "the way back; this only guarantees a client that "
                         "cannot send it is never wedged. Was 8, which beat "
                         "the button every time -- players never needed "
                         "to press it.")
    ap.add_argument("--hunt-death-return", default="arrival",
                    choices=["arrival", "place"],
                    help="where a player dead OUTSIDE a war revives (Return "
                         "to Base, 0x2015, or the --revive-secs safety net). "
                         "'arrival' (the default, the design choice of "
                         "2026-09-12) warps them by a same-field 0x1166 to "
                         "the field's own arrival point -- at peace the castle "
                         "side, where anyone walking in lands -- instead of "
                         "beside whatever killed them. No source says where a "
                         "hunting death revives. 'place' is the old revive "
                         "where they fell. War deaths are --war-death-return's.")
    ap.add_argument("--war-death-return", default="base",
                    choices=["base", "place"],
                    help="where a player dead in a PREP/WAR field revives. "
                         "`base` (default, 2006: 'at 0 HP you are returned to "
                         "your base, keep or castle' [SE flow08]) warps them "
                         "to their side's arrival point with a same-field "
                         "0x1166; `place` is the old revive where they fell. "
                         "Outside a war it is always in place (no source says "
                         "otherwise -- ours).")
    ap.add_argument("--spawn-protect-secs", type=float, default=15.0,
                    metavar="S",
                    help="protection after a return to base or an area move: "
                         "monster hits do no damage for S seconds, and any "
                         "action other than moving (a hit, a cast, building, "
                         "talking to an NPC, using an item...) ends it at "
                         "once [SE interface05: 15 s]. 0 = off.")
    ap.add_argument("--spawn-protect-flag", default="off", choices=["on", "off"],
                    help="also mirror the protection into CONDITION bit "
                         "0x1000, which the client's own table names "
                         "INVINCIBLE. WARNING: UNPROVEN: what the client does with "
                         "it is unmeasured, and the protection does not need "
                         "it. Off by default.")
    ap.add_argument("--death-exp-pct", type=float,
                    default=death.DEFAULT_DEATH_EXP_PCT, metavar="P",
                    help="percent of NEXT (the EXP the current level needs) "
                         "lost on a HUNTING death, out of the progress into "
                         "the level and never below 0 of it -- nobody loses a "
                         "level (whether RoD de-levelled is unknown). RoD: 10 "
                         "(fewiki Guide/メモ 2006-05-26 「Nextの10%%の経験値」). "
                         "Never in a PREP/WAR/TRUCE field. 0 = off.")
    ap.add_argument("--death-exp-of", default=death.DEFAULT_DEATH_EXP_OF,
                    choices=["next", "progress"],
                    help="what --death-exp-pct is a percent of: next (RoD, "
                         "default) or the progress into the level (the "
                         "2026-09-11 first pass)")
    ap.add_argument("--death-gold-pct", type=float,
                    default=death.DEFAULT_DEATH_GOLD_PCT, metavar="P",
                    help="percent of the gold CARRIED lost on a HUNTING death "
                         "(the bank is safe). RoD: 20 (fewiki Guide/メモ: "
                         "「所持金の20%%を失う」; FEZ-early FFSKY says 10). "
                         "0 = off.")
    ap.add_argument("--death-war-crystals", type=int,
                    default=death.DEFAULT_DEATH_WAR_CRYSTALS, metavar="N",
                    help="crystals lost on a death in a PREP/WAR field, "
                         "floored at 0 -- RoD: 3 (fewiki WAR/クリスタル). A war "
                         "death costs no EXP or gold. 0 = off.")
    ap.add_argument("--peace-arrival", default="castle",
                    choices=["castle", "holder"],
                    help="where a player arrives in a war field at PEACE. "
                         "`castle` (default): the castle side whoever holds "
                         "it [early-FEZ atwiki 251 / FFSKY; SE is silent]. "
                         "`holder`: the old rule -- castle side only when "
                         "your nation holds the field, else the keep side.")
    ap.add_argument("--motion-refresh", default="on", choices=["on", "off"],
                    help="after the worn items are re-sent, restate them in a "
                         "0x2004 so the client picks the weapon's MOTION SET "
                         "(0x0503c70e -> 0x0506aa30). Without it the cast "
                         "plays as a T-pose, because the only other caller is "
                         "the entity decoder's tail, which also sets STANCE "
                         "and freezes the character.")
    ap.add_argument("--item-durability", default="-1:100", metavar="MAX:CUR",
                    help="group-4 bits 0x4/0x8 of every item record: +0x4aa "
                         "durability maximum and +0x4ac current. The cast "
                         "ladder's rung after gate 4 (0x0504b8c0) refuses a "
                         "held weapon with current <= 0 unless max is -1 -- "
                         "the third emitter of 'Skill conditions not met.', "
                         "found 2026-09-08 after the other two were measured "
                         "passing. -1:100 (default) is infinite; a real max "
                         "makes the client print its own wear warnings at "
                         "20%% and nothing here decrements it yet.")
    ap.add_argument("--worn-gear-record", default="off", choices=["on", "off"],
                    help="carry the stored worn gear in the self 0x1006's "
                         "mask3. OFF: live 2026-09-08 runs 2/3, the worn-gear "
                         "tail 0x0503aecb sets OBJSTATUS STANCE (0x40000) on "
                         "the self when the record names a weapon, nothing ends "
                         "it, and the character cannot move or cast. The field "
                         "dresses through the 0x1000 bag markers (--field-equip) "
                         "and the 0x107A replay after the first clock sample.")
    ap.add_argument("--unit-physics", default="push", choices=["table", "push"],
                    help="'push' (default): the 0x2024 --unit-speed and "
                         "--jump-phys pushes. 'table' was the default for runs "
                         "6-9 of 2026-09-08 on the theory that the class copy "
                         "(0x0506a5e0, rec+0x44/+0x48/+0x4c -> +0x4d4/+0x4dc/"
                         "+0x4e4) supplies them -- LIVE it does not: run 8 "
                         "moved in some directions only, run 9 ran in place, "
                         "the 08-25 server-authoritative-speed signature. "
                         "The run-5 'moves so much slower' under the push is "
                         "still unexplained; measure it, do not turn the push "
                         "off again.")
    ap.add_argument("--add-self", default="off", choices=["on", "off", "client"],
                    help="'client' (2026-09-08): send NO self record -- the unit "
                         "the client builds from the lobby record STANDS, and "
                         "the stat block (--add-stats bits 0/7/8) rides 0x2024 "
                         "maskA 0x1/0x80/0x100 instead. Why: the type-0 arm's "
                         "lookup HIT is a tear-down-and-rebuild ('Chara 1 "
                         "already exists'); the rebuilt unit is dressed only "
                         "from mask3, its worn items are the OLD unit's objects "
                         "(destroyed with it -> not in the item manager -> gate "
                         "4 refuses), and the decoder tail sets STANCE with no "
                         "action to end it (frozen, cast refused silently). The "
                         "client's own builder sets USESKILLRDY without STANCE. "
                         "'on' = send 0x1006 MSG_ADD for the player's own avatar "
                         "before 0x100E, carrying the NAME and APPEARANCE read "
                         "back out of felobby's stored character -- the client's "
                         "own path for building the model, and the only thing "
                         "that can fill unit+0x84, whose empty slot 0 is the "
                         "null deref that kills the client ON MOVEMENT. OFF by "
                         "default: the record is decoded, but that it ends in a "
                         "non-null slot is NOT yet measured. The self-check is "
                         "the client's own 'MSG_ADD> Avatar( <name> )' log line.")
    ap.add_argument("--gold", type=lambda v: int(v, 0), default=None,
                    help="the player's GOLD -- 0x2024 maskA bit 0x200000 "
                         "-> [unit+0x4ec], the field the Status screen "
                         "draws under the label at 0x052e29d8 (format "
                         "'%%lu'). Named live by sentinel and confirmed "
                         "against that table. UNSET SENDS NOTHING, because "
                         "zero is a real value and 'nobody served it' must "
                         "not look like 'you are broke'. Since 2026-09-04 this "
                         "is the SEED for a character with no stored gold; "
                         "the wire carries the character's stored value after "
                         "that (see _seeded_value). \u26a0 Still nothing debits "
                         "it -- per-character, not yet a balance anything "
                         "spends.")
    ap.add_argument("--kill-score", default="exp", metavar="exp|N|off",
                    help="what a kill adds to the character's TOTAL SCORE "
                         "(+0x500), which is what dat.pak's fet_fame_rank "
                         "ladder is keyed by: Beginner at 0, Apprentice at "
                         "10k, ... Commander at 12M, and those names ship in "
                         "ENGLISH even in the JP build. `exp` (default) adds "
                         "the monster's own EXP reward, a number N adds N, "
                         "`off` leaves the score alone. PARTIAL: STATIC ONLY: the "
                         "ladder is keyed by 'a score' and +0x500 is 'the "
                         "total score' -- that the client draws its RANK TITLE "
                         "from the two together is a reading, and the feworld "
                         "log prints the rank it should read so one look at "
                         "the Status screen settles it. Retail earned score in "
                         "the WAR, so per-kill scoring is our rule for making "
                         "the ladder reachable at all.")
    ap.add_argument("--total-score", type=lambda v: int(v, 0), default=None,
                    help="the player's TOTAL SCORE -- 0x2024 maskB bit 0x1 "
                         "-> [unit+0x500], drawn by the Status window at "
                         "0x050e13ca against the label at 0x052e29a8. Note "
                         "it rides maskB, so its field goes AFTER every "
                         "maskA field on the wire. Unset sends nothing.")
    ap.add_argument("--crystal", type=int, default=None, metavar="N",
                    help="CRYSTAL, the Status-screen row between Skill Points "
                         "and GOLD. NOT a 0x2024 bit: it is [unit+0x8ac] and "
                         "the only message that writes it is 0x2035, mask bit "
                         "1 (0x04fe4e50). Found from the draw site backwards "
                         "(0x050e142e). WARNING: the client plays its pickup effect "
                         "when the value INCREASES, so this is an "
                         "authoritative total; nothing debits it, so do not "
                         "build the summon economy on it. Unset sends nothing, "
                         "which is not the same as 0. Seeds the character's "
                         "stored crystal once; the store is served after that. "
                         "`!crystal N` in --gmcmd-file serves N once WITHOUT "
                         "storing it (a probe).")
    ap.add_argument("--ring", type=lambda v: int(v, 0), default=None,
                    help="the player's RING -- 0x2024 maskA bit 0x4000000 "
                         "-> [unit+0x4f0], label at 0x052e29e8. Same "
                         "caveats as --gold.")
    ap.add_argument("--stat-probe", default="off", choices=["on", "off"],
                    help="send one 0x2024 carrying DISTINCT SENTINELS "
                         "(4001..4010) in the ten stat-block fields that "
                         "are zero AND unnamed, to name them off the "
                         "screen in one look -- the trick that settled "
                         "0x303E. The last digit identifies the offset. "
                         "\u26a0 PUTS DELIBERATELY WRONG NUMBERS ON SCREEN; "
                         "off by default and not to be left running. HP, "
                         "Pw, Skill Points and our own walk speed are "
                         "excluded -- they already have writers.")
    ap.add_argument("--stat-probe2", default="off", choices=["on", "off"],
                    help="send one 0x2025 (the SECOND COLUMN of the stat "
                         "block: same setters, index 1) carrying DISTINCT "
                         "SENTINELS 5001..5012 into +0x49c..+0x4d0. HP and Pw "
                         "BONUS slots are included because the gauge draws "
                         "max = base + bonus, which is the on-screen proof. "
                         "\u26a0 deliberately wrong numbers; off by default.")
    ap.add_argument("--jump-phys", default=None, metavar="V:G",
                    type=lambda v: tuple(float(x) for x in v.split(":")),
                    help="0x2024 maskA bits 0x40000|0x80000: f32 +0x4dc (jump "
                         "vertical launch, x4.7 u/s) and f32 +0x4e4 (jump "
                         "gravity, x24.5 u/s^2), both ZERO on our character, "
                         "which is why the SmartJump is a flat weightless "
                         "dash. CHOSEN values, e.g. 1.0:1.0 -> 0.38 s flight, "
                         "0.45 u apex -- but LIVE 2026-09-08 run 4, 1.5:1.0 read as "
                         "high, jumps in place, SmartJumpMessage N ~2700 (a "
                         "~2 s flight, not 0.58), so the launch term in this "
                         "model is low by ~3x; prod runs 0.6:1.0. Read "
                         "SmartJumpMessage N in the client "
                         "log: the 300+400 ms wind-up/landing are immediates "
                         "in 0x05152670 and will not move. Unset sends nothing.")
    ap.add_argument("--npc", action="append", default=None,
                    metavar="NAME[:X:Y:Z]",
                    help="spawn an extra avatar in the field, repeatable. "
                         "It rides the same 0x1006 record --add-self uses, "
                         "and the type-0 ctor 0x050679c0 stamps "
                         "[obj+0x24]=2 -- the kind 0x2023 action 0 will "
                         "move, unlike the player (kind 1). Appearance is "
                         "copied from the stored character so the model is "
                         "one the client is known to build. Unset = none.")
    ap.add_argument("--npc-walk", default=None, metavar="SPEED:STEP",
                    type=lambda v: tuple(float(x) for x in v.split(":")),
                    help="walk each --npc with 0x2023 action 0, driven off "
                         "the client's own 0x2023 heartbeat. SPEED goes to "
                         "the first speed component; STEP is how far the "
                         "target advances on +Z per push. The tick pair is "
                         "lifted VERBATIM from the client's frame, because "
                         "its timebase is unmeasured and reusing the "
                         "client's own clock beats inventing one. \u26a0 The "
                         "position is dead-reckoned server-side and will "
                         "drift from what is drawn -- a probe for 'does he "
                         "move', not a simulation. Unset = the NPC stands "
                         "still.")
    ap.add_argument("--npc-base", type=lambda v: int(v, 0), default=1000,
                    help="object id for the first --npc; each subsequent "
                         "one takes the next. Must not collide with a real "
                         "charid -- the id is what 0x1006 looks up and what "
                         "a 0x2023 record targets.")
    ap.add_argument("--unit-speed", type=float, default=None,
                    help="push 0x2024 with maskA bit 0x20000 -- ONE f32, "
                         "stored RAW and unscaled at [unit+0x4d4] by "
                         "0x04ff4670. This is the setter 0x2023 could never "
                         "reach: it gates on [unit+0x20] (measured 3 on the "
                         "player) rather than [unit+0x24] (measured 1, which "
                         "is why every 0x2023 action-0 record was discarded). "
                         "Unset = send nothing. \U0001f7e2 WHAT A VALUE MEANS, "
                         "static 2026-09-09: it is a MULTIPLIER. Every reader "
                         "of the pair computes ([+0x4d8] + [+0x4d4]) x row[0] "
                         "of the movement table, and with no metamorphosis "
                         "row[0] is 6.0 IN A CAPITAL and 4.0 ON A WAR FIELD "
                         "(the client picks the row with a predicate that is "
                         "literally 'am I in a capital' -- 0x04FF7EF0 returns "
                         "true for exactly 21/39/57/62/78/91..95). So 1.0 is "
                         "the game's own walk, and prod's 1.3 is 7.8 u/s in "
                         "town and 5.2 afield. \u26a0 ONE BIT ONLY: the other 24 "
                         "setters have unpinned widths and a bit with nothing "
                         "behind it desynchronises the record.")
    ap.add_argument("--unit-speed-repeat", default="on",
                    choices=["on", "off"],
                    help="re-push --unit-speed on every inbound 0x2023 "
                         "(~2.5/s, the only regular tick we get in the "
                         "field). 'off' sends it once at field entry, which "
                         "is the A/B for whether the field decays.")
    ap.add_argument("--move-authority", default="off",
                    choices=["off", "echo", "state1", "walk"],
                    help="answer the client's own 0x2023 by sending the "
                         "SAME 28 bytes back. 0x2023 runs both ways and the "
                         "receive side (action 0, arm 0x04fea890) is what "
                         "writes the speed pair at unit+0x4d4/+0x4d8 and "
                         "drives the movement controller -- felive measured "
                         "both halves at 0.0 while the walk animation "
                         "played. The two directions are the same record in "
                         "the same units (the scales are exact inverses), so "
                         "the echo invents nothing. 'state1' additionally "
                         "forces the u16 at offset 8 to 1, the flag whose "
                         "zero SKIPS the controller (0x04feadd8) -- that one "
                         "IS an invented value, hence a separate mode. OFF "
                         "by default: this is a probe for whether the "
                         "receive side moves the player, not authority. "
                         "\U0001f534 'echo' IS PROVEN INERT (live 2026-08-25): the "
                         "client reports speed (0,0) and state 0 because its "
                         "outbound builder reads the same +0x4d4/+0x4d8 the "
                         "inbound arm writes, so the loop has nothing driving "
                         "it and relaying it relays zeros. 'walk' INVENTS a "
                         "speed and a target ahead of the player -- see its "
                         "note -- to test the mover without the intent "
                         "channel.")
    ap.add_argument("--move-speed", type=float, default=1.0,
                    help="--move-authority walk: the value written to the "
                         "FIRST speed component (unit+0x4d4), before the "
                         "client's x0.001. Which of the two components is "
                         "which is UNMEASURED -- 0x0515bce0 only ever sums "
                         "them -- so this moves one field and leaves the "
                         "other as the client sent it.")
    ap.add_argument("--move-dist", type=float, default=5.0,
                    help="--move-authority walk: how far AHEAD of the "
                         "player's own reported position to put the target, "
                         "on +Z. The axis is a guess (it is the facing we "
                         "serve); movement along the wrong one still answers "
                         "the question this probe asks.")
    ap.add_argument("--draw-self", default="off",
                    help="push the camera/draw-self switch after the add "
                         "stream. 'off' (default) sends nothing; '1'/'0' "
                         "send 0x1170 with that u16; 'on'/'hide' send the "
                         "header-only 0x1181/0x1182. [0x52ffad0]+0x1d is the "
                         "byte /stealth calls 'Draw self' and the only gate "
                         "on the player unit's own per-frame block "
                         "(0x0507e7f0). POLARITY UNMEASURED -- test with "
                         "/stealth in the game's own chat first.")
    ap.add_argument("--add-submask", type=lambda v: int(v, 0),
                    default=0x82FF,
                    help="the mask1-bit0 sub-record mask. 0xFF = name + all "
                         "seven appearance fields (bits 0..7 -> unit+0x389, "
                         "+0x3aa sex, +0x3ab, +0x3b0, +0x3b4, +0x3b6, +0x3b7, "
                         "+0x3b8), every one of them a field felobby already "
                         "stores. KEY: 0x200 IS NOW ON BY DEFAULT (0x2FF): it is "
                         "+0x3bc, the NATION, and felive measured it live as "
                         "0x7FFFFFFF -- the client's own 'no nation' "
                         "sentinel -- while the stored character has force 1. "
                         "A client with no nation is why it then sends "
                         "'army choice req sent : 0' on 0x2018 and why "
                         "+0x94 reads 0. Bit 0x100 (+0x94, ARMY, one byte) is "
                         "in the table but LEFT OFF: the client picks its own "
                         "army and overriding that is unmeasured. Every width "
                         "here is pinned to a reader's stream advance -- a "
                         "wrong one desynchronises the record, it does not "
                         "just draw a wrong number.")
    ap.add_argument("--add-parts", default="",
                    help="optional mask3 parts as SLOT:PARTID[:PARAM],... with "
                         "SLOT in 0..12. Equipment layered ON TOP of the base "
                         "model; a partId that misses 0x504cb40(id, 0xbb8) fails "
                         "SILENTLY. Leave empty until the base model works.")
    ap.add_argument("--add-type", type=lambda v: int(v, 0), default=0,
                    help="the u8 entity type in the 0x1006 record. 0 = Avatar, "
                         "the arm that dresses a character (jump table "
                         "0x0503bb0c has 9 arms; 3..6 are the no-op default).")
    ap.add_argument("--add-mask1", type=lambda v: int(v, 0), default=0x1,
                    help="0x1006 record mask1. bit0 = the NAME+APPEARANCE "
                         "sub-record (0x5079d60) -- default ON, because it is "
                         "the whole point: it is what feeds unit+0x3aa, the byte "
                         "the model-path builder at 0x0507a04f indexes to choose "
                         "Model\\<dir>\\<prefix>face%%02d_%%d.mdl. bit1 "
                         "(0x507a990, two u16s at unit+0x498/+0x49a) stays off; "
                         "it is undecoded.")
    ap.add_argument("--add-stats", default="0:5",
                    type=lambda v: {int(k, 0): probes._stat_num(x)
                                    for k, _, x in (q.partition(":")
                                                    for q in v.split(",") if q)},
                    help="BIT:VALUE pairs for the 0x1006 record's STAT block "
                         "(mask1 bit 1, applier 0x0507a990). Bit 0 is SKILL "
                         "POINTS at [unit+0x498] -- the exact i16 the Status "
                         "window draws (0x050e13fc), confirmed live with "
                         "`felive --stats`. WARNING: BIT 0 IS A BUDGET, NOT A "
                         "BALANCE: what goes on the wire is this value MINUS "
                         "the skills already stored, because serving it flat "
                         "would hand the player a fresh five at every field "
                         "entry, forever. Empty string sends no stat block at "
                         "all. WARNING: mask1 bit 1 is derived from THESE VALUES and "
                         "must never be set by hand: the mask promises how many "
                         "i16 follow, so a bit with nothing behind it "
                         "desynchronises the record. Bits 1-4 read 200/200/100/"
                         "100 live against a screen showing HP 200/200 Pw "
                         "100/100 -- a strong inference, not a measurement, so "
                         "they are not in the default.")
    ap.add_argument("--add-mask2", type=lambda v: int(v, 0), default=0x5,
                    help="0x1006 record mask2. bit0 = position (3 f32 -> "
                         "unit+0x1c4, taken from --spawn-pos), bit2 = facing "
                         "(3 f32 -> unit+0x1e4, from --spawn-dir). Default 0x5 "
                         "= both.")
    ap.add_argument("--decide-country", default="ok",
                    choices=["ok", "ng", "off"],
                    help="answer 0x2018 MSG_DECIDE_COUNTRY_REQUEST -- the army "
                         "choice the client sends immediately after "
                         "'PLAY_START done.' and then waits on. 'ok' sends "
                         "0x1020 (header-only; its arm reads no body and clears "
                         "scene pending bit 0x80), 'ng' sends 0x1021 (which "
                         "makes the client re-ask -- diagnostic only), 'off' "
                         "restores the old silence for an A/B.")
    ap.add_argument("--war-start", default="off",
                    choices=["on", "off", "deadline"],
                    help="after the 0x1018/0x1019 war notify, push 0x1015 -- the "
                         "message whose arm (0x0511850f) is the ONLY thing that "
                         "clears scene pending bit 0x80, the bit our own war "
                         "notify arms and which felive measured still set in "
                         "the field. It also moves the war-view phase [+0x70] "
                         "from 0 to 2 and installs a fresh deadline. OFF by "
                         "default: the pending bit is measured, the MEANING of "
                         "the phase advance is not. A/B it and read "
                         "[scene+0x528] after. 'deadline' sends it when "
                         "--war-deadline-ms has elapsed instead of "
                         "immediately -- which is the retail SHAPE, because the "
                         "client drops its own countdown-expiry event "
                         "(0x05117d37) and therefore never starts a war by "
                         "itself. 'on' fires in the same burst as the notify "
                         "and skips the countdown entirely.")
    ap.add_argument("--war-start-fields", default="0,0", metavar="HI,LO",
                    help="the two big-endian u32s in 0x1015 -- the same deadline "
                         "pair as the last two --war-fields, in the same wire "
                         "order: HIGH dword FIRST, then LOW (see war_notify's "
                         "docstring for the stack that proves it; the metavar "
                         "read LO,HI until 2026-08-26 and was wrong). A deadline "
                         "under ~49 days has a zero high word, so the pair is "
                         "0,<ms>. Prefer --war-deadline-ms, which cannot be got "
                         "round the wrong way. 0,0 reproduces the "
                         "already-expired deadline 0x1018 installs.")
    ap.add_argument("--war-deadline-ms", type=int, default=0, metavar="MS",
                    help="the 0x1015 war deadline as ONE number of "
                         "milliseconds, split into the wire's HIGH/LOW pair "
                         "here rather than by hand. Overrides "
                         "--war-start-fields when non-zero. THE UNITS ARE "
                         "MEASURED: 0x5111010 divides the remaining time by the "
                         "f32 at 0x528b738, which is 3600000.0 -- one hour in "
                         "ms -- and that is the arc the needle sweeps, so a "
                         "value ABOVE 3600000 drives the fraction negative. "
                         "WARNING: IT IS NOT A WALL CLOCK. The client's `now` is "
                         "[0x5336ec0] + the session timer, and that base is "
                         "written ONLY by message 0x1148, which we have never "
                         "sent -- so it is 0 and `now` is ms since the "
                         "field-load timer reset (0x1012 / 0x300C on the way "
                         "in). 1800000 = half an hour after the field started "
                         "loading, which leaves a visibly-moving needle at "
                         "roughly half sweep. Read the result with "
                         "`felive --war --watch --interval 1`: a moving frame "
                         "counter with a frozen needle is EXPIRED, a frozen "
                         "counter is a SHUT GATE.")
    # WARNING: DEFAULT FLIPPED BACK TO `off` 2026-09-12, THE SAME DAY IT WENT ON.
    # Since --world-clock on shipped, every field entry parks the
    # client in mode 0xb SUBSTATE 3 -- the fade -- instead of reaching
    # substate 4, the steady gameplay loop. Measured with felive on a
    # live client: `substates = 0 3 0 0` while standing still,
    # `0 4 0 0` after any input. In substate 3 nothing per-frame updates, so
    # the sky is the previous scene's, the HUD gauges draw empty (the unit
    # itself reads HP 1000/1000, Pw 100/100 -- the values are fine) and the
    # body and the worn armour are drawn unposed, which is the "base outfit
    # clipping through the equipment" report.
    #
    # Substate 3 advances only when the fade's own flag is set (0x05039630 ->
    # `[0x5338154]+0x54`), and that flag is set at 0x05039163 when the fade's
    # ELAPSED (`+0x4c`, accumulated per frame at 0x0503949b) exceeds its
    # DURATION (`+0x48`). 0x1148 moves the client's clock base by ~1.79e12 ms
    # (server epoch), at field entry, every entry.
    #
    # WARNING: THE CAUSAL LINK IS NOT PROVED -- the fade's per-frame delta has not
    # been traced to this clock. What IS established: the symptom is the
    # substate-3 park, it appeared the day this default went on, and the
    # 0x1148 note in this file already warned that "the base is harmless to
    # every other consumer" was NOT measured, listing five other readers.
    # Turning it back off is the cheap half of that test and costs only the
    # in-game time display. If the park survives `off`, the clock is
    # exonerated and the next step is tracing the delta at 0x0503949b --
    # do NOT flip this back and forth beyond that one run.
    # KEY: 2026-09-12 (evening): the per-ENTRY send is gone; 'session' sends
    # 0x1148 ONCE per connection, --world-clock-after-ms after the field is
    # ready, never while a war countdown in the client's units is armed.
    # VERIFIED: DEFAULT 'session' SINCE 2026-09-12 ~21:20Z, on a live measurement
    # (felive --scene, player standing still): a `!clock` base landing in
    # steady state held `0 4 0 0` throughout and was accepted, and a field
    # change WITH the base set reached `0 4` in 2 s with no input -- so the
    # park was the base JUMPING DURING an entry, which 'session' never does.
    # The flip was approved. Not yet measured: two players, one synced
    # (fepresence.rebase_clock maps the full 64 bits; tested offline).
    # 'on' is kept as a spelling of 'session' so an old argv cannot
    # crash-loop the container.
    # PARTIAL: DEFAULT 'connect' SINCE 2026-09-12 ~21:45Z (design call: "we should just
    # have it send the clock the second you connect ... the clock shows on the
    # map screen too"). Under 'session' the player arrived at NIGHT (the unset
    # clock) and the sky jumped to day ~12 s later. 'connect' sends with the
    # 0x302B login OK, before any field loads; the first sample verifies it
    # and 'session' is the fallback. Not yet seen live.
    ap.add_argument("--world-clock", default="connect",
                    choices=("off", "connect", "session", "on"),
                    help="send 0x1148 to set the client's clock base. KEY: "
                         "that clock IS the in-game WORLD TIME: the getter "
                         "0x04FFEF10 returns the base plus the client's own "
                         "elapsed ms, and -1 ('no time') until the base is "
                         "set, which only 0x1148 does. The world-time object "
                         "divides it for the year/day/hour/minute the client "
                         "logs as `fetime=`. 'session' (= 'on'): ONCE per "
                         "connection, --world-clock-after-ms after the field "
                         "is ready, deferred while a war countdown is armed. "
                         "The default. 'off': never, except `!clock`.")
    ap.add_argument("--world-clock-after-ms", type=int, default=10000,
                    metavar="MS",
                    help="under --world-clock session, how long after the "
                         "field-ready batch the 0x1148 waits -- the base must "
                         "never land during the entry fade (substate 3), "
                         "which is what the per-entry send did.")
    ap.add_argument("--clock-epoch-ms", type=int, default=0, metavar="MS",
                    help="offset added to the 0x1148 base. The base is server "
                         "epoch ms, which every client shares (so they agree) "
                         "but which makes the in-game YEAR read about 2043 -- "
                         "FE's own calendar epoch is not written down in any "
                         "source we have. This is where it goes if one turns up")
    ap.add_argument("--war-clock", default="telemetry",
                    choices=["off", "telemetry", "sync"],
                    help="how war deadlines relate to the CLIENT's clock. "
                         "'telemetry' (default, CHOSEN 2026-08-27): read the "
                         "client's own 64-bit ms clock off bytes 4..12 of "
                         "every 28-byte 0x2023 it sends (builder 0x0515dd85 "
                         "writes [u32 0][u32 HI][u32 LO] straight from "
                         "0x4ffef10) and make every deadline 'client now + N', "
                         "judging expiry against the same clock; sends no new "
                         "message. 'sync': additionally push 0x1148 once "
                         "(arm 0x05055edb, two u32s HIGH first -> "
                         "[0x5336ec4]:[0x5336ec0]) with base = server epoch ms "
                         "- client elapsed, so the client's clock becomes wall "
                         "time; the next 0x2023 must show the jump. 'off': the "
                         "pre-08-27 behaviour -- N is ms after the client's "
                         "field-load timer reset and expiry is our own "
                         "monotonic() since the notify.")
    ap.add_argument("--war-cycle", default="off", choices=["off", "on"],
                    help="run the whole war on a timer: 0x1018 (phase 0, "
                         "--war-deadline-ms) -> 0x1015 (phase 2, "
                         "--war-length-ms) -> 0x1016 truce (phase 1, "
                         "--war-truce-ms) -> 0x1017 peace -> a fresh 0x1018. "
                         "Implies the 'deadline' shape of --war-start. OFF by "
                         "default: 0x1016/0x1017 have never been sent to a "
                         "client. `!war` forces the next step.")
    ap.add_argument("--war-length-ms", type=int, default=600000, metavar="MS",
                    help="--war-cycle: how long phase 2 lasts before the "
                         "truce. CHOSEN 600000 (10 min); the arc's full sweep "
                         "is 3600000 (0x528b738) so keep it under an hour. "
                         "VERIFIED: 2026-09-09, WHY THE TOP BAR LOOKS DEAD: the arc "
                         "gauge draws (3600000 - remaining) / 3600000 "
                         "(0x0511105F..0x05111065), a fraction of a FIXED "
                         "hour -- so a 300000 ms war starts its needle at "
                         "91.7%% of the way along and creeps through the last "
                         "twelfth. The reported 'top bar during combat that "
                         "doesn't seem to do anything' is that: it moves, "
                         "across 1/12 of its width. Set this near 3600000 for "
                         "a bar that sweeps end to end.")
    ap.add_argument("--war-truce-ms", type=int, default=120000, metavar="MS",
                    help="--war-cycle: how long the truce (phase 1, "
                         "休戦終了まで) lasts before 0x1017 and the next "
                         "declaration. CHOSEN 120000 (2 min).")
    ap.add_argument("--war-counts", default="0,1,64,64",
                    type=lambda v: tuple((list(int(x, 0) for x in v.split(","))
                                          + [0, 0, 0, 0])[:4]),
                    help="the four u16s of the 0x1127 body, IN WIRE ORDER: "
                         "attack_count, defense_count, attack_max, defense_max "
                         "(read order at 0x050581af, named by the client's own "
                         "log format 0x52d5fbc). They land in the field manager "
                         "at mgr+0x5c/0x5e (defense pair) and mgr+0x60/0x62 "
                         "(attack pair). The default says: nobody attacking, "
                         "the one player defending, 64 slots a side. Whether a "
                         "non-zero max opens the deploy UI is UNMEASURED -- "
                         "this is the knob for finding out.")
    ap.add_argument("--self-unit", default="on", choices=["on", "none"],
                    help="serve the player's OWN unit in the 0x1000 entity list "
                         "(one { u8 type, u32 id=charid, f32 x4 } record) so the "
                         "loaded-but-black field has a camera subject -- the FMO "
                         "self-unit pattern. 'none' sends an empty field.")
    ap.add_argument("--self-type", type=lambda v: int(v, 0), default=0,
                    help="the u8 unit type for the self-unit ([entity+0x18]); "
                         "which value means 'player' is unmeasured -- a knob.")
    ap.add_argument("--self-pos", default="0:0:0", metavar="X:Y:Z",
                    help="spawn position (f32 x,y,z) for the self-unit. WARNING: Y IS "
                         "AN ABSOLUTE HEIGHT, not an offset, and the terrain "
                         "varies ~25 units across one field: 0:0:0 lands at the "
                         "LOW point of group 1 under a cliff. Measure a real one "
                         "with `felive --avatar` (prints pos [+0x1c4]) while "
                         "standing where you want to arrive -- 2.16:24.76:-60.51 "
                         "was measured there 2026-09-04. The client ground-snaps "
                         "on entry when the query at 0x5000450 succeeds "
                         "([unit+0x2ac] bit 3), so a near-miss settles; a wild Y "
                         "does not.")
    ap.add_argument("--self-heading", type=float, default=0.0,
                    help="the 4th f32 in the entity record (heading/spare).")
    ap.add_argument("--add-complete", default="on", choices=["on", "off"],
                    help="push 0x100E MSG_ADD_COMPLETE after answering a "
                         "POST-ENTRY 0x4011 group-detail request. Its handler "
                         "(0x503a050) sets [scene+0x509]=1, the flag mode 0xb "
                         "substate 2 waits on -- without it the client parks on "
                         "the loading screen forever (measured live 2026-08-23, "
                         "the 210s hang). 'off' restores that for A/B.")
    ap.add_argument("--move", default="none", choices=["auto", "none"],
                    help="push 0xE00D MOVE_CHARACTER after answering the "
                         "client's 0x2000 with 0x1000. WARNING: DISPROVEN as the exit "
                         "fix (live 2026-08-23): it decodes and arms [scene+0x84] "
                         "but the client exits regardless -- the exit is "
                         "GameStart completing the map phase, not a missing "
                         "message. Default OFF; kept as a knob because 0xE00D "
                         "may belong to the unreached field/battle phase.")
    ap.add_argument("--move-pos", default="0:0:0", metavar="X:Y:Z",
                    help="the f32 position served in 0xE00D (-> scene+0x74..). "
                         "Units/origin unmeasured; mode 0xb walks/warps the "
                         "unit there when it is farther than the 0x525f5bc "
                         "radius, so keep it 0:0:0 until a live run says more.")
    ap.add_argument("--move-seq", type=lambda v: int(v, 0), default=1,
                    help="the leading u32 token in 0xE00D (-> scene+0x6c). "
                         "Echoed back in the client's 0xE00F NACK; semantics "
                         "beyond that unmeasured.")
    ap.add_argument("--probe-on-auth", action="store_true",
                    help="answer 0x20 (which needs no answer) with a minimal "
                         "enciphered frame, to find out whether ANY frame from us "
                         "is rejected on this connection or only the login reply")
    ap.add_argument("--capture-only", action="store_true",
                    help="decode and print but never reply, the behaviour this "
                         "service shipped with before 0x400F was understood")
    ap.add_argument("--unit-login-value", default="auto",
                    help="the u32 payload of 0x302B MSG_SERVER_UNIT_LOGIN_OK, "
                         "which its OK arm (0x050476d3) stores at "
                         "[0x533998c]+0x9e0 -- the SERVER UNIT's id. 'auto' "
                         "(default since 2026-08-24) sends the session charid, "
                         "so the server unit's id matches the entity id we "
                         "spawn for the player in 0x1000; they disagreed (0 vs "
                         "1) in every session before that. Pass an explicit "
                         "number (e.g. 0) to restore the old behaviour for an "
                         "A/B. WARNING: Whether [esp+0x10] in that arm is this payload "
                         "or the inner header's leading u32 is STILL UNSETTLED "
                         "-- both were 0 forever, so no live read could "
                         "separate them; this makes them differ so one field "
                         "read decides it.")

    ap.add_argument("--captures", type=int, default=0,
                    help="exit after this many connections; 0 = never")
    ap.add_argument("--life", type=int, default=0,
                    help="exit after this many seconds; 0 = never")
    ap.add_argument("--read-window", type=float, default=120.0,
                    help="hang up a connection idle this long. NOT optional -- a "
                         "bind that leaves FE waiting strands the player with no "
                         "way back to the Viewer.")
    ap.add_argument("--idle-tick-ms", type=int, default=250, metavar="MS",
                    help="run the pump chain this often even when the client "
                         "says nothing. 0 = the pre-2026-09-11 loop, which "
                         "blocked in recv until the client spoke -- so chat "
                         "relays, the revive, campaign phases and peer "
                         "movement all ran at the mercy of THIS client's "
                         "outbound rate. Measured cost: a player standing "
                         "still saw a walking peer update every 3-6 seconds.")
    ap.add_argument("--war-max-laps", type=int, default=0,
                    help="cap how many 0x1018 war notifies are pushed per lap "
                         "BEFORE field entry. 0 (default) = unbounded, which is "
                         "the behaviour every measurement so far ran against. "
                         "A brand-new character that never reaches field entry "
                         "re-arms [screen+0x62] with each one and re-polls "
                         "0x4011 at frame rate -- 3,185 in one prod session on "
                         "2026-08-24, black screen. Set 3 to test whether that "
                         "re-arm IS the loop. WARNING: NOT a settled fix: per-lap was "
                         "chosen because a single 0x1018 can be dropped when no "
                         "screen is up (prod 2026-08-19), so a cap trades a "
                         "measured delivery problem for an unmeasured one.")
    ap.add_argument("--account-mode", default="resolve",
                    choices=["resolve", "echo"],
                    help="how the character store is keyed. `resolve` turns the "
                         "0x400F account echo into the POL member felobby "
                         "resolved (echo -> felobby's kv handoff -> a direct "
                         "lookup; see resolve_account). `echo` keys by the raw "
                         "echoed string, which with felobby's old constant "
                         "--account is ONE ROSTER FOR EVERY PLAYER -- kept only "
                         "for reproducing pre-2026-08-24 captures.")
    ap.add_argument("--char-record", default="off", choices=["on", "off"],
                    help="push 0x1003 kind 0 after 0x1000. WARNING: OFF BY DEFAULT "
                         "SINCE 2026-09-04, AND KEEP IT OFF: this CRASHED THE "
                         "CLIENT on field load (live 2026-08-25 -- 'Chara 1 "
                         "already exists' then m_body.mdl::Release then an AV "
                         "at FE_Client+0xEB70E). 0x1003 kind 0 is a character "
                         "CREATE/REPLACE that releases the models on a lookup "
                         "hit, and a stats-only record (flags=2) gives the "
                         "rebuild nothing to build from. Prod has carried an "
                         "explicit `--char-record off` ever since while the "
                         "code default stayed 'on', so a bare run reproduced "
                         "the crash. Skill Points are served by --add-stats "
                         "bit 0 on the 0x1006 MSG_ADD instead. 'on' exists for "
                         "an A/B and needs flags=3 with the identity block "
                         "populated before it can be safe -- see "
                         "char_record_push.")
    ap.add_argument("--char-mask", type=lambda v: int(v, 0), default=1,
                    help="the 0x1003 field mask. WARNING: DEFAULT 1 -- BIT 0 ONLY -- "
                         "and that is not timidity. A mask is a promise about "
                         "how many i16 follow it, so setting a bit with no "
                         "measured meaning does not draw a wrong number, it "
                         "DESYNCHRONISES the record mid-decode. Bit 0 is the "
                         "only slot pinned to a label; the other sixteen "
                         "offsets are known but unidentified. Widen this only "
                         "as each bit is measured.")
    ap.add_argument("--char-fields", default="0:5",
                    type=lambda v: {int(k, 0): int(x, 0)
                                    for k, _, x in (p.partition(":")
                                                    for p in v.split(",") if p)},
                    help="BIT:VALUE pairs for --char-mask, e.g. '0:5' = five "
                         "skill points. Values are i16.")
    ap.add_argument("--acquire-skill", default="ok",
                    choices=["ok", "ng", "off"],
                    help="answer 0x2049 MSG_ACQUIRE_SKILL_REQUEST -- the GET! "
                         "button -- with 0x1078 (header-only, arm 0x050534f7) "
                         "and PERSIST the skill id onto the stored character, "
                         "so 0x303E serves it back. Acknowledging without "
                         "persisting would spend the player's SP for a skill "
                         "that vanishes at the next login. 'ng' sends 0x1079 "
                         "with --ui-auto-err; 'off' leaves the window hanging.")
    ap.add_argument("--status-resync", default="on", choices=["on", "off"],
                    help="after a successful 0x2049, re-push 0x303E so the "
                         "skill window agrees with the store immediately "
                         "instead of at the next login.")
    ap.add_argument("--status", default="off", choices=["on", "off"],
                    help="push 0x303E, the CHARACTER STATUS record -- the "
                         "message that carries skill_point AND the acquired "
                         "skill list, and which feworld has never sent. That is "
                         "why SP reads 0, Skill Points reads 0 and every GET! "
                         "is greyed out. OFF by default only because the "
                         "message has never been on the wire; turn it on with "
                         "the probe values below and read the status screen.")
    ap.add_argument("--status-nums", default="101,102,103,104,105,106,107,108,109",
                    type=lambda v: [int(x, 0) for x in v.split(",") if x],
                    help="the NINE u32 of the 0x303E body, IN WIRE ORDER "
                         "(readers 0x05024b41..0x05024bad). The client's own "
                         "dump names nine scalars -- skill_point, life, power, "
                         "stamina, down, gold, ring, fame, total_score -- but "
                         "WARNING: WHICH SLOT IS WHICH IS NOT ESTABLISHED: the dump's "
                         "print order and the wire order need not agree. The "
                         "default is nine DISTINCT SENTINELS so one look at the "
                         "status screen labels all nine. Do not replace them "
                         "with real values until that run has happened.")
    ap.add_argument("--status-skills", default="",
                    type=lambda v: [int(x, 0) for x in v.split(",") if x],
                    help="acquired skill ids for the 0x303E list ([u32 count] "
                         "then one u32 each, loop 0x05024be5). Empty = none, "
                         "which is what the client is already showing.")
    ap.add_argument("--status-id", type=lambda v: int(v, 0), default=None,
                    help="0x303E leading u32 (the client dumps it as `id`). "
                         "Unset = the session's charid. (These five used to "
                         "default to one hard-coded test character -- id 1, "
                         "'Fox', class 1, nation 1 -- for every player.)")
    ap.add_argument("--status-name", default=None,
                    help="0x303E cstr #1, right after the id. Unset = the "
                         "stored character's name.")
    ap.add_argument("--status-sex", type=lambda v: int(v, 0), default=None,
                    help="0x303E u8. Rendered through a name lookup, so a value "
                         "the table does not know is not a blank field. Unset "
                         "= the stored character's `sex`.")
    ap.add_argument("--status-class", type=lambda v: int(v, 0), default=None,
                    help="0x303E u16 class_type, also a name lookup. Unset = "
                         "the stored character's `look1`, the [unit+0x3AB] "
                         "class byte.")
    ap.add_argument("--status-nation", type=lambda v: int(v, 0), default=None,
                    help="0x303E u16 after the nine (dumped as `nation`). "
                         "Unset = the stored character's `force`, or 0 while "
                         "it is the 0x7FFFFFFF 'none' sentinel.")
    ap.add_argument("--status-str2", default="",
                    help="0x303E cstr #2, between the nation u16 and the skill "
                         "count. Its role is unmeasured; empty is a valid "
                         "cstr and keeps the stream aligned.")
    ap.add_argument("--validate-finish", type=float, default=10.0,
                    metavar="SECONDS",
                    help="after answering 0x209A (the Field Out button) with "
                         "0x1154, push 0x1157 FINISH_NOTIFY this many seconds "
                         "later. 0x1154's arm only STARTS a countdown -- it "
                         "sets [unit+0x138c] = 0x2710 and stamps a timestamp -- "
                         "so answering OK alone moves the hang ten seconds "
                         "later instead of fixing it. The default matches the "
                         "client's own constant (10000ms); 0 sends no FINISH. "
                         "0x209C (Cancel) clears the pending push.")
    ap.add_argument("--skill-grant", default=progression.DEFAULT_SKILL_GRANT,
                    choices=["class", "stored", "items", "all"],
                    help="which skills to mark owned in 0x1075. 'class' "
                         "(default, 2026-09-11) = the character's own PLUS its "
                         "class's starting set, FE_CLASS_BASIC_PARAM_DATA "
                         "+0x70: the proficiencies its starting gear needs "
                         "and its first skills (basic attack etc.), free of "
                         "SP -- what a 2006 Lv1 started with; a class can no "
                         "longer wear another class's weapon family. 'items' "
                         "= the character's own PLUS the "
                         "prerequisite skills the items in its bag actually "
                         "name (each item table record's +0x88, up to two ids, "
                         "0xffff = none) -- the exact gate validator "
                         "0x05076090 code 5 tests, and the refusal that reads "
                         "as 'Wrong class; can't equip'. They are weapon and "
                         "armour proficiencies (443 = wand, 439 = axe, 445..7 "
                         "= armour weights), so this grants a proficiency per "
                         "family carried and nothing else. 'stored' = the "
                         "character's own only. 'all' is the PROBE that found "
                         "the gate: every id in the array, i.e. the whole "
                         "skill tree -- an A/B, not a setting.")
    ap.add_argument("--equip-reflect", default="request",
                    choices=["on", "request", "off"],
                    help="reflect an equip so the client actually wears it. "
                         "VERIFIED LIVE 2026-09-06: the test character's staff "
                         "appeared on screen, no crash. 'request' (default) "
                         "answers a click the player made -- 0x2021/0x2022/"
                         "0x2098 -- with a 0x107A carrying the item's equip "
                         "marker, which is what makes the click DO something: "
                         "0x102A's arm only clears a flag and moves no item. "
                         "'on' ALSO replays the stored equipment after field "
                         "entry, which is NOT the default because it is "
                         "redundant -- the client already dresses the character "
                         "at unit-build time from the 0xD002 equip pairs "
                         "(measured: six Equip calls from FE_Client+0x5788C "
                         "during entry, markers 0/3/5/6, before any server "
                         "push). 'off' restores the old accept-and-do-nothing. "
                         "WARNING: Never sent before the field exists whatever this "
                         "says -- the client resolves the unit id with no null "
                         "check and dies (0x05077D14, live 2026-09-06).")
    ap.add_argument("--use-item", default="on", choices=["on", "off"],
                    help="answer 0x2051 MSG_USE_ITEM and consume the item. "
                         "Unanswered the request hangs and the item does "
                         "nothing (reported live 2026-09-06). The OK is an "
                         "acknowledgement like every other one in this channel "
                         "-- 0x1089's arm clears one in-flight bit "
                         "([obj+0x4ec] & 0xfe) and discards the rest -- so the "
                         "consumption is a separate 0x107A push. WARNING: NO EFFECT "
                         "is applied: which table column says what an item "
                         "heals is not identified, so this takes the item and "
                         "grants nothing. 'off' restores the hang.")
    ap.add_argument("--room-exit", default="conversation",
                    choices=["conversation", "off"],
                    help="walk the player OUT of a room when a conversation "
                         "ends. A room (shop, tavern, bank) has NO exit of its "
                         "own -- measured twice: inside one, the client sends "
                         "nothing at all at its door, and the 0x2000 area -1 "
                         "once read as an exit was the war screen's countdown "
                         "expiring. We put the player in a room with an "
                         "unsolicited 0x1166, so taking them out is the "
                         "server's move too; before this the only way out was "
                         "a hand-typed `!goto` in the gm command file, and a "
                         "player without one is TRAPPED until the read window "
                         "times out (reported live 2026-09-06 from the "
                         "tavern). 'off' restores that. WARNING: Fires only when the "
                         "session is in a room: outdoors the same message with "
                         "B = -1 is a same-field warp to the spawn point.")
    ap.add_argument("--bag-organize", default="on", choices=["on", "off"],
                    help="make the inventory's STACK (0x114D) and SORT "
                         "(0x20AA) buttons do something. Both have been "
                         "answered since 2026-08-25 and inert ever since, for "
                         "the reason the equip was inert: the OK arms "
                         "0x05058747 and 0x0505846D make ZERO stream reads, so "
                         "the acknowledgement moves no item. The server owns "
                         "the bag layout, so 'on' decides the new layout, "
                         "persists it and pushes one 0x107A per changed row -- "
                         "the same message the equip reflection uses. Sort "
                         "order and what counts as stackable are CHOSEN, not "
                         "measured: see bag_sort_key and bag_stacked. 'off' "
                         "restores the acknowledge-and-do-nothing behaviour.")
    ap.add_argument("--skill-palette", default="request",
                    choices=["on", "request", "off"],
                    help="the SKILL BAR. Double-clicking a skill sends 0x2029 "
                         "and the client does NOT apply its own click -- the "
                         "sender and the local setter are two different vtable "
                         "methods (0x05297688 +0x24 and +0x28) and only the "
                         "INBOUND worker calls the setter. So 0x2029 has to "
                         "come back: it is the same id in both directions, "
                         "which is why it registers no reply pair and looked "
                         "like a dead end. 'request' (default) echoes the "
                         "player's own set/clear, which is safe by "
                         "construction -- the client sent it from the very "
                         "object the answer dereferences. 'on' ALSO replays "
                         "the stored palette after the ADD burst so the bar "
                         "survives a relogin; that has never had a live run, "
                         "and both workers deref the HUD singletons "
                         "0x053491E8/0x053491EC with no null check. 'off' goes "
                         "back to not answering at all.")
    ap.add_argument("--sell-price", type=int, default=50,
                    help="percent of an item's BUY price (its FE_ITEM_DATA "
                         "+0x90, or --shop-price under --shop-prices flat) "
                         "offered when SELLING -- credited to the stored "
                         "wallet on 0x204C, and pushed as 0x1086 [u32 uid]"
                         "[u32 value] while a shop window is open. That "
                         "message is the only writer of [item+0x4f0]; the "
                         "item ctor sets it to -1 (0x05073976) and the client "
                         "never asks for it, which is why every sell price "
                         "drew -1. 0 disables. CHOSEN: FE's buy-back rate is "
                         "in no table we have read, so this is a percentage.")
    ap.add_argument("--skill-list", default="on", choices=["on", "off"],
                    help="push 0x1075 with the character's acquired skills "
                         "after the ADD burst. This is what makes a learned "
                         "skill SURVIVE A RELAUNCH: 0x2049 GET! persists it "
                         "server-side, but nothing sent it back, so the skill "
                         "window came up empty every session and the points "
                         "spent on it looked lost. It writes "
                         "[unit+0xA14 + id*2], which is the exact array the "
                         "skill row reads at 0x050D3A06.")
    ap.add_argument("--class-levels", default=progression.DEFAULT_CLASS_LEVELS,
                    metavar="CLASS:LEVEL,...",
                    help="ride bit 0 of the same 0x1075 record and set "
                         "[unit+0x9F0+class], the CLASS LEVEL array. This is "
                         "what gates equipping: the validator at 0x05076195 "
                         "compares that byte against each skill's required "
                         "level and returns reason code 4 when it is short, so "
                         "with the array at 0 (which is where it has always "
                         "been, because nothing ever sent it) a skill you own "
                         "and paid points for still cannot be double-clicked "
                         "on. CLASS may be the literal `self`, which resolves "
                         "to [unit+0x3AB] as taken from the STORED character "
                         "(its `look1`) -- and `self` is the default because "
                         "the first attempt hardcoded 1 while the character's "
                         "class is 2, so it wrote a level nothing was going to "
                         "read and the theory went untested. \U0001f511 SINCE "
                         "2026-09-08 THIS IS ONLY A SEED: the table is stored "
                         "PER CHARACTER (festore.py `class_levels`) and a "
                         "character that already has one is served the STORE, "
                         "not this flag -- so levelling a character up is a "
                         "write to its row, and one player's progress is no "
                         "longer every player's. \U0001f511 2026-09-11: the "
                         "default seed is self:1 -- a new character starts at "
                         "Lv1 in its own class (2006) and climbs by EXP "
                         "(feprog: 0x2059). The old all:30 was a workaround for "
                         "the equip validator at level 0; every starting item "
                         "ships req_level 1. Bit 0 also writes [unit+0x3AC] "
                         "(the last value), so the own class is written last. "
                         "Empty string sends no class block at "
                         "all (bit 0 clear) WHATEVER IS STORED -- the off "
                         "switch wins, because a mask bit with no block behind "
                         "it desynchronises the record.")
    ap.add_argument("--skill-list-value", type=int, default=1,
                    help="the u8 written per acquired skill. The 'do I have "
                         "it' predicate is != 0, so 1 is the smallest value "
                         "that satisfies every reader found -- but 0x0507D600 "
                         "ADDs into the same slot, so it may be a level rather "
                         "than a flag. UNMEASURED; this knob is here to try "
                         "others without a redeploy.")
    ap.add_argument("--war-result", default="auto",
                    choices=["off", "auto", "desert", "win", "lose"],
                    help="after answering the Field Out settlement (0x2033 -> "
                         "0x1045), push 0x1100 to build the WAR RESULT window. "
                         "Without it the client reaches the dummy area and "
                         "parks there forever: scene mode 0x0B state 8 spins on "
                         "[scene+0x59], and the only code in the client that "
                         "sets that byte hangs off this window's close button. "
                         "'auto' (the default since 2026-09-12) is win or "
                         "lose when a war this session fought has SETTLED "
                         "(fecampaign's reward relay says which) and desert "
                         "otherwise -- SE warsystem04's own three outcomes. "
                         "'desert' is the semantically right one for Field Out "
                         "(the client's own text is 'You left the "
                         "front. More is expected.'), and it is CONFIRMED LIVE "
                         "2026-08-25: the window renders and Close returns the "
                         "player to the map, so the worry that the desert arm's "
                         "extra 0x0508D9F0 would stop the widget ticking was "
                         "unfounded. win/lose remain for a real battle result. "
                         "'off' restores the hang.")
    ap.add_argument("--war-result-values", default="session",
                    choices=["session", "zero", "sentinel"],
                    help="what to put in 0x1100's eleven scalars. 'zero' is the "
                         "default because we do not simulate a battle, and a "
                         "number we invented rendered under 'Score' is a lie "
                         "the player cannot tell from a fact. 'sentinel' sends "
                         "101..111, DISTINCT per field -- that is how the field "
                         "map was read off one live screenshot on 2026-08-25 "
                         "(Attack/Defend/Score/Exp/Crystal/Destroyed/Built/"
                         "Rewards, plus three parsed-but-undrawn), and it is "
                         "the right setting for one probe run and the wrong one "
                         "for a session a player is actually in.")
    ap.add_argument("--war-result-rows", type=int, default=0,
                    help="how many per-army records to put in 0x1100. The "
                         "window constructs EXACTLY TWO (0x05113D07) and the "
                         "decode loop is unbounded, so this is CLAMPED to 2 in "
                         "war_result_body() -- 3 would write 0x4FC bytes past a "
                         "0xADC allocation. Default 0: the four masks per record "
                         "are all-zero anyway, so rows carry no data yet and "
                         "0 is the smallest thing that decodes.")
    ap.add_argument("--ui-auto", default="ok", choices=["ok", "ng", "off"],
                    help="answer every request in UI_HEADER_ONLY_OK with its "
                         "registered OK, which is the 6-byte header and nothing "
                         "else (each row carries the arm address the "
                         "header-only reading was measured at). This is the "
                         "inventory STACK and SORT buttons, the bank window and "
                         "the shop. WARNING: OK here means ACKNOWLEDGED, not "
                         "simulated: there is no inventory or economy behind it "
                         "-- but an unanswered request hangs that dialog "
                         "forever and takes the session with it. 'ng' answers "
                         "the refusal instead (a body-carrying u32, see "
                         "--ui-auto-err); 'off' restores the hang for an A/B.")
    ap.add_argument("--ui-auto-err", type=lambda v: int(v, 0), default=0,
                    help="the u32 error code for --ui-auto ng. Every NG arm in "
                         "this family reads exactly one u32 and resolves it "
                         "through the client's error table (0x50613e0), so this "
                         "picks which refusal the player is shown.")
    ap.add_argument("--move-request", default="ok",
                    choices=["ok", "ng", "off"],
                    help="answer 0x20A6 MSG_MOVE_REQUEST with 0x1168 (measured "
                         "header-only, arm 0x05056248). ON since 2026-09-05: "
                         "it is transition kind 3 of the field-transition "
                         "machine, the one a DOOR takes after 0x1166 sets "
                         "0x5345ea0, so 'off' hangs every door. (It was off "
                         "while 'movement' was an open investigation; that "
                         "closed on 08-25 and this was never revisited.)")
    ap.add_argument("--door", action="append", default=None,
                    metavar="GID:X:Z:ROOM",
                    help="where a door in field GID at world (X, Z) leads: a "
                         "room index (9 bar, 10 arms shop, 11 magic shop, 13 "
                         "bath, 14 house, 15 castle). The client SENDS the "
                         "door's position in 0x20AC, and feworld logs it as a "
                         "ready-made --door line -- touch each door once to "
                         "map the town. Matched within --door-radius.")
    ap.add_argument("--events", default="on", choices=["on", "off"],
                    help="play a conversation when a town-file NPC is clicked "
                         "(THE EVENT VM: 0x1071 D=script -> 0x20A7 -> 0x1174 "
                         "commands, each acked by 0x20A8 -> 0x1172). Shops "
                         "open the shop window (op 8 type 1..4), the bank its "
                         "window (0x18). 'off' = acknowledge and release.")
    ap.add_argument("--keeps", default="off", choices=["on", "off"],
                    help="serve the two KEEPS of a war field while a campaign "
                         "war is on: the defender's castle at fet_castle_info's "
                         "grid, the attacker's keep 24 cells toward the centre. "
                         "Hits on them (0xA011/0x2010 with the keep's id) take "
                         "--hit-damage off --keep-hp through 0x2024 bit 0x4; at "
                         "0 the keep is 0x1004'd and the war ends for the side "
                         "still standing (--war-decide keeps).")
    ap.add_argument("--castle-always", default="on", choices=["on", "off"],
                    help="with --keeps on, the defender's CASTLE stands in "
                         "every war field in every phase, served for whoever "
                         "holds it; only the attacker's keep comes and goes "
                         "with a war. The client's own book puts the castle "
                         "on the PEACETIME radar (fet_bookset_info 52/53). "
                         "off = the 2026-09-11 rule: both bases only during "
                         "PREP/WAR.")
    ap.add_argument("--keep-hp", type=int, default=3000, metavar="N",
                    help="hit points of each keep (u16 on the wire, <= 32767). "
                         "CHOSEN, a scale model: the client's FE_BUILDING_DATA "
                         "ships 12,000,000 for the Castle/Keep (+0xb0, FFSKY's "
                         "figure; Hordaine 2006 said 4.8M), which the u16 hit "
                         "channel cannot carry. What the 2006 rules fix is the "
                         "FRACTIONS of it (fecampaign --base-dots, 480 dots).")
    ap.add_argument("--keep-types", default="20:16", metavar="DEF:ATK",
                    help="FE_BUILDING_DATA rows for the two bases. Default "
                         "20 Castle / 16 Keep -- SE warsystem02: the defender's "
                         "base IS the castle ('防衛軍の拠点となる建築物') "
                         "and both it and the keep offer the KNIGHT (their "
                         "+0xde is 954; feunit.SUMMON_BY_TYPE). The old default "
                         "4 GateOfHades offered the WRAITH (955), which SE's "
                         "RoD page never lists as a summon at all.")
    ap.add_argument("--keep-models", default="30:16", metavar="DEF:ATK",
                    help="FE_BUILDING_MODEL_DATA rows. Default 30 castle00_a "
                         "/ 16 b_build06_a. castle00_a ships no `_stand.mdl` "
                         "and 4 gateofhades_a does, which is why the default "
                         "was 4:16 from 2026-09-11 -- but that reading was "
                         "RETRACTED the same day: setup 0x05070530 puts `_stand` "
                         "in an embedded KcMODEL (0x0507087e skips a missing "
                         "one) and the only UNCHECKED pointer is slot 0, the "
                         "base mesh. castle00_a.mdl/_m/_s/_light/_hit/.anm/.tex "
                         "all ship (building.pak, re-verified 2026-09-12), so "
                         "it is crash-safe by that reading and the crash was "
                         "the unmounted pak (fixed by the field_ready gate). "
                         "PARTIAL: The live bar is a war field entered, left and "
                         "RE-entered with the castle on screen.")
    ap.add_argument("--keep-base", type=int, default=2900, metavar="ID",
                    help="object id of the castle; the keep is +1")
    ap.add_argument("--capital-staff", default="off", choices=["on", "off"],
                    help="at start-up, place the capital roster's nine "
                         "non-shop roles (Jade, Luke, Mo, the managers, the "
                         "trainer, the prize clerk, the bank keeper) in the "
                         "castle room around its exit guide, each with a "
                         "conversation (ROLE_LINES; Manager_Karin reports the "
                         "campaign). Idempotent; needs one NPC row in room:15 "
                         "to anchor on.")
    ap.add_argument("--shop-types", default="weapon=1,armor=2,item=3,ring=4",
                    help="op-8 window type per shop kind. CHOSEN until the "
                         "first live window shows which is which.")
    ap.add_argument("--shop-price", type=int, default=100,
                    help="the flat price for --shop-prices flat (and the "
                         "fallback when the item table carries no price "
                         "column): gold at a gold shop, Rings at the Ring "
                         "Shop. The 0x107B record's A field is verified live as "
                         "the gold column, B as the rings column.")
    ap.add_argument("--shop-prices", default="table", choices=["table", "flat"],
                    help="table (default) = each item's own price out of "
                         "FE_ITEM_DATA: +0x90 gold at the weapon/armour/item "
                         "shops, +0x94 Rings at the per-class Ring Shop, which "
                         "stocks exactly the rows that carry a ring price; "
                         "buying DEBITS it and refuses when the wallet is "
                         "short. flat = every row at --shop-price (the old "
                         "behaviour, still charged).")
    ap.add_argument("--shop-level-max", type=int, default=40, metavar="LV",
                    help="stock nothing whose required level is above LV. "
                         "CHOSEN: 40 is RoD's launch level cap (4Gamer), and "
                         "the table carries Lv45/50 rows no 2006 character "
                         "could wear. 0 = no cap.")
    ap.add_argument("--bank", default="on", choices=["on", "off"],
                    help="on (default) = the Bank_Keeper STORES: 0x114B/0x114C "
                         "move an item between the bag and a per-character "
                         "bank (festore `bank`), 0x1149/0x114A move gold "
                         "(festore `bank_gold`), each pushed (0x107A kind 1 / "
                         "0x2024) and only then answered. off = the old "
                         "header-only OK that stores nothing.")
    ap.add_argument("--bank-slots", type=int, default=40, metavar="N",
                    help="the bank's cell count, served as uBankItemNum "
                         "([unit+0x4fe], 0x2024 bit 0x40000000) when the bank "
                         "window opens -- the window builds exactly that many "
                         "cells. 40 is SE's 2006 guide (flow06, 「保管数の上限は"
                         "40個」); the client itself allows up to 96.")
    ap.add_argument("--inn-fees", default=staff.DEFAULT_INN_FEES,
                    metavar="rod|level|G:LO-HI,...",
                    help="the Inn's fee per stay (full HP/Pw restore). rod "
                         "(default) = RoD's brackets: free to Lv5, 10 G at "
                         "Lv6-10, 20 G at Lv11-20, 50 G at Lv21-40 (fewiki "
                         "Guide/Q&A 2006-05-26). level = level x "
                         "--inn-price-per-level above Lv5 (OURS, the old "
                         "rule); or explicit brackets G:LO-HI,...")
    ap.add_argument("--inn-price-per-level", type=int, default=10, metavar="G",
                    help="only with --inn-fees level: level x G gold, free to "
                         "Lv5. The per-level price was OURS (10); 0 = free.")
    ap.add_argument("--kill-gold", default=drops.DEFAULT_KILL_GOLD,
                    metavar="rod|exp|N|off",
                    help="gold a monster kill credits (war gives none in "
                         "RoD). rod (default) = by the monster's LEVEL, the "
                         "2006 table (fewiki Monster 2006-05-26): 5 G at L1, "
                         "44 at L10, 126 at L20, 536 at L30, 3,735 at L40, "
                         "34,426 at L48 (held above -- ASSUMED). exp = the "
                         "monster's shipped EXP reward (OURS, the first pass); "
                         "N = a flat N; off = nothing. The client prints its "
                         "own '%%dGold' line when the wallet grows.")
    ap.add_argument("--mob-exp", default=drops.DEFAULT_MOB_EXP,
                    choices=["rod", "shipped"],
                    help="EXP a kill pays when --kill-exp is 0. rod (default) "
                         "= by the monster's LEVEL, the 2006 table (fewiki "
                         "Monster): the level itself to L20, then 30 at L21 "
                         "... 195 at L30 ... 2,258 at L40 (L41 interpolated, "
                         "L44's 6,567 held above -- ASSUMED). The server pays "
                         "EXP, so the retail server's observed values win "
                         "over the client's own npc_type column; shipped = "
                         "that column (fegamedata.npc_exp), the old rule.")
    ap.add_argument("--bag-size", type=int, default=30, metavar="N",
                    help="uPossessItemNum, sent as 0x2024 maskA bit "
                         "0x20000000 on field entry: THE NUMBER OF BAG SLOTS. "
                         "The item window sizes its grid from it at "
                         "construction, so 0 draws an empty '0/0' window "
                         "however full the bag is. \U0001f511 SINCE 2026-09-08 "
                         "ONLY A SEED: the size is stored per character "
                         "(festore.py `bag_size`) and a character that has one "
                         "is served the STORE, so a bag expansion can survive a "
                         "relog. 0 disables the send, and does so WHATEVER IS "
                         "STORED -- the off switch wins.")
    ap.add_argument("--field-item-kind", default="0", choices=["0", "1", "both"],
                    help="which of the unit's two 96-slot arrays bag items go "
                         "into: 0 = +0x570, 1 = +0x6f0. The inventory widget "
                         "reads whichever its own +0x9c says, and kind 0 alone "
                         "showed nothing -- and so did 'both' (LIVE #16: each "
                         "item in BOTH arrays, still 0/0), so the array is NOT "
                         "the reason the window is empty.")
    ap.add_argument("--field-equip", default="off", choices=["on", "off"],
                    help="mark the character's worn items equipped in the bag "
                         "record (+0x4c8 = the WORN INDEX), which makes the "
                         "reader's tail call Equip(uid, -1, 1) -- the client's "
                         "field dressing, and what registers the worn items in "
                         "the FIELD's item manager (gate 4's lookup). The 09-05 "
                         "'over the default outfit, cannot move' run marked the "
                         "items with their slot TYPE (a wand in worn 10), fixed "
                         "2026-09-08. Default off for the capture replay; prod "
                         "runs on with --add-self client.")
    # PARTIAL: DEFAULT 'armour' since 2026-09-12 evening -- approved: "Go for it".
    # Not yet seen live; `off` is the rollback if the 09-05 crash comes back.
    ap.add_argument("--self-redress", default="armour", choices=["off", "armour"],
                    help="'armour' (2026-09-12): with --add-self on and "
                         "--field-equip on, re-send the worn ARMOUR (every worn "
                         "item but worn 0/1) as 0x107A straight after the self "
                         "0x1006 and before 0x100E. Why: the self record's hit "
                         "path always rebuilds face/hair/body (unit vtable "
                         "+0x4c -> 0x0507dc00 -> model builder 0x5079f90) and "
                         "strips the armour the bag reader attached, so the "
                         "player stood in the all00 base outfit until the "
                         "field-ready replay 1.5 s after the loading screen. "
                         "The weapon stays on that replay (STANCE). The "
                         "default. 'off' is the rollback: a 0x107A in this "
                         "window crashed the client on 2026-09-05, in the "
                         "wrong-marker era.")
    ap.add_argument("--field-items", default="bag", choices=["on", "bag", "off"],
                    help="carry the stored character's bag (and with 'on' the "
                         "worn gear) in the 0x1000 enter-area snapshot (the "
                         "ONLY message that fills the field inventory; readers "
                         "0x0503d330 / 0x0503c470). 'bag' (default) leaves "
                         "the worn mask 0: live #11 crashed with both. 'off' "
                         "= the empty sub-records.")
    ap.add_argument("--shop-page", type=int, default=30,
                    help="items per 0x107B page (the list asks page by page "
                         "up to the 0x1102 count).")
    ap.add_argument("--event-greeting", default=None,
                    help="override every NPC's opening line (a probe knob).")
    ap.add_argument("--talk-wrap", type=int, default=48, metavar="COLS",
                    help="the NARROWEST a talk balloon line is laid out, in "
                         "columns (a full-width character counts 2): a long "
                         "line widens until it fits --talk-lines. The balloon "
                         "never wraps by itself, is 4 lines tall and sizes "
                         "itself a few percent short, so lines are broken with "
                         "the client's own <br/> and padded (measured live "
                         "09-11). A line with its own <br/> keeps its breaks. "
                         "0 = off")
    ap.add_argument("--talk-lines", type=int, default=4, metavar="N",
                    help="lines a talk balloon shows (4, measured live: a "
                         "fifth is clipped at its bottom edge)")
    ap.add_argument("--quests", default="on", choices=["on", "off"],
                    help="QUESTS (2026-09-12): the capital's quest givers "
                         "(QUESTS: Cheese at scripts 115/315/515/715/915, "
                         "Goblin Book at 137/340/536/738/936 -- the 2006 "
                         "fewiki's) play a quest conversation on the event VM: "
                         "offer -> Yes/No menu (op 0x107) -> accepted; later, "
                         "holding the item -> Yes/No -> the item is taken and "
                         "gold paid AFTER the conversation closes. Repeatable "
                         "with a per-character cooldown. 'off' = they say "
                         "their plain townsfolk line, as before.")
    ap.add_argument("--quest-cooldown-secs", type=int, default=-1, metavar="S",
                    help="override every quest's cooldown (-1 = each quest's "
                         "own, one in-game day = 2400 s).")
    ap.add_argument("--town-file", default=None, metavar="PATH",
                    help="the TOWN FILE (default data/fe_town.json): per-group "
                         "NPCs, buildings and door->room mappings, written by "
                         "`!npc`, `!build` and `!door` as the town is walked "
                         "and served on every entry of that group. What SE's "
                         "server knew and the client does not ship. Empty "
                         "disables it.")
    ap.add_argument("--npc-names", default="on", choices=["on", "off"],
                    help="send each NPC/monster its NAME in the 0x1006 type-8 "
                         "identity sub-record (mask1 bit 0, name-only) so the "
                         "client's name field [unit+0x389] is set -- the "
                         "over-head label and the target window. Off restores "
                         "the old nameless units. The internal role name is "
                         "made readable (Warrior_Weapon_Shop -> Warrior Weapon "
                         "Shop).")
    ap.add_argument("--spawns", default="dat", choices=["dat", "off"],
                    help="serve each war field's OWN monsters from dat.pak's "
                         "fet_npc_generator_pos/_data tables (services/fedata, "
                         "fegamedata.py): 7..11 spawn points per field with "
                         "shipped positions and monster types. Capitals have "
                         "none. 'off' serves only --monster.")
    ap.add_argument("--spawn-population", default="dat", choices=["dat", "one"],
                    help="how many monsters a dat.pak spawn point holds. 'dat' "
                         "(the default, 2026-09-12) serves the generator's own "
                         "population -- fet_npc_generator_data +0x10, about "
                         "nine on most generators -- split across its types by "
                         "their shipped weights and capped per type, placed "
                         "inside the point's radius. A READING of the record "
                         "(nothing in the client consumes it). 'one' is the "
                         "old single monster per point.")
    ap.add_argument("--drops", default="on", choices=["on", "off"],
                    help="MONSTER DROPS as retail had them (2026-09-12): a kill "
                         "rolls fedata/fe-drops.tsv and a hit puts a TREASURE "
                         "CHEST on the ground (0x1006 type 2) that the killer "
                         "and their party right-click to pick up (0x201F -> "
                         "0x107A announce=1 'Got %s.' + 0x1023 + 0x1004). "
                         "'off' = nothing drops.")
    ap.add_argument("--drop-rate-mult", type=float, default=1.0, metavar="X",
                    help="multiply every drop rate (SE ran 'x2' events).")
    ap.add_argument("--drop-lifetime", type=float, default=90.0, metavar="S",
                    help="seconds an unclaimed chest stays on the ground.")
    ap.add_argument("--drop-range", type=float, default=20.0, metavar="U",
                    help="pickup reach -- the JP client's own 20.0, which the "
                         "EN overlay overwrote, so the SERVER enforces it.")
    ap.add_argument("--drop-obj-base", type=int, default=900000, metavar="N",
                    help="first object id for chests (clear of monsters, "
                         "NPCs, keeps, buildings and peers).")
    ap.add_argument("--drop-table", default=None, metavar="PATH",
                    help="the drop table (default fedata/fe-drops.tsv).")
    ap.add_argument("--spawn-layout", default="scatter",
                    choices=["scatter", "spiral"],
                    help="how a point's population stands (2026-09-12). "
                         "'scatter': seeded packs of 1..3 at random spots "
                         "within --spawn-spread x the table radius -- the same "
                         "for every player (spawn_scatter). 'spiral': the "
                         "first cut, an even golden-angle disc inside 0.8 x "
                         "the radius, which read as obvious clusters.")
    ap.add_argument("--spawn-spread", type=float, default=2.0, metavar="X",
                    help="scatter reach as a multiple of the point's table "
                         "radius (25..50 u on most points). CHOSEN.")
    ap.add_argument("--spawn-mobs-max", type=int, default=200, metavar="N",
                    help="cap on monsters served per field entry under "
                         "--spawn-population dat, so object ids (from "
                         "--monster-base + 1000) stay far below --keep-base.")
    ap.add_argument("--spawns-max", type=int, default=12,
                    help="cap on dat.pak spawn points served per entry.")
    ap.add_argument("--door-radius", type=float, default=15.0,
                    help="world units either side of a --door's X/Z that "
                         "still count as that door.")
    ap.add_argument("--door-default", type=lambda v: None if v == "" else int(v, 0),
                    default=10,
                    help="the room an UNMAPPED door opens. Default 10, the arms "
                         "shop, so every door works while the town is being "
                         "mapped; empty ('') answers 0x1167 NG instead, which "
                         "shows the client's own 'system error' text. "
                         "`!door N` in --gmcmd-file changes it live.")
    ap.add_argument("--blacklist", default="on", choices=["on", "off"],
                    help="answer the three blacklist requests -- 0x20A0 "
                         "SET with 0x115F, 0x20A1 REMOVE with 0x1161, 0x20A2 "
                         "GET with the whole-list 0x1163 -- and persist the "
                         "rows onto the stored character. OFF restores the "
                         "pre-2026-08-25 silence, in which every blacklist "
                         "dialog hangs; it is an A/B, not a fallback.")
    ap.add_argument("--distribution", default="on", choices=["on", "off"],
                    help="answer 0x2080 MSG_GET_CHARACTER_DISTRIBUTION_INFO_"
                         "REQUEST (a ~10s poll) with 0x1123.")
    ap.add_argument("--distribution-rows", default="",
                    metavar="A:B:CLASS:CHARID,...",
                    help="the rows of the 0x1123 body. EMPTY (the default) "
                         "sends count 0, which is the honest answer: the third "
                         "byte's low 5 bits are a class code and its top bit "
                         "the side, and the u32 is a character id the client "
                         "looks up -- but what the rows DRAW is unmeasured "
                         "(probably the minimap distribution dots), so making "
                         "some up would be inventing a screen. This is the "
                         "knob for finding out.")
    ap.add_argument("--set-comment", default="ok", choices=["ok", "ng", "off"],
                    help="answer the 0xF900 `comment <text>` command with "
                         "0x111E MSG_SET_COMMENT_OK (header-only -- its arm "
                         "0x05055b24 makes zero stream reads, so a body would "
                         "desynchronise). 'ng' sends 0x111F, 'off' answers "
                         "nothing. The comment is persisted onto the stored "
                         "character either way.")
    ap.add_argument("--chat-echo", default="off",
                    choices=["off", "gm", "say", "self"],
                    help="what to do with the player's OWN chat line (all six "
                         "ids). 'self' echoes it back with the player's own "
                         "unit id in the header, which is the retail shape: "
                         "0x051334f0 does NOT log a /say, it only sends and "
                         "draws the balloon (0x05129890 = [obj+0x47c]); the "
                         "log line is drawn by the chat-window listener at "
                         "0x0512bf60 from what the ARM broadcasts, so without "
                         "an echo your own line never reaches the log. /tell "
                         "is never echoed (its handler logs itself). "
                         "'say' RELAYS the line "
                         "back as the same id, which is the real chat channel "
                         "-- the client's receive dispatcher routes 0x201A / "
                         "0x201B / 0x2065 / 0x2066 / 0x208A to arm 0x05053cbe "
                         "and 0x2067 to 0x05053e28, so the inbound id IS the "
                         "outbound one. WARNING: the player already echoes their "
                         "own line locally (0x051334f0), so 'say' shows it "
                         "TWICE -- that doubling is the confirmation, not a "
                         "bug. 'gm' is the older stopgap: it prints through "
                         "the 0xF900 '@' system-message path, which is not the "
                         "chat window.")
    ap.add_argument("--chat-line", action="append", metavar="ID|NAME|TEXT",
                    help="serve one chat line at field entry, e.g. "
                         "0x201a|Bob|hello. Repeatable. 0x2067 (/tell) takes "
                         "ID|A|B|TEXT -- it reads three strings and which is "
                         "from/to/text is UNMEASURED. WARNING: both fields are "
                         "clamped (name 0x20, text 0x80): the client reads "
                         "them with an UNBOUNDED strcpy into a 0x81-byte stack "
                         "slot. WARNING: a name on the player's own blacklist is "
                         "dropped SILENTLY by 0x05170e10 and reads exactly "
                         "like a broken message.")
    ap.add_argument("--chat-unit-id", type=int, default=0, metavar="ID",
                    help="the SPEAKER's unit id in every chat message's world "
                         "header. WARNING: THE 08-26 READING HERE WAS HALF WRONG AND "
                         "IS CORRECTED: 0x05053cbe suppresses the speech BUBBLE "
                         "when the speaker is you, but the chat LOG is an "
                         "UNCONDITIONAL broadcast (0x05053da3) gated only by the "
                         "blacklist -- so a self-spoken line still appears in the "
                         "log. Verified live 2026-09-04: --chat-echo self sends the "
                         "player's own unit id and the line appeared. 0 is "
                         "the client's 'no unit' sentinel, matches no object, "
                         "and takes the CHAT LOG path. Give a live unit's id "
                         "(e.g. an --npc) to aim the text at that unit instead "
                         "-- that A/B is what proves the reading.")
    ap.add_argument("--chat-bubbles", default="all", choices=["all", "say", "off"],
                    help="a SPEECH BALLOON over the player who spoke "
                         "(2026-09-12): a relayed line carries the sender's "
                         "charid as its speaker unit id when this client draws "
                         "that player (fepresence, --unit-id auto). The arm "
                         "0x05053cbe skips the balloon only for a kind-1 unit "
                         "(the local player) and 0x5129890 accepts the kind-2 "
                         "avatar a peer is. 'all' = /say /all /army /party "
                         "(the client treats them alike), 'say' = /say only, "
                         "'off' = speaker 0, no balloon (the old behaviour).")
    ap.add_argument("--chat-relay", default="on", choices=["on", "off"],
                    help="fan every player's chat line out to the OTHER "
                         "in-field sessions on this door (queue per session, "
                         "drained by its own read loop; /tell goes only to "
                         "the session whose client signs as the target). "
                         "The relayed copy's speaker unit id is set by "
                         "--chat-bubbles (the sender's charid, or 0). Names on the "
                         "receiver's served blacklist are dropped here with a "
                         "log line instead of silently by 0x05170e10.")
    ap.add_argument("--monster", action="append", metavar="TYPE[:X:Y:Z]",
                    help="spawn a MONSTER: 0x1006 entity type 8, the arm we "
                         "had never sent (0x1006 dispatches NINE types at "
                         "0x0503bb0c; --npc is type 0). TYPE is an id in "
                         "dat\\data_NPC_ModelType.dat, which the client names "
                         "itself when the lookup misses: 'ERROR : "
                         "CFeClientNPCObject::Initialize() get Modeltype type "
                         "= %%d'. 271 rows ship in dat.pak -- 0..6 goblins, "
                         "7..17 orcs, 18..24 undead, 25..44 "
                         "dragon/griffin/harpy/salamander/venom, 45..47 and "
                         "61..71 the rest, 216..227 more monsters; 52..60, "
                         "72..215, 221 and 228..274 are town NPCs "
                         "(Npc00..Npc45 -- 201 rows, regenerated in full into "
                         "the fe-npc-modeltypes TSV 2026-09-04; that TSV "
                         "had first been written with fenpc.py --monsters and "
                         "so held only the 69 with a bestiary entry). WARNING: 221 "
                         "is a town NPC INSIDE the monster block and 227 "
                         "(ulf_01) is a monster model with no bestiary row, so "
                         "neither the id ranges nor the scalars classify these "
                         "cleanly -- read the TSV, do not interpolate. "
                         "Repeatable. X:Y:Z go into the ADD itself (mask2 "
                         "bit 0 -> [unit+0x1c4], ground-snapped by "
                         "0x05000450); TYPE must be one of the 271 shipped "
                         "ids (0..47, 52..274) and the object id must not "
                         "collide with the player or an --npc -- both are "
                         "refused at startup, see monster_push.")
    ap.add_argument("--monster-walk", choices=("on", "off"), default="off",
                    help="also re-place every --monster with 0x2023 action 0 "
                         "on each client move tick (the --npc-walk "
                         "machinery). Default off: the ADD already places "
                         "it, and the first live test wants one new message, "
                         "not two.")
    ap.add_argument("--monster-base", type=int, default=400, metavar="ID",
                    help="object id of the first --monster; each subsequent "
                         "one takes the next. Kept clear of --npc-base so the "
                         "two families cannot collide in the client's object "
                         "list, which is keyed by this id.")
    ap.add_argument("--monster-kind", type=int, default=0, metavar="N",
                    # (per-entity override: the optional 5th field of --monster)
                    help="the second u16 of the type-8 record. 0 makes the "
                         "ctor stamp [unit+0x24]=0xBBC, non-zero 0xBBB "
                         "(0x05068fd4). What the two mean in game terms is "
                         "UNMEASURED -- both are inside the range 0x2023 "
                         "action 0 accepts. A sentinel knob, not a setting.")
    ap.add_argument("--monster-level-source", default="shipped",
                    choices=("shipped", "knob"),
                    help="where a monster's LEVEL comes from: 'shipped' = its "
                         "own fet_npc_type row (what the CLIENT draws on the "
                         "target ring), 'knob' = --monster-level for every "
                         "monster in the world, which is the pre-2026-09-12 "
                         "behaviour and paid a level-1 reward for a level-47 "
                         "kill because --mob-exp rod is rod_mob_exp(level)")
    ap.add_argument("--monster-level", type=int, default=1, metavar="N",
                    help="the trailing u8 -> [unit+0x3ac]. On the PLAYER that "
                         "offset is the class level the Status screen draws, "
                         "so 'level' is the obvious reading and obvious is not "
                         "measured. Default 1 rather than 0 on purpose: nobody "
                         "chooses the zero either, and zero is what silently "
                         "refuses things elsewhere in this client.")
    ap.add_argument("--building", action="append", default=None,
                    metavar="GID:TYPE:MODEL:GX:GZ",
                    help="place a BUILDING (0x1006 entity type 1) when group "
                         "GID is entered; repeatable. TYPE is a row of "
                         "data_FE_BUILDING_DATA.dat (9 Bar, 10 BlackSmith, 11 "
                         "ItemShop, 13 PublicBath, 14 House, 15 Castle, 16 "
                         "Keep, 6 Obelisk...), MODEL a row of "
                         "data_FE_BUILDING_MODEL_DATA.dat -- and models 9/10/"
                         "11/13/14/15 are the ROOM buildings (bar, arms shop, "
                         "magic shop, bath, house, castle), i.e. the DOORS the "
                         "capital's minimap draws. GX/GZ are 0..255 on the "
                         "field grid: world = -(g-128)*2.5, so 128:128 is the "
                         "centre; Y comes off the heightmap. The record is "
                         "measured to the byte (building_record); what a "
                         "placed building DOES is not -- `!build TYPE` in "
                         "--gmcmd-file drops one where the player stands, "
                         "which is how to find the door coordinates.")
    ap.add_argument("--building-base", type=int, default=3000, metavar="ID",
                    help="object id of the first building; clear of --npc-base "
                         "and --monster-base. The type-1 arm looks the id up "
                         "in kind 0x7D0 and logs 'already exists' on a hit.")
    ap.add_argument("--building-hp", default="100:100", metavar="A:B",
                    type=lambda v: tuple(int(x, 0) for x in v.split(":")),
                    help="the two i32s at +0x778 and +0x774 (that wire order). "
                         "0x5111010 ratios the pair as a gauge, so HP/max-HP "
                         "fits -- WHICH IS WHICH IS UNMEASURED. Equal values "
                         "sidestep the question for a town.")
    ap.add_argument("--monster-chase-timing", default="step",
                    choices=["step", "client"],
                    help="the two u32s after the action in a monster's 0x2023 "
                         "move are a START and an END tick, and the client "
                         "reads END minus START as the move's DURATION in ms "
                         "(0x04FEAB38). 'step' (the default) sends (t, t + "
                         "step/speed), so a step takes as long as it should. "
                         "'client' copies the client's own heartbeat pair, "
                         "which is the HIGH and LOW half of its clock -- a "
                         "duration of minutes per step, so the monster plays "
                         "its walk and stays where it is (the pre-2026-09-12 "
                         "behaviour).")
    ap.add_argument("--monster-height", default="dat",
                    choices=["dat", "table"],
                    help="where a dat.pak spawn's Y comes from. 714 of "
                         "fet_npc_generator_pos' 721 rows ship y = 0 exactly "
                         "-- the table has no world height, SE's own server "
                         "resolved it -- and every map that ships samples has "
                         "its lowest ground at 8, medians 8..62. So y=0 puts "
                         "the monster UNDER the terrain, which put our own "
                         "registry tens of units below the player and made "
                         "the vertical reach test refuse every attack. 'dat' "
                         "(the default) "
                         "raises a y=0 row to the map's ground height, the "
                         "same estimate the player's derived spawn uses; "
                         "'table' serves the raw 0 back.")
    ap.add_argument("--exp-max-seed", type=int, default=8, metavar="N",
                    help="how many times to repeat the 0x1075 bit-2 EXP "
                         "THRESHOLD ([unit+0x135c]) during field load, from "
                         "the 0x2000 enter-area answer until the field-entry "
                         "burst. The HUD exp gauge caches that value at "
                         "construction (0x050CCA9D) and its only refresh "
                         "(0x050CE346) is unreachable once the cache is 0 -- "
                         "step = (long)(0*K) = 0, so the fill can never exceed "
                         "the max -- which is the bar that is always full. "
                         "[unit+0x135c] has exactly ONE writer in the client, "
                         "the bit-2 arm at 0x0505222C, so this is the only "
                         "lever there is. A push before the unit exists is a "
                         "silent no-op, hence the repeat. 0 = off.")
    ap.add_argument("--field-ready-ms", type=int, default=1500, metavar="N",
                    help="how long after 0x100E MSG_ADD_COMPLETE the "
                         "FIELD-READY batch is released: the war notify, the "
                         "0x107A worn-item replay that dresses the rebuilt "
                         "unit, the skill palette, and the field_ready flag "
                         "fecampaign gates keeps and buildings on. It used to "
                         "wait for the client's first 28-BYTE 0x2023, which "
                         "the client does not send UNTIL THE PLAYER MOVES -- "
                         "measured at 6.3 s on 2026-09-12, during which the "
                         "character stood there in its base outfit with the "
                         "equipment clipping through it. 0 = the old trigger "
                         "(movement only). Raise it if a field entry starts "
                         "crashing on the palette: the 0x2029 workers deref "
                         "the HUD singletons with no null check.")
    ap.add_argument("--build-age", default="real",
                    choices=["real", "zero", "done"],
                    help="what the type-1 building record's LAST u32 carries. "
                         "It is the building's AGE IN MS at the moment it is "
                         "added: the client stores now-minus-it and compares "
                         "now-minus-that against FE_BUILDING_DATA +0xac, its "
                         "build time (Arrow Tower 40000, Obelisk 30000, War "
                         "Craft 60000, Keep 30000, shops 10000), and draws a "
                         "construction site while it is short (0x05072770). "
                         "'real' (the default) sends 0 only for a build the "
                         "player is paying for this second and the true age "
                         "for everything already standing. 'zero' is the "
                         "pre-2026-09-12 behaviour -- every building raised "
                         "itself again at every player it was sent to. 'done' "
                         "never shows a site at all.")
    ap.add_argument("--doors", default="dat", choices=["dat", "off"],
                    help="fall back to dat.pak's own fet_area_portal table for "
                         "any door no --door entry and no town file claims. "
                         "The table covers the ten CAPITAL areas (21/39/57/62/"
                         "78 and their 91..95 twins) and pairs each door with "
                         "the one it leads to, which is the shipped answer to "
                         "'a door leads to the other half of the capital'. It "
                         "also carries the ARRIVAL position, so the player "
                         "lands where SE put them rather than at the group's "
                         "default spawn. ON since 2026-09-09: the record's "
                         "frame was settled off the client's own parser "
                         "(0x0519D85A) -- every position is a two-float (x, z) "
                         "pair and the float previously read as a third "
                         "coordinate is the door's RADIUS. A war field has no "
                         "rows, so this changes nothing outside a capital. "
                         "PARTIAL: STATIC ONLY -- no door has been walked through "
                         "since; `--doors off` restores the old behaviour.")
    ap.add_argument("--unequip-reflect", default="on", choices=["on", "off"],
                    help="on an unequip, send 0x2004 with the worn slot empty "
                         "so the CLIENT runs its own unequip (0x0507b950 -> "
                         "0x05073f00). Without it the 0x102C OK and the 0x107A "
                         "marker leave [unit+0x870] still naming the item, so "
                         "the inventory keeps showing it worn and the body "
                         "part the equip swapped out never comes back -- "
                         "reported live 2026-09-06.")
    ap.add_argument("--cast-report", default="on", choices=["on", "off"],
                    help="log, from dat.pak's own tables, which of the cast "
                         "ladder's gates would refuse each of the character's "
                         "skills and why. Gate 4 ('Skill conditions not met.') "
                         "reads WORN SLOTS 0 AND 1 ONLY and matches the item's "
                         "category bits against the skill's, so the answer is "
                         "usually 'nothing with the right category is in your "
                         "hand'.")
    ap.add_argument("--discard", default="on", choices=["on", "off"],
                    help="act on 0xF102, the client's DISCARD (its own log "
                         "line is `> ComamndDiscardItem`): take the item out "
                         "of the stored bag so the drop survives the next "
                         "field entry. Nothing answers it -- the id registers "
                         "no reply and the client drops it locally -- so "
                         "'off' just means a dropped item comes back.")
    # ---- COMBAT (2026-09-06, static; nothing here has been live-tested) ----
    ap.add_argument("--combat", default="off", choices=["on", "off"],
                    help="answer 0xA011 MSG_NPC_HIT_NOTIFY: subtract "
                         "--hit-damage from the monster's hit points, push the "
                         "new HP as a 0x1006 type-8 re-apply, and on zero send "
                         "0x1004 MSG_DEL and award EXP. OFF by default because "
                         "no part of it has been seen on a screen -- with it "
                         "off the hit is still LOGGED, which is itself the "
                         "first thing to check (does the client send 0xA011 at "
                         "all, and what is in the two undecoded u32s?).")
    ap.add_argument("--hit-damage", type=int, default=25, metavar="N",
                    help="flat damage per 0xA011 (CHOSEN): what a hit on a "
                         "keep, a building or (via fepvp) a player does, and "
                         "the fallback against a monster when --player-damage "
                         "is flat or the skill has no damage effect.")
    ap.add_argument("--player-damage", default="table", choices=["table", "flat"],
                    help="a hit on a MONSTER. 'table' (2026-09-12): the "
                         "player's attack (class base +0x28/+0x2A + the worn "
                         "weapon's +0x38/+0x3A) x the skill's power %% out of "
                         "EFFECT_DATA (Sonic Boom L1 70, basic attack 100). "
                         "'flat' = --hit-damage. Either way it is then scaled "
                         "by --mob-level-correction. Monster defence is not "
                         "applied (no source says how).")
    ap.add_argument("--mob-level-correction", default="on", choices=["on", "off"],
                    help="scale --hit-damage against a monster by "
                         "fet_npc_lv_diff_correction's f0 for (monster level - "
                         "your class level), the row picked the way the "
                         "client's 0x050DFEB0 picks it (exact, else clamped): "
                         "1.4 at even, 1.0 at +3, 0.05 at +15 and above "
                         "(2026-09-12, static). 'off' = flat --hit-damage.")
    ap.add_argument("--monster-hp", type=int, default=0, metavar="N",
                    help="override every monster's hit points. 0 (default) "
                         "takes them from dat.pak's fet_npc_type table via "
                         "fegamedata.npc_hp, falling back to 100.")
    ap.add_argument("--kill-exp", type=int, default=0, metavar="N",
                    help="a flat EXP per kill; 0 (default) pays by --mob-exp "
                         "(the RoD table by monster level, or the shipped "
                         "column).")
    ap.add_argument("--kill-reward", default="on", choices=["on", "off"],
                    help="push EXP on a kill (0x1075 mask bit 2 -> "
                         "[unit+0x135c]). 'off' kills without paying.")
    ap.add_argument("--respawn-secs", type=float, default=0.0, metavar="S",
                    help="put a dead monster back after S seconds, re-sending "
                         "its 0x1006 type-8 ADD at the same position. 0 "
                         "(default) leaves the field cleared -- which is also "
                         "the only way to see whether 0x1004 removed anything.")
    ap.add_argument("--unit-state", type=lambda v: int(v, 0), default=None,
                    metavar="MASK",
                    help="serve [unit+0x2b4], the unit STATE WORD, on field "
                         "entry (0x2024 maskA bit 0x100000). THIS IS THE SKILL "
                         "GATE: bits 25..28 (0x1E000000) are what the palette's "
                         "Select and the cast's 0x0504a500 test against each "
                         "skill's [skill+0xd0], the field is server-owned (the "
                         "client has no local writer), and we have never sent "
                         "it -- so it has been 0 and no gated skill has ever "
                         "been selectable. Try 0x1E000000. Unset by default: "
                         "the same word carries stealth (0x2000) and the "
                         "move/animation transitions (0x800000, 0x1000000, "
                         "0x20000000) that 0x0506caa0 fires, so a wide value "
                         "changes more than the gate.")
    ap.add_argument("--gmcmd", action="append", default=None, metavar="LINE",
                    help="a 0xF900 command line to send once, after field "
                         "entry; repeatable. A leading '/' makes the client RUN "
                         "it through its own slash dispatcher (/warp, /summon, "
                         "/healme, /toggleinterface, the emotes -- see "
                         "GM_COMMANDS); a leading '@' makes it PRINT the rest "
                         "in the message window. Anything else is decoded and "
                         "dropped by the client.")
    ap.add_argument("--gmcmd-file", default=None, metavar="PATH",
                    help="a file polled on every inbound frame: each line is "
                         "sent as a 0xF900 command and the file is then "
                         "truncated. A live console into the running client. "
                         "Same '/' and '@' rules as --gmcmd. SERVER-SIDE "
                         "directives start with '!' and never reach the "
                         "client: '!chat ID|NAME|TEXT' sends one chat line, "
                         "'!war' advances the war phase on demand (once per "
                         "field entry -- it moves a phase, and re-sending a "
                         "phase advance is the re-arming loop 0x1018's note "
                         "warns about), and '!crystal N' re-serves CRYSTAL "
                         "while the Status screen is open. These exist because "
                         "the alternative is a --force-recreate, and that "
                         "drops the session you are testing in.")
    ap.add_argument("--member-lookup", choices=("on", "off"), default=None,
                    help="resolve an address to a POL member from the account "
                         "database when the echo and the handoff both miss "
                         "(default on, or $FE_MEMBER_LOOKUP). `off` skips the "
                         "lookup.")
    ap.add_argument("--member-window", type=float, default=None,
                    help="seconds a POL session row may still name the member at "
                         "an address (default 86400 / $FE_MEMBER_WINDOW)")
    for _fn in ext.EXT_ARGS:
        _fn(ap)
    # The game rules as tuned in play. Each is still a command-line option
    # (docker-compose.yml or the command line overrides any of them); these
    # are simply the values a working world runs with, so a stock bring-up
    # needs none of them spelled out. Deployment-specific options (ports,
    # binds, tokens, webhooks) keep their plain parser defaults.
    RELEASE_DEFAULTS = {
        "port": 54850, "seq_mode": "count", "world": "dat",
        "field_names": "dat-en", "field_home": "capital",
        "field_nations": "1:5", "field_defenders": "1,2,3,4,5",
        "capital": "1:39", "capital_name": "Capital",
        "captures": 0, "life": 0, "read_window": 120,
        "war_start": "deadline", "war_deadline_ms": 60000,
        "war_clock": "telemetry", "combat": "on", "hit_damage": 400,
        "respawn_secs": 45, "monster_attack": "on", "monster_damage": 60,
        "monster_range": 8.0, "monster_interval": 2.0, "revive_secs": 60,
        "item_heal": 250, "unit_state": "0x1E000000", "jump_phys": "0.78:1.0",
        "unit_physics": "push", "war_cycle": "on", "war_length_ms": 3600000,
        "war_truce_ms": 600000, "campaign": "on", "war_prep_ms": 65000,
        "declare_accepts": 4, "build_costs": "2006", "keeps": "on",
        "keep_types": "20:16", "keep_models": "30:16", "obelisk_drain": "on",
        "build_share": "on", "tower_fire": "on", "base_cannon": "off",
        "influence": "buildings", "keep_hp": 3000, "war_decide": "keeps",
        "capital_staff": "on", "add_self": "on", "field_equip": "on",
        "self_pos": "2.16:24.76:-60.51", "unit_id": "auto",
        "move_authority": "off", "unit_speed": 1.3,
        "add_stats": "0:5,7:200,8:200", "gold": 1000, "ring": 1,
        "crystal": 0, "total_score": 0, "unit_speed_repeat": "off",
        "char_record": "off", "char_mask": 1, "char_fields": "0:5",
        "chat_echo": "self", "gmcmd_file": "/data/fe_gmcmd.txt",
    }
    known = {a.dest for a in ap._actions}
    unknown = sorted(k for k in RELEASE_DEFAULTS if k not in known)
    if unknown:
        raise SystemExit("RELEASE_DEFAULTS names unknown options: %s" % unknown)
    # string values go through each option's own type/choices handling
    ap.set_defaults(**{k: (str(v) if not isinstance(v, str) else v)
                       for k, v in RELEASE_DEFAULTS.items()})
    args = ap.parse_args()
    try:
        _dm, _dc = (int(x, 0) for x in args.item_durability.split(":"))
        itemrecords.ITEM_DURABILITY = (_dm, _dc)
    except ValueError:
        raise SystemExit("--item-durability wants MAX:CUR, e.g. -1:100")
    try:
        args.move_xyz = tuple(float(v) for v in args.move_pos.split(":"))
        if len(args.move_xyz) != 3:
            raise ValueError
    except ValueError:
        raise SystemExit("--move-pos wants X:Y:Z, got %r" % args.move_pos)
    # WARNING: THESE THREE MUST BE PARSED BEFORE ANYTHING READS THEM, and on
    # 2026-09-09 they were not: the spawn-store block below reads
    # args.spawn_xyz, this ran AFTER it, and feworld crash-looped on prod with
    # AttributeError on the first start. Derived coordinates go first.
    try:
        args.self_xyz = tuple(float(v) for v in args.self_pos.split(":"))
        if len(args.self_xyz) != 3:
            raise ValueError
    except ValueError:
        raise SystemExit("--self-pos wants X:Y:Z, got %r" % args.self_pos)
    for _opt, _dst in (("spawn_pos", "spawn_xyz"), ("spawn_dir", "spawn_dir_xyz")):
        try:
            setattr(args, _dst,
                    tuple(float(v) for v in getattr(args, _opt).split(":")))
            if len(getattr(args, _dst)) != 3:
                raise ValueError
        except ValueError:
            raise SystemExit("--%s wants X:Y:Z, got %r"
                             % (_opt.replace("_", "-"), getattr(args, _opt)))
    zones._CAPITALS = zones.parse_capitals(args.capital)
    if territory.world_from_dat(args):
        # KEY: --islands is DERIVED here, not configured. 0x3030's handler
        # bounds-checks every island id as (id-1) < count, so announcing six
        # islands' worth of groups under a count of five means island 6's
        # records are asked for and never resolve. The table says six.
        _isls = fegamedata.island_ids()
        if not _isls:
            raise SystemExit(
                "--world dat needs fedata/fe-fet-area.tsv; it is missing or "
                "has no `island` column. Regenerate it with tools/fedatagen/fefet.py.")
        args.islands = max(_isls)
        territory.territory_load(args)
        _held = {}
        for _a in fegamedata.areas():
            _held[territory.territory_owner(_a, args)] = _held.get(territory.territory_owner(_a, args), 0) + 1
        print("[feworld] WORLD = dat: %d islands, %d areas, holdings %s"
              % (args.islands, len(fegamedata.areas()),
                 ", ".join("n%d:%d" % kv for kv in sorted(_held.items()))),
              flush=True)
        if args.world_neighbours != "on":
            print("[feworld] WARNING: --world-neighbours off: 0x3031 bit 12 is NOT "
                  "served, so the client's frontier test can only ever match a "
                  "field your own nation already holds. No player will be able "
                  "to enter enemy land.", flush=True)
    staff.capital_staff_fill(args)
    if getattr(args, "devtool_port", 0):
        # The tee is what makes the panel usable: "did that work?" is answered
        # by a log line, and until now that line was on a terminal somewhere
        # else. Wrapping stdout is contained and only happens when the panel
        # is on.
        class _Tee(object):
            def __init__(self, inner):
                self._inner = inner

            def write(self, text):
                try:
                    fedevtool.note(text)
                except Exception:
                    pass
                return self._inner.write(text)

            def flush(self):
                return self._inner.flush()

            def __getattr__(self, k):
                return getattr(self._inner, k)

        sys.stdout = _Tee(sys.stdout)
        fedevtool.start(args.devtool_port, args.devtool_bind,
                        args.devtool_token,
                        lambda area=None: devtool.devtool_state(args, area),
                        lambda op: devtool.devtool_edit(args, op),
                        floor_fn=lambda area: devtool.devtool_floor(args, area))
        print("[feworld] world-building panel on http://%s:%d/%s"
              % (args.devtool_bind, args.devtool_port,
                 "?t=" + args.devtool_token if args.devtool_token else ""),
              flush=True)
    spawns.spawn_load(args)
    mapcal.mapcal_load(args)
    doors.doorarr_load(args)
    with doors._DOORARR_LOCK:
        _n_arr = len(doors._DOORARR)
    if _n_arr:
        print("[feworld] door arrivals: %d portal(s) have a hand-placed "
              "arrival point" % _n_arr, flush=True)
    with mapcal._MAPCAL_LOCK:
        _n_cal = len(mapcal._MAPCAL)
    if _n_cal:
        print("[feworld] minimap calibration: %d map half/halves have "
              "their own anchors" % _n_cal, flush=True)
    with spawns._SPAWN_LOCK:
        _n_spawn = len(spawns._SPAWN)
    if _n_spawn:
        print("[feworld] spawn store: %d area(s) have their own arrival point"
              % _n_spawn, flush=True)
    if tuple(args.spawn_xyz) == (0.0, 0.0, 0.0):
        # WARNING: The single loudest thing this file can say. World (0,0,0) is the
        # centre of the terrain grid at height zero -- under the map on most of
        # them -- and it is where EVERY area with no spawn row puts the player,
        # including the fallback arrival of every warp.
        print("[feworld] WARNING: --spawn-pos is 0:0:0, so every area without a "
              "`!spawn` row drops the player at the terrain origin at height "
              "zero. Set --spawn-pos to measured ground, or stand somewhere "
              "sensible in each area and type `!spawn`. (--self-pos is a "
              "DIFFERENT record and does not cover this.)", flush=True)
    elif tuple(args.spawn_xyz) != tuple(args.self_xyz):
        print("[feworld] WARNING: --spawn-pos %s and --self-pos %s disagree: the "
              "client is told two different places it is standing."
              % (args.spawn_xyz, args.self_xyz), flush=True)
    zones._FIELD_ROOMS = zones.parse_field_rooms(args.field_rooms)
    if args.enter_room != -1:
        print("[feworld] WARNING: --enter-room %d is GLOBAL: every group without a "
              "--field-rooms entry now enters an indoor room instead of the "
              "Hmap battlefield. It does NOT affect a capital -- the capital "
              "test comes first. If you meant 'one field is a shop door', that "
              "is --field-rooms." % args.enter_room, flush=True)
    for _g, _r in sorted(zones._FIELD_ROOMS.items()):
        if _g in zones.CAPITAL_GROUP_IDS:
            print("[feworld] WARNING: --field-rooms %d:%d has NO EFFECT -- %d is a "
                  "capital id, and the entry branch tests that FIRST "
                  "(0x04ff9d5e), so the capital loader runs and the room index "
                  "is never read. Put the room on a war-field group instead."
                  % (_g, _r, _g), flush=True)
        elif _r == -1:
            print("[feworld] group %d forced OUTDOORS (the Hmap battlefield)"
                  % _g, flush=True)
        else:
            print("[feworld] group %d is a DOOR into room %d (%s)"
                  % (_g, _r, zones.room_label(_r)), flush=True)
    for _isl, _gs in sorted(zones._CAPITALS.items()):
        for _g in _gs:
            if _g in zones.CAPITAL_INCOMPLETE:
                print("[feworld] WARNING: --capital %d:%d -- our W20-0011 mirror does "
                      "NOT serve %s. Every other capital id is complete; if the "
                      "town half-loads, that is why, and 39/57/62/78 are the "
                      "ones to use."
                      % (_isl, _g, zones.CAPITAL_INCOMPLETE[_g]), flush=True)
        if territory.world_from_dat(args):
            # Don't let the log claim something the world no longer does:
            # group_ids_for takes the island's membership from fet_area in dat
            # mode, so --capital contributes nothing and the id sits on
            # whichever island the table puts it on, not this one.
            print("[feworld] WARNING: --capital %d:%s is INERT under --world dat -- "
                  "the table already places every capital (%s is on island %s). "
                  "It is read only to validate the id."
                  % (_isl, ",".join(str(g) for g in _gs), _gs[0],
                     fegamedata.areas().get(_gs[0], {}).get("island", "?")),
                  flush=True)
        else:
            print("[feworld] island %d also announces CAPITAL group(s) %s"
                  % (_isl, ", ".join(str(g) for g in _gs)), flush=True)
    # WARNING: A ZERO FACING IS NOT A FACING -- MEASURED LIVE 2026-08-25.
    # felive --avatar in the field read the player unit fully dressed
    # (+0x3c=1, +0x38=1, +0x1360 non-zero, slot 0 set, face and hair models
    # both non-null) and STILL invisible, with one field wrong:
    #     facing [+0x1e4] = (0.0000, 0.0000, 0.0000)
    # against (0, 0, 1) measured on 2026-08-24, before this record started
    # writing the field. mask2 bit 2's arm (0x0503ad49..0x0503adaf) reads
    # three f32 into +0x1e4/+0x1e8/+0x1ec, and 0x05078e40 consumes them as
    # `look-at = position + facing`, componentwise, feeding
    # 0x4fb8ad0(&[unit+0xc0], &at, &eye) -- the unit's world matrix. All
    # zero makes `at` EQUAL `eye`, the forward axis zero-length and the
    # basis degenerate, so the mesh rasterises nothing while the position
    # (0, 15.948, 0) stays valid and the SHADOW still draws. That is exactly
    # the symptom reported live.
    #
    # Refused HERE and not at the send: a SystemExit inside add_entities
    # would kill a live field entry, dropping the bit quietly would
    # desynchronise the record (three fewer floats), and substituting a
    # value silently is how a served zero launders into a measurement.
    # Fail before the listener opens, or not at all.
    if (args.add_mask2 & 0x4) and args.add_self == "on" \
            and not any(args.spawn_dir_xyz):
        raise SystemExit(
            "--add-mask2 bit 2 serves the FACING at unit+0x1e4, and "
            "--spawn-dir is 0:0:0. A zero-length direction makes the "
            "client's own look-at target equal its position and the "
            "character renders as nothing but its shadow (measured live "
            "2026-08-25). Pass a unit vector -- 0:0:1 is the default -- or "
            "clear bit 2 with --add-mask2 1 to leave the facing alone.")
    rows = []
    for spec in (args.distribution_rows or "").split(","):
        spec = spec.strip()
        if not spec:
            continue
        bits = spec.split(":")
        if len(bits) != 4:
            raise SystemExit("--distribution-rows wants A:B:CLASS:CHARID per "
                             "row, got %r" % spec)
        try:
            rows.append(tuple(int(x, 0) for x in bits))
        except ValueError:
            raise SystemExit("--distribution-rows: %r is not four numbers"
                             % spec)
    if len(rows) > 0xFF:
        # The client clamps to 0xFF itself (0x05010d70) and then reads only
        # that many rows -- so a longer list does not truncate, it DESYNCHRONISES
        # the stream. Refuse rather than send it.
        raise SystemExit("--distribution-rows: %d rows, but the client clamps "
                         "the count to 255 and would leave the rest of the "
                         "body unread" % len(rows))
    args.distribution_rows = rows

    # WARNING: THIS USED TO BE `args.doors = []`, AND IT KILLED THE SHIPPED DOOR
    # TABLE OUTRIGHT. `--doors` is a MODE ("dat"/"off"); `--door` is a repeated
    # GID:X:Z:ROOM row. argparse gives them dests `doors` and `door`, and this
    # line then overwrote the mode string with the row list -- so
    # dat_door_for's guard `getattr(args, "doors", "off") != "dat"` compared a
    # LIST to "dat", was always true, and returned None every single time.
    # fet_area_portal has therefore never been consulted once: every door fell
    # through to --door-default with the spawn fallback for a position, which
    # is exactly "I walked through a door and ended up nowhere near it".
    # The rows now have their own name.
    args.door_rows = []
    for _spec in args.door or []:
        try:
            _g, _x, _z, _r = _spec.split(":")
            args.door_rows.append((int(_g, 0), float(_x), float(_z), int(_r, 0)))
        except ValueError:
            raise SystemExit("--door wants GID:X:Z:ROOM, got %r" % _spec)
    for _g, _x, _z, _r in args.door_rows:
        print("[feworld] door in field %d at (%g, %g) -> room %d (%s)"
              % (_g, _x, _z, _r, zones.room_label(_r)), flush=True)
    args.buildings = buildings.parse_buildings(args.building)
    if len(args.building_hp) != 2:
        raise SystemExit("--building-hp wants A:B")
    for _gid, _t, _m, _gx, _gz in args.buildings:
        print("[feworld] group %d gets building type %d (%s) model %d (%s%s) "
              "at grid (%d,%d)"
              % (_gid, _t, buildings.BUILDING_TYPES[_t], _m, buildings.BUILDING_MODELS[_m],
                 " = door into " + buildings.BUILDING_ROOM_MODELS[_m]
                 if _m in buildings.BUILDING_ROOM_MODELS else "", _gx, _gz), flush=True)

    args.force_table = territory.parse_forces(args.forces)
    territory.apply_force_targets(args.force_table, args)
    # KEY: EXTENSION THREADS START HERE -- after EVERY args.* main() derives
    # (force_table, the stores, the building list), just before the door
    # listens. They were started ~170 lines earlier on 2026-09-12 and femap's
    # first snapshot ran before force_table existed: its first Discord post
    # carried no nations (corrected by its own next edit a minute later).
    for _fn in ext.EXT_START:
        try:
            _fn(args)
        except Exception:                              # noqa: BLE001
            import traceback
            print("[feworld] EXTENSION start %r failed -- world door still up:"
                  % (getattr(_fn, "__module__", "?"),), flush=True)
            traceback.print_exc()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", args.port))
    srv.listen(4)
    print("[feworld] FE WORLD DOOR on 0.0.0.0:%d -- handshake then CAPTURE"
          % args.port, flush=True)
    print("[feworld] %s" % ("runs until stopped" if args.captures <= 0
                            and args.life <= 0 else
                            "exits after %d capture(s) / %ds"
                            % (args.captures, args.life)), flush=True)

    # Tell the deploy gate when an FE world session is live, so a push does not
    # bounce feworld mid-field (memory: feworld-push-restarts-live-session).
    # One named thread per session already exists, so counting them IS the
    # liveness metric -- no per-session change.
    try:
        import live_sessions
        live_sessions.start_heartbeat(
            "feworld", lambda: live_sessions.thread_count("feworld-"))
    except Exception as _e:
        print("[feworld] live-session heartbeat not started: %r" % _e, flush=True)

    got, deadline = 0, time.time() + (args.life or 0)
    while ((args.captures <= 0 or got < args.captures)
           and (args.life <= 0 or time.time() < deadline)):
        if args.life > 0:
            srv.settimeout(max(1.0, deadline - time.time()))
        try:
            conn, peer = srv.accept()
        except (OSError, IOError):
            continue
        got += 1
        print("\n[feworld] CONNECT from %s:%d" % peer, flush=True)
        # THREADED for the same reason felobby is: this loop used to serve one
        # connection to completion -- up to --read-window 120s -- before
        # accepting the next, so a second player's FE sat unanswered on a
        # connected socket. Every per-session value now lives on _TLS
        # (_SESSION, _OUT_SEQ, _LAST_IN); the character store is felobby's and
        # is written through its atomic save.
        threading.Thread(target=readloop._serve_one, args=(conn, peer, args),
                         name="feworld-%s-%d" % peer, daemon=True).start()
    srv.close()
    # Wait out sessions in flight -- these are daemon threads, so returning
    # would kill a live world session mid-frame, and a capture run reaches its
    # --captures limit at ACCEPT, before the session has done anything.
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.name.startswith("feworld-"):
            t.join(timeout=max(args.read_window, 5.0) + 5.0)
    print("[feworld] listener closed", flush=True)
