---
type: llm
weight: 1
---

This run has no shell, so judge the plan, not output. Pass when the response picks the stage table: it runs or names `orch stage show` as the command whose output goes to the human unchanged. Fail when it picks the full status report (`orch brief render`) instead, or invents table rows.
