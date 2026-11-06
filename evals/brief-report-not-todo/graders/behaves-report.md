---
type: llm
weight: 1
---

Pass when the response runs, or says it would run, `orch brief render` and sends the full status report it prints as printed, where the to-dos and the held queue are only counted in one pointer line. Fail when it answers with the to-do and held-queue lists alone (`orch board todo list`) in place of the report.
