# Linear: operation → command

In an agent session: `orca linear`, in its plain text output (add `--json` only when you parse the
result). The Linear MCP tools are for what `orca linear` lacks; `hooks/tracker_gate.py` holds the
call-by-call mapping and passes those calls. A milestone is the `tracker.py` line `use-tracker`
"Milestones" names. Scripts always call `tracker.py`.

## Config (`~/.claude/agent-skills.json` → `tracker.linear`)

| Key | Used by | Meaning |
|---|---|---|
| `workspace` | `tracker.py` | Orca's connected Linear workspace id; null = Orca's default |
| `team` | `write-ticket` | Default team key for a new ticket when neither the program nor an originating ticket names one. `tracker.py create` ignores it: it takes the team from the project |
| `project` | `write-ticket` | Default project for a new ticket, same fallback rule |
| `templates` | `write-ticket` | Issue template names: `{"dev": "개발 티켓", "spike": "조사 티켓"}`. Absent → `write-ticket` uses its bundled skeletons |
| `review_status` | `tracker.py` | State name `--to review` moves to, whatever its type. Absent → "In Review", else the one started state whose name contains "review" |
| `language` | `write-ticket` | Language of a new ticket's title and body, after the site rules file (`use-tracker`, "Site rules"). Neither sets one → Claude's language setting governs |

```json
{"tracker": {"adapter": "linear",
             "linear": {"workspace": null, "team": "ENG", "project": null,
                        "templates": {"dev": "개발 티켓", "spike": "조사 티켓"}}}}
```

Filing with a template: pass the filled `description` only, never `template` too. A description
replaces the template body rather than merging with it, so passing both discards your text.

## Operations

| Operation | `orca linear` CLI | `tracker.py` |
|---|---|---|
| List a project | `list-issues --project P --include-archived --json` | `list --project P` |
| Read one | `issue ID` (`--comments`, `--attachments`, `--relations`, `--children`, `--full` for more) | `get ID` |
| Search | `search TEXT` | — |
| File | `create --title T --project P --team K --body-file F [--label L] [--parent ID]` | `create ...` |
| Add labels | `label add ID --label L` | `label ID --add L` |
| Comment | `comment add ID --body-file F` | `comment ID --body-file F` |
| Link a PR | `attach ID --url U --title PR` | `attach ID --url U` |
| Move | `status set ID --to "<exact state name>"` | `transition ID --to started\|review\|completed\|canceled` |
| Relate | `relation add ID --related OTHER --type related` | `create --related OTHER` |
| Block (dependency) | `relation add ID --related OTHER --type blocked-by\|blocks` | — |
| Link an Orca card | `orca worktree create … --reference <issue URL>` (`skills-sync card --reference`), or `orca reference add <issue URL> --worktree <sel>` on a card that has none; not mixed with the legacy `--linear-issue` | — |
| States / labels | `team states --team K`, `team labels --team K` | — |

Outside `orca linear` (use the MCP tools; `orca linear --help` lists what the CLI has): documents,
uploads and embedded images, diffs and their review, releases, milestones and cycles, project
writes, templates, label definitions, an issue's cycle, milestone, release, delegate and SLA, a
partial description `patch`, and the exact close times and parents the CLI's issue JSON lacks
(below).

## Orca CLI limits (checked against Orca's shipped CLI)

- **Identifiers that start with a digit are rejected.** Every per-issue command (`issue`, `label add`,
  `comment add`, `status set`, `relation add`, `save-issue ID`) only accepts `^[A-Za-z][A-Za-z0-9_]*-\d+$`.
  A team key such as `9X` fails with `linear_issue_required`, in both the `9X-1` and the
  `https://linear.app/<org>/issue/9X-1` forms. `tracker.py get` falls back on that error (or skips
  straight to it for such IDs): `search`, then a `list-issues --team --updated-at` re-read for full
  fields. `tracker.py` writes stop with a
  clear error. Agents use the MCP tools for those issues.
- **No `completedAt`, `canceledAt` or parent in issue JSON.** `tracker.py` sets `completed_at` /
  `canceled_at` to `updatedAt` for closed issues, and `parent` to null. When exact close times or
  parents matter, use MCP `list_issues` with those `fields`.
- **`--cursor` does not carry the filters.** Repeat `--project`, `--include-archived`, etc. on every
  page (`tracker.py` does).
- `--order-by createdAt` is newest first; `--created-at` means created on or after.
- `status set` needs the exact state name. `tracker.py transition` looks up `team states` and picks
  the lowest-position state of the target type (Linear's `duplicate` type counts as `canceled`).
- Writes may return `linear_write_unconfirmed`; follow `error.data.nextSteps` once, never blindly retry.
