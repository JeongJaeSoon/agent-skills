#!/usr/bin/env python3
"""registry.py: the code on this checkout keeps the registry, and each kind of break is caught."""
import dataclasses, pathlib, sys, tempfile, textwrap

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import registry  # noqa: E402
from registry import CANONICAL, DERIVED, Store  # noqa: E402

tmp = pathlib.Path(tempfile.mkdtemp())


def module(name, src):
    p = tmp / f"{name}.py"
    p.write_text(textwrap.dedent(src))
    return p


# The scripts as they are: no violation beyond the known two-canonical facts the registry lists.
real = registry.scan()
found = registry.check(writes=real)
assert found == [], "\n".join(f"{v['kind']} {v['key']}: {v['detail']}" for v in found)
assert len({p.stem for p in registry.sources()}) == len(registry.sources()), "two scripts share a module name"
writers = {w.writer for w in real}
for must in ("fleet:Fleet.tick", "board:change", "tidy:append", "patrol:write_round", "reorient:declare"):
    assert must in writers, must  # the scan sees writes through wrappers, **kwargs and self attributes

# The scan follows a wrapper to its callers, `path or DEFAULT`, a temporary renamed over the file, and **{name: text}.
scan_src = module("fx_scan", '''
    import os, pathlib
    STATE = pathlib.Path("/s")
    LOG = STATE / "fx.log"

    def write_json(path, data):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(data)
        tmp.replace(path)

    def save_board():
        write_json(STATE / "fx-board.json", "{}")

    def log(msg, path=None):
        path = path or LOG
        with path.open("a") as f:
            f.write(msg)

    class Keeper:
        def save(self, **files):
            for name, text in files.items():
                write_json(STATE / name, text)

        def tick(self):
            self.save(**{"fx-state.json": "{}"})

    def unnamed(p):
        os.replace("x", p)
''')
got = {(w.writer, w.targets) for w in registry.scan([scan_src])}
assert ("fx_scan:save_board", ("s/fx-board.json",)) in got, got  # the name and the directory it sits in
assert ("fx_scan:log", ("s/fx.log",)) in got, got
assert ("fx_scan:Keeper.tick", ("fx-state.json",)) in got, got
assert ("fx_scan:unnamed", ()) in got, got  # a sink nobody calls keeps its write, unnamed
assert not any(w[0] == "fx_scan:write_json" for w in got), got  # the wrapper's own write is its callers'
assert len(got) == 4, got

# Forms that once hid a write: a bound method's argument, a wrapper writing two paths, a reassigned path,
# with_name on a parameter, open() with keywords or from io.
forms = module("fx_forms", '''
    import io, pathlib
    D = pathlib.Path("/d")

    class Box:
        def put(self, path):
            path.write_text("x")

        def go(self):
            self.put(D / "fx-bound.json")

    def two(a, b):
        a.write_text("1")
        b.write_text("2")

    def call_two():
        two(D / "fx-a.json", D / "fx-b.json")

    def again():
        p = D / "fx-first.json"
        p.write_text("1")
        p = D / "fx-second.json"
        p.write_text("2")

    def side(path):
        path.with_name("fx-side.json").write_text("x")

    def opens():
        open(file=D / "fx-kw.json", mode="w").close()
        io.open(D / "fx-io.json", "a").close()
''')
got = {(w.writer, w.targets) for w in registry.scan([forms])}
for want in [("fx_forms:Box.go", ("d/fx-bound.json",)), ("fx_forms:call_two", ("d/fx-a.json",)),
             ("fx_forms:call_two", ("d/fx-b.json",)), ("fx_forms:again", ("d/fx-first.json",)),
             ("fx_forms:again", ("d/fx-second.json",)), ("fx_forms:side", ("fx-side.json",)),
             ("fx_forms:opens", ("d/fx-kw.json",)), ("fx_forms:opens", ("d/fx-io.json",))]:
    assert want in got, (want, got)

