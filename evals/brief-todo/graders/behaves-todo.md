---
type: llm
weight: 1
---

Pass when the response runs, or says it would run, `orch board todo list` and sends only the to-do and held-queue tables it prints. Fail when it renders or lays out a full status report (`orch brief render`, sections such as 지금 오너가 할 일 or 결정 필요) instead of those lists.
