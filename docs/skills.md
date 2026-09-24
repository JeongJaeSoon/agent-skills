# 스킬 카탈로그

`agent-skills` 플러그인이 싣는 스킬 20개와 별칭 3개. 스킬은 설명(description)에 적힌 상황이 오면 자동으로 불리고, 직접 부를 때는 `/agent-skills:<이름>`(다른 플러그인과 이름이 겹치지 않으면 `/<이름>`)을 쓴다.

## 흐름

```text
티켓 하나   write-ticket → deliver-ticket → handoff-ticket / end-session
              (곁가지·다른 저장소는 dispatch-card)
프로젝트    orchestrate ─ 워커마다 deliver-ticket ─ orch land ─ main 가디언·QA 리드
              └ 끝나면 measure-delivery
어디서나    use-tracker(티켓) · use-notes(노트) · pstack 스킬(설계·검토·검증)
```

## 티켓 하나

| 스킬 | 언제 부르나 | 하는 일 | 동봉 파일 |
|---|---|---|---|
| `write-ticket` | "티켓 만들어줘", "이슈로 남겨줘", 작업 중 나온 피드백을 별도 티켓으로 | 요청 출처 확인 → 유형 분류 → 템플릿을 저장소 기준으로 채움 → 승인 후 등록 | `references/templates.md` |
| `deliver-ticket` | 여러 파일을 고치기 전, PR 올리기·머지 전, "이 티켓 끝내줘" | 계획 → 구현 → Codex 교차 검토 → 커밋 → GitHub stack·E2E·머지 → 완료 기준 확인. 옛 이름 `ship-pr` | `scripts/sticky-comment.sh`(PR 결과 댓글을 하나로 유지) |
| `handoff-ticket` | "핸드오프", "다음 티켓 진행해줘", 티켓이 끝난 순간 | 완료 확인 → 다음 티켓을 새 Orca worktree 카드로 띄움 → 시작 확인 → 이 세션 종료 | |
| `dispatch-card` | "~ repo에 지시해줘", "곁가지로 띄워줘" — 이 세션은 계속 일할 때 | 대상 저장소 확인 → 브리프 작성 → 카드 생성 → 시작 확인 후 원래 일로 복귀. 옛 이름 `dispatch-work` | |
| `end-session` | "세션 종료해줘", "worktree 정리해줘" | Orca에게 이 세션이 무엇인지 묻고, 기록을 남긴 뒤 카드·worktree·대화를 순서대로 닫음 | |

## 프로젝트 하나 (Orca 워커 여러 개)

| 스킬 | 언제 부르나 | 하는 일 | 동봉 파일 |
|---|---|---|---|
| `orchestrate` | 한 세션이 여러 워커로 마일스톤을 끝까지 끌고 갈 때, 남이 돌리던 프로그램을 이어받을 때, PR이 왜 안 움직이는지 물을 때 | 프로그램 노트·원장·브리프 관리, 동시 실행 상한, 착지 순서와 독점 레인, main 가디언·QA 리드 역할, 대시보드 | `orch`(원장·착지 게이트), `orch-dash`(대시보드), `references/`(brief, landing, roles, program-note, dashboard) |
| `measure-delivery` | "성과 측정", 후속 티켓이 늘었는지, 재작업·토큰 비용, 프로그램 종료 시 | 트래커 이슈를 모아 후속 티켓 증가·수렴·재작업·PR당 토큰을 계산해 보고 | `scripts/measure.py` |

## 어댑터

| 스킬 | 언제 부르나 | 하는 일 | 동봉 파일 |
|---|---|---|---|
| `use-tracker` | 티켓을 읽기·찾기·만들기·라벨·댓글·상태 변경할 때, 스크립트가 티켓 데이터를 쓸 때 | 설정된 트래커(Linear, Jira)로 보내고 후속 티켓 형식·상태 어휘를 공유 | `scripts/tracker.py`, `references/linear.md`, `jira.md` |
| `use-notes` | 설계 문서·worklog·프로그램 노트를 읽고 쓸 때 | 설정된 노트 저장소(Obsidian vault, Markdown 폴더)로 보냄 | `references/obsidian.md`, `markdown.md` |

트래커와 노트 저장소는 `~/.claude/agent-skills.json`에서 고른다.

## pstack (Lauren Tan, MIT)

원본 이름을 유지해 upstream을 따라간다(`scripts/pstack-sync.py`).

