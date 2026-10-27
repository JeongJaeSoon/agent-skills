---
name: stage
description: "Use for \"단계표 보여줘\", \"stage table\", `/agent-skills:stage`: the per-item merge / dev / prod table the brief only links to."
---

# Stage

```bash
orch stage show [--program P] [--telegram] [--all]
```

Send what it prints, whole and unchanged: `--telegram` when the human wrote over Telegram, `--program` when they named one. It leaves out rows finished or unchanged for `board.stage_idle_d` days (7) and says how many; `--all` keeps them. To fit 78 columns it cuts a long name or PR key to `…[n]` and lists the whole text under the table; it exits 1 when the stage cells alone pass 78: send it as printed.

The dashboard generates a row per program predicate item and worker card each minute, the merge cell from the merge sha, and leaves cells without evidence empty. When you verify a cell, record it, then show again: `orch stage set <row> --col dev --mark ok --evidence v1.8.0` (a generated row's id is `<program>/<item>`; your cell wins). Never edit the output. The rest of the record is orchestrate's `references/dashboard.md`, "The stage table".
