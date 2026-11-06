# Jira: operation → command

In an agent session, use `orca jira` (`issue`, `search`, `create`, `comment add`, `status list|set`,
`label add|set|rm`, `assignee set`, `priority set`, `project list|types|fields`; each command's
`--help` has an example). The Atlassian MCP tools are for what it lacks (issue links, editing
a summary or description, Confluence); `hooks/tracker_gate.py` holds the call-by-call mapping and
passes those calls. From scripts, and when Orca has no Jira connection, use `tracker.py --adapter jira`
(Jira Cloud REST v3).

| Operation | `orca jira` | `tracker.py` |
|---|---|---|
| List a project | `search "project = P ORDER BY created ASC"` | `list --project P [--since T]` |
| Read one | `issue ABC-1` | `get ABC-1` |
| File | `create --project P --type <issueTypeId> --title T --body-file F` (ids: `project types --project P`) | `create --project P --title T --body-file F [--label L] [--parent ID] [--related ID]` |
| Add labels | `label add ABC-1 --label L` | `label ABC-1 --add L` |
| Comment | `comment add ABC-1 --body-file F` | `comment ABC-1 --body-file F` |
| Move | `status list ABC-1`, then `status set ABC-1 --to "<name>"` | `transition ABC-1 --to started\|review\|completed\|canceled` |
| Relate | — (MCP issue-link tool) | `create --related ID` (link type "Relates") |

Through the Atlassian MCP, a child's epic goes in `additional_fields: {"parent": {"id": "<numeric id>"}}`. Its `parent` argument returns 400 and creates nothing. `getJiraIssue` returns only the comment count; read comment bodies with `listJiraIssueComments`.

## How tracker.py maps Jira

| Jira | state_type |
|---|---|
| `statusCategory.key = new`, status named "Backlog" | `backlog` |
| `statusCategory.key = new` | `unstarted` |
| `statusCategory.key = indeterminate` | `started` |
| `statusCategory.key = done`, resolution Won't Do / Cancelled / Duplicate | `canceled` |
| `statusCategory.key = done` | `completed` |

- `completed_at` / `canceled_at` come from `resolutiondate`; `parent` from `fields.parent.key`;
  `description` is ADF flattened to plain text; `priority` maps Highest→1, High→2, Medium→3, Low/Lowest→4.
- `transition` picks the first transition whose `to.statusCategory.key` matches the target
  (`indeterminate` for started and review, `done` for completed/canceled). A done-category transition whose name
  mentions cancel / won't do / reject / decline / duplicate counts as canceled, any other as completed.
  `review` goes to the status named by `tracker.jira.review_status` (matched by name, any category)
  when that is set, else to the one target status whose name contains "review".
  If none fits, it fails and lists the available transitions. It does not set a resolution field.
- `create` uses issue type `tracker.jira.issue_type` (default `Task`) and turns the body file into ADF
  paragraphs.
- `list` uses `/rest/api/3/search/jql` with `nextPageToken`, and falls back to `/rest/api/3/search`
  with `startAt` on sites that lack it. `--since` becomes `created >= "YYYY-MM-DD HH:mm"`, which Jira
  reads in the API user's timezone; results are then re-filtered in UTC.

## Switching to Jira

```json
{"tracker": {"adapter": "jira",
             "jira": {"base_url": "https://acme.atlassian.net", "email_env": "JIRA_EMAIL", "token_env": "JIRA_API_TOKEN"}}}
```

Put that in `~/.claude/agent-skills.json`, export `JIRA_EMAIL` and `JIRA_API_TOKEN` (an Atlassian API
token), then check it with `python3 <tracker.py> list --project KEY --limit 1`. Optional:
`"issue_type": "Task"` for `create`, `"review_status": "<status name>"` for `--to review`, and
`team`, `project`, `templates`, `language` as for Linear ([linear.md](linear.md), "Config").
Tokens are read from the environment only and never printed.
