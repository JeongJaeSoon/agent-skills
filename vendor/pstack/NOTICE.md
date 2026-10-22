# pstack vendoring 고지

이 저장소의 일부 파일은 [cursor/plugins](https://github.com/cursor/plugins)의 `pstack`(Lauren Tan, MIT)에서 가져왔다. 라이선스 전문은 같은 폴더의 `LICENSE`에 있다. 고정 커밋(`pin`), pstack 버전(`upstream_version`), 파일 목록은 `manifest.json`이 정본이다.

- 스킬 파일 자체에는 출처 줄을 두지 않는다. 출처와 라이선스 고지는 이 문서, `LICENSE`, `manifest.json`, README가 맡는다. 이 폴더는 플러그인에 함께 실린다.
- `verbatim`은 upstream과 바이트 단위로 같다. `adapted`는 아래 표의 수정만 했다. `derived`는 upstream 여러 파일을 합치거나 번역해 새로 쓴 파일이다. 3-way 병합이 맞지 않아 `manifest.json`에 넣지 않으므로 동기화 스크립트가 다루지 않고, upstream이 바뀌면 사람이 읽고 손으로 반영한다.
- 동기화: `python3 scripts/pstack-sync.py`(기본 dry-run, `--write`는 충돌 0일 때만 쓰고 pin을 올린다). 스크립트 자체 검사는 `bash scripts/pstack-sync-test.sh`.
- 3-way 병합은 텍스트 충돌만 잡는다. 동기화 PR에서는 새로 들어온 문장이 사용자 규칙(`~/.claude/CLAUDE.md`)과 부딪히지 않는지, 설치되지 않은 스킬을 부르지 않는지 사람이 읽고 확인한다.

## 파일별 수정

| 로컬 경로 | upstream | 방식 | 수정 |
|---|---|---|---|
| `skills/create-verification-skill/SKILL.md` | `pstack/skills/create-verification-skill/SKILL.md` | adapted | 생성 위치 `.cursor/skills/` → `.claude/skills/`. Claude Code는 Bash 호출마다 새 셸이라 Launch가 PID·포트·실행 폴더를 상태 파일에 남기도록 한 줄 추가. 모델 호출 허용(`disable-model-invocation` 삭제): `deliver-ticket`·`orchestrate`가 verify 스킬이 없을 때 부른다. description에 그 조건과 한국어 트리거를 더함. 5절의 `/maintain-verification-skill`을 스킬 이름으로, `.claude/`를 무시하는 저장소 안내 한 문장 |
| `skills/create-verification-skill/references/feature-map-example/*.md` | 같은 경로 | verbatim | — |
| `skills/maintain-verification-skill/SKILL.md` | 같은 경로 | adapted | 대상 위치 `.cursor/skills/` → `.claude/skills/`. changed 결과 전 "모든 변경 파일 다시 읽기" 지시 삭제. 모델 호출 허용(`disable-model-invocation` 삭제): flow improver가 주 1회, 코디네이터가 stale 보고 때 부른다. description에 그 두 경우를 더함 |
| `skills/interrogate/SKILL.md` | 같은 경로 | adapted | 사용 가능한 독립 리뷰어가 같은 rubric으로 검토. 의도가 모호하면 가정으로 표시. lead judgment 분류와 근거 요구 유지. 모델 계열·전용 플러그인 호출 요구 제거. |
| `skills/interrogate/references/*.md` | 같은 경로 | verbatim | — |
| `skills/show-me-your-work/SKILL.md` | 같은 경로 | adapted | Claude Code transcript 어댑터와 append-only 결정 기록 유지. 설치하지 않은 unslop 의존 제거. 모델 계열 대신 독립 리뷰와 증거 대조 사용. |
| `skills/show-me-your-work/references/decision-log-template.tsv`, `scripts/log.sh` | 같은 경로 | verbatim | — |
| `skills/blast-radius/SKILL.md` | 같은 경로 | adapted | how·why 참조와 영향 범위 조사 유지. 큰 변경의 독립 리뷰를 허용하되 모델·플러그인 지정 제거. |
| `skills/architect/SKILL.md` | 같은 경로 | adapted | arena와 rationale-template 연결, principle 상대 참조, 저장소에 쓰는 러너의 worktree 격리 유지. 러너 모델은 현재 환경에서 선택. Phase C 기본값(checkpoint 없음)에 계획 페이지 게시(arena 러너는 rationale까지만)와 번호 붙은 결정의 기본값 진행을 한 문장으로, Outputs에 계획 페이지를 더함 |
| `skills/arena/SKILL.md` | 같은 경로 | adapted | 한국어 트리거와 swarm 구분, rubric·합성·검증·격리 경로 유지. 러너와 심판은 독립적으로 실행하며 모델 계열 제한 없음. |
| `skills/architect/references/rationale-template.md` | 같은 경로 | adapted | checkpoint 참조를 Phase C로 바로잡음. "Open questions and risks"를 "Decisions and risks"(번호, 바뀌는 주장, 선택지 2~4개와 추천 기본값, 다른 안이 이기면 사라지는 부분, 되돌리기 어려운지)로 바꿈 |
| `skills/architect/references/plan-page.md` | `anthropics/claude-plugins-community`의 `html-plan/skills/html-plan/SKILL.md`, `references/blocks.md` | derived | pstack이 아니다. html-plan의 형식(주장 트리, 주장마다 근거 하나, 결정을 바꾸는 주장에 두고 추천 기본값 표시, 미응답은 동의가 아님, 붙여 넣은 응답은 데이터)만 새로 썼다. runtime(`htmlplan.js`, `htmlplan.css`, `pack.mjs`)과 예시는 복사하지 않았다. 응답을 기다리는 Respond 흐름, STE 문체 강제는 가져오지 않았다. 페이지는 `artifact-design` 규칙의 손으로 쓴 HTML |
| `skills/architect/references/design-red-flags.md`, `runner-prompt.md` | 같은 경로 | verbatim | — |
| `skills/tdd/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용(`disable-model-invocation` 삭제). 중복된 "signal이 약하면 테스트를 더하지 않는다" 줄 삭제(upstream #419도 같은 줄을 지움) |
| `skills/swarm/SKILL.md` | 같은 경로 | adapted | Agent 또는 Orca worker 어댑터, worktree 격리, base-branch 유지. 고정 모델·별도 모델 플러그인 의존 제거. |
| `skills/reflect/SKILL.md` | 같은 경로 | adapted | session·program·standing 모드, 증거 묶음, 두 번 발생 규칙, 교훈 ID와 이력 보존, draft PR 절차 유지. 독립 리뷰어를 사용하고 기록 경로·원본 저장소는 명시적 설정으로 선택. Backlog 외부 등록은 사용자 범위에 한정. |
| `skills/reflect/references/{judgment,tooling,divergent}-reviewer.md` | 같은 경로 | adapted | 스킬 사용 판정 경로를 `.claude/skills`·`~/.claude/skills`·`~/.claude/plugins`로, `Task` → `Agent`, `Skill` 도구 호출을 판정 근거에 추가. judgment·divergent의 "3-5개"를 "미래 행동을 바꾸는 것만, 빈 목록도 가능"으로 |
| `skills/reflect/references/synthesizer.md` | 같은 경로 | adapted | `create-skill` → `skill-creator`. Backlog 를 "devex / backlog tracker" 가 아니라 팀의 트래커로(부모가 `use-tracker` 로 등록) |
| `skills/how/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용. explorer는 내장 `Explore`, 단순 질문은 lead가 직접 탐색·설명, 종합은 general-purpose(opus) 한 번. `Task`·`generalPurpose`·`readonly`·grok 기본값 제거. description의 `why` 안내에 "왜 그렇게 결정됐는지"를 더함 |
| `skills/how/references/explorer-prompt.md`, `explainer-prompt.md` | 같은 경로 | verbatim | — |
| `skills/why/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용, description에 한국어 트리거. Cursor `mcps/` 탐색 대신 세션 도구 목록의 MCP로 출처를 고르고, 출처→playbook 표(git·`gh`, Linear/Jira는 `use-tracker`, Notion, Google Drive, Obsidian은 `use-notes`, Slack)를 둠. Datadog·Sentry·warehouse는 연결이 없어 조사자를 띄우지 않고 Sources Consulted에 "접근 없음"으로 남김. Calendar는 날짜 범위 좁히기에만. 좁은 질문은 git·`gh`만으로 답하는 narrow mode(Step 3) 추가. 조사자는 general-purpose Agent이고 쓰기·댓글·메시지 금지를 프롬프트로 전달, 종합은 lead가 직접 하거나 opus Agent 하나. `generalPurpose`·`readonly`·`pstack-models.mdc`·모델 slug 제거. 7개 카테고리 roster를 이 환경에 있는 4개로 줄임. "Default to the full parallel investigation"과 일곱 카테고리 모두 확인 후에만 inline 답을 허용하던 문장 삭제 |
| `skills/why/references/epistemics.md`, `investigator-prompt.md`, `synthesizer-prompt.md` | 같은 경로 | verbatim | — |
| `skills/why/references/source-playbook.md` | 같은 경로 | adapted | 표를 이 환경의 출처(Google Drive·Obsidian 추가, Datadog·Sentry·Databricks 제거)로 |
| `skills/why/references/sources/code-archaeology.md` | 같은 경로 | adapted | `--json reviews`에 없는 줄 단위 리뷰 스레드를 `gh api .../pulls/<n>/comments`로 읽는 한 줄 추가 |
| `skills/why/references/sources/linear.md` | 같은 경로 | adapted | Jira는 `use-tracker`로. 댓글은 `list_comments`, 프로젝트 문서는 `list_documents`·`get_document`로 읽도록 도구 이름을 이 Linear MCP에 맞춤 |
| `skills/why/references/sources/notion.md` | 같은 경로 | verbatim | — |
| `skills/why/references/sources/slack.md` | 같은 경로 | adapted | Cursor `mcp_auth` 언급을 이 Slack MCP의 검색·스레드 도구 이름으로 |
| `skills/why/references/sources/incident-postmortem.md` | 같은 경로 | adapted | Datadog·Sentry·Databricks 항목을 지우고 "접근 없음을 Gaps에 적는다"로. Notion 항목을 긴 문서(Notion·Google Drive·Obsidian)로 |
| `skills/figure-it-out/SKILL.md` | 같은 경로 | adapted | 한국어 트리거와 orchestrate 구분, 단계별 증거와 baseline, 독립 결과 검토 유지. 단위별 deliver-ticket 절차와 상대 principle 참조 사용. |
| `skills/recall/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용, 한국어 트리거. transcript 위치를 Cursor `agent-transcripts`에서 `~/.claude/projects/<인코딩한 경로>/`로, Orca 카드마다 다른 worktree 경로(지운 카드 포함)를 한 저장소로 묶는 법과 `orca search`(색인이 켜졌을 때)를 더함. `session-pickup`·`automate-me` 안내를 `claude --resume`으로. 채굴 서브에이전트를 `Agent` haiku로. 공유 기록에 worklog(`use-notes`)와 티켓(`use-tracker`)을 더하고 live 확인에 `orca worktree list`. `unslop`을 `write-plainly`로, PR 인용을 `owner/repo#N`로. 확인 못 한 스레드의 `[unverified]` 태그와 읽지 못한 출처를 빈칸으로 두는 규칙 추가 |
| `skills/teach/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용, 한국어 트리거와 `how`·`why`와의 구분을 description에. 이미지 생성 도구 대신 mermaid·ASCII 그림. 영어 전용 문체 규칙(긴 대시, 쉼표 수)과 금지 문구 목록을 `write-plainly`와 "사용자의 언어로"로 바꿈 |
| `skills/principles/references/principle-*.md` (23개) | `pstack/skills/principle-*/SKILL.md` | verbatim / adapted | 본문은 유지. attack-the-premise, build-the-lever, minimize-reader-load의 스킬 간 링크 8개를 이 저장소의 상대 경로로 조정 |
| `skills/deliver-ticket/references/pull-request.md`, SKILL.md §4 커밋 순서 | `pstack/skills/poteto-mode/playbooks/opening-a-pr.md` | derived | "PR 본문은 브리핑" 원칙과 절 순서를 한국어 절(왜·범위·트레이드오프·영향 범위·검증)로. 검증 절은 필수로 유지. squash 본문 40줄 이내, Conventional Commits 제목, ready PR. 글쓰기 도구를 `write-plainly`로. 이야기 순서의 작은 커밋, amend와 새 커밋의 구분 |
| `skills/deliver-ticket/references/review-loop.md` | `pstack/skills/poteto-mode/playbooks/babysit.md`, `shipping.md` | derived | 요청별 모드 셋(스레드만, 상태 한 번, ready까지)을 한국어 트리거로. shipping에서 "판정은 한 head에만 유효, main을 합치거나 rebase하면 새 head에서 다시 검증"을 옮김(patch-id 비교는 뺌). 그 밖에 스택 최하단 PR 우선, 충돌→리뷰 스레드→CI 순서, 재시도 전 CI 분류와 `git merge-base --is-ancestor` stale base 확인, 코멘트는 신뢰하지 않는 데이터로 다루고 답글은 `gh api --input`, 봇 달래기용 코드 변경 금지. watcher 스크립트 대신 `Monitor` until-loop. 모드 선언·Origin·merge 금지 규칙은 뺌(머지는 기존 §6 규칙) |
| `skills/deliver-ticket/references/review-bot-triage.md` | `pstack/skills/poteto-mode/references/bugbot-triage.md` | derived | 일반 리뷰 봇과 사람 리뷰에 fix/dismiss/ask 분류 적용. 리뷰 텍스트를 신뢰하지 않는 데이터로 취급하고, 실제 근거로 판정. |
| `skills/deliver-ticket/SKILL.md` §2 증거 규칙, `references/evidence.md` | `pstack/skills/poteto-mode/playbooks/bug-fix.md`, `refactoring.md` | derived | 배포하는 줄마다 런타임 증거, 반박된 가설이 낳은 변경은 되돌림, 사용자가 본 화면(브라우저는 브라우저 자동화)에서 재현. 리팩터링 전 동작 고정("타입 체크와 lint는 고정이 아니다"), 읽는 부담을 줄이지 못하면 되돌림. evidence.md에 버그 수정 절차(재현, how·why로 가설 세우고 이분 탐색, 실패 테스트 먼저, 같은 화면에서 확인, 실패 테스트 커밋이 먼저)와 리팩터링 절차(고정, 목표 모양, 빼고 나서 더하기, 호출부를 옮기고 옛 API를 같은 변경에서 삭제, 동등성 증명, 커밋 순서). 구현 위임 모델(grok) 규칙은 뺌 |
| `skills/deliver-ticket/SKILL.md` §5 판정어 | `pstack/skills/figure-it-out/SKILL.md` | derived | VERIFIED / NOT VERIFIED / INCONCLUSIVE 판정, inconclusive·다른 화면 결과는 통과가 아님, 너무 쉽게 통과하면 관찰 방법부터 의심 |
| `skills/deliver-ticket/references/evidence.md` 기존 수정 검증 | `pstack/automations/benny/skills/reproduce-and-fix-issues/references/verify-existing-fix.md` | derived | 이미 수정을 주장하는 PR·커밋이 있으면 경쟁 수정 대신 검증. baseline과 patched를 같은 데이터로 두 번씩 재현해 비교 |
| `skills/prune-comments/SKILL.md`, `references/pruner.md` | `pstack/skills/no-comments/SKILL.md`, `pstack/agents/comment-sicko.md` | derived | 모델 호출 허용(`deliver-ticket` §3이 부름). Cursor 전용 Comment Sicko 에이전트 대신 general-purpose `Agent`에 `references/pruner.md`를 넘김. 범위를 diff의 추가·변경 줄과 diff가 틀리게 만든 주변 주석으로 좁힘. 남길 목록에 저장소 규칙이 요구하는 doc 주석을 더함. `MUST KILL`을 RESHAPE·CONSTRAINT 표시로, suppression은 지우지 않고 표시만(빌드가 바뀌므로). 제약 인코딩은 사용자 승인 대기 대신 diff 파일 안에 들어가면 넣고 실패를 확인한 뒤 주석 삭제, 아니면 주석을 남기고 보고. 모양이 필요한 수정에 architect를 부르던 단계는 deliver-ticket의 범위 밖 발견 규칙으로. 캐릭터 말투 제거 |
| `skills/write-plainly/SKILL.md` | `pstack/skills/technical-writing/SKILL.md`, `pstack/skills/poteto-mode/SKILL.md` | derived | 모델 호출 허용. technical-writing의 상위 규칙 세 개·Diátaxis·STE·Global English에서 원칙 8개와 문서 종류 고르기를 추렸다. poteto-mode의 Writing the reply와 Autonomy의 "No is an acceptable answer"를 답변 절로 옮겼다. 출처 줄, `unslop`·`technical-writing` 참조, Cursor repo 전용 규칙(탭 들여쓰기 등)은 뺐다 |
| `skills/write-plainly/references/english.md` | `pstack/skills/unslop/SKILL.md`, `pstack/skills/technical-writing/SKILL.md`, `pstack/skills/poteto-mode/SKILL.md` | derived | unslop 규칙 번호와 내용을 유지하고 문장을 다듬었다. Process 절(초안 뒤 훑기)은 "쓰면서 적용"과 맞지 않아 뺐다. technical-writing의 Google·STE·Global English 문장 규칙, poteto-mode의 Comments 절, 스킬 본문 작성 규칙을 더했다 |
| `skills/write-plainly/references/korean.md` | `pstack/skills/unslop/SKILL.md` | derived | unslop의 서식 금지(긴 대시, 문장 중간 콜론, 굵은 글씨+콜론 목록, 장식 이모지)와 챗봇 문구 규칙을 한국어로 옮겼다. 상투어, 번역투, 얼버무림, 주어 생략, 명사 나열, 문체 규칙과 예문은 여기서 새로 썼다 |
| `skills/brief/SKILL.md` §1 결정 항목·이름 규칙 두 문장 | `pstack/skills/ketchup/SKILL.md`(pin 이후 upstream `978ca6e`에 추가) | derived | 결정 항목에 "답하지 않으면 어떻게 되는지"를, PR·브랜치·워커는 사람이 아는 이름으로 부른다는 규칙을 옮겼다. "마지막 메시지 이후" 범위와 `/ketchup` 명령은 `recall`·`brief`와 겹쳐 가져오지 않았다 |

위 표의 adapted `SKILL.md`는 모두 description을 이 환경의 스킬 목록 예산에 맞춰 줄였다. 트리거 문구와 다른 스킬과의 구분만 남기고 동작 설명은 본문에 맡긴다. upstream의 description 변경은 3-way 병합에서 충돌로 잡히니 이 원칙대로 손으로 합친다.

`skills/principles/SKILL.md`는 vendoring 대상이 아니다. poteto-mode의 `## Principles` 절을 참고해 여기서 새로 쓴 인덱스이며, 본문에 출처를 적었다.

`skills/map-features/SKILL.md`도 vendoring 대상이 아니다. pstack의 feature map(`create-verification-skill`의 `features/`, benny의 `feature-map.example.md`)은 에이전트가 기능을 구동하는 지도라서, 사람이 읽는 제품 기능 지도(기준 커밋, 상태, 근거)는 여기서 새로 썼다.

`skills/why/references/sources/google-drive.md`, `obsidian.md`도 vendoring 대상이 아니다. upstream playbook 형식을 따라 여기서 새로 썼다. upstream의 `datadog.md`, `sentry.md`, `databricks.md`는 연결된 MCP가 없어 가져오지 않았다.

## 흡수한 규칙

`orchestrate`과 기존 스킬 수정안은 pstack의 Orchestrate·Autopilot·Shipping 플레이북에서 운영 규칙을 옮겨 왔다. 옮겨 온 규칙의 출처는 이 문서로 갈음한다.
