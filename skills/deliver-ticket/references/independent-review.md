# Independent review

Review the exact head and its diff against the intended base. Give a reviewer who did not
implement the change the requirements, repository, base and head, and access to the relevant
files and tests. Do not seed the review with the implementer's conclusions.

## Pick the reviewer

Run `python3 -I "${CLAUDE_SKILL_DIR}/scripts/review_pick.py" --repo <checkout>` first. It prints
`<codex|claude|none> <why>`; run the reviewer it names and record it as `review:codex` or `review:claude`
(`orch verdict --source review:codex --act-on <N>` in a program, N the round's Act-on count; the PR body names it
either way). In a program the land gate treats Act-on ≥ 1 as a fail, and asks the other family (codex or claude) to
review a head whose tree changed since the last review; `orchestrate` `references/landing.md`, "Two review rules".
The setting is
`review` in `~/.claude/agent-skills.json`:

```json
"review": {"reviewer": "auto | codex | off"}
```

- `auto` (default): Codex when `codex login status` answers within 10 s, else a fresh-context Claude
  reviewer (a subagent, or `claude -p --no-session-persistence --permission-mode plan` with the prompt on stdin).
- `codex`: Codex only. When it does not answer the script prints `none` and exits 3: the review waits;
  it does not fall back.
- `off`: never calls Codex; the Claude reviewer always runs. Independent review is not skipped.

A repository with `repos.<owner/repo>.external_llm: false` always gets Claude. Exit 2 is a config error to fix.
Codex that passed the probe and then returns no review is INCONCLUSIVE (below): under `auto`, run the Claude
reviewer on the same head and record that one. A human reviewer also counts; record `review:<name>`.

Read the repository's data-sharing restrictions before selecting a reviewer. A ban on
external services applies to every provider; use a reviewer within the allowed boundary or
report review unavailable. Include that restriction in delegated review instructions.

Check correctness, security, acceptance criteria and regressions. Use adversarial design
questions when the change alters a public contract or a difficult invariant. Findings need
file locations, a concrete failure case and severity. Record the reviewer, reviewed SHA,
coverage, findings, disposition and unresolved limitations.

A successful tool exit without a review is NOT VERIFIED. Missing access, an empty result,
a timeout or unavailable reviewer is INCONCLUSIVE: report the cause, use another authorized
reviewer if available, or leave review pending. Retry only after a concrete transient cause
has been addressed; do not loop on a vendor outage, change credentials or weaken permissions.
A changed head requires review of the changed diff and fresh relevant checks.

For the optional orchestration ledger, use `independent-review`, `review:<reviewer>`,
`verifier:<reviewer>` or `live:<feature>` as applicable. The source describes the evidence,
not proof of independence by itself. Never record the implementer's own opinion as an
independent verdict. Existing ledger source strings remain historical input and are not rewritten.
