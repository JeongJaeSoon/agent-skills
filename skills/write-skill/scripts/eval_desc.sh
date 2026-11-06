#!/usr/bin/env bash
# Score one skill's eval cases on the old and the new checkout, so a changed description shows whether
# it still fires. Each arm prints the report `claude plugin eval` gives; the exit status is the new
# arm's as `claude` returned it (1 under the threshold, 2 over a cost ceiling), and the old arm's
# status is printed beside it. A temporary directory that cannot be made stops it with 2.
# Usage: eval_desc.sh <old-checkout> <new-checkout> <skill>
# Both checkouts need evals/<skill>/ (copy a case the old one lacks), and the eval costs model calls.
set -u

if [ "$#" -ne 3 ]; then
	printf 'usage: eval_desc.sh <old-checkout> <new-checkout> <skill>\n' >&2
	exit 2
fi
old="$1"
new="$2"
skill="$3"

for dir in "$old" "$new"; do
	if [ ! -d "$dir/evals/$skill" ]; then
		printf 'no evals/%s in %s: add a case there, and copy it into the old checkout so both arms run it\n' "$skill" "$dir" >&2
		exit 2
	fi
done

out="$(mktemp -d "${TMPDIR:-/tmp}/eval-desc.XXXXXX")" || exit 2
status_old=0
status_new=0
for arm in old new; do
	dir="$old"
	[ "$arm" = new ] && dir="$new"
	printf '== %s: %s\n' "$arm" "$dir"
	claude plugin eval "$dir" --ablation none --no-publish --trust-plugin --case "$skill" \
		--output-dir "$out/$arm" --report "$out/$arm.html"
	status=$?
	printf -- '-- %s: exit %s, results %s\n' "$arm" "$status" "$out/$arm"
	if [ "$arm" = old ]; then status_old=$status; else status_new=$status; fi
done

printf 'old exit %s, new exit %s\n' "$status_old" "$status_new"
exit "$status_new"
