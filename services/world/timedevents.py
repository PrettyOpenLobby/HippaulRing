"""SE's timed events on a schedule: drop and EXP multipliers, the Dragon feast, the shop sale."""
import calendar as _cal
import os
import threading
import time
from . import deps

# ---------------------------------------------------------------------------
# KEY: SE'S 2006 TIMED EVENTS (2026-10-22). The archived notices
# (www.playonline.com/fe/polnews) show SE running its events as patch-day
# switches with an end time, all of them server side except the quests:
#
#   * 2006-06-15 .. 06-22 14:00  higher drop rate for "certain items"
#     (polnews 7785 / 7791; SE named neither the items nor the factor)
#   * 2006-09-21 .. 10-05        a limited-time quest and a sale in every
#     shop (polnews 8578 / 8680)
#   * 2006-10-05 .. 10-19 14:00  the Fantasy Earth Double Up Campaign: 2x
#     EXP from monster kills and from wars, 2x monster drops (polnews 8680)
#   * 2006-10-05 .. 10-19        a second limited-time quest
#   * 2006-10-19 .. 10-26 14:00  錬金成功！？戦竜の饗宴: the Dragon summon
#     for both armies from the start of a war whatever the war situation,
#     an item needed, at most 5 per army (polnews 8785 / 8816 / 8887)
#
# THE TABLE: fedata/fe-timed-events.tsv (its header says every column), one
# row per (event, effect), read by name like hippaulfront's event battles.
# SE ran each event ONCE; repeating it every year on the same dates is OURS.
# The quests are recorded with served = 0: a quest is a conversation on the
# event VM, which is not built, so nothing is served for them and they are
# not on the Calendar.
#
# Where each effect is applied (each logs a line naming the event when it
# changes a number):
#   drop_rate     drops.drop_roll           kill_exp  combat.kill_rewards
#   war_exp       fecampaign.settle         shop_sale shops.shop_stock
#   dragon_feast  feunit._war_rules / dragon_item
#
# --timed-events: 'table' (default) = the shipped file, 'off' = none, else a
# path to another table.
# ---------------------------------------------------------------------------
EVENTS_TSV = os.path.join(deps._HERE, "fedata", "fe-timed-events.tsv")
JST = 9 * 3600
EFFECTS = ("drop_rate", "kill_exp", "war_exp", "dragon_feast", "shop_sale", "quest")
#: what each effect does, for the Calendar's about line
EFFECT_ABOUT = {
    "drop_rate": "monster drop rates x%s",
    "kill_exp": "monster kill EXP x%s",
    "war_exp": "war EXP x%s",
    "dragon_feast": "the Dragon for both armies from the war's start, up to %s per army",
    "shop_sale": "shop prices x%s",
}
_LOCK = threading.Lock()
_CACHE = {}


def _mmdd(text):
    """'10-19 15:30' -> (10, 19, 15, 30); raises ValueError on a bad time."""
    d, _, t = (text or "").strip().partition(" ")
    mo, dd = (int(x) for x in d.split("-"))
    hh, mi = (int(x) for x in (t or "00:00").split(":"))
    if not (1 <= mo <= 12 and 1 <= dd <= 31 and 0 <= hh <= 23 and 0 <= mi <= 59):
        raise ValueError(text)
    return mo, dd, hh, mi


def _items(text):
    out = set()
    for x in (text or "").replace(" ", "").split(","):
        if x:
            out.add(int(x, 0))
    return frozenset(out)


