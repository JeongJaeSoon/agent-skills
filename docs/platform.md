# 로컬 운영 기반과 최상위 orchestrator 설계

이 저장소를 어느 PC, 어느 저장소에서나 같은 개발 경험으로 쓰기 위한 설계다. 다루는 것은 여섯 가지다.

1. 로컬 등록
2. 실시간 반영
3. 자동 pull/push
4. reload 브로드캐스트
5. 다른 PC 부트스트랩
6. 오케스트레이션 스킬 개선(최상위 orchestrator 계층)

모든 판단은 2026-09-25 이 PC(macOS, Claude Code 2.1.282, Orca CLI)에서 잰 결과를 근거로 한다. 잰 방법과 출력은 각 절의 "실측"에 적는다.

## 0. 바꾸기 전 상태 (2026-09-25 기록)

| 항목 | 값 |
|---|---|
| `~/.claude/plugins/known_marketplaces.json` | `jeongjaesoon` 없음. 있는 것은 공식 마켓플레이스, `agent-guard`, 그 밖의 서드파티·사내 마켓플레이스(이 작업은 건드리지 않음) |
| `~/.claude/plugins/installed_plugins.json` | `agent-skills@*` 없음 |
| `~/.claude/settings.json` `enabledPlugins` | `agent-skills@*` 없음 |
| `~/.claude/settings.json` `env.CLAUDE_CODE_PLUGIN_DIRS` | 없음 |
| `~/.claude/skills/` | `slack-post`, `wrap-up`, `synced/`, `.trash/`만 있음. 옛 `install.py` symlink 없음 |
| 로컬 체크아웃 | `~/conductor/repos/agent-skills` (main @ fe3b173, origin = GitHub HTTPS) |

즉 이 PC에는 원격이든 로컬이든 agent-skills가 등록되어 있지 않았다. 되돌리기는 §7에 적는다.

같은 날 사용자 요청으로 코디네이터가 먼저 로컬 마켓플레이스를 등록했다: `claude plugin marketplace add ~/conductor/repos/agent-skills`, `claude plugin install agent-skills@jeongjaesoon`(scope user). `installPath`는 `~/.claude/plugins/cache/jeongjaesoon/agent-skills/11a51e63a89c`로, §1의 실측과 같이 커밋 단위 복사본이다. 되돌리기: `claude plugin uninstall agent-skills@jeongjaesoon && claude plugin marketplace remove jeongjaesoon`.

## 1. 로컬 등록: 마켓플레이스가 아니라 `CLAUDE_CODE_PLUGIN_DIRS`

### 실측

로컬 디렉터리 마켓플레이스는 실시간 반영이 되지 않는다. 설치할 때 커밋 단위로 cache에 복사하기 때문이다. 격리된 설정 디렉터리(`CLAUDE_CONFIG_DIR=/tmp/cc-sbx`)와 저장소 사본(`/tmp/mkt-src`)으로 확인했다.

```text
$ claude plugin marketplace add /tmp/mkt-src
✔ Successfully added marketplace: jeongjaesoon
$ claude plugin install agent-skills@jeongjaesoon
# installed_plugins.json
"installPath": "/tmp/cc-sbx/plugins/cache/jeongjaesoon/agent-skills/6f28dbae2fec",
"version": "6f28dbae2fec", "gitCommitSha": "6f28dbae2fec…"
# cache 안의 SKILL.md 는 symlink 가 아니라 일반 파일(Regular File)

# 소스를 고치고(커밋 안 함) update
$ claude plugin update agent-skills@jeongjaesoon
✔ agent-skills is already at the latest version (6f28dbae2fec).
# 커밋한 뒤 update
✔ Plugin "agent-skills" updated from 6f28dbae2fec to 4317b53743f7 … Restart to apply changes.
```

반면 Claude Code 2.1.282에는 `--plugin-dir`과 같은 일을 하는 환경 변수 `CLAUDE_CODE_PLUGIN_DIRS`가 있다. 바이너리의 안내 문구는 이렇다: "Unset CLAUDE_CODE_PLUGIN_DIRS (in the environment, or in the settings `env` block that sets it)". 즉 settings의 `env` 블록에 넣을 수 있다. 절대 경로만 받는다.

```text
$ CLAUDE_CONFIG_DIR=/tmp/cc-sbx claude plugin list      # settings.json 에 env 만 넣은 상태
  ❯ agent-skills@inline
    Path: /tmp/mkt-src
    Status: ✔ loaded
```

이렇게 올린 플러그인은 디스크에서 바로 읽힌다. `bin/`도 PATH에 들어간다(`command -v orch` → `/tmp/mkt-src/bin/orch`).

마켓플레이스 설치와 `CLAUDE_CODE_PLUGIN_DIRS`를 함께 켜면 둘 다 `✔`로 올라온다(`agent-skills@jeongjaesoon` enabled + `agent-skills@inline` loaded). hook이 두 번 돌게 되므로 둘 중 하나만 쓴다.

### 다른 방법을 고르지 않은 이유

