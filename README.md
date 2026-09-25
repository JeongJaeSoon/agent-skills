# agent-skills

Claude Code 개인 스킬 저장소. 티켓 하나를 끝까지 끌고 가는 흐름과, Orca 워커 여러 개로 프로젝트 하나를 끝내는 흐름을 스킬로 만든다. 일부 스킬은 Lauren Tan의 [pstack](https://github.com/cursor/plugins)(MIT)에서 가져왔다.

한눈에 보기: [스킬 카탈로그](https://claude.ai/artifact/16394ZGS3RX9sTU6h2ME8d)(스킬별 언제·무엇, `docs/skills.md`에서 생성), [사용 안내서](https://claude.ai/artifact/1ifoY8ykKHBqcZMrKN9ACR)(흐름과 `orch` 착지 과정 그림).

두 페이지는 한국어·English·日本語를 오른쪽 위에서 고른다. 링크 끝에 `#en`이나 `#ja`를 붙이면 그 언어로 열린다. 카탈로그는 `python3 scripts/catalog/build.py <out.html>`이 `docs/skills.md`와 번역본 `docs/skills.{en,ja}.md`로 만든다. 번역본 첫 줄의 `translated-from` 커밋 뒤에 `docs/skills.md`가 바뀌었으면 경고하고, 그 언어 하단에 원문보다 오래됐다고 표시한다. 안내서는 손으로 쓴 한 장짜리 `scripts/catalog/guide.html`이라, 스킬이 늘거나 흐름이 바뀌면 세 언어를 함께 고친다.

전제는 Orca(멀티 에이전트 IDE, `orca` CLI), GitHub, 티켓 트래커(Linear, Jira 어댑터 준비)다. 노트는 Obsidian vault 또는 일반 Markdown 폴더를 쓴다.

선택 도구: 교차 검토는 [Codex 플러그인](https://github.com/openai/codex-plugin-cc)의 companion 스크립트, 브라우저 검증은 Aside를 쓴다. 없으면 그 단계만 다른 도구로 바꾼다.

작업 방식은 pstack이 기준이다. superpowers 플러그인과 함께 쓰면 두 흐름이 겹치므로 끄고 쓴다(`enabledPlugins`에서 `superpowers@claude-plugins-official: false`).

## 설치

이 저장소를 고쳐 가며 쓰는 PC는 체크아웃을 Claude Code가 직접 읽게 한다. 스킬을 고치면 떠 있는 세션에도 reload 한 번으로 반영되고, 원격 변경은 15분마다 자동으로 받는다. 설계와 실측 근거는 [docs/platform.md](docs/platform.md).

```bash
git clone https://github.com/JeongJaeSoon/agent-skills ~/conductor/repos/agent-skills
python3 ~/conductor/repos/agent-skills/scripts/bootstrap.py           # 바뀔 내용 보기
python3 ~/conductor/repos/agent-skills/scripts/bootstrap.py --write   # 적용
```

`bootstrap.py --write`가 하는 일:

- `~/.claude/settings.json`을 백업하고, `env` 블록의 `CLAUDE_CODE_PLUGIN_DIRS`에 체크아웃 경로를 넣는다. 마켓플레이스로 설치한 `agent-skills@jeongjaesoon`이 켜져 있으면 끈다. 둘 다 켜면 hook이 두 번 돈다.
- macOS면 launchd 작업(`io.github.jeongjaesoon.agent-skills-sync`)을 설치해 15분마다와 로그인 때 `skills-sync sync`를 돌린다. Linux면 crontab 한 줄을 출력한다.
- 이미 된 단계는 건너뛰므로 다시 돌려도 된다. 새 설정은 새 세션부터 적용되고, 떠 있는 세션은 재시작해야 바뀐다. 체크아웃 폴더에서 `claude`를 처음 띄우면 폴더 신뢰 창이 한 번 뜬다.

고치지 않고 쓰기만 하는 PC는 마켓플레이스로 설치해도 된다. 설치본은 커밋 단위 복사본이라 갱신은 `claude plugin marketplace update jeongjaesoon` 뒤 `claude plugin update agent-skills@jeongjaesoon`이다.

```bash
claude plugin marketplace add JeongJaeSoon/agent-skills
claude plugin install agent-skills@jeongjaesoon
```

- 스킬 이름은 `/agent-skills:<이름>`. 다른 플러그인과 겹치지 않으면 `/<이름>`도 된다.
- 권한: 플러그인은 권한 규칙을 설정으로 실을 수 없어 `hooks/guard.py`가 대신 결정한다. 이 플러그인의 스킬, `orca orchestration` 명령(reset·worker-abandon·gate-resolve 제외), `orch` 명령(`heavy`·`set`·`init`·`backfill` 제외)을 허용하고, 다른 터미널의 Orca 메일함 읽기를 거부한다. 나머지는 평소 권한 흐름을 탄다. 자세한 규칙은 [스킬 카탈로그](docs/skills.md#권한-결정).
- 이름이 바뀐 스킬(`ship-pr` → `deliver-ticket`, `dispatch-work` → `dispatch-card`, `use-obsidian` → `use-notes`)은 `legacy/`의 안내용 별칭으로 남아 있다.

예전 `scripts/install.py`(symlink와 settings 병합)로 설치했다면, 그 설치로 시작한 프로그램이 모두 끝난 뒤 `python3 scripts/migrate.py`(바뀔 내용 보기), `--write`(symlink·옛 규칙·옛 hook 제거 뒤 `bootstrap.py --write`) 순서로 옮긴다.

## 스킬 고치기

- 고치는 곳은 worktree 브랜치다. 메인 체크아웃은 세션들이 직접 읽으므로 fast-forward로만 바꾼다. 메인 체크아웃에 커밋 안 한 변경이 있으면 자동 동기화가 멈춘다.
- worktree의 스킬로 세션 하나를 띄워 보려면 `claude --settings '{"env":{"CLAUDE_CODE_PLUGIN_DIRS":"<worktree 절대 경로>"}}'`. `--plugin-dir`을 더하면 메인 체크아웃과 두 벌이 함께 올라온다.
- 메인 체크아웃에 들어간 뒤에는 `skills-sync sync`(push까지)와 `skills-sync broadcast`를 돌린다. 브로드캐스트는 `SKILL.md`가 바뀌었으면 `/reload-skills`, hook 연결(`hooks/hooks.json`)이나 manifest가 바뀌었으면 `/reload-plugins`를, 빈 입력칸에서 쉬고 있는 세션에만 보낸다. 권한 창이나 질문 창이 뜬 세션에 Enter가 가면 그 창에 답해 버리기 때문이다. 못 보낸 세션은 남겨 두었다가 다음 동기화 때 다시 시도한다.
- description이나 트리거 문구를 바꿨으면 고치기 전 체크아웃과 나란히 `scripts/trigger-probe.sh`를 돌려 발동이 달라졌는지 본다. 임시 저장소에서 헤드리스 세션을 띄우고 쓰기·셸·MCP 호출은 모두 거부하므로 다른 곳을 건드리지 않는다. 한 번에 0.2달러 안팎이 든다.

```bash
bash scripts/trigger-probe.sh <체크아웃> "남은 작업들 병렬로 진행해줘" 3 [복사할 저장소]
```

설정 파일(`~/.claude/agent-skills.json`)로 트래커와 노트 저장소를 고른다. 형식은 `use-tracker`, `use-notes` 스킬에 있다.

`recall`이 지난 세션을 빨리 찾게 하려면 Orca 앱에서 Settings → Agent Session Search → Search inside sessions의 이 컴퓨터(Local Mac) 스위치를 켠다. CLI로는 켤 수 없고, 켜기 전에는 `recall`이 `~/.claude/projects`의 transcript를 grep한다. 켜졌는지는 `orca search --index-status`의 `enabled`로 확인한다.

## 스킬

스킬별 호출 시점, 실제 내용, 동봉 파일, `orch` 명령, hook, pstack과의 차이는 [스킬 카탈로그](docs/skills.md)에 있다.

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

**글쓰기**: `write-plainly`(한국어·영어 문체), `prune-comments`(diff의 주석 정리).

**pstack**: `architect`, `arena`, `blast-radius`, `create-verification-skill`, `figure-it-out`, `how`, `interrogate`, `maintain-verification-skill`, `principles`, `recall`, `reflect`, `show-me-your-work`, `swarm`, `tdd`, `teach`, `why`. 원본 이름을 유지해 upstream을 따라간다.

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
for t in skills/orchestrate/scripts/test_*.py skills/use-tracker/scripts/test_tracker.py hooks/test_*.py scripts/test_sync.py; do python3 "$t"; done
bash scripts/pstack-sync-test.sh
```

## 라이선스

MIT. `vendor/pstack`과 거기서 가져온 스킬은 원저작자의 MIT 라이선스를 따른다(`vendor/pstack/LICENSE`).
