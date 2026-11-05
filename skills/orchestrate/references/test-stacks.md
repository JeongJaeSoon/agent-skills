# Test stacks

The collector can show the state of the test stacks a team deploys to. It is off until `dashboard.stacks` is set in `~/.claude/agent-skills.json`; no stack, port or environment name is assumed.

```json
{"dashboard": {"stacks": [{"name": "staging", "url": "https://stack.example.test", "repo": "acme/app"}]}}
```

| Key | Meaning |
|---|---|
| `name` | Label shown for the stack. |
| `url` | `http(s)` base URL with an optional port and path. The collector reads `<url>/readyz`. A URL with credentials, a query or a fragment is skipped, and a redirect is not followed (the stack counts as not ready). |
| `repo` | GitHub `owner/name` of the repository the stack deploys. `main` of this repository is what "behind" is measured against. |
| `sha_field` | Optional. The key in the `/readyz` JSON that holds the deployed commit. Without it `sha`, `git_sha`, `commit` and `revision` are tried, in that order. Only a 7–40 character hex value counts. |

The collector needs `curl` (one `/readyz` request, 5 seconds in all) and `gh` (the compare call) on `PATH`. At most 8 entries are read. An entry without a name, a valid url or a valid repo is skipped.

`scripts/stacks.py` makes one row per entry in `state.stacks` of `state.json`, at most once a minute (a page opening or a changed entry forces a read). The stacks are read at once and a stack that has not answered after 20 seconds is a row with `error: timed out`:

```
{name, url, repo, ready, status, sha, behind, error, checked_at}
```

- `ready` is true for a 2xx `/readyz`, false for any other status, null when nothing answered. `status` is the HTTP status.
- `sha` is the deployed commit read from the `/readyz` body, null when the body is not JSON or names none.
- `behind` is the number of commits `main` has that `sha` lacks, from `gh api repos/<repo>/compare/main...<sha>`. It is null when there is no `sha` or GitHub could not answer.
- `error` says which part could not be read (`readyz: …`, `behind: …` or `timed out`) in a short fixed phrase, never the response or the tool's own text; the rest of the row still holds what was read. A failing stack never stops the others.

With `dashboard.stacks` unset, `state.stacks` is `[]` and nothing is requested.
