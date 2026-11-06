---
type: llm
weight: 1
---

Pass when the response runs, or says it would run, `orch brief render` and sends the full status report it prints, with the to-dos and held queue counted in the report's pointer line and its 보류 section. Fail when it answers with the to-do and held-queue lists alone (`orch board todo list`) in place of the report.