| 방법 | 고르지 않은 이유 |
|---|---|
| cache 경로를 체크아웃으로 symlink | `installPath`가 커밋 sha 이름의 디렉터리라, 다음 `plugin update`나 자동 갱신이 새 디렉터리를 만들고 symlink는 버려진다. Claude Code 내부 형식에 기대는 수정이라 버전이 오르면 조용히 깨진다 |
| pull 뒤 `claude plugin update` 자동화 | 커밋된 것만 반영되고(미커밋 편집은 "already at the latest version"), update 출력이 "Restart to apply changes"라 떠 있는 세션에는 여전히 reload가 필요하다. 복사가 한 단계 더 늘 뿐 실시간이 아니다 |
| 세션마다 `--plugin-dir` | Orca가 워커를 띄울 때 인자를 넣을 수 없다. settings `env`의 `CLAUDE_CODE_PLUGIN_DIRS`가 같은 일을 모든 세션에 한다 |

### 결정

- `~/.claude/settings.json`의 `env`에 `CLAUDE_CODE_PLUGIN_DIRS=<체크아웃 절대 경로>`를 넣는다. 이 PC의 체크아웃은 `~/conductor/repos/agent-skills`다.
- 마켓플레이스 설치는 끈다. 이 PC에는 코디네이터가 설치해 두었으므로, 부트스트랩이 `enabledPlugins`의 `agent-skills@jeongjaesoon`을 false로 둔다(두 번 로드 방지). 마켓플레이스 등록 자체는 남겨 두어도 해가 없다. `marketplace.json`은 남긴다. 이 저장소를 고치지 않고 쓰기만 하는 사람의 설치 경로이기 때문이다.
- 이것은 settings 변경이다. 권한 설정(`permissions`)은 건드리지 않는다. 바꾸기 전 파일은 `settings.json.bak-<시각>`으로 백업한다.
- 플러그인 이름은 `agent-skills@inline`이 된다. 스킬 이름(`agent-skills:<이름>`)과 `${CLAUDE_PLUGIN_ROOT}`는 같다. 저장소 안에 `@jeongjaesoon`을 전제한 코드는 없다(`grep`으로 확인, README와 `migrate.py`의 설치 안내만 있음).

## 2. 실시간 반영

### 실측

Orca 터미널에 `CLAUDE_CODE_PLUGIN_DIRS=/tmp/mkt-src claude --model haiku`를 띄우고, 사본을 고친 뒤 reload 명령을 보내 확인했다. 두 명령 모두 2.1.282에 실제로 있다.

```text
reload-plugins: "Activate pending plugin changes in the current session"  argumentHint "[--force]"
reload-skills:  "Pick up skills added or changed on disk during this session"
```

| 바꾼 것 | 보낸 명령 | 결과 |
|---|---|---|
| 새 스킬 `probe-live` 추가 | 없음 | 모델이 "없다"고 답함. 자동 감지 안 됨 |
| 〃 | `/reload-skills` | `Reloaded skills: 92 skills available (1 added)` |
| 기존 스킬 본문 수정(`BODY-MARKER-444`) | 없음 | Skill 호출 결과가 옛 본문(`probe`) |
| 〃 | `/reload-skills` | `(no changes)`라고 출력하지만 Skill 호출 결과는 `BODY-MARKER-444`. 본문은 갱신됨 |
| 스킬 본문 + `hooks/hooks.json`에 UserPromptSubmit hook 추가 | `/reload-plugins` | `Reloaded: 15 plugins · 76 skills · 13 agents · 23 hooks · 6 plugin MCP servers · 1 plugin LSP server`. 다음 프롬프트에서 `BODY-MARKER-555`와 `HOOK-MARKER-666` 둘 다 보임 |

`/reload-skills`의 "(no changes)"는 추가·삭제만 센 숫자다. 본문은 바뀐다.

### 결정

| 바뀐 경로 | 반영 방법 |
|---|---|
| `skills/**`, `legacy/**` | `/reload-skills` |
| `hooks/**`, `.claude-plugin/**`, `agents/**` | `/reload-plugins` |
| `bin/**`, `skills/*/scripts/**`, `docs/**`, 테스트 | 아무것도 안 보낸다. 스크립트는 실행할 때마다 디스크에서 읽힌다 |

`/reload-plugins`는 다른 플러그인의 MCP 서버까지 다시 붙인다(출력의 "6 plugin MCP servers"). 그래서 스킬만 바뀐 경우에는 가벼운 `/reload-skills`를 쓴다.

편집은 지금처럼 worktree 브랜치에서 한다. 로드되는 것은 메인 체크아웃이므로, worktree의 변경은 메인 체크아웃에 fast-forward되는 순간 반영 대상이 된다. 메인 체크아웃을 직접 고치면 그 즉시 대상이 되지만, 그동안 자동 pull은 멈춘다(§3).

## 3. 자동 pull/push

### 트리거 비교

