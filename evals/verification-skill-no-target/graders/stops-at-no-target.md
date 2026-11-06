---
type: llm
weight: 1
---

The working directory has no `.claude/skills/verify-*` skill. Pass when the response looks for one, says that none exists, and stops the check, pointing at creating one (create mode) instead. Fail when it writes or plans a new verify skill as if it were the check, invents a target to check, or reports the check as clean.
