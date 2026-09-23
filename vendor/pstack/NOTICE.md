# pstack vendoring 고지

이 저장소의 일부 파일은 [cursor/plugins](https://github.com/cursor/plugins)의 `pstack`(Lauren Tan, MIT)에서 가져왔다. 라이선스 전문은 같은 폴더의 `LICENSE`에 있다. 고정 커밋(`pin`), pstack 버전(`upstream_version`), 파일 목록은 `manifest.json`이 정본이다. 각 파일 머리의 `pstack-vendor:` 줄은 그 파일을 마지막으로 가져온 커밋을 적는다.

- 파일마다 frontmatter 바로 뒤(스크립트는 shebang 뒤)에 `pstack-vendor:` 출처 줄이 있다. TSV처럼 주석이 없는 형식은 이 문서로 갈음한다.
- `verbatim`은 upstream과 바이트 단위로 같다(출처 줄 제외). `adapted`는 아래 표의 수정만 했다.
- 동기화: `python3 scripts/pstack-sync.py`(기본 dry-run, `--write`는 충돌 0일 때만 쓰고 pin을 올린다). 스크립트 자체 검사는 `bash scripts/pstack-sync-test.sh`.
- 3-way 병합은 텍스트 충돌만 잡는다. 동기화 PR에서는 새로 들어온 문장이 사용자 규칙(`~/.claude/CLAUDE.md`)과 부딪히지 않는지, 설치되지 않은 스킬을 부르지 않는지 사람이 읽고 확인한다.

## 파일별 수정

| 로컬 경로 | upstream | 방식 | 수정 |
|---|---|---|---|
| `skills/create-verification-skill/SKILL.md` | `pstack/skills/create-verification-skill/SKILL.md` | adapted | 생성 위치 `.cursor/skills/` → `.claude/skills/`. Claude Code는 Bash 호출마다 새 셸이라 Launch가 PID·포트·실행 폴더를 상태 파일에 남기도록 한 줄 추가 |
| `skills/create-verification-skill/references/feature-map-example/*.md` | 같은 경로 | verbatim | — |
| `skills/maintain-verification-skill/SKILL.md` | 같은 경로 | adapted | 대상 위치 `.cursor/skills/` → `.claude/skills/` |
| `skills/interrogate/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용. 리뷰어를 Claude(Agent) + Codex(codex-companion, 설정 기본 모델, 어려운 설계 문제만 astra)로. 의도가 모호하면 묻지 않고 가정으로 표시 |
| `skills/interrogate/references/*.md` | 같은 경로 | verbatim | — |
| `skills/show-me-your-work/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용. transcript 경로를 Claude Code 형식으로. 교차 모델 리뷰를 Codex로. 설치하지 않은 `unslop` 언급 제거 |
| `skills/show-me-your-work/references/decision-log-template.tsv`, `scripts/log.sh` | 같은 경로 | verbatim | — |
| `skills/blast-radius/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용. 설치하지 않은 `how`·`why`·`arena`·`unslop` 언급을 `gh`·Codex·평이한 문장으로 |
| `skills/architect/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용. Phase B의 runner 기본값(opus·sol·grok)과 설치하지 않은 `arena` 위임을 Agent(opus, fable) + Codex `task`(설정 기본 모델, 가장 어려운 설계 문제만 astra) 병렬 실행과 직접 종합으로. 설치하지 않은 `why`는 `git log -S`/`git blame`과 인용 PR·티켓 확인으로. principle 스킬 이름을 `principles`의 references로 연결하는 한 줄 추가 |
| `skills/architect/references/rationale-template.md` | 같은 경로 | adapted | "Synthesis decision" 작성 주체를 `arena` 링크에서 architect lead(Phase B)로 |
| `skills/architect/references/design-red-flags.md`, `runner-prompt.md` | 같은 경로 | verbatim | — |
| `skills/tdd/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용(`disable-model-invocation` 삭제)만. 본문은 upstream 그대로 |
| `skills/swarm/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용. cloud worker·`generalPurpose`·`cloud_base_branch`·`pstack-models.mdc`·grok 기본값을 Agent(`isolation: "worktree"`, `run_in_background`)나 Orca worker(`orca skills get orchestration` 먼저 읽고 `worker-start`, `--base-branch`)로. 모델 경주에서 Codex arm은 `codex-companion.mjs task` |
| `skills/reflect/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용. transcript 경로를 `~/.claude/projects/<encoded-cwd>/`의 session·subagent 두 형식으로. 리뷰어를 Agent(opus, MCP가 있는 general-purpose) 둘 + Codex `task`(tooling)로, 종합도 Agent(opus). `create-skill` → `skill-creator`. 편집 대상을 agent-skills repo의 worktree 브랜치로 한정하고 plugin 스킬은 Backlog로. Backlog는 `use-tracker`로 |
| `skills/reflect/references/{judgment,tooling,divergent}-reviewer.md` | 같은 경로 | adapted | 스킬 사용 판정 경로를 `.claude/skills`·`~/.claude/skills`·`~/.claude/plugins`로, `Task` → `Agent`, `Skill` 도구 호출을 판정 근거에 추가 |
| `skills/reflect/references/synthesizer.md` | 같은 경로 | adapted | `create-skill` → `skill-creator` |
| `skills/how/SKILL.md` | 같은 경로 | adapted | 모델 호출 허용. explorer는 내장 `Explore`, 단순 질문은 `Explore`(opus) 한 번, 종합은 general-purpose(opus) 한 번. `Task`·`generalPurpose`·`readonly`·grok 기본값 제거. description의 설치하지 않은 `why` 안내를 git history 확인으로 |
| `skills/how/references/explorer-prompt.md`, `explainer-prompt.md` | 같은 경로 | verbatim | — |
| `skills/principles/references/principle-*.md` (23개) | `pstack/skills/principle-*/SKILL.md` | verbatim | — |

`skills/principles/SKILL.md`는 vendoring 대상이 아니다. poteto-mode의 `## Principles` 절을 참고해 여기서 새로 쓴 인덱스이며, 본문에 출처를 적었다.

## 흡수한 규칙

`orchestrate`과 기존 스킬 수정안은 pstack의 Orchestrate·Autopilot·Shipping 플레이북에서 운영 규칙을 옮겨 왔다. 문장을 그대로 인용한 곳에는 해당 파일에 "출처: cursor/plugins pstack (MIT, Lauren Tan)"을 적었다.