def load_table(path=None):
    """[row] from the timed-event table at `path` (default the shipped one),
    cached per path. A row with a bad time, an unknown effect or a bad
    number is refused and logged. [] when the file is absent."""
    p = path or EVENTS_TSV
    with _LOCK:
        if p in _CACHE:
            return _CACHE[p]
    rows, refused, header = [], [], None
    try:
        fh = open(p, encoding="utf-8")
    except OSError as e:
        print("[feworld]    timed events: no table %s (%s) -- none run" % (p, e),
              flush=True)
        with _LOCK:
            _CACHE[p] = []
        return []
    with fh:
        for ln in fh:
            ln = ln.rstrip("\r\n")
            if not ln.strip() or ln.lstrip().startswith("#"):
                continue
            cols = ln.split("\t")
            if header is None:
                header = cols
                continue
            r = dict(zip(header, cols))
            try:
                eff = (r.get("effect") or "").strip()
                if eff not in EFFECTS:
                    raise ValueError("effect %r" % eff)
                row = {"event": r["event"].strip(), "name": r["name"].strip(),
                       "name_jp": (r.get("name_jp") or "").strip(),
                       "tag": (r.get("tag") or "").strip() or r["name"].strip().upper(),
                       "start": _mmdd(r["start"]), "end": _mmdd(r["end"]),
                       "effect": eff, "value": float(r.get("value") or 0),
                       "items": _items(r.get("items")),
                       "served": (r.get("served") or "1").strip() != "0",
                       "source": r.get("source", ""), "note": r.get("note", ""),
                       "news": (r.get("news") or "").strip(),
                       "announce": (r.get("announce") or "1").strip() != "0"}
                if not row["event"] or not row["name"]:
                    raise ValueError("no event id or name")
            except (KeyError, TypeError, ValueError) as e:
                refused.append("%s (%s)" % (cols[0] if cols else "?", e))
                continue
            rows.append(row)
    quiet = sorted({r["name"] for r in rows if not r["served"]})
    print("[feworld]    timed events: %d row(s) from %s%s%s" % (
        len(rows), os.path.basename(p),
        "; not served: %s" % ", ".join(quiet) if quiet else "",
        "; REFUSED %d: %s" % (len(refused), "; ".join(refused)) if refused else ""),
        flush=True)
    with _LOCK:
        _CACHE[p] = rows
    return rows


def table(args=None):
    """The rows --timed-events names: 'table' (the command line's default)
    the shipped file, 'off' none, anything else a path. An args object with
    NO timed_events (a selftest's hand-made namespace) means off, so no
    other suite's numbers move with the date it runs on; args None = the
    shipped table."""
    spec = "table" if args is None else str(
        getattr(args, "timed_events", "off") or "off").strip()
    if spec == "off":
        return []
    return load_table(None if spec == "table" else spec)


def event_on(row, now=None):
    """True when `row` is running at unix `now` (its window, JST, this year;
    a window across New Year wraps)."""
    t = time.gmtime((time.time() if now is None else float(now)) + JST)
    cur = (t.tm_mon, t.tm_mday, t.tm_hour, t.tm_min)
    lo, hi = tuple(row["start"]), tuple(row["end"])
    return lo <= cur < hi if lo <= hi else (cur >= lo or cur < hi)


def running(args, effect, now=None, rows=None):
    """The served rows of `effect` running at `now`."""
    return [r for r in (table(args) if rows is None else rows)
            if r["effect"] == effect and r["served"] and event_on(r, now)]


def multiplier(args, effect, now=None, item=None, rows=None):
    """(factor, [rows]) for a rate `effect` at `now`: the product of every
    running row's value. A row with an item list only counts for those items
    (`item` None = rows with no list only)."""
    f, hit = 1.0, []
    for r in running(args, effect, now, rows):
        if r["items"] and (item is None or int(item) not in r["items"]):
            continue
        f *= r["value"]
        hit.append(r)
    return f, hit


def tags(hit):
    """The tags of the rows in `hit`, once each: "DOUBLE UP/DROP BOOST"."""
    out = []
    for r in hit:
        if r["tag"] not in out:
            out.append(r["tag"])
    return "/".join(out)


def note(hit, what):
    """The log line an applied effect prints: 'DOUBLE UP: war EXP x2 ...'."""
    if hit:
        print("[feworld]    %s: %s" % (tags(hit), what), flush=True)


def scale(args, effect, amount, what, now=None, rows=None):
    """`amount` times `effect`'s running multiplier, as an int, logged when an
    event changed it ('DOUBLE UP: war EXP x2 (100 -> 200)')."""
    f, hit = multiplier(args, effect, now, rows=rows)
    amount = int(amount or 0)
    if not hit or f == 1.0 or amount <= 0:
        return amount
    out = max(0, int(amount * f + 1e-9))
    note(hit, "%s x%g (%d -> %d)" % (what, f, amount, out))
    return out


def dragon_feast(args, now=None, rows=None):
    """The running Dragon feast row, or None."""
    on = running(args, "dragon_feast", now, rows)
    return on[0] if on else None


# ---------------------------------------------------------------------------
# The Calendar and the news
# ---------------------------------------------------------------------------
def _occurrence(row, year):
    """(start, end) unix seconds of `row` opening in `year` (JST); an end
    before the start falls in the next year."""
    mo, dd, hh, mi = row["start"]
    lo = _cal.timegm((year, mo, dd, hh, mi, 0)) - JST
    mo, dd, hh, mi = row["end"]
    hi = _cal.timegm((year, mo, dd, hh, mi, 0)) - JST
    if hi <= lo:
        hi = _cal.timegm((year + 1, mo, dd, hh, mi, 0)) - JST
    return lo, hi