| 방식 | 장점 | 단점 | 판단 |
|---|---|---|---|
| launchd (`StartInterval`) | Claude 세션이 없어도 돈다. 다른 설정을 안 건드린다. 이 PC에서 이미 사용자 LaunchAgent 여러 개가 같은 방식으로 돈다 | macOS 전용 | **주 방식** |
| 플러그인 SessionStart hook | 세션을 열 때마다 최신이 된다. Linux에서도 된다 | 세션 시작을 늦추지 않으려면 백그라운드로 떼어야 한다. hook 추가는 승인 사항 | 선택 사항(아래) |
| 스킬(지시가 있을 때) | 단순 | 사람이 잊으면 안 돈다 | 수동 경로로만 둔다 |

- **주기:** 15분(`StartInterval 900`) + 로그인 시 1회(`RunAtLoad`). `git fetch` 한 번이 전부라 비용이 거의 없다. 1시간이면 다른 PC에서 고친 것을 쓰기까지 너무 오래 걸린다.
- **수동:** `skills-sync sync`. orchestrator 스킬은 "스킬 업데이트해줘"를 받으면 이것을 백그라운드로 돌린다.
- **SessionStart hook (제안, 승인 필요):** launchd가 없는 PC를 위해 `hooks.json`에 SessionStart(`startup`) hook을 추가해 `skills-sync sync --if-older 15m`를 떼어서 돌리는 안. macOS에서는 launchd로 충분하므로 이번 구현에는 넣지 않고, 승인되면 넣는다.

### 동작 (`bin/skills-sync` → `scripts/sync.py`)

`sync` 한 번은 이 순서로 판정한다. 하나라도 걸리면 아무것도 바꾸지 않고 멈춘 뒤 알린다.

1. 체크아웃이 `main`이 아니면 멈춘다.
2. 커밋하지 않은 변경이 있으면(`git status --porcelain`, untracked 포함) 멈춘다.
3. `git fetch origin main`.
4. 앞뒤 커밋 수를 센다(`rev-list --left-right --count main...origin/main`).
   - 갈라졌으면(양쪽 다 > 0) 멈춘다. rebase·merge·force는 하지 않는다.
   - 뒤처지기만 했으면 `git merge --ff-only origin/main`.
   - 앞서기만 했으면 push 판정으로 간다.
5. push: 올라갈 커밋마다 서명이 있어야 한다. `git log --format=%G?`가 `N`(서명 없음)이나 `B`(서명이 틀림)인 커밋이 하나라도 있으면 멈춘다. `G`·`U`만 허용하지 않는 이유: `allowedSignersFile`이 없는 새 PC에서는 서명이 있어도 `E`(검증 불가)가 나올 수 있다. 이 PC의 최근 커밋은 모두 `U` 또는 `G`였다. 통과하면 `git push origin main`(force 없음).
6. HEAD가 바뀌었으면 바뀐 경로로 reload 브로드캐스트를 정한다(§4).

- **main 직접 push:** 이 저장소에는 branch protection과 ruleset이 없다(`gh api …/branches/main/protection` → 404 "Branch not protected", rulesets `[]`). 최근 main 커밋 15개는 모두 서명된 직접 커밋이고 PR은 2개뿐이다. 그래서 동기화는 main에 직접 push한다. 리뷰를 받고 싶은 큰 변경은 지금처럼 PR로 올리고, 동기화는 그 결과를 pull할 뿐이다.
- **인증 대기로 멈추지 않기:** launchd에는 터미널이 없어서 인증을 물으면 영원히 기다린다. `GIT_TERMINAL_PROMPT=0`으로 돌리고, `git config credential.helper`가 비어 있으면 fetch 전에 멈춘다. git 명령마다 시간 제한(60초)을 건다.
- **서명과 1Password:** 동기화는 커밋을 만들지 않는다. 이 체크아웃은 `commit.gpgsign=true`, `gpg.format=ssh`라 1Password 에이전트가 꺼져 있으면 커밋 자체가 실패한다. 그래도 서명 없는 커밋이 끼어 있으면 5단계에서 멈추고 "서명 없는 커밋 <sha>: 1Password SSH 에이전트를 확인한 뒤 다시 서명"이라고 알린다.
- **알림:** macOS 알림 센터(`osascript display notification`)와 상태 파일 `~/.local/state/agent-skills/sync.json`(마지막 결과, 멈춘 이유, 시각). 터미널에는 아무것도 쓰지 않는다. 입력 중인 세션에 알림이 끼어드는 문제를 만들지 않기 위해서다. 같은 이유로 멈춘 상태가 이어지면 다시 알리지 않고, 상태가 바뀔 때만 알린다. 대시보드는 이 파일을 읽어 인박스에 올릴 수 있다(`orchestrator-dashboard` 몫).
- **동시 실행:** `~/.local/state/agent-skills/sync.lock`에 파일 잠금(`fcntl.flock`, macOS에는 `flock` 명령이 없다)을 건다. launchd와 수동 실행이 겹쳐도 하나만 돈다.

## 4. reload 브로드캐스트

### 언제 보내는가

- `sync`가 HEAD를 옮겼고, 바뀐 경로가 §2 표에서 reload가 필요한 쪽일 때만 보낸다. 문서나 스크립트만 바뀌었으면 보내지 않는다.
- 수동: `skills-sync broadcast [--skills|--plugins]`. 스킬을 고친 세션이 fast-forward 뒤 직접 부를 때 쓴다.
- 이전에 못 보낸 세션이 남아 있으면 다음 `sync` 때마다 다시 시도한다.

