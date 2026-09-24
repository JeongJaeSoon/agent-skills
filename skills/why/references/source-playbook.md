# Source playbooks

The why skill spawns one investigator per available source, each reading a single source-specific playbook below. Adapt a playbook for a different tool in the same category.

| Category | Playbook | Source it documents |
|---|---|---|
| Source control history | [`code-archaeology.md`](./sources/code-archaeology.md) | git, `gh` |
| Issue / ticket tracker | [`linear.md`](./sources/linear.md) | Linear MCP (Jira through the `use-tracker` skill) |
| Long-form documents | [`notion.md`](./sources/notion.md) | Notion MCP |
| Long-form documents | [`google-drive.md`](./sources/google-drive.md) | Google Drive MCP |
| Long-form documents | [`obsidian.md`](./sources/obsidian.md) | Obsidian notes through the `use-notes` skill |
| Real-time team chat | [`slack.md`](./sources/slack.md) | Slack MCP |

Cross-cutting:

- [`incident-postmortem.md`](./sources/incident-postmortem.md). Add this if the target code looks defensive (null checks, retry, timeout, rate limit, feature flag, egress guard, OOM handler).