| 스킬 | 언제 부르나 | 하는 일 |
|---|---|---|
| `architect` | "설계해줘", 바로 코드부터 쓰면 모양이 굳어버릴 작업 | 타입·시그니처·모듈 구조를 먼저 그리고, 구현 중에도 스케치와 대조. 틀렸으면 버림 |
| `blast-radius` | "이거 바꾸면 뭐가 깨져?", 믿기 어려운 작은 diff | diff 밖의 영향을 찾고, 안전하다는 근거 하나를 실제 코드 실행으로 증명 |
| `how` | "X는 어떻게 동작해?", "이건 어디에 둬야 해?" | 구조·실행 흐름·계층 설명. 복잡하면 탐색 에이전트를 먼저 보냄 |
| `interrogate` | "적대적 리뷰", "다른 모델로 검토", "빈틈 찾아줘" | 서로 다른 관점의 리뷰어 여럿(Codex 포함)이 변경을 공격하고, 리드가 채택할 것만 고름 |
| `principles` | 설계·리팩터·검증·위임 판단에 근거가 필요할 때 | 23개 원칙(근본 원인 수정, 동작을 테스트, 빼고 나서 더하기 등) 중 맞는 것을 인용 |
| `reflect` | "reflect" | 대화 기록을 리뷰어 셋이 읽고, 배운 점을 기존 스킬의 구체적 수정으로 연결. 적용 전 승인 |
| `show-me-your-work` | 오래 걸리거나 사람이 자리를 비운 작업 | 결정마다 무엇·왜·증거·결과를 TSV 한 줄로 남김(`scripts/log.sh`) |
| `swarm` | "병렬로 훑어줘", 경쟁 조건·탐색을 넓게 | 워커 N개를 펼쳐 모으고 보고서 하나로 정리 |
| `tdd` | TDD·실패 테스트를 명시적으로 요청하거나, 싼 로컬 테스트 대상이 뻔한 버그 | 실패 테스트 → 수정 → 통과 |
| `create-verification-skill` | 저장소에 앱을 사용자처럼 돌려 보는 스크립트가 없을 때 | 저장소 전용 검증 스킬(`.claude/skills/verify-<app>/`)과 기능 지도를 만들고 실제로 돌려 확인 |
| `maintain-verification-skill` | "verify 스킬 점검해줘" | 기능 지도를 소스와 실제 실행으로 대조해 증명된 수정만 PR 하나로 |

## 별칭

이름을 바꾸기 전에 시작한 세션이 옛 이름을 불러도 새 스킬로 안내한다: `ship-pr` → `deliver-ticket`, `dispatch-work` → `dispatch-card`, `use-obsidian` → `use-notes`.

## 플러그인이 대신 내리는 권한 결정

플러그인은 권한 규칙을 설정으로 실을 수 없어서 `hooks/guard.py`(PreToolUse)가 대신 결정한다. 워커가 스킬을 읽고 몇 시간 뒤 명령을 실행할 때 확인 창에서 멈추지 않게 하는 것이 목적이다.

| 결정 | 대상 |
|---|---|
| 허용 | 이 플러그인의 스킬 호출 |
| 허용 | `orca orchestration <명령>` (reset, worker-abandon, gate-resolve 제외) |
| 허용 | `orch <명령>` (임의 명령을 실행하는 `heavy`, 머지 정책을 바꿀 수 있는 `set`·`init`, 원장을 다시 쓰는 `backfill` 제외) |
| 거부 | 다른 터미널의 Orca 메일함을 읽는 `check`/`inbox --terminal <남의 handle>` |

허용은 셸 연산자·리다이렉션·치환·변수가 없는 단일 명령에만 준다. 허용 문자열 뒤에 다른 명령을 붙일 수 없게 하기 위해서다. 나머지는 평소 권한 흐름(auto mode 분류기, 사용자의 deny·ask 규칙)을 그대로 탄다.

`hooks/reorient.py`(SessionStart, 자동 압축 직후)는 이 터미널이 `orch init`으로 등록된 프로그램의 코디네이터일 때만 문맥 한 단락을 넣는다: 프로그램 노트를 다시 읽고 `orch status`를 돌린 뒤 `orch wait`로만 드레인하라는 내용이다. 압축 뒤 코디네이터가 착지한 카드 제거 단계를 빠뜨려 카드 8개가 남은 일이 있었다.
