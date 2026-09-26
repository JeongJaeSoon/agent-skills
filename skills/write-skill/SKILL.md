---
name: write-skill
description: "Use before writing or changing any skill, agent definition, or CLAUDE.md-style instruction file — in this plugin's public repo or in the user's own ~/.claude — and before committing such a change: \"스킬 만들어줘\", \"스킬 고쳐줘\", \"스킬 수정\", \"write a skill\", \"update the skill\", reflect's Apply step, the flow improver's commit. Checks for overlap first, then carries the steps every skill change needs: prompt audit, internal-name grep, tests, reload."
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

Say in the report which skills you checked and why the change went where it did. Drafting a new skill from scratch, or tuning a description that fails to fire, goes through the `skill-creator` skill's loop; this skill's steps 3 to 7 still follow it.

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
```

`hits: 1` (grep found nothing) is the pass. Any hit is rewritten generically, then the grep runs again. Show the command and its output in the report.

## 5. Tests and validation (public repo)

- The loop in README "테스트", then `bash scripts/pstack-sync-test.sh`.
- `claude plugin validate <checkout>`.
- A skill added or its "when" changed: its entry in `docs/skills.md` and the README table. The English and Japanese catalogs keep their `translated-from` line; `python3 scripts/catalog/build.py <out.html>` warns when they lag.
- A description or trigger phrase changed: `bash scripts/trigger-probe.sh` on the old and the new checkout, one prompt that should fire it and one that should not.

## 6. Land (public repo)

Commit and push as part of finishing this change, not in a batch at the end of the session: other machines pull from `origin`, so a change that is only local is invisible there. Commit signed, message in the log's form (`feat(<skill>): …`, Korean), with what was ruled out when a design choice was made. Then fast-forward the loaded checkout and push, each as its own command with a literal path (a `-C` path from a shell variable is blocked):

```bash
git -C <main checkout> merge --ff-only <branch>
git -C <main checkout> push origin main
```

Other machines pick it up through `skills-sync sync` (launchd every 15 minutes: fetch, fast-forward only, stops with a notification on a dirty or diverged checkout; `docs/platform.md` §3). Changes under `~/.claude` have no such sync; say so in the report.

## 7. Reload

A running session keeps the old text until it reloads, and a session cannot type a slash command into itself. End the report by asking the user to run `/reload-skills` (a `SKILL.md` or reference file changed) and `/reload-plugins` (`hooks/hooks.json` or the plugin manifest changed) in each open session. Changes to `~/.claude/agents/` or `CLAUDE.md` apply to sessions started after the change; say so. Never type input into another session's terminal to reload it.

## Report

- Target, files changed, commit sha and push result (public repo).
- Where the change went and which existing skills were checked (step 1).
- Prompt audit: findings count, each applied or not with the reason.
- The internal-name grep command and its output.
- Tests and validation output.
- The exact reload commands the user must run.
