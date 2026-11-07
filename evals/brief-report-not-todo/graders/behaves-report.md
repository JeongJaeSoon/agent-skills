---
type: llm
weight: 1
---

This run has no shell, so judge the plan, not output. Pass when the response picks the full status report: it runs or names `orch brief render` as the command whose output goes to the human unchanged. Fail when it picks the to-do and held-queue lists alone (`orch board todo list`) in place of the report, or invents report rows.
