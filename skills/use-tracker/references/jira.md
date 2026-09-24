# Jira: operation → tool

In an agent session, use an Atlassian MCP server when one is connected (its tool prefix varies by
install, e.g. `mcp__atlassian__*` or `mcp__claude_ai_Atlassian__*`; check the tool list). Without
it, and always from scripts, use `tracker.py --adapter jira` (Jira Cloud REST v3).

| Operation | Atlassian MCP (if connected) | `tracker.py` |
|---|---|---|
| List a project | `searchJiraIssuesUsingJql` (`project = P ORDER BY created ASC`) | `list --project P [--since T]` |
| Read one | `getJiraIssue` | `get ABC-1` |
| File | `createJiraIssue` | `create --project P --title T --body-file F [--label L] [--parent ID] [--related ID]` |
| Add labels | `editJiraIssue` (`update.labels: [{add: L}]`) | `label ABC-1 --add L` |
| Comment | `addCommentToJiraIssue` | `comment ABC-1 --body-file F` |
| Move | `getTransitionsForJiraIssue`, then `transitionJiraIssue` | `transition ABC-1 --to started\|review\|completed\|canceled` |
| Relate | issue-link tool if the server has one, else `tracker.py` | `create --related ID` (link type "Relates") |

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
  `review` needs the one target status whose name contains "review".
  If none fits, it fails and lists the available transitions. It does not set a resolution field.
- `create` uses issue type `tracker.jira.issue_type` (default `Task`) and turns the body file into ADF
  paragraphs.
- `list` uses `/rest/api/3/search/jql` with `nextPageToken`, and falls back to `/rest/api/3/search`
  with `startAt` on sites that lack it. `--since` becomes `created >= "YYYY-MM-DD HH:mm"`, which Jira
  reads in the API user's timezone; results are then re-filtered in UTC.
