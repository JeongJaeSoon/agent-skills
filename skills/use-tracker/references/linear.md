# Linear: operation → tool

Order in an agent session: `orca linear` first, in its plain text output (add `--json` only when
you parse the result); the Linear MCP tools only for what `orca linear` lacks ("Not in
`orca linear`" below); a milestone is the `tracker.py` line `use-tracker` "Milestones" names.
Scripts always call `tracker.py`. The MCP tools are the same under either prefix: a local server
(`mcp__linear__*`) or the claude.ai connector (`mcp__claude_ai_Linear__*`). Use whichever the
session has; with both, use the local server. The tables below name tools without the prefix.

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

| Operation | Linear MCP | `orca linear` CLI | `tracker.py` |
|---|---|---|---|
| List a project | `list_issues` (`project`, `includeArchived: true`, `orderBy: createdAt`, `fields` incl. `completedAt`, `canceledAt`, `parentId`) | `list-issues --project P --include-archived --json` | `list --project P` |
| Read one | `get_issue` | `issue ID` (`--comments`, `--attachments`, `--relations`, `--children`, `--full` for more) | `get ID` |
| Search | `list_issues` (`query`) | `search TEXT` | — |
| File | `save_issue` (`team`, `project`, `title`, `description`, `labels`, `parentId`, `relatedTo`) | `create --title T --project P --team K --body-file F [--label L] [--parent ID]` | `create ...` |
| Add labels | `save_issue` (`id`, `addLabels`) | `label add ID --label L` | `label ID --add L` |
| Comment | `save_comment` | `comment add ID --body-file F` | `comment ID --body-file F` |
| Link a PR | `save_issue` (`id`, `links: [{url, title}]`) | `attach ID --url U --title PR` | `attach ID --url U` |
| Move | `save_issue` (`id`, `state` = type or name) | `status set ID --to "<exact state name>"` | `transition ID --to started\|review\|completed\|canceled` |
| Relate | `save_issue` (`id`, `relatedTo`) | `relation add ID --related OTHER --type related` | `create --related OTHER` |
| Block (dependency) | `save_issue` (`id`, `blockedBy` / `blocks`) | `relation add ID --related OTHER --type blocked-by\|blocks` | — |
| Read a template | `get_template` (name from `templates`) | — | — |
| Link an Orca card | — | `orca worktree create … --reference <issue URL>` (`skills-sync card --reference`), or `orca reference add <issue URL> --worktree <sel>` on a card that has none; not mixed with the legacy `--linear-issue` | — |
| States / labels | `list_issue_statuses`, `list_issue_labels` | `team states --team K`, `team labels --team K` | — |

## Not in `orca linear`

Use the MCP tools for these; `orca linear --help` and each command's `--help` list what the CLI has.

- Documents: `list_documents`, `get_document`, `save_document`.
- Uploads: files and images (`prepare_file_upload`, `prepare_attachment_upload`,
  `create_attachment_from_upload`) and reading embedded images (`extract_images`). `attach` takes a
  link only; `issue --attachments` lists what is attached.
- Diffs and their review: `list_diffs`, `get_diff`, `get_diff_threads`, `save_diff_comment`,
  `submit_diff_review`, `merge_diff`.
- Releases and release notes: `list_releases`, `save_release`, `list_release_notes`. The CLI has only
  the `list-issues --release` filter.
- Milestones (`list_milestones`, `save_milestone`) and cycles (`list_cycles`). The CLI has only the
  `list-issues --cycle` filter.
- Projects beyond the list: `get_project`, `save_project`, project labels, status updates. The CLI
  has `project list`.
- Issue fields `save-issue` has no flag for, all through `save_issue`: putting an issue in a cycle,
  milestone or release (`cycle`, `milestone`, `addReleases`), its agent delegate (`delegate`), its
  SLA (`slaBreachesAt`, `slaType`), and a partial description edit (`patch`; the CLI replaces the
  whole body).
- Templates (`get_template`, `list_templates`); creating or editing labels (`save_issue_label`) and
  retiring them (`retire_issue_label`). The CLI adds, removes and sets labels that exist.
- Users outside a team's members (`list_users`, `get_user`), notifications, sharing an issue.
- The exact close times and parents the CLI's issue JSON lacks (below).

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
