# Exit condition and follow-up tickets

The one source for NOW / LATER / DROP; other skills point here. `$T` is `use-tracker`'s
`scripts/tracker.py`.

## Classify a finding

A finding the work did not ask for, and cannot fix inside its own diff and scope, takes one class
against the exit condition below:

- **NOW** (an exit item needs it, or a reproduced correctness, security or data defect in what
  this work shipped) → a follow-up ticket filed with `write-ticket`, shaped as "Follow-up (derived)
  tickets" says; search the tracker first so you do not duplicate one. One ticket per independent
  diff; findings that must land together share one.
- **LATER** → one line on the deferred list with its revisit trigger. No ticket.
- **DROP** (speculative, not reproduced, already covered, out of scope) → one line with the
  reason in the report or worklog. No ticket.

Inside a program (the brief has a `PROGRAM:` line), file nothing: each finding goes with its
proposed class in the `worker_done` body, and the lead classifies and files.

## Exit condition

Every body of work has one, written before it starts or grows (principle **Work to an Exit
Condition**). It lives in the tracker, on the work's top ticket:

- **An epic or parent issue:** its body carries `## ✅ 완료 조건` (numbered exit items as a
  checklist, `- [ ] 1. <item> — 확인: <how>`, ticked when met), `## 🚫 범위 밖`, and `## ⏸️ 보류`
  (the deferred list: one line per LATER finding, `- <what> — 재검토: <trigger> (<source ticket>)`).
  Heading text and the `확인` label word may follow the tracker's language; the checklist form may not. Add a LATER line by editing
  the body; never file a ticket for it.
- **A single ticket with no parent:** its acceptance criteria and `🚫 범위 밖` are the exit
  condition. A LATER finding goes in a comment on it starting `⏸️ 보류:`.
- **A program:** the program note's predicate and out-of-scope list (`orchestrate`).

Report progress as "N/M 완료 조건 충족", read from the checked items, never as a count of
tickets filed.

## Follow-up (derived) tickets

Only a finding classified NOW against the exit condition becomes a ticket. LATER goes on the
deferred list above, DROP gets one line with its reason in the report or worklog. A NOW
follow-up is shaped the same way everywhere so `reflect`'s measure mode and the dashboard can count
it:

- label `follow-up`
- body starts with the line `파생: <source ID> · 원인: <분류>` (분류: 리뷰 지적 | 계약 불일치 | QA | 스펙 공백 | 구현 한계 | 기타),
  and the next line names the exit item it serves: `완료 조건: <n. item>`
- a **related** relation to the source ticket; its parent is the epic whose exit item it serves,
  so the epic's children are its NOW set. Inside a program it gets no parent: the dashboard
  counts the leaves under each parent as that stage's planned work
- same project as the source

```bash
printf '파생: ENG-12 · 원인: 리뷰 지적\n완료 조건: 2. <exit item>\n\n<what, why, acceptance>\n' > /tmp/body.md
python3 $T create --project P --title "..." --body-file /tmp/body.md --label follow-up --related ENG-12 --parent ENG-1   # ENG-1: the epic; no --parent inside a program
```

The `follow-up` label must already exist in the tracker (Linear labels are per team/workspace).
