"""What the campaign extension says about a field, and the per-field head count."""
import sys
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import extrun

def campaign_war_cap(args):
    """The per-side cap a war is fought under (SE warsystem01: 50 v 50), or 0
    when no campaign is running -- the `/%d` half of D%d/%d and A%d/%d."""
    if getattr(args, "campaign", "off") != "on":
        return 0
    mod = sys.modules.get("fecampaign")
    if mod is None or not hasattr(mod, "war_cap"):
        return 0
    try:
        return int(mod.war_cap(args) or 0)
    except Exception:                                  # noqa: BLE001
        return 0


def campaign_side_count(args, area, side):
    """Sign-ups on `side` of `area`'s live war, or None when no war is on."""
    if getattr(args, "campaign", "off") != "on":
        return None
    mod = sys.modules.get("fecampaign")
    if mod is None:
        return None
    try:
        r = mod.state_of(int(area))
        if r.get("phase") in (mod.PREP, mod.WAR):
            return int((r.get("signups") or {}).get(side, 0) or 0)
    except Exception:                                  # noqa: BLE001
        return None
    return None


def field_census(args):
    """{area: players in it} over every live session -- what the continent
    map's per-field MARKER is chosen from.

    KEY: 2026-09-12, and it is why the map had no field markers at all. The
    marker atlas `Data\\Window\\mapselect.tex` holds two sheets: `Mark`, four
    outline shapes (a circle and a pin, large and small -- the selection
    cursor), and `Mark2`, which is FOUR PERSON SILHOUETTES OF DECREASING SIZE
    LABELLED 「100~」「50~」「10~」「~9」 plus a CROWN. So a field's marker is a
    POPULATION TIER, not a nation flag -- there is no per-nation crest in the
    atlas at all -- and the crown is the capital.

    The counts on the wire are `chars_all` (+0x0c, the client logs it as
    "(ALL%d)"), `chars_f` (+0x0e, "(F%d)") and `def_chars` (+0x7c, "D%d"), at
    mask bits 2, 3 and 11. Prod's group mask was 0x00369681 -- bits 2, 3 and 11
    ALL CLEAR. We had never sent a population for any field, so the client had
    nothing to size a marker from and drew none. SE's own continent-map page
    lists a 国アイコン per field as well ("see the box below", a box the Wayback
    capture does not include); whatever draws that, it is not in this atlas,
    so it stays open.

    Counted from the live sessions, which is the only true answer -- and it is
    also what makes the marker move as people gather for a war."""
    if str(getattr(args, "field_census", "on") or "off") != "on":
        return {}
    # WARNING: THE PROBE, and why it exists. A tester looking at the continent map
    # is usually out of every field, so with one player every field reports 0
    # and a client that draws no silhouette for "nobody here" is
    # indistinguishable from one that ignores the field entirely. WARNING: THIS
    # COMMENT USED TO SAY "to field OUT -- `in_field` goes false" AS THOUGH IT
    # WERE A FACT. It was not: nothing cleared that flag until the Field Out
    # handler was taught to (2026-09-12), which is why the population marker
    # stayed on a field the player had left. --field-census-probe N reports N for every field for one run:
    # if a marker appears, chars_all IS the marker source and it only ever
    # needed real players; if none appears, it is not, and the hunt moves on.
    # A single-variable A/B, not a loop.
    probe = int(getattr(args, "field_census_probe", 0) or 0)
    if probe > 0:
        out = {int(a): probe for a in fegamedata.areas()}
        print("[feworld]    FIELD CENSUS PROBE: reporting %d players in every "
              "one of the %d fields (--field-census-probe). This is NOT a real "
              "count -- set it back to 0 after the run." % (probe, len(out)),
              flush=True)
        return out
    out = {}
    try:
        for ent in extrun.ext_sessions():
            ps = ent["session"]
            if not ps.get("in_field"):
                continue
            a = ps.get("field")
            if a is None:
                continue
            out[int(a)] = out.get(int(a), 0) + 1
    except Exception:                                  # noqa: BLE001
        return {}
    if out:
        print("[feworld]    field census: %s"
              % ", ".join("area %d: %d" % (a, n) for a, n in sorted(out.items())),
              flush=True)
    return out


