#!/usr/bin/env python3
"""Report how cursor/plugins' pstack has moved past the pristine copy in vendor/pstack.

Usage: python3 scripts/pstack-sync.py [--to REF] [--notify] [--write] [--manifest PATH]

vendor/pstack/<upstream path> holds each manifest file exactly as upstream had it at the pin. The skills under
skills/ are this repo's own and nothing here touches them: an upstream change is read and, when worth it, ported
by hand. One line per file that differs, then a summary line:
  changed  upstream at REF differs from the copy
  deleted  upstream at REF no longer has the file
  new      upstream added it since the pin: a new skill, or a new file in a vendored skill
  drift    the copy no longer equals upstream at the pin (edited or missing)
--notify  a desktop notification when anything differs, once per upstream commit (the periodic job passes it)
--write   by hand only: rewrite the copies as REF has them, drop the deleted ones and advance the pin
Exits 0 whether or not anything differs; a git or fetch failure exits non-zero.
"""
import json, os, platform, subprocess, sys, pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
CACHE = os.path.expanduser(os.environ.get("PSTACK_CACHE", "~/.cache/pstack-upstream"))


def git(*args, check=True):
    r = subprocess.run(["git", "-C", CACHE, *args], capture_output=True)
    if check and r.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed: {r.stderr.decode(errors='replace').strip()}")
    return r


def out(*args):
    return git(*args).stdout.decode()


TREES = {}


def show(ref, path):
    """The file's bytes at ref, or None when ref has no such path. The cache is a blob:none clone, so a blob may
    still need the network: that failure exits here instead of reading as a deleted file that --write would drop."""
    if ref not in TREES:
        TREES[ref] = set(filter(None, out("ls-tree", "-r", "-z", "--name-only", ref).split("\0")))  # -z: no quoting
    return git("show", f"{ref}:{path}").stdout if path in TREES[ref] else None


def compare(m, vendor, target):
    """(rows, fresh): rows are (state, upstream path); fresh maps each vendored path to its bytes at target."""
    pin, up = m["pin"], m["upstream_root"]
    rows, fresh = [], {}
    for f in m["files"]:
        path = f["upstream"]
        copy = (vendor / path).read_bytes() if (vendor / path).is_file() else None
        base, new = show(pin, f"{up}/{path}"), show(target, f"{up}/{path}")
        states = ["drift"] if copy != base else []
        states += ["deleted"] if new is None else ["changed"] if new != base else []
        if states:
            rows.append(("+".join(states), path))
        fresh[path] = new
    skill_dirs = {p.split("/")[1] for p in fresh if p.startswith("skills/")}
    added = out("diff", "-z", "--name-only", "--no-renames", "--diff-filter=A", pin, target, "--", f"{up}/skills")
    for name in filter(None, added.split("\0")):
        rel = name[len(up) + 1:]
        if rel not in fresh and (rel.endswith("/SKILL.md") or rel.split("/")[1] in skill_dirs):
            rows.append(("new", rel))
    return rows, fresh


NOTIFIED = "refs/pstack-sync/notified"


def notify(text, target):
    """Once per upstream commit, so a daily job does not repeat itself until someone refreshes the copy. The commit
    last notified is a ref in the upstream cache, not agent-skills state: losing the cache costs one repeat."""
    if git("rev-parse", "-q", "--verify", NOTIFIED, check=False).stdout.decode().strip() == target:
        return
    if platform.system() == "Darwin" and not os.environ.get("SKILLS_SYNC_NO_NOTIFY"):
        script = f"display notification {json.dumps(text)} with title \"agent-skills pstack upstream\""
        try:
            rc = subprocess.run([os.environ.get("PSTACK_NOTIFY_CMD", "osascript"), "-e", script],
                                capture_output=True, timeout=10).returncode
        except (OSError, subprocess.TimeoutExpired):
            rc = -1
        if rc:
            print("notification failed; the next run tries again")  # no marker, so a failed delivery is retried
            return
    git("update-ref", NOTIFIED, target)
    print(f"notified ({target[:12]})")


def refresh(m, manifest_path, vendor, fresh, target):
    plugin = show(target, f"{m['upstream_root']}/.cursor-plugin/plugin.json")  # read before the first write
    try:
        version = json.loads(plugin).get("version") if plugin else None
    except ValueError:
        sys.exit("upstream plugin.json is not JSON: nothing written")
    keep = []
    for f in m["files"]:
        p, data = vendor / f["upstream"], fresh[f["upstream"]]
        if data is None:
            p.unlink(missing_ok=True)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        if p.suffix == ".sh":
            p.chmod(0o755)
        keep.append(f)
    dropped, m["files"], m["pin"] = len(m["files"]) - len(keep), keep, target
    if version:
        m["upstream_version"] = version
    manifest_path.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {len(keep)} file(s), dropped {dropped}, pin -> {target[:12]} (version {m.get('upstream_version', '?')})")


def main():
    argv = sys.argv[1:]
    manifest_path = pathlib.Path(argv[argv.index("--manifest") + 1]) if "--manifest" in argv else REPO / "vendor/pstack/manifest.json"
    to = argv[argv.index("--to") + 1] if "--to" in argv else "origin/main"
    m = json.loads(manifest_path.read_text())
    vendor = manifest_path.parent

    if not os.path.isdir(CACHE):
        subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout", m["remote"], CACHE], check=True)
    git("fetch", "-q", "origin")
    pin, target = m["pin"], out("rev-parse", to).strip()
    log = out("log", "--format=%h %cs %s", f"{pin}..{target}", "--", m["upstream_root"]).strip()
    print(f"pin {pin[:12]} -> {target[:12]}\nupstream commits touching {m['upstream_root']}/:\n{log or '  (none)'}\n")

    rows, fresh = compare(m, vendor, target)
    for state, path in rows:
        print(f"{state:<14} {path}")
    if rows:
        summary = f"pstack upstream: {len(rows)} file(s) differ from vendor/pstack (pin {pin[:7]}, upstream {target[:7]})"
    else:
        summary = f"pstack upstream: vendor/pstack matches upstream {target[:7]}"
    if rows and "--notify" in argv:
        notify(summary, target)
    print(summary)
    if "--write" in argv:
        refresh(m, manifest_path, vendor, fresh, target)


if __name__ == "__main__":
    main()
