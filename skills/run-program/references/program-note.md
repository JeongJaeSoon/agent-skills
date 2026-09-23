# Program note template

Create it at `Project/<project>/program-<slug>.md` with `vault_write` (see `use-obsidian`; check the vault's own CLAUDE.md for tags and headings). Korean prose, identifiers as-is. The note is the handoff: a new coordinator reads only this note and `program.json`.

```markdown
#<project> #program

## 개요

- 목표: <사람이 준 목표 한 문장>
- slug `<slug>` · Orca run `<run id>` · 저장소 `<owner/repo>` · Linear 프로젝트 `<name>`
- 머지 정책: autonomous | human-gate · 동시성 천장 6 · 마감 <ISO8601 또는 없음>
- 장부: `~/.claude/programs/<slug>/` (ledger.jsonl, briefs/, decisions.tsv)

## 완료 조건 (predicate)

- [ ] <TICKET-ID> <한 줄>
- [ ] 최종 확인: <실제 산출물에서 돌릴 검증>

(제안 상태면 "제안" 표시와 근거 한 줄)

## 상시 지시

1. <사람의 원 goal 문장을 그대로, 한 문장씩>
2. ...

같은 지시를 두 번 하게 되면 행동하기 전에 여기 한 줄을 먼저 추가한다. 이 목록은 모든 브리프의 STANDING에 그대로 붙는다.

## 사람 대기

- <결정이나 되돌릴 수 없는 작업, 선택지, 답이 없을 때의 기본값>

## 결정

- <날짜> <결정 한 줄> — 근거 (자세한 trail은 decisions.tsv)

## 진행 기록

- <시각> <prog.py status 네 줄 요약 + 이번 drain에서 바뀐 것>
```
