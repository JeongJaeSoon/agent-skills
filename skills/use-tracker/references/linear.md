# Linear: operation → tool

Order of preference in an agent session: Linear MCP (`mcp__claude_ai_Linear__*`) when connected,
else `orca linear`, else `tracker.py`. Scripts always call `tracker.py`.

## Config (`~/.claude/agent-skills.json` → `tracker.linear`)

| Key | Used by | Meaning |
|---|---|---|
| `workspace` | `tracker.py` | Orca's connected Linear workspace id; null = Orca's default |
| `team` | `write-ticket` | Default team key for a new ticket when neither the program nor an originating ticket names one. `tracker.py create` ignores it: it takes the team from the project |
| `project` | `write-ticket` | Default project for a new ticket, same fallback rule |
| `templates` | `write-ticket` | Issue template names: `{"dev": "개발 티켓", "spike": "조사 티켓"}`. Absent → `write-ticket` uses its bundled skeletons |

```json
{"tracker": {"adapter": "linear",
             "linear": {"workspace": null, "team": "ENG", "project": null,
                        "templates": {"dev": "개발 티켓", "spike": "조사 티켓"}}}}
```

Filing with a template: pass the filled `description` only, never `template` too. A description
replaces the template body rather than merging with it, so passing both discards your text.

## Operations

| Operation | Linear MCP | `orca linear` CLI | `tracker.py` |
|---|---|---|---|
| List a project | `list_issues` (`project`, `includeArchived: true`, `orderBy: createdAt`, `fields` incl. `completedAt`, `canceledAt`, `parentId`) | `list-issues --project P --include-archived --json` | `list --project P` |
| Read one | `get_issue` | `issue ID --json` | `get ID` |
| Search | `list_issues` (`query`) | `search TEXT --json` | — |
| File | `save_issue` (`team`, `project`, `title`, `description`, `labels`, `parentId`, `relatedTo`) | `create --title T --project P --team K --body-file F [--label L] [--parent ID]` | `create ...` |
| Add labels | `save_issue` (`id`, `addLabels`) | `label add ID --label L` | `label ID --add L` |
| Comment | `save_comment` | `comment add ID --body-file F` | `comment ID --body-file F` |
| Move | `save_issue` (`id`, `state` = type or name) | `status set ID --to "<exact state name>"` | `transition ID --to started\|completed\|canceled` |
| Relate | `save_issue` (`id`, `relatedTo`) | `relation add ID --related OTHER --type related` | `create --related OTHER` |
| Block (dependency) | `save_issue` (`id`, `blockedBy` / `blocks`) | `relation add ID --related OTHER --type blocked-by\|blocks` | — |
| Read a template | `get_template` (name from `templates`) | — | — |
| Link an Orca card | — | `orca worktree create … --linear-issue ID` (or `orca worktree set --worktree <sel> --linear-issue ID`) | — |
| States / labels | `list_issue_statuses`, `list_issue_labels` | `team states --team K`, `team labels --team K` | — |

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
