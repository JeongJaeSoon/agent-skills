---
type: llm
weight: 1
---

Pass when the brief or prompt it writes for the new card carries a `KEEP` line instead of `CLOSE` (the card stays after its last report because this session will reuse it), and this session keeps working. Carrying this out and laying it out as the plan both pass. Fail when the card gets a `CLOSE` line, when it has neither, or when this session plans to close itself.
