# 스킬 카탈로그

`agent-skills` 플러그인이 싣는 스킬 27개, 별칭 3개, 명령 2개(`orch`, `orch-dash`), hook 2개를 정리한다. 스킬은 description에 적힌 상황이 오면 모델이 스스로 부른다. 예외는 `create-verification-skill`과 `maintain-verification-skill`으로, `disable-model-invocation`이라 사용자가 직접 불러야 한다. 직접 부를 때는 `/agent-skills:<이름>`을 쓰고, 다른 플러그인과 이름이 겹치지 않으면 `/<이름>`도 된다.

## 흐름

```text
티켓 하나   write-ticket → deliver-ticket → handoff-ticket → end-session
              (이 세션은 계속 일하면서 다른 저장소·곁가지로 보낼 때는 dispatch-card)
프로젝트    orchestrate ─ 워커마다 deliver-ticket ─ orch land ─ main 가디언·QA 리드 ─ 대시보드
              └ 끝나면 measure-delivery
어디서나    use-tracker(티켓) · use-notes(노트) · pstack 스킬(설계·검토·검증·회고)
```

## 티켓 하나

### write-ticket
- **언제:** "티켓 만들어줘", "티켓 기표해줘", "Linear 티켓으로 만들어줘", "티켓으로 남겨두고 종료하자". 다른 질문에 섞인 한 줄 요청, 진행 중인 작업에 대한 피드백, 작업 중 발견한 후속 티켓도 여기로 온다.
- **내용:**
  - 먼저 요청 출처를 가린다. 섞인 요청은 먼저 등록한 뒤 질문에 답하고, 진행 중 작업에 대한 피드백은 별도 티켓으로 만든다.
  - 유형은 Feature, Bug, Improvement, Spike로 나눈다. Spike는 제목에 `[조사]`를 붙인다.
  - 골격은 설정의 트래커 템플릿(`tracker.<adapter>.templates`)을 먼저 쓰고, 없으면 동봉 템플릿을 쓴다. 템플릿을 읽다 실패하면 멈춘다.
  - `🛠 구현 힌트`에는 실제로 열어 본 경로만 적는다.
  - 제목은 동사로 시작하고, 수용 기준에는 확인 방법을 붙이고, `🚫 범위 밖`은 반드시 적는다.
  - 의존은 트래커 relation으로 걸고, 1티켓 = 1PR로 나눈다. 서로 의존하는 PR은 GitHub stack으로 계획한다.
  - 초안 전체를 한 번 보여 주고 승인받은 뒤 등록한다. 섞인 요청과 세션이 스스로 올리는 follow-up은 먼저 등록한다.
- **동봉:** `references/templates.md`(개발 티켓과 조사 티켓 골격).
- **관계:** 트래커 조작은 모두 `use-tracker`로 한다. follow-up 형식은 `orchestrate`와 `measure-delivery`가 센다.

### deliver-ticket (옛 이름 `ship-pr`)
- **언제:** 여러 파일을 고치기 전부터 완료까지 쓴다. PR 생성·갱신·머지("pr 작성까지", "머지까지 진행해줘"), 릴리즈, 리뷰 코멘트 대응, codex 교차 검증, "동작확인", "내가 확인할 거 있어?", "이 티켓 끝내줘", stack 작업도 여기에 들어간다.
- **내용:**
  - **계획:** plan mode에 들어가지 않고 승인도 기다리지 않는다. 티켓에는 계획 댓글 하나만 남기고(바뀌면 고친다), 진행은 worklog에, 결과는 완료 댓글에 적는다. 일의 모양에 따라 스킬을 부른다. 큰 마이그레이션이나 여러 단계짜리 변경은 `figure-it-out`, 되돌리기 어려운 설계 결정은 `architect`(→ `arena`), 낯선 코드는 `how`, 이유를 모르는 코드는 `why`.
  - **구현:** worktree 브랜치에서만 한다. 사소하지 않은 로직은 실행 가능한 테스트 없이 커밋하지 않는다. 버그는 실패하는 재현 테스트부터 쓴다. 범위 밖에서 발견한 것은 이 diff에서 고치거나, follow-up 티켓으로 올리거나, worklog에만 적는다.
  - **증거 규칙:**
    - 배포하는 줄마다 런타임 증거가 있어야 하고, 반박된 가설이 낳은 변경은 되돌린다.
    - 버그는 사용자가 본 화면(브라우저는 Aside)에서 재현하고 확인한다.
    - 이미 수정을 주장하는 PR이나 커밋이 있으면 경쟁 수정 대신 baseline과 patched를 같은 데이터로 두 번씩 돌려 검증한다.
    - 리팩터링은 동작을 먼저 고정한다. 타입 체크와 lint는 고정이 아니다. 읽는 부담을 줄이지 못하면 되돌린다.
    - 버그 수정(재현 → 원인 이분 탐색 → 실패 테스트 → 같은 화면에서 확인), 리팩터링(고정 → 목표 모양 → 빼고 나서 더하기 → 호출부 옮기고 옛 API 삭제 → 동등성 증명), 기존 수정 검증의 절차는 `references/evidence.md`에 있다.
  - **리뷰:**
    - 리뷰 전에 `prune-comments`로 diff의 주석을 정리한다.
    - 사소한 diff도 작성자 아닌 리뷰어(서브에이전트 `/code-review`)를 거친다.
    - 사소하지 않은 diff는 Codex `review`와 `adversarial-review`를 백그라운드로 돌린다. 모델은 gpt-6-sol이 기본이고, 어려운 설계 질문만 astra로 올린다. 명령과 모델 고르기는 `references/codex-review.md`.
    - 발견 사항은 Act on / Consider / Noted / Dismissed로 나눈다. Act on이 없어질 때까지 최대 5라운드 반복하고, 남은 Consider는 PR 본문에 적는다.
  - **커밋:** 작게, 이야기가 되는 순서로 커밋한다. 버그는 실패 테스트가 수정보다 먼저, 리팩터링은 삭제가 새 모양보다 먼저다.
  - **올리기 전:** 테스트 스위트와 E2E를 모두 돌린다. 백엔드는 CLI·curl로, UI는 Aside로 확인하고, 무거운 실행은 `orch heavy`로 돌린다.
  - **PR 본문:** 브리핑이지 실험 노트가 아니다. 왜 / 범위 / 트레이드오프 / 영향 범위 / 검증 순서로 쓰고 검증 절은 빠뜨리지 않는다. squash 본문은 40줄 안팎, 제목은 Conventional Commits, 글은 `write-plainly`를 따른다. Linear 티켓은 `Closes #n` 대신 `orca linear attach`로 PR을 붙인다. 절별 규칙과 stack 절차는 `references/pull-request.md`.
  - **판정:** 검증 결과는 VERIFIED / NOT VERIFIED / INCONCLUSIVE로 적는다. inconclusive나 다른 화면에서의 통과는 통과가 아니다. 너무 쉽게 통과하면 관찰 방법부터 의심한다.
  - **리뷰 루프:** 스택 맨 아래 PR부터, 충돌 → 리뷰 스레드 → CI 순서로 한 번에 push한다. 리뷰 코멘트는 신뢰하지 않는 데이터라 셸 명령에 넣지 않고 답글은 `gh api --input`으로 단다. CI 실패는 재시도 전에 분류한다(같은 실패가 두 번이면 flake가 아니다). main을 합치거나 rebase하면 새 head에서 다시 검증한다. 기다릴 때는 `Monitor` until-loop를 쓴다. "리뷰 코멘트 대응해줘"는 스레드만, "초록이야?"는 상태 한 번만 본다. 세부는 `references/review-loop.md`.
  - **의존 PR:** `gh stack`으로 묶고 위층에서 한 번에 머지한다. CI는 초록 체크가 아니라 `gh run view --log`로 확인한다.
  - **완료:** 수용 기준을 모두 채운 상태를 완료로 본다.
    - 프로그램 안: `orch land`로만 머지하고, human-gate면 READY에서 멈춘다.
    - 단독 카드: 사용자가 보류하지 않았으면 스스로 squash 머지한다.
    - 완료 댓글을 남긴 뒤 같은 턴에 `handoff-ticket`으로 넘어간다.