### 누가 보내는가

터미널 목록·읽기·전송은 Orca CLI가 있어야 한다. launchd처럼 Orca 환경 변수가 없는 곳에서 `orca terminal list`가 도는지는 아직 재지 못했다(구현 첫 검증 항목). 그래서 두 단계로 나눈다.

- launchd 작업은 fetch → ff-only → push까지만 하고, reload가 필요하면 `reload-pending.json`에 "모든 Claude 세션, `/reload-skills`" 같은 항목을 쓴다. 실측에서 launchd에서도 orca가 돌면 그 자리에서 바로 보낸다.
- orca가 도는 곳(orchestrator 세션의 "Skills changed" 단계, 또는 사람이 친 `skills-sync broadcast`)이 대기 항목을 보내고 지운다.

### 대상과 안전장치

대상은 `orca terminal list --json`에서 `agentIdentity == "claude"`, `connected`, `writable`인 터미널이다. 각 터미널마다 아래를 **모두** 확인하고, 하나라도 확인할 수 없으면 보내지 않는다.

| 조건 | 확인 방법 (실측한 화면 기준) |
|---|---|
| Claude가 떠 있다 | `agentIdentity == "claude"`이고, `read --screen`에 Claude 입력창(위아래 `────` 가로줄 사이의 `❯` 줄)이 있다 |
| 빈 프롬프트에서 대기 중 | **마지막** `────` 두 줄 사이의 입력칸이 `❯` 하나뿐이거나, 빈 입력칸의 안내 문구(`❯ Try "…"`)만 있다. 그 위 history에 찍힌 `❯ <이미 보낸 프롬프트>`는 입력칸이 아니다. `read` 결과에 `draft`가 있으면 비어 있어야 한다 |
| 턴이 진행 중이 아니다 | 화면에 스피너 줄이 없다. 스피너 줄은 `✶ Contemplating… (32s · ↓ 912 tokens)`, `· Crunching… (running UserPromptSubmit hooks…)`처럼 글자 하나 + 단어 + `…`로 시작한다. 끝난 턴의 요약 줄(`✻ Worked for 2m 7s · done 5:48 AM`)에는 `…`가 없다. 턴 진행 중에도 빈 `❯` 입력칸은 보이므로 입력칸만으로는 판정하지 않는다. 탭 제목의 `✳`는 필요조건으로만 쓴다(`◑` 등 다른 글자면 작업 중). `✳`는 AskUserQuestion 대기 세션에도 붙기 때문에 충분조건이 아니다 |
| 권한·신뢰·질문 창이 없다 | 화면에 이런 문구가 하나도 없다: `Do you want to proceed?`, `❯ 1.`처럼 번호 선택지에 커서가 있는 줄, `Esc to cancel`, `Tab to amend`, `Enter to confirm`, `trust this folder`, `Enter to select`, `↑/↓ to navigate`, `Chat about this` |
| 사용자가 입력 중이 아니다 | 1.5초 간격으로 두 번 읽은 화면이 같다 |
| 셸이 아니라 Claude다 | 마지막 줄이 셸 프롬프트(`$`)가 아니고, bracketed paste 잔해(`^[[200~`)가 없다 |

`orca terminal wait --for tui-idle`은 쓰지 않는다. 실측에서 UserPromptSubmit hook이 도는 중(`· Crunching… (running UserPromptSubmit hooks… 3/4 · 0s)`)에도 `satisfied: true`를 돌려줬다.

권한 창이 떠 있는 화면(실측):

```text
 Bash command
   command -v orch orch-dash ; echo $CLAUDE_PLUGIN_ROOT
 Do you want to proceed?
 ❯ 1. Yes
   2. No
 Esc to cancel · Tab to amend
```

여기에 `/reload-skills` + Enter를 보내면 Enter가 "1. Yes"에 답해 버린다. 그래서 위 표의 조건은 "대기 중이 아니면 보내지 않는다"가 아니라 "대기 중임이 확인되지 않으면 보내지 않는다"로 읽는다.

### 보낸 뒤

- `orca terminal send --terminal <h> --text "/reload-skills" --enter`.
- 3초 뒤 화면에서 `Reloaded skills:` 또는 `Reloaded:`를 찾는다. 없으면 실패로 적는다.
- 못 보낸 세션과 이유는 `~/.local/state/agent-skills/reload-pending.json`에 남긴다. 다음 `sync`, 또는 `skills-sync broadcast --retry`가 다시 시도한다. 세션이 사라졌으면 목록에서 뺀다.
- 확인과 전송 사이의 짧은 틈에 사용자가 타이핑을 시작할 수 있다. 이 틈은 없앨 수 없어서, 두 번 읽기로 좁히는 데서 멈춘다.

README의 옛 문장("작업 중인 세션이면 대기열에 들어갔다가 … idle을 기다릴 필요가 없다")은 이 규칙으로 바꾼다. 턴 진행 중에는 대기열에 들어가더라도, 권한 창에서는 Enter가 답이 되기 때문이다.

## 5. 다른 PC 부트스트랩

