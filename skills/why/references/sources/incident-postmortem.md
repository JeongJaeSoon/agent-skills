# Incident & Postmortem Context

Not a separate source, a **cross-cutting angle**. Incidents often motivate defensive code ("we added this check after the X outage"), so if the target looks defensive (null checks, retry logic, timeout handling, rate limiting, feature flags), specifically hunt for incident history inside your own source:

- **Long-form docs (Notion, Google Drive, Obsidian)**: search for postmortems mentioning the target file, feature, or error string
- **Linear**: look for tickets labeled `incident`, `sev-*`, `postmortem-action-item`, `reliability`
- **Slack**: search `#sev-*` and `#incident-*` channels around the dates the target code was added
- **Git**: commits with messages like "fix for incident", "add defensive check", "revert" followed by "re-apply with..." are strong signals

If you find an incident link, fetch the full postmortem. Postmortems typically have an "Action Items" section that ties directly to code changes. When multiple sources corroborate (an incident ID appears in a Linear ticket, which appears in a postmortem, which appears in a Slack thread that links to the target PR), the evidence is especially strong.

Monitoring, error tracking, and analytics records (Datadog, Sentry, a data warehouse) are not reachable here. When an incident origin is plausible, list them under Gaps as unsearched, since they are where the runtime signal would be.

Worth spending time on when the code's defensive character makes an incident-driven origin plausible. Skip it for code that doesn't look defensive.