# (1) a fact with two canonical stores
two = registry.STORES + [Store("fxcopy", "D/fx-copy.json", {"decision.item": CANONICAL}, (), ("brief",))]
v = registry.check(stores=two, writes=real)
assert [(x["kind"], x["key"]) for x in v] == [("dual", "decision.item")], v
# a third canonical store for a fact the known-duplicate list allows only between two
three = registry.STORES + [Store("fxthird", "D/fx-third.json", {"work.unit": CANONICAL}, (), ("brief",))]
v = registry.check(stores=three, writes=real)
assert [(x["kind"], x["key"]) for x in v] == [("dual", "work.unit")], v
# a derived copy is fine
ok = registry.STORES + [Store("fxcopy", "D/fx-copy.json", {"decision.item": DERIVED}, (), ("brief",))]
assert registry.check(stores=ok, writes=real) == []

# (2) a write to a file no store names, and (3) a write from a function the store does not list
rogue = module("fx_rogue", '''
    import pathlib, fleet
    D = pathlib.Path("~/.local/state/agent-skills/dashboard")

    def keep_notes():
        fleet.write_atomic(D / "mystery.json", "{}")

    def sneak():
        fleet.append_line(D / "tidy.jsonl", {"ev": "done"})

    def broad():
        (D / "anything.json").write_text("{}")
''')
broken = registry.scan(registry.sources() + [rogue])
v = registry.check(writes=broken)
kinds = sorted((x["kind"], x["key"]) for x in v)
assert ("unregistered", "dashboard/mystery.json") in kinds, v
assert ("writer", "fx_rogue:sneak") in kinds, v
assert ("unregistered", "dashboard/anything.json") in kinds, v  # `P/*/*`-style wildcards name files only through their writers
assert len(v) == 3, v
# A registered writer writing a registered name in another directory is not that store's write.
elsewhere = [registry.Write("board", "change", 1, ("elsewhere/board.json",))]
v = registry.check(writes=real + elsewhere)
assert [(x["kind"], x["key"]) for x in v] == [("unregistered", "elsewhere/board.json")], v
# Registered, the same writes pass.
fixed = [dataclasses.replace(s, writers=s.writers + ("fx_rogue:sneak",)) if s.id == "tidy" else s for s in registry.STORES]
fixed.append(Store("fxnotes", "D/mystery.json", {"fx.note": CANONICAL}, ("fx_rogue:keep_notes",), ("nobody",)))
fixed.append(Store("fxany", "D/anything.json", {"fx.any": CANONICAL}, ("fx_rogue:broad",), ("nobody",)))
assert registry.check(stores=fixed, writes=broken) == []

# The registry itself: a writer that is no function, an unknown reader, root or role, a stale known-duplicate entry.
bad = registry.STORES + [Store("fxbad", "Q/x.json", {"fx.bad": "owner"}, ("fleet:no_such",), ("nowhere",))]
v = registry.check(stores=bad, writes=real, dual={**registry.DUAL_CANONICAL, "fx.gone": "fixed long ago"})
details = sorted(x["detail"] for x in v if x["kind"] == "registry")
assert details == sorted(["unknown root 'Q'", "fx.bad: unknown role 'owner'", "unknown reader 'nowhere'",
                          "writer fleet:no_such is not a function in the scripts",
                          "listed in DUAL_CANONICAL but has one canonical store now: drop it from the list"]), details

# What data-inventory names lives in the registry, outside sources included.
paths = {s.path for s in registry.STORES}
for p in ("D/tidy.jsonl", "D/acks.jsonl", "S/patrol-verdicts.json", "S/patrol.jsonl", "S/card-watch/*.json",
          "P/_locks/*.lock", "S/pressure-queue.json", "S/janitor-plan.json", "S/reap-hard.jsonl", "S/preview-watch.json",
          "S/ledger.jsonl", "S/transcripts/*.json", "D/decisions.json", "ext:orca", "ext:github"):
    assert p in paths, p

print("test_registry: ok")
