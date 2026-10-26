---
name: write-skill
description: "Use before writing or changing a skill, agent definition or CLAUDE.md-style instruction file, in this repo or the user's ~/.claude, and before committing it — \"스킬 만들어줘\", \"스킬 고쳐줘\", \"write a skill\", reflect's Apply step, the flow improver's commit."
---

# Write or update a skill

A skill change is not done when the text reads well. It is done when it has been checked against the skills already there, audited, scrubbed, tested, landed where sessions load it, and reloaded. This skill carries those steps so none is skipped.

## 0. Which target

| Target | Where to edit | Internal names |
|---|---|---|
| This plugin (`agent-skills`, a public repo) | A worktree branch of its checkout, never the loaded main checkout or `~/.claude/plugins/cache/` (`docs/platform.md`) | Forbidden in every file, commit message and PR body |
| The user's own skills, agents and instructions (`~/.claude/skills/`, `~/.claude/agents/`, `~/.claude/CLAUDE.md`) | In place, after copying each file to a dated backup outside `~/.claude` (these are not under git) | Allowed |
| Another plugin | Not edited here. Record the change it needs for its owner | — |

Content moving from a personal file into the public repo is rewritten in generic terms first.

## 1. Fold in before adding

Before a new skill, or a new section longer than about ten lines, look for its home:

1. List what exists: `ls skills/` in the checkout, `ls ~/.claude/skills ~/.claude/agents`, and the skills this session lists as available.
2. Grep the topic's key terms across those directories' `SKILL.md` and agent files.
3. A skill that already owns the moment (its description names the trigger) gets the change. A new skill only when no existing one is a real home and the need recurs.

Say in the report which skills you checked and why the change went where it did. Drafting a new skill from scratch, or tuning a description that fails to fire, goes through the `skill-creator:skill-creator` skill's loop; this skill's steps 3 to 7 still follow it.

## 2. Edit

Match the target's conventions: frontmatter `name` and `description` (the description carries the user's trigger phrases), and the body in the language the file already uses. A command, flag or term you change gets grepped across the whole target, and every copy changes in the same commit. Cut any sentence the change makes redundant; prefer rewriting a sentence to adding one.

A reusable skill holds no company-internal or project-specific information: no company, team, channel, person, ticket key, internal repo or service name, and no fact that is true for one project only. Write the rule in general terms; a one-project fact goes to that project's notes or memory. The user's own files may name their workplace where the workflow is theirs (the tracker project, the channel), but a one-project fact still does not belong in a skill.

## 3. Prompt audit

Run the `claude-api` skill's `prompt-audit` on the changed files: invoke the skill with the args `prompt-audit` and name the changed files in the request. It returns a findings report (`file:line`, pattern, why, confidence) and a proposed diff. Apply the findings that hold for this text, leave the ones on its keep list, and keep one line per finding for the report: applied, or not applied with the reason. A personal-file change is audited the same way.

## 4. Internal-name grep (public repo only)

The pattern list lives outside the repo, one pattern per line: `~/.config/agent-skills/internal-names.txt`. If it is missing, stop and ask the user for it; never write the list into the repo.

```bash
git add <changed paths> && git diff --cached | grep -n -i -E -f ~/.config/agent-skills/internal-names.txt; echo "hits: $?"
git log -1 --format=%B | grep -i -E -f ~/.config/agent-skills/internal-names.txt   # after committing
gh pr view <n> --json title,body -q '.title+"\n"+.body' | grep -i -E -f ~/.config/agent-skills/internal-names.txt   # after creating or editing the PR
```

`hits: 1` (grep found nothing) is the pass. Any hit is rewritten generically, then the grep runs again. Show the command and its output in the report.

## 5. Tests and validation (public repo)

