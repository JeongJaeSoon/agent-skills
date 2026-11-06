---
name: fan-out
description: "Fan out parallel workers. Sweep splits one question, returns one report: /swarm, \"병렬로 훑어줘\", \"나눠서 동시에 확인해줘\". Compete runs candidates at one task, picks a base, grafts in the rest: /arena, \"경쟁시켜줘\", \"여러 버전 만들어서 비교해줘\". Landing tickets is orchestrate."
---

# Fan-out

Run N parallel workers and return one result. Pick the mode by what comes back, then read its file and follow it:

- **sweep**: one report from split or raced work. Workers cover separate slices of one question, race the same brief, or both. [references/sweep.md](references/sweep.md).
- **compete**: one artifact. Every candidate does the same task; you pick a base, graft in the best of the others and verify the result. [references/compete.md](references/compete.md).

A report from split work is sweep; one merged artifact is compete. Splitting a compete task into slices makes it a different job, so do not. Several tickets landed in parallel on their own cards is `orchestrate`.
