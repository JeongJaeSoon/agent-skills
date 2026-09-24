#!/usr/bin/env python3
"""Sync the vendored pstack subset against cursor/plugins with a three-way merge.

Usage: python3 scripts/pstack-sync.py [--to REF] [--write] [--manifest PATH]

Dry run by default: one row per vendored file, nothing touched. --write applies new, clean and
merged files and advances the pin, and only when no file conflicts. The merge base is read from
upstream git at the pinned SHA, so no base copy is stored here.
"""
import json, os, re, subprocess, sys, tempfile, pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
CACHE = os.path.expanduser(os.environ.get("PSTACK_CACHE", "~/.cache/pstack-upstream"))
# Cursor-only tokens that should not survive in an adapted file. Advisory: negated mentions
# ("never require gt") also match, so a hit is a prompt to look, not a failure.
DENY = [r"\.cursor/", r"agent-transcripts", r"cursor-team-kit", r'environment: ?"cloud"',
        r"cloud_base_branch", r"\bAskQuestion\b", r"\bgeneralPurpose\b", r"\breadonly: ?(true|false)",
        r"pstack-models\.mdc", r"\bgrok-\d", r"\bgt\b", r"control-(ui|cli)", r"\bcreate-skill\b", r"/deslop\b"]


def git(*args, check=True):
    r = subprocess.run(["git", "-C", CACHE, *args], capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r


def show(ref, path):
    r = git("show", f"{ref}:{path}", check=False)
    return r.stdout if r.returncode == 0 else None


def merge3(local, base, new):
    with tempfile.TemporaryDirectory() as d:
        p = {k: os.path.join(d, k) for k in ("local", "base", "new")}
        for k, v in (("local", local), ("base", base or ""), ("new", new)):
            pathlib.Path(p[k]).write_text(v)
        r = subprocess.run(["git", "merge-file", "-p", "-L", "local", "-L", "base", "-L", "upstream",
                            p["local"], p["base"], p["new"]], capture_output=True, text=True)
    return r.stdout, r.returncode


def main():
    argv = sys.argv[1:]
    manifest_path = pathlib.Path(argv[argv.index("--manifest") + 1]) if "--manifest" in argv else REPO / "vendor/pstack/manifest.json"
    to = argv[argv.index("--to") + 1] if "--to" in argv else "origin/main"
    write = "--write" in argv
    m = json.loads(manifest_path.read_text())

    if not os.path.isdir(CACHE):
        subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout", m["remote"], CACHE], check=True)
    git("fetch", "-q", "origin")
    pin, target = m["pin"], git("rev-parse", to).stdout.strip()
    up = m["upstream_root"]

    log = git("log", "--format=%h %cs %s", f"{pin}..{target}", "--", up).stdout.strip()
    print(f"pin {pin[:12]} -> {target[:12]}\nupstream commits touching {up}/:\n{log or '  (none)'}\n")

    rows, blocked, writes = [], 0, {}
    for f in m["files"]:
        base, new = show(pin, f"{up}/{f['upstream']}"), show(target, f"{up}/{f['upstream']}")
        local_path = REPO / f["local"]
        local = local_path.read_text() if local_path.exists() else None
        if new is None:
            rows.append((f["upstream"], "deleted-upstream", "")); blocked += 1; continue
        if local is None:
            merged, state = new, "new"
        elif base == new:
            # A verbatim file must still equal upstream even when upstream did not move.
            drift = f["mode"] == "verbatim" and local != base
            rows.append((f["upstream"], "drift" if drift else "unchanged", "")); blocked += drift; continue
        elif local == base:
            merged, state = new, "clean"
        else:
            merged, n = merge3(local, base, new)
            state = "merged" if n == 0 else f"conflict({n})"
            if f["mode"] == "verbatim":
                # A verbatim file edited locally is no longer verbatim; the manifest has to say so.
                state = "drift+" + state
            blocked += n > 0 or f["mode"] == "verbatim"
        hits = sorted({p for p in DENY if re.search(p, merged)}) if f["mode"] == "adapted" else []
        rows.append((f["upstream"], state, ",".join(hits)))
        writes[local_path] = merged

    tracked = {f["upstream"] for f in m["files"]}
    names = git("ls-tree", "-r", "--name-only", target, "--", f"{up}/skills").stdout.split()
    untracked = sorted(n[len(up) + 1:] for n in names if n.endswith("SKILL.md") and n[len(up) + 1:] not in tracked)

    w = max(len(r[0]) for r in rows)
    for name, state, hits in rows:
        print(f"{name:<{w}}  {state:<16} {('denylist: ' + hits) if hits else ''}")
    print(f"\nupstream SKILL.md files not vendored: {len(untracked)}")

    if not write:
        return
    if blocked:
        sys.exit(f"{blocked} blocked file(s): nothing written, pin stays at {pin[:12]}")
    for path, text in writes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if path.suffix == ".sh":
            path.chmod(0o755)
    m["pin"] = target
    plugin = show(target, f"{up}/.cursor-plugin/plugin.json")
    if plugin:
        m["upstream_version"] = json.loads(plugin).get("version", m.get("upstream_version"))
    manifest_path.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(writes)} file(s), pin -> {target[:12]} (version {m.get('upstream_version', '?')})")


if __name__ == "__main__":
    main()