```bash
git clone https://github.com/JeongJaeSoon/agent-skills ~/conductor/repos/agent-skills
python3 ~/conductor/repos/agent-skills/scripts/bootstrap.py           # 바뀔 내용만 보여 준다
python3 ~/conductor/repos/agent-skills/scripts/bootstrap.py --write   # 적용
```

`bootstrap.py`는 `migrate.py`처럼 기본이 dry run이다. `--write`일 때 하는 일:

1. `git`, `python3`, `claude`가 있는지, 체크아웃이 main인지 본다.
2. 옛 `install.py` 흔적(symlink, 옛 권한 규칙, 옛 hook)을 `migrate.py`와 같은 규칙으로 걷어 낸다.
3. `settings.json`을 백업하고 `env.CLAUDE_CODE_PLUGIN_DIRS`를 이 체크아웃으로 넣는다. 다른 값이 이미 있으면 덮어쓰지 않고 멈춘다.
4. `agent-skills@jeongjaesoon`이 설치·활성화되어 있으면 `enabledPlugins`에서 false로 둔다(두 번 로드 방지). 바꾸기 전 값을 출력한다.
5. macOS면 `~/Library/LaunchAgents/io.github.jeongjaesoon.agent-skills-sync.plist`를 쓰고 `launchctl bootstrap`한다. Linux면 같은 명령을 넣을 crontab 한 줄을 출력만 한다.
6. `skills-sync sync`를 한 번 돌린다.

새 PC에서 체크아웃 폴더로 처음 `claude`를 띄우면 신뢰 창이 한 번 뜬다(신뢰는 원본 clone 경로에 기록된다, §6.2). 부트스트랩은 이 창을 대신 누르지 않고 "체크아웃에서 claude를 한 번 띄워 신뢰를 수락하라"고 출력한다.

같은 PC에서 다시 돌려도 결과가 같다(이미 된 단계는 "ok"로 넘어간다). 체크아웃 경로는 PC마다 달라도 된다. 스크립트는 자기 위치를 체크아웃으로 쓴다.

## 6. 오케스트레이션 스킬 개선

### 6.1 즉답 원칙 (사용자 지시, 2026-09-25)

orchestrator는 사용자 메시지를 받으면 바로 답할 수 있는 상태를 유지한다. 스스로 태스크를 진행하지 않고, 받자마자 서브에이전트에 위임하거나 다른 Orca 세션을 띄운다.

`orchestrate` SKILL.md 앞부분에 "Stay answerable" 절로 넣을 초안(영어, 스킬 본문 언어를 따른다):

```markdown
## Stay answerable

The human must get an answer from you within seconds, at any time. You route; you do not do the work.

- Inside your turn, only three things happen: routing a request to its owner, checks that finish in
  seconds (`orch status`, one `orca … --json` read, one `gh … --json` read), and answering the human.
- Investigation, implementation, verification, reviews, long waits and monitoring go to a background
  subagent (`run_in_background`) or an Orca worker, the moment the request arrives. Hand over the
  facts you already have; do not "take a quick look first".
- Never block in the foreground: no `until` loops, no `sleep`, no `check --wait`, no `orch wait`
  without `run_in_background`, no back-to-back polling reads.
- Completion reaches you as a notification: the background task's completion, or the `orch wait`
  that wakes on worker_done, escalation or question. Between notifications, your turn is over.
- A request that would take you more than one short tool call is a delegation, even when you know
  the answer's shape.
```

`principles`에는 Delegation 묶음에 한 줄을 더하는 안을 제안한다: "**Stay Answerable** — the session the human talks to routes and answers; every piece of work runs in a background subagent or another session." 다만 `principles`는 pstack upstream을 따라가는 스킬이라, 구현할 때 `scripts/pstack-sync-test.sh`로 upstream 동기화가 깨지지 않는지 먼저 본다. 깨지면 `orchestrate`에만 둔다.

### 6.2 실측 근거 (2026-09-24~25 코디네이터 세션 한 개의 트랜스크립트)

사내 이름은 빼고 일반화해서 적는다. 규모: Bash 478회, AskUserQuestion 11회, 서브에이전트 20회, 자동 압축 3회.