def field_census_nations(args):
    """{area: {nation: players of that nation standing in it}}.

    KEY: A peer's nation is its presence card's `force`, NOT `ps["nation"]` --
    that key does not exist on a session, and reading it is what made the
    arrow tower shoot its own army on 2026-09-12 (fepvp._nation_of_session has
    always had this right). Same mistake, same file, twice in one day: read it
    the proven way."""
    if str(getattr(args, "field_census", "on") or "off") != "on":
        return {}
    out = {}
    try:
        for ent in extrun.ext_sessions():
            ps = ent["session"]
            if not ps.get("in_field"):
                continue
            a = ps.get("field")
            if a is None:
                continue
            try:
                n = int((ps.get("pres_card") or {}).get("force") or 0)
            except (TypeError, ValueError):
                n = 0
            if n:
                out.setdefault(int(a), {})[n] = out.setdefault(int(a), {}).get(n, 0) + 1
    except Exception:                                  # noqa: BLE001
        return {}
    return out


def campaign_attacker(args, area):
    """The nation attacking `area` right now, or 0 -- the live campaign's own
    answer, for the map record's atk_id. A thin lookup like campaign_phase, so
    feworld keeps not depending on its extensions."""
    if getattr(args, "campaign", "off") != "on":
        return 0
    mod = sys.modules.get("fecampaign")
    if mod is None:
        return 0
    try:
        r = mod.state_of(int(area))
        if r.get("phase") in (mod.PREP, mod.WAR, mod.TRUCE):
            return int(r.get("atk") or 0)
    except Exception:                                  # noqa: BLE001
        return 0
    return 0


def campaign_phase(args, area):
    """The live war phase for `area`, or None when no campaign is running.

    A thin lookup rather than an import, so feworld keeps not depending on its
    extensions: fecampaign registers itself, and if it is absent or switched
    off this returns None and the constant stands.
    """
    if getattr(args, "campaign", "off") != "on":
        return None
    mod = sys.modules.get("fecampaign")
    if mod is None:
        return None
    try:
        return mod.phase_of(int(area))
    except Exception:                                  # noqa: BLE001
        return None


def campaign_deadline_ms(args, area):
    """Milliseconds left in `area`'s campaign phase, or None when no campaign
    owns it. war_notify reads it so the client's countdown is the campaign's
    own clock. Same thin lookup as campaign_phase."""
    if getattr(args, "campaign", "off") != "on" or area is None:
        return None
    mod = sys.modules.get("fecampaign")
    if mod is None:
        return None
    try:
        return mod.deadline_ms_of(int(area))
    except Exception:                                  # noqa: BLE001
        return None


def campaign_report(args):
    """The nation's news a Manager gives: fecampaign.report, or none."""
    mod = sys.modules.get("fecampaign")
    if mod is None:
        return "There is no news from the front today."
    try:
        return mod.report(args)
    except Exception as e:                             # noqa: BLE001
        return "The Manager shuffles papers and says nothing useful. (%s)" % e


def campaign_force_vals(args, force_id, vals):
    """The 0x3027 nation record with fecampaign's LIVE numbers (territory,
    wars, wins, population, King's message) instead of zeros; `vals` as-is
    when the module is absent. Same thin lookup as campaign_phase."""
    mod = sys.modules.get("fecampaign")
    if mod is None or not hasattr(mod, "force_vals"):
        return vals
    try:
        return mod.force_vals(args, force_id, vals)
    except Exception as e:                             # noqa: BLE001
        print("[feworld]    nation record: fecampaign.force_vals failed (%s) "
              "-- served the static row" % e, flush=True)
        return vals
