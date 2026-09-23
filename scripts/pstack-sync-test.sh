#!/bin/bash
# Exercises pstack-sync.py against a throwaway upstream: new, clean, merged, drift and conflict.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
T=$(mktemp -d)
trap 'command rm -rf "$T"' EXIT
g() { git -C "$T/up" -c user.email=t@t -c user.name=t "$@"; }
put() { mkdir -p "$(dirname "$1")"; printf -- "$2" > "$1"; }

mkdir -p "$T/up" "$T/repo/scripts" "$T/repo/vendor/pstack"
cp "$here/pstack-sync.py" "$T/repo/scripts/"
g init -q -b main
put "$T/up/pstack/skills/a/SKILL.md" '---\nname: a\n---\nline1\nline2\n'
put "$T/up/pstack/skills/b/SKILL.md" '---\nname: b\n---\nx1\nx2\nx3\n'
put "$T/up/pstack/.cursor-plugin/plugin.json" '{"version": "1.0.0"}\n'
g add -A; g commit -qm one; p1=$(g rev-parse HEAD)
g remote add origin "$T/up"
cat > "$T/repo/vendor/pstack/manifest.json" <<EOF
{"remote":"x","remote_slug":"cursor/plugins","upstream_root":"pstack","pin":"$p1","files":[
 {"upstream":"skills/a/SKILL.md","local":"skills/a/SKILL.md","mode":"verbatim"},
 {"upstream":"skills/b/SKILL.md","local":"skills/b/SKILL.md","mode":"adapted"}]}
EOF
sync() { (cd "$T/repo" && PSTACK_CACHE="$T/up" python3 scripts/pstack-sync.py "$@"); }
expect() { grep -q -- "$1" <<<"$2" || { echo "FAIL: expected '$1' in:"; echo "$2"; exit 1; }; }

out=$(sync --to "$p1" --write); expect "a/SKILL.md  new" "$out"
sed -i '' 's/^x1$/x1 local/' "$T/repo/skills/b/SKILL.md"

put "$T/up/pstack/skills/a/SKILL.md" '---\nname: a\n---\nline1\nline2 up\n'
put "$T/up/pstack/skills/b/SKILL.md" '---\nname: b\n---\nx1\nx2\nx3 up\n'
put "$T/up/pstack/.cursor-plugin/plugin.json" '{"version": "1.1.0"}\n'
g commit -qam two
out=$(sync --write); expect "a/SKILL.md  clean" "$out"; expect "b/SKILL.md  merged" "$out"; expect "version 1.1.0" "$out"
grep -q '^x1 local$' "$T/repo/skills/b/SKILL.md" && grep -q '^x3 up$' "$T/repo/skills/b/SKILL.md"
grep -q '"upstream_version": "1.1.0"' "$T/repo/vendor/pstack/manifest.json"

# A verbatim file edited locally is caught even when upstream has not touched it.
echo "local edit" >> "$T/repo/skills/a/SKILL.md"
put "$T/up/pstack/.cursor-plugin/plugin.json" '{"version": "1.1.1"}\n'
g commit -qam two-b
out=$(sync --write 2>&1) && { echo "FAIL: write should refuse verbatim drift"; exit 1; }
expect "a/SKILL.md  drift " "$out"; expect "nothing written" "$out"
sed -i '' '$d' "$T/repo/skills/a/SKILL.md"
out=$(sync --write); expect "a/SKILL.md  unchanged" "$out"

echo "local extra" >> "$T/repo/skills/a/SKILL.md"
put "$T/up/pstack/skills/a/SKILL.md" '---\nname: a\n---\nline1 up3\nline2 up\n'
put "$T/up/pstack/skills/b/SKILL.md" '---\nname: b\n---\nx1 up3\nx2\nx3 up\n'
g commit -qam three
pin_before=$(python3 -c "import json;print(json.load(open('$T/repo/vendor/pstack/manifest.json'))['pin'])")
out=$(sync --write 2>&1) && { echo "FAIL: write should refuse"; exit 1; }
expect "a/SKILL.md  drift+merged" "$out"; expect "b/SKILL.md  conflict(1)" "$out"; expect "nothing written" "$out"
[ "$pin_before" = "$(python3 -c "import json;print(json.load(open('$T/repo/vendor/pstack/manifest.json'))['pin'])")" ]
echo "pstack-sync: all cases pass"