| 막힌 지점 | 실제로 본 것 | 원인 |
|---|---|---|
| 워커 기동 직후 Claude가 안 뜸 (3회) | `worker-start` 결과가 `turn_start_unobserved` 또는 `outcome_unknown`. 화면에는 셸 프롬프트와 `^[[200~You are working inside Orca…`가 남아 있었다 | 새 폴더 신뢰 창 위에 지시문이 붙여넣어지고 Enter가 눌렸다. 기본 선택인 "No, exit"가 골라져 Claude가 꺼졌고, 나머지 붙여넣기는 셸로 떨어졌다. `outcome_unknown`은 성공이 아니라 "확인 못 함"이다 |
| 새 폴더 신뢰 창 | 신뢰 기록은 worktree 경로가 아니라 **원본 clone 경로**에 남는다(`~/.claude.json`). 상위 폴더가 신뢰되어 있어도 창이 떴다 | 한 번도 신뢰한 적 없는 저장소(처음 쓰는 저장소, 새 clone)의 첫 워커에서만 생긴다. 창이 그려진 직후 보낸 ↓ 키는 먹히지 않았고 20초 뒤에 다시 보내야 했다 |
| 알림이 사용자 입력에 끼어듦 | Orca가 코디네이터 입력창에 `You have N orchestration message(s). Run orca orchestration check …`를 치고 Enter를 누른다. 이런 턴이 21번이었다. 한 번은 사용자가 치던 문장 중간에 붙어 함께 제출되었고, 사용자는 17분 뒤 전문을 다시 보냈다 | 신뢰 창 사고와 같은 구조다. "글자를 치고 Enter"가 입력 중인 문장이나 선택 창에 답해 버린다 |
| 끝난 dispatch에 send 실패 | `Dispatch … is completed; its worker will never read that mailbox. Send to run:… instead, or start a new Dispatch` | 같은 줄 `&& echo`가 안 찍혔는데도 코디네이터는 다음으로 넘어갔다. 종료 코드를 보지 않았다 |
| `terminal send --wait-submit` 거짓 실패 | "input was accepted but no turn start was observed"가 두 번 났지만, 화면에서는 이미 턴이 돌고 있었다 | 경고만 보고 다시 보내면 같은 지시가 두 번 들어간다 |
| 분류기 거부 | 프로세스 kill(`Interfere With Workloads`), 답장 본문에 "kill"이라는 단어만 있어도 거부, 운영 배포 workflow(`Production Deploy`), 워커의 PR 머지(`Merge Without Review`), 워커 heartbeat가 3번 연속 막힌 뒤 확인 창에서 정지(`External System Writes`) | 본문을 파일로 빼서 `--body "$(cat <file>)"`로 보내자 통과했다. 배포와 kill은 사용자가 직접 하거나 명시적으로 허락한 뒤 통과했다 |
| 사용자가 기다림 | 포그라운드 until 루프 101초·91초·101초(워커 기동 대기). 턴 길이 27·31·37·21분. AskUserQuestion 하나가 44분 열려 있는 동안 워커 메시지 16개가 쌓였다. 사용자가 `!`로 친 셸 명령이 37분짜리 턴이 끝날 때까지 6분 넘게 대기했다 | 이 한가운데서 사용자가 즉답 원칙을 지시했다(§6.1) |

이번 워커 자신도 하나 겪었다. 파일로 전달받은 지시문에 `--dispatch-capability` 값이 빠져 있어서 heartbeat가 `The Dispatch capability is missing`으로 거부되었다. 지시문을 파일로 옮길 때 이 값이 떨어진 것으로 보인다.

### 6.3 계층

```text
orchestrator (사용자가 말을 거는 세션 하나, orchestrate의 top-level 모드)
  ├ 단독 태스크 세션 1..n        (deliver-ticket, dispatch-card, 사용자가 직접 연 세션)
  ├ 프로젝트 1 코디네이터 A..D    (orchestrate program 모드: 원장, 착지 게이트, 대시보드)
  ├ 프로젝트 2 코디네이터 A
  └ 프로젝트 3 코디네이터 A, B
```

- 세션 목록, 자동 편입, 인박스와 타임라인 화면은 `orchestrator-dashboard` 워커 몫이다. 스킬은 그 결과를 읽고 행동하는 규칙만 가진다. 편입 수단(`orca worktree set --parent-worktree` 등)과 데이터 모델은 그쪽 설계를 따른다.
- 사용자가 직접 연 세션도 편입되면 명단에 오른다. orchestrator는 그 세션을 **읽기만** 한다. 권한 창에 답하거나 대신 행동하지 않는다. 확인이 필요한 것은 인박스로 올린다.
- program 모드(지금의 `orchestrate`)는 그대로 프로젝트 코디네이터의 규칙이다. top-level은 그 위에서 코디네이터를 띄우고, 코디네이터의 digest와 `orch status`를 모아 볼 뿐 프로젝트 안을 직접 조종하지 않는다.

### 6.4 무엇을 바꾸고 무엇을 두는가

**바꾸는 것**

