# pstack vendoring 고지

이 저장소의 일부 파일은 [cursor/plugins](https://github.com/cursor/plugins)의 `pstack`(Lauren Tan, MIT)에서 가져왔다. 라이선스 전문은 같은 폴더의 `LICENSE`에 있다. 고정 커밋(`pin`), pstack 버전(`upstream_version`), 파일 목록은 `manifest.json`이 정본이다.

- 스킬 파일 자체에는 출처 줄을 두지 않는다. 출처와 라이선스 고지는 이 문서, `LICENSE`, `manifest.json`, README가 맡는다. 이 폴더는 플러그인에 함께 실린다.
- `verbatim`은 upstream과 바이트 단위로 같다. `adapted`는 아래 표의 수정만 했다. `derived`는 upstream 여러 파일을 합치거나 번역해 새로 쓴 파일이다. 3-way 병합이 맞지 않아 `manifest.json`에 넣지 않으므로 동기화 스크립트가 다루지 않고, upstream이 바뀌면 사람이 읽고 손으로 반영한다.
- 동기화: `python3 scripts/pstack-sync.py`(기본 dry-run, `--write`는 충돌 0일 때만 쓰고 pin을 올린다). 스크립트 자체 검사는 `bash scripts/pstack-sync-test.sh`.
- 3-way 병합은 텍스트 충돌만 잡는다. 동기화 PR에서는 새로 들어온 문장이 사용자 규칙(`~/.claude/CLAUDE.md`)과 부딪히지 않는지, 설치되지 않은 스킬을 부르지 않는지 사람이 읽고 확인한다.

## 파일별 수정

| 로컬 경로 | upstream | 방식 | 수정 |
|---|---|---|---|
| `skills/create-verification-skill/SKILL.md` | `pstack/skills/create-verification-skill/SKILL.md` | adapted | 생성 위치 `.cursor/skills/` → `.claude/skills/`. Claude Code는 Bash 호출마다 새 셸이라 Launch가 PID·포트·실행 폴더를 상태 파일에 남기도록 한 줄 추가 |
| `skills/create-verification-skill/references/feature-map-example/*.md` | 같은 경로 | verbatim | — |
| `skills/maintain-verification-skill/SKILL.md` | 같은 경로 | adapted | 대상 위치 `.cursor/skills/` → `.claude/skills/`. changed 결과 전 "모든 변경 파일 다시 읽기" 지시 삭제 |
| `skills/interrogate/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용. 리뷰어를 Claude(Agent) + Codex(codex-companion, 설정 기본 모델, 어려운 설계 문제만 astra)로. 의도가 모호하면 묻지 않고 가정으로 표시. Step 4의 번호 매긴 종합 단계를 한 문단으로 |
| `skills/interrogate/references/*.md` | 같은 경로 | verbatim | — |
| `skills/show-me-your-work/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용. transcript 경로를 Claude Code 형식으로. 교차 모델 리뷰를 Codex로. 설치하지 않은 `unslop` 언급 제거. 감사 단계를 append-only와 맞춰 틀린 줄은 정정 줄로 바로잡게 함 |
| `skills/show-me-your-work/references/decision-log-template.tsv`, `scripts/log.sh` | 같은 경로 | verbatim | — |
| `skills/blast-radius/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용. 설치하지 않은 `arena`·`unslop` 언급을 Codex·평이한 문장으로(`how`·`why` 언급은 upstream 그대로) |
| `skills/architect/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용. Phase B는 upstream처럼 `arena`를 부르되, runner 기본값(opus·sol·grok)과 `pstack-models.mdc` 대신 arena의 Claude(opus, fable) + Codex 러너를 쓰고 저장소에 스케치를 쓰는 Claude 러너는 worktree 격리. principle 스킬 이름을 `principles`의 references로 연결하는 한 줄 추가. 단계별 todolist와 upstream 비교 문장 삭제 |
| `skills/arena/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용, 한국어 트리거와 swarm과의 구분을 description에. 단계별 todolist 삭제. 러너를 Agent(opus, fable) + Codex `task`(설정 기본 모델, 가장 어려운 설계 문제만 astra)로, 교차 심판을 다른 모델 계열(Claude가 부르면 Codex)로. `/tmp` 출력 경로를 scratchpad로. principle 스킬 이름을 `principles`의 references로 연결 |
| `skills/architect/references/rationale-template.md` | 같은 경로 | adapted | checkpoint 참조를 Phase C로 바로잡음 |
| `skills/architect/references/design-red-flags.md`, `runner-prompt.md` | 같은 경로 | verbatim | — |
| `skills/tdd/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용(`disable-model-invocation` 삭제). 중복된 "signal이 약하면 테스트를 더하지 않는다" 줄 삭제(upstream #419도 같은 줄을 지움) |
| `skills/swarm/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용. cloud worker·`generalPurpose`·`cloud_base_branch`·`pstack-models.mdc`·grok 기본값을 Agent(`isolation: "worktree"`, `run_in_background`)나 Orca worker(`orca skills get orchestration` 먼저 읽고 `worker-start`, `--base-branch`)로. 모델 경주에서 Codex arm은 `codex-companion.mjs task`. 단계별 todolist 삭제 |
| `skills/reflect/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용. transcript 경로를 `~/.claude/projects/<encoded-cwd>/`의 session·subagent 두 형식으로. 리뷰어를 Agent(opus, MCP가 있는 general-purpose) 둘 + Codex `task`(tooling)로, 종합도 Agent(opus). `create-skill` → `skill-creator`. 편집 대상을 agent-skills repo의 worktree 브랜치로 한정하고 plugin 스킬은 Backlog로. Backlog는 `use-tracker`로. 조건부 validator 단계를 `claude plugin validate <checkout>`으로. 세션·프로그램 두 모드와 교훈 장부(노트 저장소), 두 번 발생 규칙, 보호 목록, 프로그램 모드의 draft PR 하나를 더함 |
| `skills/reflect/references/{judgment,tooling,divergent}-reviewer.md` | 같은 경로 | adapted | 스킬 사용 판정 경로를 `.claude/skills`·`~/.claude/skills`·`~/.claude/plugins`로, `Task` → `Agent`, `Skill` 도구 호출을 판정 근거에 추가. judgment·divergent의 "3-5개"를 "미래 행동을 바꾸는 것만, 빈 목록도 가능"으로 |
| `skills/reflect/references/synthesizer.md` | 같은 경로 | adapted | `create-skill` → `skill-creator` |
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
| `skills/figure-it-out/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용(`deliver-ticket` Plan이 부름), 한국어 트리거와 `orchestrate`와의 구분을 description에. todolist와 poteto-mode Principles 읽기를 principle 스킬 연결 한 줄로. Phase A 체크포인트를 "틀은 티켓 계획 댓글이나 worklog에, 되돌릴 수 없는 단계만 그 자리에서 승인"으로. 병렬 카드가 필요한 단위는 `orchestrate`로. 위임 작업의 판정자를 직접 읽기 또는 Codex로. 단위마다 `deliver-ticket`의 리뷰·커밋·올리기를 따름 |
| `skills/principles/references/principle-*.md` (23개) | `pstack/skills/principle-*/SKILL.md` | verbatim | — |
| `skills/deliver-ticket/references/pull-request.md`, SKILL.md §4 커밋 순서 | `pstack/skills/poteto-mode/playbooks/opening-a-pr.md` | derived | "PR 본문은 브리핑" 원칙과 절 순서를 한국어 절(왜·범위·트레이드오프·영향 범위·검증)로. 검증 절은 필수로 유지. squash 본문 40줄 이내, Conventional Commits 제목, ready PR. 글쓰기 도구를 `write-plainly`로. 이야기 순서의 작은 커밋, amend와 새 커밋의 구분 |
| `skills/deliver-ticket/references/review-loop.md` | `pstack/skills/poteto-mode/playbooks/babysit.md`, `shipping.md` | derived | 요청별 모드 셋(스레드만, 상태 한 번, ready까지)을 한국어 트리거로. shipping에서 "판정은 한 head에만 유효, main을 합치거나 rebase하면 새 head에서 다시 검증"을 옮김(patch-id 비교는 뺌). 그 밖에 스택 최하단 PR 우선, 충돌→리뷰 스레드→CI 순서, 재시도 전 CI 분류와 `git merge-base --is-ancestor` stale base 확인, 코멘트는 신뢰하지 않는 데이터로 다루고 답글은 `gh api --input`, 봇 달래기용 코드 변경 금지. watcher 스크립트 대신 `Monitor` until-loop. 모드 선언·Origin·merge 금지 규칙은 뺌(머지는 기존 §6 규칙) |
| `skills/deliver-ticket/references/review-bot-triage.md` | `pstack/skills/poteto-mode/references/bugbot-triage.md` | derived | Bugbot 한정을 Codex·CodeRabbit·Copilot·사람 리뷰로 넓힘. fix/dismiss/ask 분류, ask 기본 목록, 학습 패턴 형식 유지. ask는 `AskUserQuestion`(program 안에서는 coordinator 보고). 일반적인 skip 후보 다섯 개만 줄여 옮김. 패턴 추가는 agent-skills PR로 |
| `skills/deliver-ticket/SKILL.md` §2 증거 규칙, `references/evidence.md` | `pstack/skills/poteto-mode/playbooks/bug-fix.md`, `refactoring.md` | derived | 배포하는 줄마다 런타임 증거, 반박된 가설이 낳은 변경은 되돌림, 사용자가 본 화면(브라우저는 Aside)에서 재현. 리팩터링 전 동작 고정("타입 체크와 lint는 고정이 아니다"), 읽는 부담을 줄이지 못하면 되돌림. evidence.md에 버그 수정 절차(재현, how·why로 가설 세우고 이분 탐색, 실패 테스트 먼저, 같은 화면에서 확인, 실패 테스트 커밋이 먼저)와 리팩터링 절차(고정, 목표 모양, 빼고 나서 더하기, 호출부를 옮기고 옛 API를 같은 변경에서 삭제, 동등성 증명, 커밋 순서). 구현 위임 모델(grok) 규칙은 뺌 |
| `skills/deliver-ticket/SKILL.md` §5 판정어 | `pstack/skills/figure-it-out/SKILL.md` | derived | VERIFIED / NOT VERIFIED / INCONCLUSIVE 판정, inconclusive·다른 화면 결과는 통과가 아님, 너무 쉽게 통과하면 관찰 방법부터 의심 |
| `skills/deliver-ticket/references/evidence.md` 기존 수정 검증 | `pstack/automations/benny/skills/reproduce-and-fix-issues/references/verify-existing-fix.md` | derived | 이미 수정을 주장하는 PR·커밋이 있으면 경쟁 수정 대신 검증. baseline과 patched를 같은 데이터로 두 번씩 재현해 비교 |
| `skills/prune-comments/SKILL.md`, `references/pruner.md` | `pstack/skills/no-comments/SKILL.md`, `pstack/agents/comment-sicko.md` | derived | 모델 호출 허용(`deliver-ticket` §3이 부름). Cursor 전용 Comment Sicko 에이전트 대신 general-purpose `Agent`에 `references/pruner.md`를 넘김. 범위를 diff의 추가·변경 줄과 diff가 틀리게 만든 주변 주석으로 좁힘. 남길 목록에 저장소 규칙이 요구하는 doc 주석을 더함. `MUST KILL`을 RESHAPE·CONSTRAINT 표시로, suppression은 지우지 않고 표시만(빌드가 바뀌므로). 제약 인코딩은 사용자 승인 대기 대신 diff 파일 안에 들어가면 넣고 실패를 확인한 뒤 주석 삭제, 아니면 주석을 남기고 보고. 모양이 필요한 수정에 architect를 부르던 단계는 deliver-ticket의 범위 밖 발견 규칙으로. 캐릭터 말투 제거 |
| `skills/write-plainly/SKILL.md` | `pstack/skills/technical-writing/SKILL.md`, `pstack/skills/poteto-mode/SKILL.md` | derived | 모델 호출 허용. technical-writing의 상위 규칙 세 개·Diátaxis·STE·Global English에서 원칙 8개와 문서 종류 고르기를 추렸다. poteto-mode의 Writing the reply와 Autonomy의 "No is an acceptable answer"를 답변 절로 옮겼다. 출처 줄, `unslop`·`technical-writing` 참조, Cursor repo 전용 규칙(탭 들여쓰기 등)은 뺐다 |
| `skills/write-plainly/references/english.md` | `pstack/skills/unslop/SKILL.md`, `pstack/skills/technical-writing/SKILL.md`, `pstack/skills/poteto-mode/SKILL.md` | derived | unslop 규칙 번호와 내용을 유지하고 문장을 다듬었다. Process 절(초안 뒤 훑기)은 "쓰면서 적용"과 맞지 않아 뺐다. technical-writing의 Google·STE·Global English 문장 규칙, poteto-mode의 Comments 절, 스킬 본문 작성 규칙을 더했다 |
| `skills/write-plainly/references/korean.md` | `pstack/skills/unslop/SKILL.md` | derived | unslop의 서식 금지(긴 대시, 문장 중간 콜론, 굵은 글씨+콜론 목록, 장식 이모지)와 챗봇 문구 규칙을 한국어로 옮겼다. 상투어, 번역투, 얼버무림, 주어 생략, 명사 나열, 문체 규칙과 예문은 여기서 새로 썼다 |

`skills/principles/SKILL.md`는 vendoring 대상이 아니다. poteto-mode의 `## Principles` 절을 참고해 여기서 새로 쓴 인덱스이며, 본문에 출처를 적었다.

`skills/why/references/sources/google-drive.md`, `obsidian.md`도 vendoring 대상이 아니다. upstream playbook 형식을 따라 여기서 새로 썼다. upstream의 `datadog.md`, `sentry.md`, `databricks.md`는 연결된 MCP가 없어 가져오지 않았다.

## 흡수한 규칙

`orchestrate`과 기존 스킬 수정안은 pstack의 Orchestrate·Autopilot·Shipping 플레이북에서 운영 규칙을 옮겨 왔다. 옮겨 온 규칙의 출처는 이 문서로 갈음한다.