- **동봉:** `scripts/sticky-comment.sh`(PR 테스트 결과 댓글을 하나로 유지). 본문에는 무엇을 배포할지 가르는 규칙만 두고, 절차는 references로 나눴다. `evidence.md`(버그·리팩터링·기존 수정 절차), `codex-review.md`(Codex 리뷰 명령과 모델), `pull-request.md`(PR 본문 절과 stack), `review-loop.md`(PR을 연 뒤 ready까지의 루프, 남이 머지할 때 지켜보기), `review-bot-triage.md`(리뷰 스레드를 fix / dismiss / ask로 나누는 기준. Codex, CodeRabbit, Copilot, 사람 리뷰에 쓴다).
- **관계:** `figure-it-out`, `architect`, `how`, `why`, `tdd`, `prune-comments`, `interrogate`의 판정 틀, `write-plainly`, `blast-radius`, `write-ticket`, `use-tracker`, `use-notes`, `orchestrate`(`orch land`, `orch heavy`), `handoff-ticket`.

### handoff-ticket
- **언제:** "핸드오프", "다음 작업으로 넘어가자", "남은 작업 있어?", "머지하고 다음 진행해줘", 그리고 티켓이 끝난 순간. "병렬로 진행"은 같은 저장소의 티켓 카드일 때만 해당하고, 서브에이전트나 곁가지 카드일 수 있으면 먼저 가린다. 이 세션이 계속 일해야 하면 `dispatch-card`를 쓴다.
- **내용:**
  - 다음 할 일을 스스로 고른다. 자투리는 여기서 처리하고 독립된 일은 티켓으로 만든다. 다음 티켓은 우선순위, 의존, 파일 충돌로 순위를 매긴다. 물어도 되는 것은 "어느 티켓이 다음인가"뿐이다.
  - 프로그램 워커(브리프에 `PROGRAM:` 줄이 있는 경우)는 worker_done을 보낸 뒤 코디네이터의 결정을 기다린다.
  - 완료를 확인한 뒤 `orca worktree create --prompt "/goal <ID>"`로 새 카드를 띄운다. 병렬 요청이어도 카드는 티켓당 하나다.
  - `orca terminal wait`와 `read`로 시작을 확인하고 `end-session`으로 이 세션을 닫는다.
- **관계:** `deliver-ticket` 다음 단계다. 끝은 반드시 `end-session`으로 맺는다.

### dispatch-card (옛 이름 `dispatch-work`)
- **언제:** "별도의 세션을 만들어서 ~ 해줘", "~ repo에 작업 지시해줘"처럼 다른 저장소나 곁가지로 일을 보내면서 이 세션은 계속 일할 때.
- **내용:**
  - 대상 저장소를 `orca repo list`로 확인한다.
  - 브리프(배경, 출처, 산출물·완료 조건, 금지 사항, "그 저장소의 CLAUDE.md를 따르라")를 노트에 쓰고, 카드 프롬프트에는 노트 경로와 요약만 넣는다.
  - 중복 카드를 확인한 뒤 카드를 만든다.
  - 시작을 한 번만 확인하고 원래 일로 돌아간다. 결과를 기다리지 않고, `end-session`도 부르지 않는다.
- **관계:** `use-notes`, `write-ticket`. 이 세션을 닫고 넘기는 경우는 `handoff-ticket`이 맡는다.

### end-session
- **언제:** "세션 종료해줘", "현재 세션 정리해줘", "아카이브해줘", "머지하고 종료하자", 그리고 `handoff-ticket`의 마지막 단계. 대상 없이 "정리해줘"만 하면 기록만 남기고 대화를 이어 간다.
- **내용:**
  - 먼저 티켓 최종 상태, 완료 댓글, worklog, 메모리를 남긴다.
  - `orca worktree current --json`의 결과로 닫는 방법을 고른다.
    - 카드: 작업 트리 검사를 통과하면 `orca worktree rm`으로 지운다. 저장소의 orca.yaml에 archive hook이 있으면 `--run-hooks`를 붙이고, hook이 실패하면 강행하지 않고 묻는다.
    - 메인 checkout: 터미널만 닫는다. `worktree rm`은 쓰지 않는다.
    - Orca 밖: `EndConversation`을 쓴다. 영구적인 동작이라 한 번 확인을 받는다.
  - 커밋하지 않은 변경은 사용자에게 묻고, `--force`는 쓰지 않는다. 프로그램 워커는 자기 worktree를 지우지 않는다.
  - 보고를 먼저 쓰고 종료 명령을 마지막 도구 호출로 실행한다.

## 프로젝트 하나 (Orca 워커 여러 개)

