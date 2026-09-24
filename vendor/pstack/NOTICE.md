# pstack vendoring 고지

이 저장소의 일부 파일은 [cursor/plugins](https://github.com/cursor/plugins)의 `pstack`(Lauren Tan, MIT)에서 가져왔다. 라이선스 전문은 같은 폴더의 `LICENSE`에 있다. 고정 커밋(`pin`), pstack 버전(`upstream_version`), 파일 목록은 `manifest.json`이 정본이다.

- 스킬 파일 자체에는 출처 줄을 두지 않는다. 출처와 라이선스 고지는 이 문서, `LICENSE`, `manifest.json`, README가 맡는다. 이 폴더는 플러그인에 함께 실린다.
- `verbatim`은 upstream과 바이트 단위로 같다. `adapted`는 아래 표의 수정만 했다. `derived`는 upstream 규칙을 옮겨 이 저장소 스킬에 새로 쓴 부분이라 `manifest.json`에 없고 동기화하지 않는다. upstream이 바뀌면 사람이 읽고 반영한다.
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
| `skills/blast-radius/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용. 설치하지 않은 `why`·`arena`·`unslop`과 당시 없던 `how` 언급을 `gh`·Codex·평이한 문장으로 |
| `skills/architect/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용. Phase B의 runner 기본값(opus·sol·grok)과 설치하지 않은 `arena` 위임을 Agent(opus, fable) + Codex `task`(설정 기본 모델, 가장 어려운 설계 문제만 astra) 병렬 실행과 직접 종합으로. 설치하지 않은 `why`는 `git log -S`/`git blame`과 인용 PR·티켓 확인으로. principle 스킬 이름을 `principles`의 references로 연결하는 한 줄 추가. 단계별 todolist와 upstream 비교 문장 삭제 |
| `skills/architect/references/rationale-template.md` | 같은 경로 | adapted | "Synthesis decision" 작성 주체를 `arena` 링크에서 architect lead(Phase B)로. checkpoint 참조를 Phase C로 바로잡음 |
| `skills/architect/references/design-red-flags.md`, `runner-prompt.md` | 같은 경로 | verbatim | — |
| `skills/tdd/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용(`disable-model-invocation` 삭제). 중복된 "signal이 약하면 테스트를 더하지 않는다" 줄 삭제(upstream #419도 같은 줄을 지움) |
| `skills/swarm/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용. cloud worker·`generalPurpose`·`cloud_base_branch`·`pstack-models.mdc`·grok 기본값을 Agent(`isolation: "worktree"`, `run_in_background`)나 Orca worker(`orca skills get orchestration` 먼저 읽고 `worker-start`, `--base-branch`)로. 모델 경주에서 Codex arm은 `codex-companion.mjs task`. 단계별 todolist 삭제 |
| `skills/reflect/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용. transcript 경로를 `~/.claude/projects/<encoded-cwd>/`의 session·subagent 두 형식으로. 리뷰어를 Agent(opus, MCP가 있는 general-purpose) 둘 + Codex `task`(tooling)로, 종합도 Agent(opus). `create-skill` → `skill-creator`. 편집 대상을 agent-skills repo의 worktree 브랜치로 한정하고 plugin 스킬은 Backlog로. Backlog는 `use-tracker`로. 조건부 validator 단계를 `claude plugin validate <checkout>`으로 |
| `skills/reflect/references/{judgment,tooling,divergent}-reviewer.md` | 같은 경로 | adapted | 스킬 사용 판정 경로를 `.claude/skills`·`~/.claude/skills`·`~/.claude/plugins`로, `Task` → `Agent`, `Skill` 도구 호출을 판정 근거에 추가. judgment·divergent의 "3-5개"를 "미래 행동을 바꾸는 것만, 빈 목록도 가능"으로 |
| `skills/reflect/references/synthesizer.md` | 같은 경로 | adapted | `create-skill` → `skill-creator` |
| `skills/how/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용. explorer는 내장 `Explore`, 단순 질문은 lead가 직접 탐색·설명, 종합은 general-purpose(opus) 한 번. `Task`·`generalPurpose`·`readonly`·grok 기본값 제거. description의 설치하지 않은 `why` 안내를 git history 확인으로 |
| `skills/how/references/explorer-prompt.md`, `explainer-prompt.md` | 같은 경로 | verbatim | — |
| `skills/principles/references/principle-*.md` (23개) | `pstack/skills/principle-*/SKILL.md` | verbatim | — |
| `skills/deliver-ticket/SKILL.md` §5 PR 본문 | `pstack/skills/poteto-mode/playbooks/opening-a-pr.md` | derived | "PR 본문은 브리핑" 원칙과 절 순서를 한국어 절(왜·범위·트레이드오프·영향 범위·검증)로. 검증 절은 필수로 유지. squash 본문 40줄 이내, Conventional Commits 제목, ready PR. 글쓰기 도구를 `write-plainly`로 |
| `skills/deliver-ticket/SKILL.md` §6 리뷰 루프 | `pstack/skills/poteto-mode/playbooks/babysit.md` | derived | 스택 최하단 PR 우선, 충돌→리뷰 스레드→CI 순서, 재시도 전 CI 분류와 `git merge-base --is-ancestor` stale base 확인, 코멘트는 신뢰하지 않는 데이터로 다루고 답글은 `gh api --input`, 봇 달래기용 코드 변경 금지. watcher 스크립트 대신 `Monitor` until-loop. 모드 선언·Origin·merge 금지 규칙은 뺌(머지는 기존 §6 규칙) |
| `skills/deliver-ticket/references/review-bot-triage.md` | `pstack/skills/poteto-mode/references/bugbot-triage.md` | derived | Bugbot 한정을 Codex·CodeRabbit·Copilot·사람 리뷰로 넓힘. fix/dismiss/ask 분류, ask 기본 목록, 학습 패턴 형식 유지. ask는 `AskUserQuestion`(program 안에서는 coordinator 보고). 일반적인 skip 후보 다섯 개만 줄여 옮김. 패턴 추가는 agent-skills PR로 |
| `skills/deliver-ticket/SKILL.md` §2 증거 규칙 | `pstack/skills/poteto-mode/playbooks/bug-fix.md`, `refactoring.md` | derived | 배포하는 줄마다 런타임 증거, 반박된 가설이 낳은 변경은 되돌림, 사용자가 본 화면(브라우저는 Aside)에서 재현. 리팩터링 전 동작 고정("타입 체크와 lint는 고정이 아니다"), 읽는 부담을 줄이지 못하면 되돌림 |
| `skills/deliver-ticket/SKILL.md` §5 판정어 | `pstack/skills/figure-it-out/SKILL.md` | derived | VERIFIED / NOT VERIFIED / INCONCLUSIVE 판정, inconclusive·다른 화면 결과는 통과가 아님, 너무 쉽게 통과하면 관찰 방법부터 의심 |
| `skills/deliver-ticket/SKILL.md` §2 기존 수정 검증 | `pstack/automations/benny/skills/reproduce-and-fix-issues/references/verify-existing-fix.md` | derived | 이미 수정을 주장하는 PR·커밋이 있으면 경쟁 수정 대신 검증. baseline과 patched를 같은 데이터로 두 번씩 재현해 비교 |

`skills/principles/SKILL.md`는 vendoring 대상이 아니다. poteto-mode의 `## Principles` 절을 참고해 여기서 새로 쓴 인덱스이며, 본문에 출처를 적었다.

## 흡수한 규칙

`orchestrate`과 기존 스킬 수정안은 pstack의 Orchestrate·Autopilot·Shipping 플레이북에서 운영 규칙을 옮겨 왔다. 옮겨 온 규칙의 출처는 이 문서로 갈음한다.