| 어디 | 무엇 | 근거 |
|---|---|---|
| `orchestrate/SKILL.md` 앞부분 | "Stay answerable" 절(§6.1 초안) | 사용자 지시, 긴 턴과 포그라운드 대기 |
| `orchestrate/SKILL.md` description | 최상위 트리거 추가: "모든 세션 관리", "오케스트레이터로", "전체 태스크 현황", "이 세션도 편입해줘" | top-level 모드가 program 트리거와 다른 말로 불린다 |
| 새 `orchestrate/references/top-level.md` | 계층, 라우팅 표(요청 → 서브에이전트 / 새 워커 / 기존 세션 / 프로젝트 코디네이터), 인박스에 올릴 것, 편입된 세션을 대하는 규칙 | §6.3 |
| 같은 파일 "Start a worker" 절 | 기동 확인을 `--screen`으로 한다(stream 읽기에는 신뢰 창이 안 보였다). `outcome_unknown`은 실패 후보로 본다. 확인은 백그라운드 서브에이전트가 하고 결과만 알림으로 받는다. 신뢰 창이면 §6.5의 승인 항목에 따른다. 셸만 남았으면: `^C`, `dispatch-show --preamble`을 파일로 저장, `claude "<그 파일을 읽어라>"`로 다시 띄운다. 파일로 옮길 때 `--dispatch-capability` 값이 들어 있는지 확인한다 | 워커 기동 실패 3회, 신뢰 창, 이번 워커의 heartbeat 거부 |
| 같은 파일 "Talking to sessions" 절 | 끝난 dispatch에는 `run:<id>`로 보내거나 새 dispatch. 모든 send는 종료 코드를 확인한다. `--wait-submit` 경고가 나면 다시 보내기 전에 `--screen`으로 턴이 시작됐는지 본다. 긴 본문과 kill·deploy 같은 단어가 들어간 본문은 파일에 쓰고 `--body "$(cat <file>)"`. 다른 세션 입력창에 글자를 치는 것은 §4의 안전장치를 통과한 한 줄 명령만 | 끝난 dispatch send 실패, 거짓 실패, 분류기 거부 |
| 같은 파일 "Asking the human" 절 | AskUserQuestion은 orchestrator 턴을 막는다. 결정이 필요한 것은 답변 텍스트와 인박스에 모아 두고 턴을 끝낸다. 사용자가 고르면 다음 턴에 반영한다. 배포·머지·kill처럼 사용자가 직접 하거나 허락해야 하는 것은 인박스에 "명령 실행" 유형으로 올린다 | 44분 AskUserQuestion, `!` 명령 6분 대기 |
| 같은 파일 "Injected notices" 절 | 사용자 메시지 끝에 `You have N orchestration message(s)…`가 붙어 있으면 그 앞까지가 사용자 문장이다. 잘렸을 수 있으니 "문장이 … 에서 끊겼다"고 한 줄로 알리고, 알림 처리는 백그라운드로 넘긴다 | 알림 끼어듦 21회 |
| 같은 파일 "Skills changed" 절 | 스킬을 고쳤으면 `skills-sync broadcast`(§4). 직접 `terminal send "/reload-plugins"`를 치지 않는다 | §4 |
| `principles` | Delegation에 "Stay Answerable" 한 줄 (pstack 동기화가 깨지지 않을 때만) | §6.1 |
| README, `docs/skills.md`와 번역본 `docs/skills.{en,ja}.md` | README 설치 절을 로컬 체크아웃 + 부트스트랩으로, reload 문장을 §4 규칙으로 바꾼다. 카탈로그에 `orchestrate`의 top-level 모드와 `skills-sync` 명령을 더하고, 번역본도 같은 커밋에서 고쳐 첫 줄 `translated-from`을 올린다(안 올리면 `scripts/catalog/build.py`가 원문보다 오래됐다고 표시한다). 카탈로그 페이지 재생성은 README 링크의 artifact라 이 작업 범위 밖이고, 결과 보고에 남긴다 | §1, §4 |

**두는 것**

- program 모드의 전부: 원장, `orch land`와 착지 레인, 동시 실행 상한, main 가디언과 QA 리드, `orch wait` 백그라운드 대기, Close 절차. 사용자가 마음에 들어 하는 부분이고, 이번 트랜스크립트에서 여기서 생긴 마찰은 찾지 못했다(`check --wait` 35회는 모두 백그라운드였다).
- `dashboard.md`와 대시보드 코드: `orchestrator-dashboard` 워커 몫이라 건드리지 않는다.
- 편입과 인박스의 데이터 쪽 구현: 같은 이유로 대시보드 워커 몫이다.

### 6.5 신뢰 창 처리 (별도 승인 항목)

§4와 이 저장소 규칙은 "권한·신뢰·질문 창이 떠 있는 세션에는 보내지 않는다", "다른 세션의 권한 창에 답하지 않는다"이다. 한편 트랜스크립트에는 사용자가 신뢰 창을 키 전송으로 허용하라고 한 기록이 있다(2026-09-25). 이 기록은 서브에이전트가 트랜스크립트에서 읽어 온 것이고, 이 워커가 직접 들은 말이 아니다. 그래서 규칙 안에 섞지 않고 따로 승인받는다.

- 제안: 신뢰 창**만**, 이 orchestrator가 **방금 띄운** 워커에 **한해**, 창이 그려지고 2초 뒤 ↓, 화면에서 `❯ Yes, I trust this folder`로 바뀐 것을 확인한 뒤 Enter. 한 번 해서 안 바뀌면 멈추고 인박스에 올린다.
- 권한 창, AskUserQuestion, 사용자가 직접 연 세션의 신뢰 창에는 절대 적용하지 않는다.
- 승인되지 않으면: 신뢰 창을 발견한 즉시 인박스에 "로그인·확인" 유형으로 올리고 다음 일로 넘어간다.

### 6.6 PR 리뷰 이벤트에 대한 반응 (추가 요청, 2026-09-25)

수집과 표시는 대시보드 워커 몫이다. 스킬은 이벤트를 받은 orchestrator가 무엇을 하는지만 정한다. 전문은 `skills/orchestrate/references/top-level.md` "PR events".