def _value(v):
    return ("%g" % v)


def entries(lo, hi, rows):
    """The served events of `rows` touching [lo, hi), one entry per event
    per year, in the admin Calendar's shape ({"id", "title", "start", "end",
    "kind", "about", "times"}, unix seconds). Pure."""
    first = time.gmtime(int(lo) + JST).tm_year - 1
    last = time.gmtime(int(hi) + JST).tm_year
    out = []
    for year in range(first, last + 1):
        by = {}
        for r in rows:
            if not r["served"]:
                continue
            a, b = _occurrence(r, year)
            if b <= lo or a >= hi:
                continue
            e = by.setdefault(r["event"], {"row": r, "times": set(), "what": []})
            e["times"].add((a, b))
            what = EFFECT_ABOUT.get(r["effect"], r["effect"]) % _value(r["value"])
            if r["items"]:
                what += " (items %s)" % ", ".join(str(i) for i in sorted(r["items"]))
            if what not in e["what"]:
                e["what"].append(what)
        for ev, e in by.items():
            r = e["row"]
            times = sorted([a, b] for a, b in e["times"])
            what = "; ".join(e["what"])
            about = "%s%s. SE's %s%s, 2006; repeated every year." % (
                what[:1].upper(), what[1:], r["name_jp"] or r["name"],
                " (%s)" % r["source"].split("(")[0].strip() if r["source"] else "")
            out.append({"id": "fe-%s-%d" % (ev, year), "title": r["name"],
                        "start": times[0][0], "end": times[-1][1],
                        "kind": "shop" if all(x["effect"] == "shop_sale" for x in rows
                                              if x["event"] == ev) else "event",
                        "about": about[:300], "times": times})
    return sorted(out, key=lambda e: (e["start"], e["end"], e["id"]))


def _dragon_named(args, rows):
    """Whether a Dragon feast can raise a Dragon at all: its rows name an
    item, or --summon-items DRAGON= does (feunit.dragon_item)."""
    if any(r["items"] for r in rows):
        return True
    try:
        import feunit
        return bool((feunit.form_spec(getattr(args, "summon_items", "") or "").get(1)
                     or [0])[0])
    except Exception:                                  # noqa: BLE001 -- no feunit, no Dragon
        return False


#: what the Calendar says of a Dragon feast that can raise no Dragon
NO_DRAGON = "Not running: no item for the Dragon is named (--summon-items DRAGON=). "
#: what the Calendar says of an event the table keeps out of the news (announce 0)
NOT_ANNOUNCED = "Running, not announced (announce 0 in the table). "


def calendar(args, lo, hi, news=False):
    """The timed events for the admin panel's Calendar (devtool's
    calendar.json), from the table --timed-events names. A Dragon feast with
    no Dragon item named changes nothing in a war, so the Calendar says so
    and the news (`news`, femap's fe:events) leaves it out: players are not
    told of a Dragon nobody can summon. The news also reads the table's
    `news` line, written for players, in place of the Calendar's description."""
    rows = table(args)
    idle, held, said = set(), set(), {}
    for ev in {r["event"] for r in rows if r["served"]}:
        mine = [r for r in rows if r["event"] == ev and r["served"]]
        if all(r["effect"] == "dragon_feast" for r in mine) and not _dragon_named(args, mine):
            idle.add(ev)
        said[ev] = next((r["news"] for r in mine if r.get("news")), "")
        if not all(r.get("announce", True) for r in mine):
            held.add(ev)
    out = []
    for e in entries(lo, hi, rows):
        ev = e["id"][len("fe-"):].rsplit("-", 1)[0]
        if ev in idle:
            if news:
                continue
            e = dict(e, about=(NO_DRAGON + e["about"])[:300])
        elif ev in held:
            if news:
                continue
            e = dict(e, about=(NOT_ANNOUNCED + e["about"])[:300])
        elif news and said.get(ev):
            e = dict(e, about=said[ev][:300])
        out.append(e)
    return out


def events_between(lo, hi, path=None):
    """THE PUBLISHED LIST, for a news module: the served events touching
    [lo, hi) (unix seconds), in the Calendar's shape plus "game": "fe".
    Reads the shipped table (or `path`) and changes nothing."""
    return [dict(e, game="fe") for e in entries(lo, hi, load_table(path))]
