---
type: llm
weight: 1
---

This run has no shell, so judge the plan, not output. Pass when the response picks the to-do and held-queue lists: it runs or names `orch board todo list` as the command whose output goes to the human unchanged. Fail when it picks the full status report (`orch brief render`) instead, or invents list items.
