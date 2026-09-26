# Ticket body skeletons

The user's ticket shapes. Use this file only when the active tracker has no templates
configured (`tracker.<adapter>.templates` in `~/.claude/agent-skills.json`, see `use-tracker`).
When templates are configured, the tracker's copy is the source and this file is not read.

Italic lines are hints for the writer: strip them from the filed body.

## 개발 티켓 (Feature, Bug, Improvement)

```markdown
## 🐞 증상

*버그일 때만. 재현 절차 / 현재 동작 / 기대 동작. 에러·로그는 요약하지 말고 원문 그대로. 버그가 아니면 이 섹션 삭제.*

## 🎯 배경 & 목표

*왜 지금 해야 하는지, 끝났을 때 어떤 상태인지. 2~4줄.*

## ✅ 인수 조건

*체크 가능한 것만. 각 항목에 확인 방법을 같이 적는다.*

- [ ]  조건 — *확인:* 어떻게 확인하는지

## 🚫 범위 밖

*이번에 의도적으로 건드리지 않는 것. 없으면 "없음".*

* 

## 🛠 구현 힌트

*리포에서 실제로 확인한 것만. 확인하지 못했으면 이 섹션을 통째로 삭제한다.*

* 진입점:
* 재사용: *이미 있는 헬퍼·유틸·패턴. 새로 만들지 않게 경로까지 적는다.*
* 흐름:
* 제약:

## 🔗 참고

*링크만. 결정 근거는 배경에 쓴다. 의존 티켓은 트래커의 blocked-by 관계로 건다.*

* 
```

## 조사 티켓 (Spike)

```markdown
## 🎯 배경

*무엇을 모르고 있고, 왜 지금 알아내야 하는지.*

## ❓ 답할 질문

*예/아니오나 선택지로 닫히는 질문만. 열린 질문은 조사가 안 끝난다.*

- [ ]  질문

## ⏱ 타임박스

*여기까지 쓰고 멈춘다(예: 2시간). 끝나면 결론을 이 티켓 코멘트에 남기고, 필요하면 후속 개발 티켓을 만든다. 조사용 코드는 버린다.*

## 🚫 조사 범위 밖

*이번에 안 재볼 선택지. 없으면 "없음".*

* 

## 🔗 참고

*링크만.*

* 
```

## 상위 티켓 (epic, parent)

인수 조건과 구현 힌트는 자식 티켓에만 둔다.

```markdown
## 🎯 배경 & 목표

*왜 이 묶음이 필요한지. 2~4줄.*

## ✅ 완료 조건

*끝났다고 말할 수 있는 조건. 번호를 매기고, 각 항목에 확인 방법을 같이 적는다. 자식 티켓은 이 항목 중 하나를 채운다.*

- [ ] 1. 조건 — *확인:* 어떻게 확인하는지

## 🚫 범위 밖

*이번 묶음에서 하지 않는 것. 없으면 "없음".*

* 

## ⏸ 보류

*LATER로 분류한 발견. 티켓을 만들지 않고 여기에 한 줄씩 적는다. 처음에는 비워 둔다.*

- <무엇> — 재검토: <다시 볼 계기> (<발견한 티켓>)
```
