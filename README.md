# agent-skills

Claude Code 개인 스킬 저장소. 티켓 하나를 끝까지 끌고 가는 흐름과, Orca 워커 여러 개로 프로젝트 하나를 끝내는 흐름을 스킬로 만든다. 일부 스킬은 Lauren Tan의 [pstack](https://github.com/cursor/plugins)(MIT)에서 가져왔다.

전제는 Orca(멀티 에이전트 IDE, `orca` CLI), GitHub, 티켓 트래커(Linear, Jira 어댑터 준비)다. 노트는 Obsidian vault 또는 일반 Markdown 폴더를 쓴다.

선택 도구: 교차 검토는 [Codex 플러그인](https://github.com/openai/codex-plugin-cc)의 companion 스크립트, 브라우저 검증은 Aside를 쓴다. 없으면 그 단계만 다른 도구로 바꾼다.

작업 방식은 pstack이 기준이다. superpowers 플러그인과 함께 쓰면 두 흐름이 겹치므로 끄고 쓴다(`enabledPlugins`에서 `superpowers@claude-plugins-official: false`).

## 설치

Claude Code 플러그인 하나로 설치한다. 스킬, `orch`·`orch-dash` 명령, hook(권한 결정, 압축 직후 코디네이터 재정렬)이 함께 들어온다.

```bash
claude plugin marketplace add JeongJaeSoon/agent-skills
claude plugin install agent-skills@jeongjaesoon
```

Claude Code 안에서는 `/plugin marketplace add JeongJaeSoon/agent-skills`, `/plugin install agent-skills@jeongjaesoon`. 버전을 고정하지 않아 커밋마다 새 버전이다. 갱신은 `claude plugin marketplace update jeongjaesoon` 뒤 `claude plugin update agent-skills@jeongjaesoon`. 새 세션은 새 버전으로 뜨고, 떠 있는 세션은 `/reload-plugins`로 바로 반영한다.

- 스킬 이름은 `/agent-skills:<이름>`. 다른 플러그인과 겹치지 않으면 `/<이름>`도 된다.
- 권한: 플러그인은 권한 규칙을 설정으로 실을 수 없어 `hooks/guard.py`가 대신 결정한다. 이 플러그인의 스킬, `orca orchestration` 명령(reset·worker-abandon·gate-resolve 제외), `orch` 명령(`heavy`·`set`·`init`·`backfill` 제외)을 허용하고, 다른 터미널의 Orca 메일함 읽기를 거부한다. 나머지는 평소 권한 흐름을 탄다. 자세한 규칙은 [스킬 카탈로그](docs/skills.md#플러그인이-대신-내리는-권한-결정).
- 이름이 바뀐 스킬(`ship-pr` → `deliver-ticket`, `dispatch-work` → `dispatch-card`, `use-obsidian` → `use-notes`)은 `legacy/`의 안내용 별칭으로 남아 있다.

예전 `scripts/install.py`(symlink와 settings 병합)로 설치했다면, 그 설치로 시작한 프로그램이 모두 끝난 뒤 옮긴다.

```bash
python3 scripts/migrate.py            # 바뀔 내용 보기
python3 scripts/migrate.py --write    # symlink·옛 규칙·옛 hook 제거(settings 백업) 후 플러그인 설치
```

스킬을 고칠 때는 이 저장소를 worktree 브랜치에서 수정하고 `claude --plugin-dir <체크아웃>`으로 확인한다.

직접 고쳐 쓰는 경우에는 GitHub 대신 로컬 체크아웃을 마켓플레이스로 둔다(`migrate.py`가 이렇게 설치한다). 설치본은 커밋된 내용의 복사본이라, worktree에서 고친 것을 메인 체크아웃에 fast-forward한 뒤 `claude plugin update agent-skills@jeongjaesoon`을 실행하면 새 세션부터 반영된다. 이미 떠 있는 세션은 `/reload-plugins`를 입력하기 전까지 시작할 때의 버전을 쓰므로, 긴 프로그램 도중에 스킬을 바꿔도 돌고 있는 워커가 저절로 바뀌지는 않는다.

```bash
claude plugin marketplace add ~/workspace/project/agent-skills
claude plugin install agent-skills@jeongjaesoon
```

설정 파일(`~/.claude/agent-skills.json`)로 트래커와 노트 저장소를 고른다. 형식은 `use-tracker`, `use-notes` 스킬에 있다.

## 스킬

스킬별 호출 시점, 하는 일, 동봉 스크립트는 [스킬 카탈로그](docs/skills.md)에 있다.

**티켓 하나**

| 스킬 | 언제 |
|---|---|
| `write-ticket` | 티켓을 쓸 때. 후속 티켓 형식, 템플릿 |
| `deliver-ticket` | 첫 수정부터 완료까지: 계획, 테스트, Codex 교차 검토, GitHub stack, E2E, 머지, 완료 기준 |
| `handoff-ticket` | 끝난 뒤 다음 티켓을 새 Orca 카드로 |
| `dispatch-card` | 이 세션은 계속하면서 다른 저장소나 곁가지를 카드로 |
| `end-session` | 세션·카드·worktree 정리 |

**프로젝트 하나 (Orca 여러 워커)**

| 스킬 | 언제 |
|---|---|
| `orchestrate` | 코디네이터가 여러 티켓을 병렬로 착지시킬 때. 착지 순서, 독점 레인, main 가디언, QA 리드, 대시보드 |
| `measure-delivery` | 프로젝트가 실제로 어땠는지: 후속 티켓 증가, 재작업, 토큰 |

**어댑터**: `use-tracker`(Linear, Jira), `use-notes`(Obsidian, Markdown).

**pstack**: `architect`, `blast-radius`, `create-verification-skill`, `how`, `interrogate`, `maintain-verification-skill`, `principles`, `reflect`, `show-me-your-work`, `swarm`, `tdd`. 원본 이름을 유지해 upstream을 따라간다.

```bash
python3 scripts/pstack-sync.py            # upstream 변경 확인 (쓰지 않음)
python3 scripts/pstack-sync.py --write    # 충돌이 없을 때만 반영하고 pin 갱신
```

출처와 수정 내용은 `vendor/pstack/NOTICE.md`, 고정 커밋은 `vendor/pstack/manifest.json`.

## orchestrate 한눈에

- 워커가 자기 PR을 `orch land`로 착지시킨다. 준비된 PR은 base보다 뒤처져 있어도 병렬로 머지되고, migration·CI·Dockerfile·compose 같은 공유 파일은 독점 레인에서 base당 하나씩 최신 base 위에서 머지된다.
- 의존 관계는 Orca task deps가 정본이다. 순서대로 들어가야 하는 체인은 GitHub stack으로 한 번에 머지한다.
- main이 red가 되면 main 가디언이 flake 여부부터 보고 hotfix나 revert를 고른다. QA 리드가 티켓 검증, 주기 E2E, 설계·코드 정합성 감사를 맡는다.
- `orch-dash serve`: 목표, 완료 조건, 착지 순서, 의존 그래프, 워커, 토큰을 보여주는 대시보드(라이트·다크, 창 크기에 맞춤). 모델 호출 없이 JSON으로 갱신한다.

## 테스트

```bash
for t in skills/orchestrate/scripts/test_*.py skills/use-tracker/scripts/test_tracker.py hooks/test_*.py; do python3 "$t"; done
bash scripts/pstack-sync-test.sh
```

## 라이선스

MIT. `vendor/pstack`과 거기서 가져온 스킬은 원저작자의 MIT 라이선스를 따른다(`vendor/pstack/LICENSE`).
