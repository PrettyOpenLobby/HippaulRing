"""The extension seam: the tables fe<feature>.py modules register into, and load_extensions()."""
from . import deps, messages

# ---------------------------------------------------------------------------
# THE EXTENSION SEAM (2026-09-10). feworld.py is 13k lines and every FE feature
# so far was written INTO it. The 2026-08-27 scope sweep left 153 message ids
# untouched across eight whole features (party, mail, trade, unit state, war
# entities, items, force admin, GM), and eight people editing one file's elif
# chain at once is a merge nobody survives. So: a feature lives in ITS OWN
# MODULE (services/fe<feature>.py) that exposes `register(fw)`, receives THIS
# module object -- never `import feworld`, which under `python feworld.py`
# would load a SECOND copy with its own _TLS/_SESSION -- and registers what it
# serves through the five tables below. Nothing here changes what any
# existing id does: the ext dispatch runs FIRST in _serve_loop's chain, and
# register_handler refuses an id this file already answers unless the module
# says override=True on purpose.
#
#   EXT_HANDLERS  inner id -> fn(ctx, inner)     the whole inner (id included);
#                                                return False to DECLINE it and
#                                                let this file's own arm answer
#                                                (anything else = handled)
#   EXT_GM        fn(ctx, line) -> bool          a `!verb` in --gmcmd-file
#   EXT_ARGS      fn(argparse.ArgumentParser)    knobs; READ THEM WITH getattr
#                                                (the test harness builds args
#                                                by hand and will not have them)
#   EXT_PUMPS     fn(ctx)                        once per inbound message, on
#                                                this session's thread, next to
#                                                chat_pump -- timers go here
#   EXT_RELAY     kind -> fn(ctx, payload)       runs on the RECEIVING session's
#                                                thread; post with ext_post()
#
# `ctx` is a Ctx: .conn .outbound .mode .be .args .session, and ctx.reply(mid,
# body, unit_id=None, why="") sends one inner message the way every arm in this
# file does. A module's register() may also fw.KNOWN.setdefault() names.
EXT_HANDLERS = {}
EXT_GM = []
EXT_ARGS = []
EXT_PUMPS = []
#: Called once from main() after the arguments are parsed, with `args` --
#: for an extension that runs its OWN thread (femap's page and Discord pump),
#: which a per-session pump cannot do: pumps only run on a player's thread.
EXT_START = []
EXT_RELAY = {}
#: In load order. A module that is NOT PRESENT is skipped with one log line
#: (they land one at a time); a module that is present and fails to import
#: raises, because a feature silently missing looks exactly like a client hang.
EXT_MODULES = ("feparty", "femail", "fetrade", "feunit", "fewar", "feitems",
               "feforce", "fegm", "fecampaign", "feprog", "fepresence",
               "fepvp", "femap")
_EXT_LOADED = set()
#: ids answered by a hard-coded `elif real_id == ...` in _serve_loop. Kept by
#: hand (grep "elif real_id" to refresh); it is a guard against two owners,
#: not a proof of coverage.
_BUILTIN_IDS = frozenset((
    0x0020, 0x206A, 0x400A, 0x4015, 0x4011, 0x2018, 0x2084, 0x400B, 0x4006,
    0x4014, 0x4012, 0x2000, 0x2017, 0x20AC, 0x2046, 0x20A7, 0x20A8, 0x2085,
    0x2099, 0xE00F, 0x204B, 0x2021, 0x2022, 0x2098, 0x204A, 0x2049, 0x20A0,
    0x20A1, 0x20A2, 0x2080, 0xF900, 0xA011, 0x209F, 0x2002, 0x2023, 0x400F,
))


def register_handler(mid, fn, override=False):
    """Claim an inner id for an extension. Refuses an id this file already
    answers (a table row or a hard-coded arm) unless override=True -- the
    shadowing is then deliberate and named in the module."""
    owned = (mid in messages.UI_HEADER_ONLY_OK or mid in messages.UI_REPLY_BODY
             or mid in messages._CHAT_IDS or mid in _BUILTIN_IDS)
    if owned and not override:
        raise ValueError("0x%04X is already answered by feworld.py; pass "
                         "override=True to shadow it on purpose" % mid)
    if mid in EXT_HANDLERS and EXT_HANDLERS[mid] is not fn:
        raise ValueError("0x%04X claimed twice (%r and %r)"
                         % (mid, EXT_HANDLERS[mid], fn))
    EXT_HANDLERS[mid] = fn


def register_gm(fn):
    EXT_GM.append(fn)


def register_args(fn):
    EXT_ARGS.append(fn)


def register_pump(fn):
    EXT_PUMPS.append(fn)


def register_start(fn):
    EXT_START.append(fn)


def register_relay(kind, fn):
    if kind in EXT_RELAY and EXT_RELAY[kind] is not fn:
        raise ValueError("relay kind %r claimed twice" % (kind,))
    EXT_RELAY[kind] = fn


def load_extensions(names=None):
    """Import and register every extension module. Idempotent per module, so
    tests may call it after importing feworld and again after their own
    module. Returns the names registered THIS call."""
    import importlib
    me = deps.facade()
    done = []
    for name in (names or EXT_MODULES):
        if name in _EXT_LOADED:
            continue
        try:
            mod = importlib.import_module(name)
        except ImportError as e:
            if getattr(e, "name", None) == name:
                print("[feworld] extension %s: not present, skipped" % name,
                      flush=True)
                continue
            raise
        mod.register(me)
        _EXT_LOADED.add(name)
        done.append(name)
    if done:
        print("[feworld] extensions: %s (%d ids, %d !verbs, %d relays)"
              % (" ".join(done), len(EXT_HANDLERS), len(EXT_GM),
                 len(EXT_RELAY)), flush=True)
    return done