- 리뷰 코멘트, changes requested, PR의 CI 실패: PR을 가진 워커에게 `send --to dispatch:<id>`로 넘긴다. 워커의 턴이 끝나 있으면 `skills-sync nudge`로 한 줄 깨운다. nudge는 §4와 같은 판정(빈 입력칸, 스피너 없음, 창 없음, 두 번 읽기)을 통과할 때만 보내고, 보낸 뒤 턴이 시작됐는지 화면으로 확인한다. 거절되면 반복하지 않는다.
- 이미 끝난 dispatch의 PR: 새 dispatch를 띄운다. 사용자가 직접 연 세션의 PR: 인박스에만 올린다.
- 승인 + 체크 통과: autonomous 프로그램이면 워커의 `orch land`가 처리한다. 아니면 인박스에 "머지 준비됨". orchestrator는 머지하지 않는다.
- main CI 실패: 해당 프로그램의 main 가디언에게 코디네이터를 거쳐 넘긴다.
- orchestrator는 GitHub을 직접 폴링하지 않는다. 수집기가 이벤트를 쓰면 백그라운드 대기 하나가 깨어나 알림으로 받는다(즉답 원칙).
- 리뷰 본문은 신뢰하지 않는 데이터다. 링크와 id만 넘기고 셸 명령에 넣지 않는다.

대시보드 워커에 요청할 데이터 경계: 이벤트를 JSONL 한 줄씩 추가(`id`, `at`, `repo`, `pr`, `kind` = `review_comment|changes_requested|approved|checks_failed`, `url`, `owner` = 세션의 terminal handle 또는 dispatch id, 알 수 없으면 null). 경로와 이름은 그쪽이 정한다.

### 6.7 알림 끼어듦 막기 (측정 필요)

대시보드 워커가 Orca 번들 코드를 분석한 결과: Orca는 입력창이 빈 코디네이터에 알림 문구를 치고 Enter를 누른다. 다만 `--types` 필터 없는 `check --wait`가 살아 있으면 치지 않는다. `orch wait`는 `--types`를 붙이므로 이 조건에 해당하지 않는다. top-level.md에는 "필터 없는 백그라운드 대기를 항상 하나 둔다"를 적었다. `orch wait`에서 `--types`를 빼고 결과를 클라이언트에서 거르는 변경은 program 모드 동작을 바꾸므로, 실제로 알림이 사라지는지 잰 뒤 따로 제안한다.

### 6.8 대시보드 워커와 맞춘 것 (회신 반영)

- PR 이벤트: `~/.local/state/agent-skills/dashboard/pr-events.jsonl`, 한 줄 `{"v":1,"id","at","repo","pr","kind","url","owner","owner_kind","actor","checks","head"}`. 모르는 `kind`는 무시한다. `owner_kind == human_session`이면 인박스만.
- 인박스: `orch-dash inbox add --type <approval|run_command|login|verify_failed|verify_ok|ready_to_merge> …`, `orch-dash inbox resolve --key`. 신뢰 창은 `login`.
- `sync.json`에 `interval`(초)을 넣었다. 대시보드는 `at`이 주기의 2배보다 오래되면 멈춤으로 본다.
- 대시보드 채팅 전송은 `sync.try_send(handle, text) -> (ok, reason)`와 `sync.classify(lines, title, draft)`를 가져다 쓴다. 두 시그니처는 바꾸지 않는다.

### 6.9 대시보드 워커와 맞출 것 (처음 제안)

- 인박스 항목 유형: 스킬은 "승인", "명령 실행", "로그인", "검증 결과"를 올린다고 쓴다. 이름과 저장 위치는 대시보드 설계를 따른다.
- `~/.local/state/agent-skills/sync.json`과 `reload-pending.json`을 대시보드가 읽어 "스킬 동기화 멈춤"과 "reload 못 보낸 세션"을 인박스에 올릴 수 있다. 형식은 구현 때 그쪽에 send로 알린다.
- 대시보드의 채팅 전송도 §4와 같은 안전장치(빈 입력칸, 스피너 없음, 창 없음, 두 번 읽기)가 필요하다. 판정 함수를 `scripts/sync.py`에 두고 그쪽이 가져다 쓸 수 있게 할지 조율한다.

## 7. 되돌리기

| 바꾼 것 | 되돌리는 법 |
|---|---|
| `settings.json` `env.CLAUDE_CODE_PLUGIN_DIRS` | 키를 지우거나 `settings.json.bak-<시각>`을 되돌린다. 떠 있는 세션은 재시작해야 빠진다 |
| launchd 작업 | `launchctl bootout gui/$(id -u)/io.github.jeongjaesoon.agent-skills-sync` 뒤 plist 삭제 |
| 상태 파일 | `~/.local/state/agent-skills/` 삭제 |
| `enabledPlugins`의 `agent-skills@jeongjaesoon` | 부트스트랩이 출력한 원래 값으로 되돌린다 (이 PC는 원래 없었음) |
| 원격 마켓플레이스로 돌아가기 | 위 넷을 되돌린 뒤 `claude plugin marketplace add JeongJaeSoon/agent-skills && claude plugin install agent-skills@jeongjaesoon` |