- The loop in README "테스트", then `bash scripts/pstack-sync-test.sh`.
- `claude plugin validate <checkout>`.
- A skill added or its "when" changed: its entry in `docs/skills.md` and the README table. The English and Japanese catalogs keep their `translated-from` line; `python3 scripts/catalog/build.py <out.html>` warns when they lag.
- A description or trigger phrase changed: `claude plugin eval <checkout> --ablation none --no-publish` on the old and the new checkout (`--case <skill>` for one skill). Each case under `evals/<skill>/` holds a prompt that should fire the skill and graders for "it fired" and "a neighbouring skill did not"; add a case when the changed skill has none. Eval runs load no user settings, so a personal skill or a `CLAUDE.md` mapping that takes the same phrase never shows there: when one could, also run the phrase through `claude -p "<phrase>" --output-format stream-json --verbose --no-session-persistence --permission-mode dontAsk` in an empty git directory with user settings, and count the `Skill` tool calls. That run raises the usage counts that decide which descriptions fit the skill listing's budget (about 1% of the context window, counted in display width, so a CJK character costs two; past it, the least-used skills lose their descriptions first); eval runs do not. `claude -p "/skill-doctor"` shows which skills are out of the listing (a `-` in its context column) and their use counts, `claude plugin details agent-skills` the always-on cost per skill, and `claude -p … --debug-file <f>` the over-budget total ("Skill listing over budget").
- An agent definition changed (this repo's `agents/` or `~/.claude/agents/`): a subagent started from the session that edited it still runs the old text. Check from a fresh `claude -p` session that delegates to the agent and asks it to quote a line only the new text has.

## 6. Land (public repo)

Commit and push as part of finishing this change, not in a batch at the end of the session: other machines pull from `origin`, so a change that is only local is invisible there. Commit signed, message in the log's form (`feat(<skill>): …`, Korean), with what was ruled out when a design choice was made. Then push from the worktree first and fast-forward the loaded checkout after, each as its own command with a literal path (a `-C` path from a shell variable is blocked):

```bash
git fetch origin && git rebase origin/main     # then rerun step 5 if origin moved
git push origin HEAD:main
git -C <main checkout> merge --ff-only <branch>
```

In this order a rejected push leaves nothing local moved. Fast-forwarding first and losing the push race leaves the loaded checkout with a commit origin replaced, which `skills-sync` then stops on.

Other machines pick it up through `skills-sync sync` (launchd every 15 minutes: fetch, fast-forward only, stops with a notification on a dirty or diverged checkout; `docs/platform.md` §3). Changes under `~/.claude` have no such sync; say so in the report.

## 7. Reload

A running session keeps the old text until it reloads. Once the change is in the loaded checkout, run `skills-sync broadcast --skills` (a `SKILL.md` or reference file changed) or `--plugins` (`hooks/hooks.json` or the plugin manifest changed); `docs/platform.md` §4 has the rules. It sends the reload with `orca terminal send --text "/reload-skills" --enter` only to sessions idle at an empty prompt, and keeps a working session pending until it is idle. Never send the reload with `orca terminal send` yourself: a permission dialog takes the Enter as its answer, and the broadcast checks for one first. This session reloads itself the same way, once its turn has ended: start this loop with `run_in_background`, `--skills` or `--plugins` as above, then end the turn.

```bash
for i in $(seq 20); do sleep 30
  out=$(skills-sync broadcast --skills --terminal "$ORCA_TERMINAL_HANDLE"); rc=$?; echo "$out"
  [ $rc = 0 ] && break
  case $out in *"sent, but"*|*"not a connected"*|*"no rendered screen"*|*"cannot list"*) break;; esac
done
```

While a turn runs, the session is busy and the broadcast sends nothing; once the turn ends, the title goes idle even with this task still running, and the reload goes in. A refusal that can clear (a dialog, the human typing, another broadcast holding the lock) is retried. Only exit 0 is success: the broadcast saw `❯ /reload-…` as the last message with `⎿ Reloaded` under it. `--dry-run` proves nothing here, since it exits 0 even when busy, and neither does a match on the reason text. `sent, but the screen does not show it was taken` means the text went in but did not run as a command: it can land as a plain user message (a newline before the `/`) that starts a turn and reloads nothing, so do not resend after it. The task's notification starts a turn in this session: report from the loop's output. A session outside Orca has no `$ORCA_TERMINAL_HANDLE`, and a loop that ended without exit 0 leaves the old text loaded: end the report by asking the user to run `/reload-skills` or `/reload-plugins` in this session. Changes to `~/.claude/agents/` or `CLAUDE.md` apply to sessions started after the change; say so.

## Report

- Target, files changed, commit sha and push result (public repo).
- Where the change went and which existing skills were checked (step 1).
- Prompt audit: findings count, each applied or not with the reason.
- The internal-name grep command and its output.
- Tests and validation output.
- The broadcast output: the sessions reloaded and the ones left pending, and this session's own reload from the loop's output. Only when that did not happen, the reload command the user runs in this session.