### orchestrate
- **언제:** 한 세션이 Orca 워커 여러 개(대개 3개 이상)로 마일스톤을 끝까지 끌고 갈 때, "대시보드 갱신해줘", "전체 진행상황 몇 퍼센트", 다른 코디네이터가 돌리던 프로그램을 이어받을 때, PR이 왜 안 움직이는지 물을 때.
- **내용:** 코디네이터는 코드가 아니라 프로그램을 소유한다. 매 세션 `orca skills get orchestration`부터 읽는다.
  1. **Frame:** 완료 조건(predicate)은 셀 수 있는 티켓 ID와 실제 산출물 검사로 정한다. 사람의 지시는 standing order로 그대로 옮긴다. 의존은 시작 순서(Orca task deps)와 착지 순서(GitHub stack, `orch dep`)로 나눈다. Run을 만들고 `orch init`으로 등록한다.
  2. **검증 준비와 Pilot:** verify 스킬이 없으면 첫 digest에서 사용자에게 `/create-verification-skill` 실행을 요청하고, 그동안은 손으로 검증하며 워커 하나로 끝까지 한 번 돌려 본다.
  3. **Scale:** 상시 역할(main 가디언, QA 리드)을 띄운다. 티켓 워커의 동시 실행 상한은 1에서 시작해 main green 착지마다 1씩 늘고(기본 ceiling 6), red면 반으로 준다.
  4. **Drain:** `orch wait`를 백그라운드로 하나만 돌린다. worker_done이 오면 같은 턴에 `CLOSE OUT`을 처리한다. 매번 `orch status`로 끝내고 STALLED, SPARE, LEDGER GAP, LANDED-BUT-OPEN 줄에 대응한다. 제품이 아니라 일하는 방식이 틀어졌으면(사람의 교정, 브리프가 답했어야 할 질문, 과정 탓의 정체, 스킬·스크립트 결함) `orch record <slug> signal`로 한 줄만 남기고 분석은 Close로 미룬다.
  5. **Triage:** follow-up은 기본적으로 미룬다(park). predicate를 막거나 재현된 결함만 받아들인다. 브리프는 follow-up을 parent가 아닌 related로 잇게 한다. Orca 기본은 parent지만, 그러면 집계와 단계 막대가 원래 티켓의 계획된 일로 센다.
  6. **Land:** 워커가 `orch land`로 직접 착지한다. 일반 PR은 병렬로 머지되고, migration·CI·Dockerfile·compose 같은 공유 파일은 독점 레인에서 base당 하나씩 머지된다.
  7. **main 검증:** red가 되면 가디언이 flake 여부부터 보고 hotfix나 revert를 고르며, 그동안은 main 수정만 착지한다. QA 리드는 티켓 검증, 주기적 E2E, 설계 정합성 감사를 맡는다.
  8. **Close:** 새 main에서 최종 확인을 하고 `record predicate_verified`로 기록한다. 이어서 역할을 풀고 `measure-delivery`를 돌린 뒤 `reflect`를 프로그램 모드로 돌린다. `worker-list --terminal-state reclaimable`이 빌 때까지는 끝내지 않는다.
  - human-gate의 "Land #N" Task는 `orch land`가 착지 때 completed로, 보류나 PR 닫힘 때 failed로 닫는다. 보류로 결정된 PR은 `orch land`가 머지하지 않는다.
  - 턴을 끝낸 워커에게는 내용을 `orchestration send --to dispatch:<id>`로 보내고, 터미널에는 "orchestration check를 돌려라" 한 줄만 보낸다. 터미널이 없는 워커는 Orca의 복구 절차를 따른다.
  - 상시 역할의 평상시 보고는 `--type status`로 보낸다. escalation은 코디네이터가 나서야 할 때만 쓴다.
  - Orca 기본 규칙과 일부러 다르게 하는 것(동시 실행 상한 1부터, 워커 모델 기본값, 티켓마다 worktree, 백그라운드 `orch wait`, 긴 `worker_done`, `terminal send` 한 줄 넛지)은 SKILL.md 표에 이유와 함께 있다.
  - 머지 정책은 autonomous(기본)와 human-gate 두 가지다. 사람에게는 대시보드와 요약만 보낸다.
  - 이어받을 때는 프로그램 노트 → `run-use` → `orch set`(노트의 정책을 원장에 맞춤) → `orch status` 순서로 한다.
- **동봉:**
  - `references/`
    - `brief.md`: 워커 브리프 템플릿.
    - `landing.md`: 레인, 착지 순서, stack, `land` 종료 코드.
    - `roles.md`: 가디언, QA 리드, flow improver.
    - `program-note.md`: 프로그램 노트 템플릿.
    - `dashboard.md`: 대시보드 설명.
  - `scripts/`
    - `prog.py`: `orch` 본체.
    - `dash.py`: `orch-dash` 본체.
    - `dash_demo.py`: 오프라인 데모.
    - `mailbox_guard.py`: 옛 설치에서 hook으로 넘어가는 전환용 shim.
    - 테스트 파일.
  - `assets/dashboard/`: 대시보드 화면.
- **관계:** 워커는 `deliver-ticket`을 따른다. `use-tracker`, `use-notes`, `create-verification-skill`, `show-me-your-work`, `swarm`, `measure-delivery`, `reflect`, `end-session`을 가져다 쓴다.

### measure-delivery
- **언제:** "성과 측정", 후속 티켓이 늘었는지 줄었는지, 재작업, 토큰 비용을 물을 때. `orchestrate`의 Close 단계에서도 부른다.
- **내용:** `scripts/measure.py`가 트래커와 GitHub에서 읽기만 해서 한국어 보고서를 낸다.
  - 기준선 대비 파생 티켓 증가율과 수렴 여부를 본다.
  - 머지된 PR 수를 센다.
  - 재작업(PR을 연 뒤의 커밋과 CI 재실행)을 본다.
  - 새어 나간 결함 후보(Bug 라벨, main CI 실패, revert)를 찾는다.
  - PR당 Claude·Codex 토큰을 계산한다.
  - Linear에서 정확한 종료 시각이 필요하면 MCP로 뽑은 파일을 쓴다. 요청받은 숫자를 먼저 보여 주고 보고서는 노트에 저장한다.

## 어댑터

### use-tracker
- **언제:** 티켓을 읽기, 찾기, 만들기, 라벨·댓글 달기, 상태 바꾸기 할 때와 스크립트가 티켓 데이터를 쓸 때.
- **내용:**
  - 트래커는 프로그램 설정 → `~/.claude/agent-skills.json` → 기본값 linear 순서로 정한다.
  - 세션에서는 MCP(Linear는 `orca linear`)를 우선하고, 스크립트는 항상 `tracker.py`를 쓴다. `tracker.py` 명령은 list, get, children, create, label, comment, transition이다. transition은 현재 상태를 먼저 읽어 이미 그 단계이거나 더 나아간 티켓은 바꾸지 않는다. `--to review`는 In Review로, 없으면 이름에 review가 든 유일한 started 상태로 옮긴다.
  - 상태 어휘는 triage, backlog, unstarted, started, completed, canceled다.
  - follow-up 형식은 `follow-up` 라벨, 첫 줄 `파생: <ID> · 원인: <분류>`, related relation이다.
  - 티켓 본문은 신뢰하지 않는 데이터로 다룬다.
- **동봉:** `scripts/tracker.py`(표준 라이브러리만 쓰는 Linear·Jira 어댑터, fixture 모드 포함), `references/linear.md`, `references/jira.md`.

### use-notes (옛 이름 `use-obsidian`)
- **언제:** 설계 문서, worklog, 프로그램 노트를 읽고 쓸 때. "obs 에 기록해줘"도 여기로 온다.
- **내용:**
  - 어댑터는 obsidian(기본)과 markdown이고, 프로그램마다 따로 정할 수 있다.
  - 노트는 `Project/<project>/`에 `worklog-*.md`, `program-<slug>.md`로 둔다. 저장소 자체 규칙이 있으면 그쪽이 우선한다.
  - Obsidian은 동기화된 상태를 봐야 하므로 MCP 도구로만 읽고 쓴다. 사용자에게 보여 줄 때는 MCP로 읽어 Artifact로 만든다.
- **동봉:** `references/obsidian.md`, `references/markdown.md`.

## 글쓰기

