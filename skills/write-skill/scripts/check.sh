#!/usr/bin/env bash
# The checks write-skill step 5 asks for, as one command: every test file CI runs (scripts/ci_tests.py),
# the ones CI cannot run (ci_tests.EXCLUDED), the pstack sync test, and `claude plugin validate`. Every
# step runs even when one fails, so one pass shows every failure. A missing `claude` is a failure,
# never a skipped step.
# Usage: check.sh   (CHECK_ROOT overrides the checkout; tests use it)
# A whole-suite run goes through the pressure gate: orch heavy <program> -- bash check.sh
set -u

root="${CHECK_ROOT:-$(cd "$(dirname "$0")/../../.." && pwd)}"
cd "$root" || exit 2

failed=()
step() {
	local name="$1"
	shift
	printf '== %s\n' "$name"
	if "$@"; then
		printf -- '-- %s: ok\n' "$name"
	else
		printf -- '-- %s: FAILED\n' "$name"
		failed+=("$name")
	fi
}

step tests python3 scripts/ci_tests.py
# CI cannot run some test files (macOS only, or a runner limit); ci_tests.EXCLUDED names them, and
# this machine is the one that runs them.
if excluded="$(python3 -c 'import sys; sys.path.insert(0, "scripts"); import ci_tests; print("\n".join(p for p in ci_tests.EXCLUDED if p.endswith(".py")))')"; then
	while IFS= read -r path; do
		# -E: an inherited PYTHONOPTIMIZE would strip the asserts these tests are made of.
		[ -n "$path" ] && step "local-only $path" python3 -E "$path"
	done <<<"$excluded"
else
	printf '== local-only\n-- local-only: FAILED (cannot read ci_tests.EXCLUDED)\n'
	failed+=(local-only)
fi
step pstack-sync bash scripts/pstack-sync-test.sh
if command -v claude >/dev/null 2>&1; then
	step plugin-validate claude plugin validate .
else
	printf '== plugin-validate\n-- plugin-validate: NOT VERIFIED (claude is not on PATH)\n'
	failed+=(plugin-validate)
fi

if [ "${#failed[@]}" -gt 0 ]; then
	printf 'FAILED: %s\n' "${failed[*]}"
	exit 1
fi
printf 'all checks passed\n'
