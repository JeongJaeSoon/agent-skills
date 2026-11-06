---
type: llm
weight: 1
---

Pass when the response runs, or says it would run, `orch brief render` and sends the full status report it prints, with the stage table only as the report's one pointer line. Fail when it answers with the stage table alone (`orch stage show`) in place of the report.