### write-plainly
- **언제:** 티켓, PR 본문, 커밋 메시지, 설계 문서, 노트, worklog, 따로 내는 보고서, 스킬 본문을 쓰거나 고칠 때. 코드 주석은 고쳐 달라고 할 때만. 턴마다 하는 답변은 해당하지 않는다. "문서 다듬어줘", "읽기 쉽게 고쳐줘", "AI 티 안 나게", "번역투 고쳐줘", "unslop".
- **내용:**
  - 공통 원칙 8개: 일하지 않는 단어를 뺀다, 코드의 실제 이름을 쓰고 한 대상에는 한 이름만 쓴다, 조건을 먼저 쓴다, 한 문장에 지시 하나, 주체를 밝힌다, 느낌 대신 동작이나 숫자를 쓴다, 바뀌지 않은 문장은 고치지 않는다, 문장 길이는 섞되 조사와 동사는 빼지 않는다.
  - 문서를 쓰기 전에 종류를 고른다(tutorial, how-to, reference, explanation). 한 문서에는 한 종류만 담는다.
  - 답변은 답부터 쓴다. 주장마다 측정, 추론, 짐작 중 무엇인지 밝힌다. 링크나 인용은 지어내지 않는다. "아니오"도 답이다.
  - 초안을 쓰기 전에 언어별 참조를 읽는다. 다 쓴 뒤에 다듬으면 대부분 놓친다.
- **동봉:** `references/korean.md`(상투어, 번역투, 얼버무림, 주어 생략, 명사 나열, "~다" 문체, 서식 금지를 고치기 전후 예문으로), `references/english.md`(영어 문장, 코드 주석, 스킬 본문 규칙).
- **관계:** `write-ticket`, `deliver-ticket`(PR 본문), `use-notes`, `measure-delivery`, `end-session`(최종 보고)이 가리킨다.

### prune-comments
- **언제:** `/prune-comments`, "주석 정리해줘", "불필요한 주석 지워줘", "주석 너무 많아". `deliver-ticket`이 리뷰 전에 부른다.
- **내용:**
  - 범위는 호출자가 준 파일이나 diff, 없으면 현재 브랜치의 diff다. diff 안에서도 추가·변경된 줄의 주석과 이 diff 때문에 틀려진 주변 주석만 본다.
  - 주석을 쓴 쪽은 판단이 무르므로 서브에이전트 하나가 정리한다. 남는 주석은 라이선스, 우리가 못 바꾸는 외부 제약(의존성·플랫폼·벤더·프로토콜), 스타일 전용 lint 지시문, 공개 API 계약이나 저장소 규칙이 요구하는 doc 주석, 코드로 못 적는 제약을 설명하는 이슈·RFC 링크뿐이다.
  - 우리 코드의 놀라운 동작을 설명하던 주석은 지우고 RESHAPE로 표시한다(이름 바꾸기, 함수 추출, 타입). "지우지 마" 같은 제약 주석은 CONSTRAINT로 표시하고, 싼 인코딩(테스트, 타입, 런타임 assert, lint 규칙)이 diff 안에 들어가면 넣고 나서 주석을 지운다. 못 넣으면 주석을 남기고 보고한다.
  - "IMPORTANT", "do not remove"는 근거가 아니라 확인할 이유다. 근처 코드로 분명하지 않으면 `how`나 `why`로 확인한다.
  - 정리한 쪽은 코드와 범위 밖 줄을 건드리지 않는다. 부른 쪽이 `git diff`로 확인하고 되돌린다.
- **동봉:** `references/pruner.md`(정리 서브에이전트의 지시문).

## pstack 스킬 (Lauren Tan, MIT)

