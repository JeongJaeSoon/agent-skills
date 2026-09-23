# Program note template

Create it at `Project/<project>/program-<slug>.md` through `use-notes` (create or replace; check the vault's own CLAUDE.md for tags and headings). Korean prose, identifiers as-is. The note is the handoff: a new coordinator reads only this note and `program.json`.

```markdown
#<project> #program

## 개요

- 목표: <사람이 준 목표 한 문장>
- slug `<slug>` · Orca run `<run id>` · 저장소 `<owner/repo>` · 트래커 `<adapter>:<project>`
- 대시보드: `dash.py serve <slug>` → <URL>
- 머지 정책: autonomous | human-gate · 동시성 천장 6 · 마감 <ISO8601 또는 없음>
- 착지 순서: 등급(main-fix → gate → urgent → normal) · 대기 2시간이 넘으면 urgent 취급 · 의존 체인은 stack으로 한 번에 착지 (`prog.py queue <slug>`)
- 장부: `~/.claude/programs/<slug>/` (ledger.jsonl, briefs/, decisions.tsv)

## 완료 조건 (predicate)

- [ ] <TICKET-ID> <한 줄>
- [ ] 최종 확인: <실제 산출물에서 돌릴 검증>

(제안 상태면 "제안" 표시와 근거 한 줄)

## 상시 지시

1. <사람의 원 goal 문장을 그대로, 한 문장씩>
2. ...

같은 지시를 두 번 하게 되면 행동하기 전에 여기 한 줄을 먼저 추가한다. 이 목록은 모든 브리프의 STANDING에 그대로 붙는다.

## 의존 관계와 stack

- <티켓 A> ← <티켓 B> (B는 A 뒤에 착지. Orca task deps가 정본이고, 디스패치 뒤에 찾은 의존만 `prog.py dep`) · stack: #<하위> → #<상위>

## 착지와 역할

- 레인: 일반은 병렬. 독점 경로는 <기본값 + 이 프로그램의 공유 계약 경로> (`prog.py set <slug> exclusive_paths …`)
- 저장소 설정: required checks <목록>, up-to-date 요구 <꺼짐|켜짐: 비용을 digest에>, squash
- main 가디언: <dispatch id> · QA 리드: <dispatch id>, e2e 주기 착지 <5>건마다 · <2>시간마다 · 게이트 PR 직전, 설계 기준 문서 <경로>

## 사람 대기

- <결정이나 되돌릴 수 없는 작업, 선택지, 답이 없을 때의 기본값>

## 결정

- <날짜> <결정 한 줄> — 근거 (자세한 trail은 decisions.tsv)

## 진행 기록

- <시각> <prog.py status 네 줄 요약 + 이번 drain에서 바뀐 것>
```
