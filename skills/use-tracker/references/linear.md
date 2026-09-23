# Linear: operation → tool

Order of preference in an agent session: Linear MCP (`mcp__claude_ai_Linear__*`) when connected,
else `orca linear`, else `tracker.py`. Scripts always call `tracker.py`.

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
| States / labels | `list_issue_statuses`, `list_issue_labels` | `team states --team K`, `team labels --team K` | — |

## Orca CLI limits (checked against Orca's shipped CLI)

- **Identifiers that start with a digit are rejected.** Every per-issue command (`issue`, `label add`,
  `comment add`, `status set`, `relation add`, `save-issue ID`) only accepts `^[A-Za-z][A-Za-z0-9_]*-\d+$`.
  A team key such as `94S` fails with `linear_issue_required`, in both the `94S-1` and the
  `https://linear.app/<org>/issue/94S-1` forms. `tracker.py get` falls back on that error (or skips
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
