#!/bin/bash
# Exercises pstack-sync.py against a throwaway upstream: copy, match, changed, deleted, new, drift, notify, refresh.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d)
trap 'command rm -rf "$T"' EXIT
g() { git -C "$T/up" -c user.email=t@t -c user.name=t "$@"; }
put() { mkdir -p "$(dirname "$1")"; printf -- "$2" > "$1"; }

mkdir -p "$T/up" "$T/repo/scripts" "$T/repo/vendor/pstack" "$T/repo/skills/a"
cp "$here/pstack-sync.py" "$T/repo/scripts/"
g init -q -b main
put "$T/up/pstack/skills/a/SKILL.md" '---\nname: a\n---\nline1\n'
put "$T/up/pstack/skills/b/SKILL.md" '---\nname: b\n---\nx1\n'
put "$T/up/pstack/.cursor-plugin/plugin.json" '{"version": "1.0.0"}\n'
g add -A; g commit -qm one; p1=$(g rev-parse HEAD)
g remote add origin "$T/up"
cat > "$T/repo/vendor/pstack/manifest.json" <<EOF
{"remote":"x","remote_slug":"cursor/plugins","upstream_root":"pstack","pin":"$p1","files":[
 {"upstream":"skills/a/SKILL.md","local":"skills/a/SKILL.md","mode":"adapted"},
 {"upstream":"skills/b/SKILL.md","local":"skills/b/SKILL.md","mode":"verbatim"}]}
EOF
echo "our own a" > "$T/repo/skills/a/SKILL.md"
sync() { (cd "$T/repo" && PSTACK_CACHE="$T/up" AGENT_SKILLS_STATE="$T/state" SKILLS_SYNC_NO_NOTIFY=1 \
  python3 scripts/pstack-sync.py "$@"); }
expect() { grep -q -- "$1" <<<"$2" || { echo "FAIL: expected '$1' in:"; echo "$2"; exit 1; }; }
pin() { python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['pin'])" "$T/repo/vendor/pstack/manifest.json"; }

# A missing copy is drift; --write at the pin makes the pristine copy, and then it matches.
out=$(sync --write); expect "drift          skills/a/SKILL.md" "$out"; expect "wrote 2 file(s)" "$out"
cmp -s "$T/repo/vendor/pstack/skills/b/SKILL.md" "$T/up/pstack/skills/b/SKILL.md"
out=$(sync --notify); expect "matches upstream" "$out"
[ ! -e "$T/state/pstack-upstream.json" ] || { echo "FAIL: notified with nothing to report"; exit 1; }

# Upstream moves: a changed, b deleted, a new file in a vendored skill and a new skill. Report only.
put "$T/up/pstack/skills/a/SKILL.md" '---\nname: a\n---\nline1 up\n'
put "$T/up/pstack/skills/a/references/r.md" 'new ref\n'
put "$T/up/pstack/skills/c/SKILL.md" '---\nname: c\n---\n'
put "$T/up/pstack/skills/c/notes.md" 'not reported: c is not vendored\n'
g rm -q pstack/skills/b/SKILL.md
put "$T/up/pstack/.cursor-plugin/plugin.json" '{"version": "1.1.0"}\n'
g add -A; g commit -qm two; p2=$(g rev-parse HEAD)
echo "edited copy" >> "$T/repo/vendor/pstack/skills/b/SKILL.md"
out=$(sync --notify)
expect "changed        skills/a/SKILL.md" "$out"; expect "drift+deleted  skills/b/SKILL.md" "$out"
expect "new            skills/a/references/r.md" "$out"; expect "new            skills/c/SKILL.md" "$out"
if grep -q "notes.md" <<<"$out"; then echo "FAIL: a file of an unvendored skill was reported"; exit 1; fi
expect "4 file(s) differ" "$out"; expect "notified" "$out"
grep -q "$p2" "$T/state/pstack-upstream.json"
[ "$(pin)" = "$p1" ] && grep -q '^line1$' "$T/repo/vendor/pstack/skills/a/SKILL.md"

# The same upstream commit is notified once; the summary is still the last line the job records.
out=$(sync --notify)
if grep -q "notified" <<<"$out"; then echo "FAIL: notified twice for one commit"; exit 1; fi
[ "$(tail -n 1 <<<"$out")" = "pstack upstream: 4 file(s) differ from vendor/pstack (pin ${p1:0:7}, upstream ${p2:0:7})" ]

# A hand refresh rewrites the copies, drops the deleted file, advances the pin and leaves skills/ alone.
out=$(sync --write); expect "wrote 1 file(s), dropped 1, pin -> ${p2:0:12} (version 1.1.0)" "$out"
grep -q '^line1 up$' "$T/repo/vendor/pstack/skills/a/SKILL.md"
[ ! -e "$T/repo/vendor/pstack/skills/b/SKILL.md" ] && [ "$(pin)" = "$p2" ]
[ "$(cat "$T/repo/skills/a/SKILL.md")" = "our own a" ]
# "new" counts from the pin: once the pin moves past them, unvendored files are no longer reported.
out=$(sync); expect "matches upstream" "$out"
echo "pstack-sync: all cases pass"