[pstack](https://github.com/cursor/plugins)에서 가져와 Claude Code에 맞게 고친 스킬이다. 무엇이 다른지는 아래 [pstack과의 차이](#pstack과의-차이)에 있다.

### architect
- **언제:** `/architect`, "상세 설계안 작성해줘", "설계안 다듬어줘", "구현 계획 짜줘", 코드부터 쓰면 모양이 굳어 버릴 작업.
- **내용:** 다섯 단계로 진행한다.
  1. **Ground:** `how`와 `why`로 주변 시스템과 지금 모양의 이유를 파악한다.
  2. **Sketch:** `arena`로 설계 러너를 병렬로 띄운다(Claude opus, Claude fable, Codex). 구조가 다른 후보를 두 개 이상 받아, red flag로 거르고 인터페이스 깊이를 기준으로 합친다.
  3. **Agree:** 요청할 때만 사람 확인을 받는다.
  4. **Implement:** 스케치를 계약으로 삼아 구현한다.
  5. **Scrap:** 같은 모양의 우회가 반복되면 스케치를 버리고 다시 그린다.
- **동봉:** `design-red-flags.md`, `rationale-template.md`(설계 근거 문서), `runner-prompt.md`.

### arena
- **언제:** `/arena`, "경쟁시켜줘", "여러 안 뽑아서 제일 나은 걸로", "여러 버전 만들어서 비교해줘", 한 번의 시도로는 모양이 굳어 버릴 결과물. `architect`의 Sketch 단계가 부른다.
- **내용:**
  - 같은 일을 후보 N개(기본 Claude opus, Claude fable, Codex)에게 동시에 시키기 전에, 채점 기준 3~6개를 먼저 정한다. 후보는 과제만 보고 기준은 모른다.
  - 후보가 다 끝나면 다른 모델 계열의 심판(Claude가 부르면 Codex)이 기준별로 채점하고 기반안을 추천한다.
  - 모든 후보를 끝까지 읽고 기준별로 채점해 기반안을 고른다. 나머지 후보에서 나은 한두 가지를 손으로 접붙인다.
  - 후보들이 같은 모양으로 모이면 그대로 쓰고, 크게 갈리면 과제 정의부터 다시 한다.
  - 결과물 하나와, 기반안·접붙인 것·버린 것·검증 결과를 적은 짧은 종합 노트를 돌려준다.
- **swarm과의 차이:** swarm은 일을 나눠 훑고 보고서를 돌려준다. arena는 같은 일을 경쟁시켜 결과물 하나를 만든다.

### blast-radius
- **언제:** "이거 바꾸면 뭐가 깨져?", 작지만 믿기 어려운 diff.
- **내용:**
  - 변경이 안전하다는 근거가 되는 사실 하나를 찾고, 실제 코드를 돌리는 스크립트로 증명한다.
  - grep이 못 보는 곳(라이브러리 소스, 와이어 포맷, 실행 타이밍)을 본다.
  - 바뀌는 코드의 PR과 커밋은 `why`의 절차로 끌어온다.
  - 큰 변경은 Codex로 교차 확인한다.
  - 결과는 하는 일, 안전의 근거, 위험, 확인한 것, 머지 전 확인할 것으로 정리한다.

### figure-it-out
- **언제:** `/figure-it-out`, "알아서 설계해서 진행해줘", "큰 마이그레이션", "자리 비운 동안 끝내줘", 맞는 절차가 따로 없는 크고 여러 단계인 일. `deliver-ticket`의 계획 단계가 부른다. 티켓 여러 개를 병렬 카드로 돌릴 일은 `orchestrate`.
- **내용:** 코드보다 먼저 일하는 순서를 설계한다.
  1. **Frame:** 완료 조건을 반증 가능한 문장으로 쓰고, 범위를 수로 적고, 위험에 맞춰 엄격함을 정한다. 틀은 티켓 계획 댓글이나 worklog에 쓰고, 되돌릴 수 있는 일은 기다리지 않는다. 되돌릴 수 없는 단계만 그 자리에서 한 번 승인을 받는다.
  2. **Design:** 따로 착지할 수 있는 단위로 쪼개고, 가장 모르는 것부터 한다. 바뀌기 전 값을 잡아 두는 검증 장치를 먼저 만든다. 되돌리기 어려운 설계는 `architect`(→ `arena`)로 정한다. 병렬은 경계가 나뉘는 곳에서만, 워커마다 worktree를 준다.
  3. **Loop:** 단위마다 가설, 가장 작은 변경, 실제 결과물에서 측정, 유지 또는 되돌리기. 위임한 일은 결과물을 직접 읽거나 Codex가 판정한다. 판정은 VERIFIED / NOT VERIFIED / INCONCLUSIVE.
  4. **Trail:** `show-me-your-work`로 결정 기록을 남기고, 보통은 커밋해 PR에서 읽게 한다.
  5. **Verify:** 전체를 Frame의 완료 조건으로 실제 제품에서 확인하고, 되풀이된 교정은 검사나 스크립트로 굳힌다.
- **deliver-ticket과의 관계:** 단위마다 리뷰, 커밋, 올리기는 `deliver-ticket`의 규칙을 따른다.

### how
- **언제:** "X는 어떻게 동작해?", "이건 어디에 둬야 해?"
- **내용:**
  - 단순한 질문은 세션이 직접 탐색하고 설명한다.
  - 복잡한 질문은 `Explore` 탐색기 2~4개를 병렬로 띄운 뒤 opus 하나로 종합한다.
  - 설명은 Overview, Key Concepts, How It Works, Where Things Live, Gotchas 순서로 쓴다.
- **동봉:** `explorer-prompt.md`, `explainer-prompt.md`.
- **관계:** 동기와 역사("왜 이렇게 됐어")는 `why`로 넘긴다.

### why
- **언제:** "왜 이렇게 됐어", "이거 왜 이렇게 짰어", "이 결정 배경이 뭐야", "이 값은 어디서 나왔어", 코드를 바꾸기 전에 지금 모양의 이유를 찾을 때. 어떻게 동작하는지는 `how`가 맡는다.
- **내용:**
  - 한 줄이나 한 커밋에 대한 질문은 git blame → 커밋 → PR만으로 답하는 narrow mode로 끝내고, 찾지 않은 출처를 밝힌다.
  - 그 밖에는 출처마다 조사자를 하나씩 병렬로 띄운다(git·`gh`, Linear/Jira, Notion, Google Drive, Obsidian, Slack). 조사자는 자기 출처만 파고 다른 출처의 단서는 적어만 둔다. 빈 결과도 발견으로 남긴다.
  - Datadog, Sentry, warehouse는 연결이 없어 "접근 없음"으로 기록한다. Calendar는 날짜 범위를 좁힐 때만 쓴다.
  - 종합은 Direct / Supported / Inferred / Speculative / Unknown 다섯 단계로 나누고 인용을 단다. 코드를 그 코드의 의도에 대한 근거로 쓰지 않고, 질문에 섞인 가설은 후보로만 다룬다.
  - 답은 What We Found, Reasonably Infer, Competing Hypotheses, What We Don't Know, Sources Consulted 순서다. 코드를 바꾸려는 질문이면 Preserve / Change / Avoid / Risk를 붙인다.
- **동봉:** `epistemics.md`(확신도 기준), `investigator-prompt.md`, `synthesizer-prompt.md`, `source-playbook.md`, `sources/`(code-archaeology, linear, notion, google-drive, obsidian, slack, incident-postmortem).
- **관계:** `how`의 짝이다. `blast-radius`와 `architect`가 부른다. 트래커는 `use-tracker`, 노트는 `use-notes`로 읽는다.

### interrogate
- **언제:** "적대적 리뷰", "codex 교차 검증", "codex 로 설계안 점검", "빈틈 찾아줘". main으로 가는 PR은 `deliver-ticket`의 Codex 리뷰가 맡는다.
- **내용:**
  - 핵심은 모델 계열의 다양성이다. Claude(opus)와 Codex가 같은 프롬프트와 rubric으로 따로 리뷰한다.
  - 리드가 결과를 합쳐 Act on / Consider / Noted / Dismissed로 판정하고 합의 지도를 쓴다.
  - 의도가 모호하면 묻지 않고 가정했다고 표시한다. 수정은 자동으로 적용하지 않는다.
- **동봉:** `reviewer-prompt.md`, `rubric.md`, `code-quality-review.md`, `lead-judgment.md`. `deliver-ticket`의 리뷰 분류가 이 판정 틀을 쓴다.

### principles
- **언제:** 설계, 리팩터, 검증, 위임 판단에 이름 붙은 원칙이 필요할 때.
- **내용:** 원칙 23개의 인덱스다(Core, Architecture, Verification, Delegation, Meta). 적용할 원칙은 leaf 파일을 끝까지 읽는다. 예: 근본 원인 수정, 동작을 테스트, 빼고 나서 더하기, 사람을 기다리지 않기.
- **동봉:** `references/principle-*.md` 23개.

### recall
- **언제:** "어디까지 했지", "X 작업 어디까지 했더라", "최근 작업 정리해줘", "이번 주에 뭐 했지", 'catch me up'. 앞선 세션이 건드린 일을 시작하거나 이어 가기 전.
- **내용:**
  - 범위(기간 기본 7일, 주제, 저장소)를 먼저 정해 말한다. 다른 저장소의 세션은 묻지 않고 읽지 않는다.
  - 지난 세션은 `~/.claude/projects/<인코딩한 경로>/`에서 찾는다. Orca 카드는 worktree마다 경로가 달라서 main 체크아웃, `git worktree list`, 이미 지운 카드(같은 상위 폴더)를 모두 본다. `orca search` 색인이 켜져 있으면 그것을 먼저 쓴다.
  - 세션이 많으면 haiku 서브에이전트가 나눠 읽고 세션마다 목표, 결정, 남은 일, 막힌 곳, 산출물을 돌려준다. 원문은 서브에이전트 안에 둔다.
  - 주제가 기능·파일·버그를 가리키면 `why`의 출처 조사, worklog(`use-notes`), 티켓(`use-tracker`)도 함께 본다. 되돌린 수정과 계속 보고되는 증상이 여기서 나온다.
  - PR·브랜치·티켓·카드는 `git`, `gh`, 트래커, `orca worktree list`로 지금 상태를 확인한다.
  - 답은 요약 5줄 이하, 스레드마다 상태 태그(`[merged #N]`, `[open PR #N]`, `[in flight <branch>]` 등), 반복된 문제 5개 이하, 다음 할 일 하나다.

### reflect
- **언제:** "reflect", "스킬에 반영해줘", "스킬이 왜 안 떴어", "이 세션 돌아보고 스킬 개선해줘", "개선 이력 보여줘". `orchestrate`의 Close에서 프로그램 모드로, `end-session`에서 사람이 일하는 방식을 고쳐 준 세션이면 세션 모드로 자동 호출된다.
- **내용:**
  - 세션 모드는 이 세션의 transcript를, 프로그램 모드는 원장의 `signal`·머지 실패·main red·실패 판정과 결정 기록, `measure-delivery` 결과를 묶은 증거 묶음을 읽는다. 리뷰어는 셋(판단 opus, 도구 사용 Codex, 발산 opus)이다.
  - 종합자(opus)가 배운 점을 Accepted / Rejected / Backlog로 나눈다. 모두 교훈 장부(노트 저장소의 `Project/agent-skills/learnings.md`)에 한 줄씩 남는다. 장부는 교훈마다 신호, 근거 포인터, 발생 횟수, 고친 대상, 변경(PR·커밋), 스크립트가 낸 검증 수치, 이후 재발 여부를 적는다.
  - 스킬을 바꾸는 건 서로 다른 사례가 두 번 이상일 때만이다(재현된 보안·데이터 결함은 예외). 한 번뿐이면 후보로 남겨 두고 다음 발생을 기다린다.
  - 세션 모드는 사용자 승인 뒤 반영한다. 프로그램 모드는 사람이 없으므로 worktree에서 고치고 검증한 뒤 draft PR 하나만 열고, digest에 승인 요청을 남긴다. 머지는 사람이 한다. description 변경은 `trigger-probe.sh`로 발동을, 본문·스크립트 변경은 바뀐 경로를 도는 테스트나 사례 재현으로 동작을 확인한다. 검증이 없으면 `none`으로 적는다.
  - 신호도 실패도 없는 프로그램에서도 Close마다 돌아, 적용된 교훈이 이번 프로그램에서 버텼는지(`held through`, 프로그램 slug로 중복 없이) 기록한다.
  - 권한 hook, `orch land` 게이트, 테스트와 채점기, reflect 자신은 고치자고 제안하지 않고 Backlog로 보낸다.
  - Backlog는 `use-tracker`로 등록한다.

### show-me-your-work
- **언제:** 오래 걸리거나 사람이 자리를 비운 작업.
- **내용:**
  - 결정마다 무엇, 왜, 증거, 결과를 TSV 한 줄로 남긴다. 기록은 추가만 하고, 틀린 줄은 정정 줄을 덧붙여 바로잡는다.
  - 끝나면 transcript와 대조해 감사한다.
  - Codex가 교차 검토하고, 응답 끝에 누가 검토했는지 적는다.
- **동봉:** `scripts/log.sh`(행 추가), `references/decision-log-template.tsv`.

### swarm
- **언제:** `/swarm`, "병렬로 훑어줘", "나눠서 동시에 확인해줘", 넓게 나눠 훑거나 먼저 결과를 내는 쪽을 가리는 경주. 경쟁시켜 결과물 하나로 합치는 일은 `arena`.
- **내용:**
  - 완료 조건과 모양(나눠 맡기, 경주, 혼합)을 먼저 정한다. 경주는 먼저 통과한 쪽(first pass)이나 전체 순위(rank all)로 가린다.
  - 워커를 `Agent`(worktree 격리, 백그라운드)로 띄우고, 오래 도는 일은 Orca 워커로 띄운다. Orca 워커의 `--base-branch`는 new-top-level·new-child 배치에서만 받는다.
  - 워커는 PASS / ISSUES / BLOCKED로 보고한다. 커밋과 방법이 빠진 보고는 한 번 다시 돌린다.
  - 결과를 표 하나로 모은다.

### teach
- **언제:** 'teach me this', "이거 제대로 이해하고 싶어", "이 변경 이해시켜줘", "처음 보는데 이 구조 설명해줘". 한 가지 동작 질문은 `how`, 이유 질문은 `why`.
- **내용:**
  - 왜 묻는지(바꾸려는지, 리뷰하는지, 처음인지)에 맞춰 가져갈 것 몇 가지를 정하고, `how`와 `why`를 병렬로 불러 하나의 설명으로 엮는다. `why`의 확신도 표현은 그대로 둔다.
  - 일반적인 정의부터 말하고 지금 코드에 잇는다. 가장 작은 완결된 답을 먼저 주고, 물으면 더 들어간다.
  - 움직이는 부분이 셋 이상이면 그림을 한 장씩 늘려 가며 그린다(mermaid, ASCII).
  - 글은 `write-plainly`로, 사용자의 언어로 쓴다.

### tdd
- **언제:** TDD나 실패 테스트를 명시적으로 요청할 때, 또는 싼 로컬 테스트 대상이 뻔한 버그.
- **내용:**
  - 실패 테스트를 먼저 돌려 실패 이유를 확인하고, 최소한으로 고친 뒤 통과를 확인한다.
  - 테스트가 비현실적이면 이유를 밝히고 가장 가까운 실행 가능한 검사를 쓴다. 기존 assertion은 약하게 만들지 않는다.

### create-verification-skill (사용자 호출 전용)
- **내용:**
  - 저장소를 조사해 앱을 사용자처럼 띄우고 조작하고 관찰하는 저장소 전용 스킬 `.claude/skills/verify-<app>/`을 만든다. Launch, Doctor, Drive, Evidence, Cleanup 단계를 둔다.
  - 주요 기능 3~5개의 기능 지도를 함께 만든다.
  - 직접 끝까지 한 번 돌려 증명한다.
  - 모델이 부를 수 없으므로 `deliver-ticket`(남은 확인 사항)과 `orchestrate`(첫 digest)가 필요할 때 사용자에게 실행을 권한다.
- **동봉:** `references/feature-map-example/`(예시 앱의 기능 지도).

### maintain-verification-skill (사용자 호출 전용)
- **내용:**
  - 기능마다 소스를 읽고, 모든 기능을 실제로 구동해 기능 지도와 대조한다.
  - 문제는 문서 drift, 하니스 결함, 제품 결함으로 나눈다. 제품 결함은 보고만 한다.
  - 결과는 clean, changed(증명된 수정만 PR 하나), blocked 중 하나다.
  - verify 스킬이 기능을 못 다루거나 잘못 설명하면 `deliver-ticket`과 QA lead 보고를 거쳐 사용자에게 실행을 권한다.

## 별칭

이름을 바꾸기 전에 시작한 세션이 옛 이름을 불러도 새 스킬로 안내한다(`legacy/`).

| 옛 이름 | 새 이름 |
|---|---|
| `ship-pr` | `deliver-ticket` |
| `dispatch-work` | `dispatch-card` |
| `use-obsidian` | `use-notes` |

## 명령

### `orch` (프로그램 원장과 착지 게이트)
프로그램 상태는 `~/.claude/programs/<slug>/`에 있다: `program.json`, 추가만 하는 `ledger.jsonl`, `briefs/`.

| 명령 | 하는 일 |
|---|---|
| `init` | 프로그램 등록(저장소, Run, 트래커, predicate, 머지 정책, ceiling, 기한, 노트). 대시보드를 띄운다 |
| `set` | 노트가 바뀌면 정책·ceiling·기한·predicate·독점 경로를 맞춘다 |
| `status` | predicate 진척, main 상태, in-flight/상한, 다음 행동. STALE, LANDED-BUT-OPEN, STALLED, SPARE, LEDGER GAP 줄 |
| `record` | 원장 이벤트 기록(spawned, parked, admitted, main_green/red, predicate_verified 등) |
| `verdict` | 리뷰한 head의 판정 기록 |
| `gate` | human-gate용 결정 gate를 연다 |
| `dep` | 착지 순서 의존 기록 |
| `queue` | 착지 순서와 PR마다 멈춘 이유 |
| `land` | 유일한 착지 경로. 종료 코드 0 착지, 2 양보, 3 조치 필요, 1 거부 |
| `land-check` | 머지 없이 준비 상태만 진단 |
| `landed` | 밖에서 한 머지를 기록 |
| `backfill` | 등록 전에 머지된 PR과 main CI를 원장에 넣는다 |
| `heavy` | 무거운 명령(compose, 이미지 빌드)을 머신 전체 2슬롯으로 제한해 실행 |
| `wait` | 코디네이터 전용. 처리할 메시지가 올 때까지 기다리고 `CLOSE OUT` 줄을 낸다 |

### `orch-dash` (대시보드)
- **실행 방식:** `orch init`과 `orch status`가 `orch-dash ensure`를 불러 알아서 띄운다. 서버는 저장소당 하나이고, 더 새 코드가 설치되면 교체된다.
- **보여 주는 것:**
  - Now: 워커마다 도구를 실행 중인지, 생각 중인지, 입력을 기다리는지, 스스로 건 대기 중인지.
  - Needs attention: 멈춘 워커, 빈 슬롯, 원장 누락, 정리할 카드.
  - Stages: 트래커 최상위 이슈별 진척.
  - 착지 순서(착지를 기다리는 PR이나 독점 레인을 쥔 PR이 있을 때만)와 burn-up.
  - 표는 열 머리를 눌러 정렬한다. 두 번째는 역순, 세 번째는 원래 순서이고, 브라우저가 선택을 기억한다.
- **부하:** 브라우저는 5초마다 묻지만 바뀐 게 없으면 304로 본문 없이 끝나고, 탭이 숨겨져 있으면 묻지 않는다. 서버는 원장 3초, Orca 20초, 트래커·GitHub 60초 주기로 모으며, 끝난 프로그램(최종 확인 기록이 유효)은 원장만 본다.
- **명령:** `collect`, `serve`, `ensure`, `note`(위험·결정 한 줄), `demo`. 환경 변수는 `ORCH_DASH_PORT`, `ORCH_DASH=off`다. 자세한 내용은 `skills/orchestrate/references/dashboard.md`에 있다.

## 설정 파일

`~/.claude/agent-skills.json`에서 트래커와 노트 저장소를 고른다. 파일이 없으면 Linear와 Obsidian vault `Private`을 쓴다. 프로그램의 `program.json`에 적은 값이 이 파일보다 우선한다.

```json
{
  "tracker": {
    "adapter": "linear",
    "linear": {"workspace": null, "team": "ENG", "project": null,
               "templates": {"dev": "개발 티켓", "spike": "조사 티켓"}},
    "jira": {"base_url": "https://…", "email_env": "JIRA_EMAIL", "token_env": "JIRA_API_TOKEN", "issue_type": "Task"}
  },
  "notes": {"adapter": "obsidian", "obsidian": {"vault": "Private"}, "markdown": {"root": "~/notes"}}
}
```

## hook

### 권한 결정
`hooks/guard.py`(PreToolUse, `Bash|Skill`). 플러그인은 권한 규칙을 설정으로 실을 수 없어서 hook이 대신 결정한다. 목적은 워커가 스킬을 읽고 몇 시간 뒤 명령을 실행할 때 확인 창에서 멈추지 않게 하는 것이다.

| 결정 | 대상 |
|---|---|
| 허용 | 이 플러그인의 스킬과 별칭 호출. 단, 접두사 없는 이름이 `~/.claude/skills`나 프로젝트 `.claude/skills`의 같은 이름 스킬에 가려지면 판정하지 않는다 |
| 허용 | `orca orchestration <명령>`. reset, worker-abandon, gate-resolve는 제외 |
| 허용 | `orch <명령>`. 임의 명령을 실행하는 `heavy`, 머지 정책을 바꿀 수 있는 `set`·`init`, 원장을 다시 쓰는 `backfill`은 제외 |
| 거부 | 다른 터미널의 Orca 메일함을 읽는 `check`/`inbox --terminal <남의 handle>`. 같은 명령 안에서 `$ORCA_TERMINAL_HANDLE`을 다시 바인딩하는 경우도 포함 |

허용은 셸 연산자, 리다이렉션, 치환, 변수가 없는 단일 명령에만 준다. 변수는 `$ORCA_TERMINAL_HANDLE` 하나만 예외다. 명령 이름 바로 뒤에 하위 명령이 와야 하고, 플래그가 먼저 오면 판정하지 않는다. 판정하지 않은 호출은 평소 권한 흐름(auto mode 분류기, 사용자의 규칙)을 탄다.

### 압축 후 재정렬
`hooks/reorient.py`(SessionStart, `compact`). 문맥이 압축된 직후(자동이든 `/compact`든) 이 터미널이 `orch init`으로 등록된 프로그램의 코디네이터일 때만 한 단락을 넣는다. 내용은 네 가지다.
- `agent-skills:orchestrate`를 다시 불러 읽는다.
- 프로그램 노트를 읽는다.
- `orch status`를 돌려 출력된 모든 줄에 대응한다.
- 이후에는 `orch wait`로만 drain한다.

## pstack과의 차이

### 한눈에

| | pstack (cursor/plugins) | agent-skills |
|---|---|---|
| 대상 | Cursor 플러그인 | Claude Code 플러그인 |
| 진입점 | `/poteto-mode` 하나가 항상 켜져 있고, 작업을 플레이북 23개 중 하나에 맞춰 단계를 따른다 | 모드 라우터가 없다. 스킬마다 description이 트리거다 |
| 스킬 호출 | 거의 모두 사용자나 poteto-mode만 부른다(`disable-model-invocation`) | 두 verify 스킬을 빼고 모델이 스스로 부른다 |
| 워커 | Cursor Task 서브에이전트, 클라우드 워커 | Claude Code `Agent`(worktree 격리), Orca 워커 |
| 모델 | 역할별 모델 규칙(`pstack-models.mdc`). 코드는 grok, 판단은 opus | Claude(opus, fable)와 Codex(gpt-6-sol 기본, 어려운 설계만 astra) |
| 교차 검토 | 여러 모델 패널 | Claude + Codex companion. Codex는 한 번에 한 작업 |
| 프로젝트 운영 | orchestrate, autopilot, shipping 플레이북과 `orch.ts`(bun, TSV 원장) | `orchestrate` 스킬, `orch`(Python, JSONL 원장), 착지 게이트, 독점 레인, main 가디언, QA 리드, 대시보드 |
| 티켓·노트 | 전제 없음 | `use-tracker`(Linear, Jira), `use-notes`(Obsidian, Markdown) |
| 권한 | Cursor 설정 | `hooks/guard.py`가 대신 결정 |
| 언어 | 영어, unslop 문체 규칙 | 스킬 본문은 영어, 문서·티켓·PR은 한국어. 문체는 `write-plainly`(한국어·영어) |

### 가져온 것
pstack 스킬 47개(원칙 23개와 나머지 24개) 가운데 원칙 23개 전부와 나머지 중 15개를 가져왔다. 고정 커밋은 `b42effe`(0.15.3)이고 파일 목록은 `vendor/pstack/manifest.json`에 있다.

- **거의 그대로 가져온 것:** 원칙 23개, 리뷰·탐색 프롬프트, 설계 red flag, 기능 지도 예시.
  - 원칙은 pstack에서 스킬 23개로 나뉘어 있던 것을 `principles` 스킬 하나의 참조 파일로 묶었다. 인덱스(`principles/SKILL.md`)는 여기서 새로 썼다.
- **고쳐서 가져온 것:** architect, arena, blast-radius, figure-it-out, how, why, interrogate, recall, reflect, show-me-your-work, swarm, tdd, teach, create-verification-skill, maintain-verification-skill. 공통으로 한 일은 네 가지다.
  - 모델이 스스로 부를 수 있게 했다.
  - Cursor 전용 요소를 Claude Code 대응물로 바꿨다. 경로는 `.cursor/` → `.claude/`, 워커는 `generalPurpose`/클라우드 → `Agent`/Orca 워커, transcript는 `agent-transcripts` → `~/.claude/projects`.
  - 모델 패널을 Claude + Codex로 바꿨다.
  - 설치하지 않은 스킬(`unslop`)을 부르던 곳을 `write-plainly`로 바꿨다. `why`와 `arena`는 2026-09-25에 들여와 원래 연결을 되살렸다(architect → arena).
  - `why`는 Cursor MCP 탐색 대신 세션에 있는 MCP 도구로 출처를 고르고, 한 줄 질문용 narrow mode를 더했다. 연결 없는 Datadog·Sentry·warehouse 출처 파일은 빼고 Google Drive·Obsidian 출처를 새로 썼다.
- **2026-09-25 추가 수정:** Opus 5.5에 맞춰 프롬프트를 감사하고 더 고쳤다(`6eec9fa`). architect와 swarm의 단계별 할 일 목록을 없앴고, how의 단순 질문은 직접 처리한다. reflect 리뷰어의 개수 하한을 없앴고, show-me-your-work는 추가만 하는 기록으로 바꿨다. 파일별 수정 내역은 `vendor/pstack/NOTICE.md`에 있다.
- **옮겨 쓴 것(derived):** 파일을 통째로 가져오지 않고 규칙만 옮겨 새로 쓴 부분이다. `manifest.json`에 없어 동기화하지 않고, upstream이 바뀌면 사람이 읽고 반영한다.
  - `write-plainly`: unslop, technical-writing, poteto-mode의 답변 규칙을 합치고 한국어 규칙을 새로 썼다.
  - `deliver-ticket`: opening-a-pr(PR 본문, 커밋 순서), babysit(리뷰 루프, 요청별 모드), shipping(판정은 한 head에만 유효), bugbot-triage(리뷰 스레드 분류), bug-fix와 refactoring(증거 규칙과 절차), figure-it-out(판정어), benny(기존 수정 검증)에서 규칙을 옮겼다.
  - `prune-comments`: no-comments와 Comment Sicko 에이전트의 남길 목록, 표시, 확인 절차를 diff 범위로 좁혀 옮겼다.

### 가져오지 않은 것

| pstack 스킬 | 하는 일 | 가져오지 않은 이유 |
|---|---|---|
| poteto-mode | 항상 켜진 모드 라우터와 플레이북 23개 | 스킬별 트리거로 대신한다. Claude Code의 output style로 모드를 흉내 낼 수 있지만, 강제 적용은 사용자 설정을 덮어쓰고 선택 적용은 켜지 않게 되어 만들지 않았다. 운영 규칙은 `orchestrate`, PR·리뷰·증거 플레이북은 `deliver-ticket`, 답변 규칙은 `write-plainly`에 옮겼다 |
| no-comments | 주석 제거 | Cursor 전용 Comment Sicko 에이전트에 기댄다. 규칙은 `prune-comments`로 옮겼다. unslop과 technical-writing은 `write-plainly`로 합쳤다 |
| typescript-best-practices | TS 규칙 | 범용 스킬이 아니다 |
| setup-pstack, make-bot-ui, bro, automate-me, benny 자동화 | Cursor 모델 설정, Grok Bot 등 | Cursor나 Grok에 묶여 있다 |

### pstack에 없는 것
- **티켓 흐름:** `write-ticket`, `deliver-ticket`, `handoff-ticket`, `dispatch-card`, `end-session`. Orca 카드와 트래커를 전제로 한 티켓 하나의 처음부터 끝까지다.
- **프로젝트 운영:** `orchestrate`의 `orch` 원장, 착지 게이트와 독점 레인, human-gate, main 가디언, QA 리드, `orch-dash` 대시보드. 모양은 pstack 플레이북을 따랐지만 Orca Run과 GitHub stack 위에서 새로 만들었다.
- **측정과 어댑터:** `measure-delivery`, `use-tracker`, `use-notes`.
- **hook:** 권한 결정(`guard.py`)과 압축 후 재정렬(`reorient.py`).

### upstream 동기화 상태
- **pin 이후 커밋:** upstream `main`(0.15.5)은 pin 뒤로 두 커밋이 더 있다.
  - #419: Opus 5.5에 필요 없는 지시 19개를 걷어냈다. 우리 `6eec9fa`와 방향이 같고, tdd와 reflect 리뷰어는 같은 곳을 고쳤다.
  - #422: 모델 규칙을 읽는 방식을 통일했고, show-me-your-work에 run마다 `start` 행을 두게 했다.
- **dry-run 결과:** `python3 scripts/pstack-sync.py --to origin/main`을 돌리면 원칙 4개와 interrogate 참조 3개는 깨끗하게 들어온다. tdd와 tooling-reviewer는 자동 병합된다. 고쳐서 가져온 9개 파일(interrogate, architect, swarm, reflect, how, why, show-me-your-work, 리뷰어 2개)은 충돌한다.
- **충돌 성격:** 대부분 Cursor 모델 규칙 줄이라 우리 쪽을 유지하면 된다. `--write`는 충돌이 0일 때만 쓰므로 손으로 병합해야 한다.
