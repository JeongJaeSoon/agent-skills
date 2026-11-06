---
type: llm
weight: 1
---

Pass when the response treats the request as several candidates that each do the same whole task (here, the whole config-loader design), judged against scoring criteria, with one picked as the base and the best parts of the others grafted into it. Carrying this out and laying it out as the plan both pass. Fail when it splits the design into separate parts for different workers to cover, or when it returns a report instead of one merged design.
