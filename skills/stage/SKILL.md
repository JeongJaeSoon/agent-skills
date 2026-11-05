---
name: stage
description: "Use for \"단계표 보여줘\", \"stage table\", `/agent-skills:stage`: the per-item merge / dev / prod table the brief only links to."
---

# Stage

```bash
orch stage show [--program P] [--telegram] [--all]
```

Send what it prints, whole and unchanged: `--telegram` when the human wrote over Telegram, `--program` when they named one. It leaves out rows finished or unchanged for `board.stage_idle_d` days (7) and says how many; `--all` keeps them. A `지금` column appears when some row's PR is also a board row's: that row's status as the brief judges it (🔄 🙋 ⏳ ⏸️ 🔴 ❔), so the two never disagree; a PR several board rows judge differently gets none. It never cuts a cell: a table wider than 78 columns goes out as one whole line per row instead (`- <name> — <PR> · <merge> · <dev> · <prod>`): send it as printed.

The dashboard generates a row per program predicate item and worker card each minute, the merge cell from the merge sha, dev and prod as 해당 없음 unless the program's `stages` names them, and leaves cells without evidence empty. A row ends when every cell is ✅ or 해당 없음, and shows `(중단)` when its ticket was released or parked, or its card ended, with nothing merged and no PR or a closed one. The Stages page counts what this command prints. When you verify a cell, record it, then show again: `orch stage set <row> --col dev --mark ok --evidence v1.8.0` (a generated row's id is `<program>/<item>`; your cell wins). Never edit the output. The rest of the record is orchestrate's `references/dashboard.md`, "The stage table".
