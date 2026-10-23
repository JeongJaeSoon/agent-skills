# Scenario walkthrough

Measurement checks how a screen is drawn. The walkthrough checks whether it makes sense to use:
click through the screen (or the mockup) as the person who will use it, doing their task from
where they start to done. Do it in the browser, not from the code. Copy the template, fill it,
and put it next to the measurement table.

## Template

```markdown
### 시나리오: <누가> <무엇을 하려고> (시작: <진입 화면>)

| # | 장면(화면·상태) | 사람이 하는 일 | 주체 | 클릭 | 스크롤 | 막힌 곳·메모 |
|---|---|---|---|---|---|---|
| 1 | 에이전트 목록 | "새 에이전트" 누름 | 사람 | 1 | 0 | |
| 2 | 만들기 › 채널 연결 | 홈 채널 고름 | 사람 | 2 | 1 | 채널 목록이 접혀 있어 한 번 더 엶 |
| 3 | 설치 콜백 | (앱 등록·주소 등록) | 시스템 | 0 | 0 | 단계로 보이면 F1 위반 |

합계: 장면 3 · 클릭 3 · 스크롤 1 · 시스템 단계 노출 0 · 막힌 곳 1

옵션 목록 (F2)
| 옵션 | 바꾸는 것 | 겹치는 옵션 |
|---|---|---|
| 멘션 받기 | 채널 멘션에 응답 | 없음 |

판정 항목
- [ ] F1 사람에게 보이는 단계가 모두 "사람"이다
- [ ] F2 같은 것을 바꾸는 옵션이 둘 이상 없다, 한 설정이 두 메뉴에 다른 모양으로 있지 않다
- [ ] F3 종류가 다른 설정이 섹션·탭·하위 메뉴로 나뉘었다
- [ ] F4 상태 라벨이 짧고 이유는 다른 곳에 있다
- [ ] C5 버튼 라벨만 보고 무엇이 생기는지 안다
- [ ] C6 켜고 끄는 설정은 오른쪽 정렬 스위치다
- [ ] L8 첫 화면이 휑하지 않다(측정 coverage와 함께)
- [ ] 같은 종류의 기존 화면과 나란히 놓고 봤다 (기준 화면: ...)
- [ ] 막힌 곳마다 고친 방법 또는 남긴 이유가 있다
```

## How to fill it

- One table per task; a screen with two jobs (set up, then change a setting) gets two.
- **주체** is who actually does the step. A step the system does (create an app, register an
  address, join a channel) is "시스템" and should not appear to the person except as a result or
  an error.
- Count clicks and scrolls at 1280; note a step that needs extra scrolling at 390.
- **막힌 곳** is anything that made you stop: a label you had to guess, a choice whose effect
  was unclear, a control you could not find, a state with no next step.
- Walk each state the change touches: first time, already set, error, no permission.
