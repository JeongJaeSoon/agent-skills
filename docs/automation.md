# 어떤 일을 어떤 장치로 돌리나

정기 작업이나 사건에 반응하는 작업을 어디에 걸지 정하는 표다. 근거는 Claude Code 2.1.292와 Orca 1.4.220에서
잰 결과다.

## 처음 설치할 때

`skills-sync setup`(`scripts/automations.py`)이 이 저장소가 거는 정기 자동화의 입구다. 주기 작업(동기화,
pressure-watch, reap-hard, 대시보드 ensure)은 macOS LaunchAgent가 아니라 Orca automation으로 돈다. 자동화마다
기본값이 있고, `apply`는 기본 on 중 한 번도 설정하지 않은 것을 켜며, 이미 켜진 에이전트 없는 job(`sync`,
`pressure-watch`, `reap-hard`, `orch-dash`)의 등록 내용이 이 체크아웃과 다르면 다시 맞춘다. `setup.py`의 dry run은
같은 판정으로 `then    automation <이름>: install`·`refresh the registered job` 줄을 미리 보여 주고(plan hash에
들어간다), 건너뛰는 기본 on에는 이유를 적는다. `--apply`가 쓸 `agent-skills.json`(개인 설정 포함)을 기준으로 판정한다.
`setup.py`와 `bootstrap.py --write`가 마지막에 `apply`를 부르므로, 처음 설치한 사람은 따로 할 일이 없다.
바꾸고 싶으면 아래 명령을 메인 체크아웃에서 돌린다.

`reap-hard`는 묻지 않고 카드를 지우는 정리라 보호 목록 파일(`reap-hard.protect_file`)이 있는 PC에서만 켠다. 파일이 없는
새 PC에서는 상태가 "꺼짐(설정 키 없음)"이고 `apply`가 건너뛰며, 파일을 쓴 뒤 다음 `apply`가 건다
([reap-hard.md](reap-hard.md)). 세션 순찰(`orch patrol`)은 따로 job이 없고 reap-hard 회차가 띄우므로 같은 조건에서만 돈다
(pressure 회차가 경고 이상으로 바뀔 때만 예외). `orch-dash`는 대시보드 서버를 확인해 내려갔으면 올릴 뿐 아무것도 지우지
않아 새 PC에서도 켠다.

```bash
skills-sync setup [--json]               # 목록과 상태
skills-sync setup apply                  # 기본값 적용
skills-sync setup enable <이름>          # 켜기
skills-sync setup disable <이름>         # 끄기
```

| 이름 | 기본 | 장치 | 켜기 | 끄기 |
|---|---|---|---|---|
| `janitor` | on | Orca automation `agent-skills janitor`(에이전트 없는 주기 작업) | 아래 "에이전트 없이 도는 주기 작업"의 다른 작업과 같이 만든다. 있으면 trigger·precheck·precheck timeout·prompt 중 다른 것만 `orca automations edit`로 맞추고 켠다. workspace는 건드리지 않는다(runner worktree로 옮긴 것은 그대로). janitor가 꺼져 있었으면 꺼 둔 `janitor-load`도 같이 켠다(켜진 janitor 옆에 따로 끈 것은 그대로). 새로 만들 때는 `janitor-load`를 만들지 않는다. `enable janitor-load`나 `apply`가 만든다 | `orca automations edit --disabled`로 `janitor-load`까지 끈다. 등록은 남겨 `apply`가 다시 켜지 않는다 |
| `janitor-load` | on | Orca automation `agent-skills janitor load` | `janitor`와 같고, 만들 때는 `reap.py schedule --load --write`. `janitor`가 꺼져 있거나 없으면 만들지 않는다 | 이 job만 끈다 |
| `preview-watch` | off | LaunchAgent `io.github.jeongjaesoon.preview-watch` | tailscale CLI가 없으면 거부한다. 있으면 `preview_watch.py install` | `preview_watch.py uninstall`(watcher가 연 serve 항목도 닫는다) |
| `sync` | on | Orca automation `agent-skills sync`(에이전트 없음, [아래](#에이전트-없이-도는-주기-작업)) | 없으면 만든다(15분마다 `sync.py sync`). 있으면 trigger·precheck·timeout·prompt 중 다른 것만 맞추고 켠다 | `orca automations edit --disabled`. 등록은 남겨 `apply`가 다시 켜지 않는다 |
| `pstack-upstream` | off | Orca automation `agent-skills pstack-upstream`(에이전트 없음) | `sync`와 같다. 하루 한 번 `pstack-sync.py --notify`로 cursor/plugins의 pstack을 `vendor/pstack` 사본과 비교해 차이를 알린다(upstream 커밋마다 한 번). `skills/`와 사본은 건드리지 않는다. 이 저장소를 고치는 PC에서만 켠다 | `sync`와 같다 |
| `pressure-watch` | on | Orca automation `agent-skills pressure-watch`(에이전트 없음) | `sync`와 같다. 3분마다 `pressure.py round`. `round`가 경고·위험으로 끝나는 exit 1·2는 실패가 아닌 결과로 기록한다 | `sync`와 같다 |
| `reap-hard` | on(보호 목록 파일이 있을 때) | Orca automation `agent-skills reap-hard`(에이전트 없음) | `sync`와 같다. 15분마다 `reap_hard.py run`. 파일이 없으면 `apply`가 건너뛴다(`enable`은 받는다) | `sync`와 같다 |
| `orch-dash` | on | Orca automation `agent-skills orch-dash`(에이전트 없음) | `sync`와 같다. 10분마다 `dash.py ensure`로 이 스토어의 대시보드 서버를 확인해 내려갔으면 올린다 | `sync`와 같다 |
| `share-server` | off | LaunchAgent(라벨은 설정 `share-server.label`, 기본 `io.github.jeongjaesoon.share-server`) | `share_server.py install`(KeepAlive로 `serve`, 그 안에서 sandbox-exec 아래 caddy. caddy가 없으면 `brew install caddy`부터. 같은 라벨의 다른 plist는 백업 후 교체) | `share_server.py uninstall` |
| `land-executor` | off | Orca automation `agent-skills land-executor`(에이전트 없음) | `sync`와 같다. 1분마다 `land_exec.py round`. 켤 때 agent-skills.db에 `5-land-queue` 단계(`land_exec.py setup`)를 기록한다. 머지 요청은 `orchestrate.land_executor`가 켜진 repo에서만 생긴다. 그 키는 `skills-config`로 따로 켠다 | `sync`와 같다. 남은 요청은 큐에 그대로 둔다 |
| `sweep` | off | Orca automation `agent-skills sweep`(에이전트 없음) | `sync`와 같다. 주기(`sweep.interval`, 기본 15분)마다 `sweep.py tick`(순찰 → 그림자 → reap-hard → 소유 규칙 정리). tick은 한 번에 하나만 돈다(`sweep-tick.lock`). 보호 목록 파일이 없으면 거부한다. precheck timeout은 2700초다. `apply`는 켜지 않는다([reap-hard.md](reap-hard.md)의 전환 절차) | `sync`와 같다 |
| `watch-prs` | on(설정 키가 있을 때) | Orca automation `agent-skills watch-prs` | `agent-skills.json`에 `prs` 키가 없으면 상태는 "꺼짐(설정 키 없음)"이고 `apply`가 건너뛴다. 있으면 `prs.py schedule --write`로 만든다. 이미 등록돼 있으면 키와 상관없이 켜기만 한다. worktree에서는 거부한다 | `orca automations edit --disabled`. 등록은 남긴다 |
| `watch-mentions` | on(설정 키가 있을 때) | Orca automation `agent-skills watch-mentions` | `watch-prs`와 같다. 키는 `mentions`, 만드는 명령은 `mentions.py schedule --write` | `orca automations edit --disabled`. 등록은 남긴다 |

- 같은 상태면 아무것도 하지 않는다. `enable`을 두 번 돌려도 두 번째는 "이미 켜져 있음"이다.
- 켜기는 job에 이 체크아웃의 절대 경로를 적으므로 worktree에서는 거부한다(worktree가 지워지면 job이 조용히
  실패한다). LaunchAgent 테스트에만 `--here`를 붙인다. janitor는 하나뿐인 공유 job이라 `--here`도 받지 않고,
  worktree에서는 차이를 비교하지도 고치지도 않는다(꺼져 있으면 켜기만 한다).
- 메인 체크아웃에서 돌린 `status`는 등록된 내용이 이 체크아웃과 다르면(다른 경로, `reap.py`의 주기·프롬프트 변경) 그 필드를 적는다.
  `enable`을 다시 돌리면 맞춰진다.
- janitor·watch-prs·watch-mentions는 Orca가 있어야 한다. `orca` CLI가 없으면 상태는 "확인 불가"이고 `enable`은 안내만 하고 실패한다.
- preview-watch는 macOS 전용이다. 다른 OS에서는 상태가 "미지원"이고, `enable`은 cron으로 `bin/preview-watch run`을
  돌리라는 안내만 한다.
- 예전 setup이 만든 LaunchAgent(`agent-skills-sync`, `pressure-watch`, `reap-hard`, `orch-dash`와 손으로 만든
  대시보드 ensure plist)는 건드리지 않는다. 남아 있으면 `setup-pc` 점검이 `legacy LaunchAgent` warn 행으로 알린다.
  제거는 오너가 직접 한다(`launchctl bootout gui/$(id -u)/<라벨>` 뒤 plist 삭제). 같은 일을 하는 자동화와 LaunchAgent가
  함께 돌아도 각 작업의 잠금이 겹침을 막지만, 제거하기 전까지는 둘 다 돈다.
- Orca 앱이 꺼져 있으면 Orca automation은 돌지 않는다. LaunchAgent는 앱과 상관없이 돌았으므로 달라지는 점이다.
  세션을 쓰는 동안은 Orca가 켜져 있으니 영향이 없고, 앱을 닫아 둔 시간만큼 동기화·감시가 쉰다.

## 에이전트 없이 도는 주기 작업

Orca automation은 항상 에이전트 프롬프트를 갖지만, 실행 전에 `--precheck` 명령을 먼저 돌리고 exit가 0이 아니면
에이전트를 띄우지 않고 `skipped_precheck`로 끝낸다. `janitor`·`janitor-load`·`sync`·`pressure-watch`·`reap-hard`·`orch-dash`는 이를 이용해
precheck 안에서 작업을 직접 하고 항상 exit 1로 끝나므로(`scripts/orca_job.py run`) 회차마다 모델 비용이 0이다.
이것은 precheck의 원래 용도(돌릴지 말지의 판단)를 벗어난 쓰임이다. Orca가 precheck의 부작용을 막거나 시간 제한을
바꾸면 이 방식이 깨진다.

- **성공과 실패 구분:** 항상 exit 1이라 Orca 쪽 기록(`skipped_precheck`)만으로는 작업이 실패했는지 알 수 없다. 그래서
  `orca_job.py run`이 매 회차 `~/.local/state/agent-skills/jobs/<이름>.json`에 시각, exit 코드(시그널로 죽었으면
  `killed by signal N`), 마지막 한 줄, 주기를 쓴다. `python3 scripts/orca_job.py status`가 읽고, `setup-pc` 점검이
  마지막 실행이 실패했거나 주기의 3배를 넘게 안 돈 작업을 warn 행으로 보인다.
- **run history:** Orca는 자동화마다 최근 100회차만 남긴다(실측: runNumber 108~207이 한 번에 읽힘). 3분 주기라도
  쌓이지 않는다. 그만큼 Orca 쪽 기록은 최근 5시간뿐이라 길게 볼 때는 위의 `jobs/*.json`을 쓴다.
- **시간 제한:** precheck는 300초에서 끊기고, 작업은 280초에서 먼저 끊어 `timed out`으로 기록한다.

## 장치별 성질

| 장치 | 세션이 끝나도 남나 | 로컬 파일·프로세스 접근 | 모델 없이 거르나 |
|---|---|---|---|
| Orca automation + `--precheck` | 남는다(Orca 앱이 돌린다) | 된다 | 된다. precheck가 0이 아니면 그 회차는 모델을 부르지 않고 건너뛴다 |
| 클라우드 routine | 남는다 | 안 된다(로컬 파일을 못 본다) | 안 된다 |
| `CronCreate`, `ScheduleWakeup` | 세션과 함께 사라진다. `claude --continue`로 같은 세션을 재개하면 cron이 다시 살아난 관측이 있다(재개하지 않으면 사라진다. 7일 만료는 확인하지 않았다) | 된다 | 안 된다 |
| `Monitor`, 백그라운드 Bash(`run_in_background`) | 세션과 함께 사라진다 | 된다 | 된다(스크립트가 거른 뒤 깨운다) |
| hooks | 설정이 남아 있는 동안 | 된다 | 된다(결정적인 반응만) |

세션 밖에서 남으면서 모델 없이 거르는 로컬 장치는 Orca automation + precheck 하나뿐이다.

## 어떤 일을 어떤 장치로

| 일 | 권장 장치 |
|---|---|
| 정기 정리 | Orca automation(`--workspace-mode existing`) + precheck |
| skill_usage 수집·reflect 깨우기 | Orca automation precheck(새 신호도, 닫힌 standing PR 뒤에 기다리는 교훈도 없으면 exit 1) + 에이전트 |
| PR 머지 후 확인 | Orca automation + precheck(gh 상태 diff). 로컬 작업이 필요 없으면 클라우드 routine |
| 채팅 멘션 감시 | Orca automation + precheck(설정·잠금만. Slack은 MCP로만 닿아 새 메시지 유무는 회차 안에서 판단) |
| PC 사용량 감시·선제 정리 | Orca automation + precheck 안에서 직접 실행(에이전트 없음. [pressure.md](pressure.md)) |
| 세션 안 사건 반응 | `Monitor` 또는 `run_in_background`. 결정적인 반응은 hooks |

## Orca automation을 걸 때 지킬 것

- `completed`는 성공이 아니다. 회차는 에이전트가 거부당하고 아무것도 못 해도 `completed`로 끝난다. 회차
  출력의 실제 결과(정리 건수, 거부된 명령)로 판정하고, 거부·중단은 설정한 inbox 주소로 올린다.
- 예약 회차의 프롬프트는 오너의 채팅 지시가 아니다. 오너의 지시를 요건으로 하는 auto mode 허용 규칙은 예약
  회차를 덮지 못한다. automation을 켜기 전에 그 회차가 실행할 명령의 허용 규칙을 따로 둔다(`tune-automode`).
- precheck의 cwd는 `--workspace`로 준 worktree가 아니라 저장소의 메인 체크아웃이다. precheck 스크립트는 절대
  경로와 `git -C`를 쓴다.
- precheck가 `schedule`을 실행한 셸의 환경 변수를 물려받는다고 가정하지 않는다. 상태 폴더 같은 경로는 명령의 플래그로
  넘기고, 명령은 `python3`로 시작한다. 회차의 세션은 `env`나 `NAME=value` 접두를 별도 명령으로 보고 거부할 수 있다
  (reap.py `schedule`은 기본값과 다른 상태 폴더와 ledger만 `--state`, `--ledger`로 붙이고, mentions.py `schedule`도
  기본값과 다른 상태 폴더만 `--state`로 붙인다).
- `orca automations run`(수동 실행)은 precheck를 건너뛴다. precheck가 맞게 거르는지는 스크립트를 직접 돌려서
  exit code로 확인한다. 지난 결과를 기억하는 precheck라면 직접 돌린 회차도 기억되므로, 검증할 때는 상태
  폴더를 따로 둔다(reap.py는 `--state`).
- `--workspace-mode existing`을 쓴다. `new-per-run`은 회차마다 worktree를 만들어 치울 것을 늘린다.
- 모델은 `--workspace` 체크아웃의 Claude Code 설정을 따른다. `reap.model`, `reflect.model`, `prs.model`,
  `mentions.model`을 넣으면 `schedule`이 `--extra-agent-args=--model <값>`을 붙인다. 이 인자는 새 세션 회차에만 붙는다.
  이미 등록된 job(janitor, janitor-load, watch-prs, watch-mentions, reflect standing)은 `skills-sync setup apply`가 그 인자만
  키 값에 맞춰 `orca automations edit`로 고친다(켜짐·꺼짐은 그대로). `enable`은 janitor의 다른 차이를 맞출 때와 꺼진 watch를 켤 때 같이 맞춘다.
  키를 지운 경우에는 저장된 인자를 비우지 않는다(`prs.py schedule --edit`만 비운다). `claude`가 아닌 에이전트의 job은 건드리지 않는다.
  기준은 판단이 거의 없는 읽기 전용 회차만 haiku,
  나머지는 sonnet이다. 세션 안의 `CronCreate`·`/loop`는 그 세션의 모델로 돌고 모델을 고를 수 없으니, 반복 작업은 automation으로 옮긴다.
  설정 키를 쓰지 않을 때는 아래 runner를 쓴다. 메인 체크아웃의 `.claude/settings.local.json`에 `model`을 넣으면 그 체크아웃에서 여는 대화형 세션까지 바뀐다.
  그래서 automation은 전용 runner worktree에서 돌린다. `schedule`을 `--workspace` 없이 돌리면 아래 절차를 함께
  출력하고, 스크립트는 설정 파일을 쓰지 않는다.
  1. `orca worktree create --repo path:<메인 체크아웃> --name automations-runner --no-parent --setup skip --json`.
     `--no-parent`가 없으면 Orca가 지금 터미널의 worktree를 부모로 기록한다.
  2. `<runner>/.claude/settings.local.json`(gitignored)에 사용자가 고른 모델을 넣는다. 예: `{"model": "sonnet"}`.
  3. 새 automation은 `schedule --workspace path:<runner>`로 만들고, 이미 있는 것은
     `orca automations edit --id <id> --workspace path:<runner>`로 옮긴다.

  precheck와 프롬프트의 스크립트는 메인 체크아웃의 절대 경로라 runner는 cwd와 설정만 준다. 다만 회차 세션이 읽는
  `CLAUDE.md`와 `.claude/settings.json`은 runner의 HEAD 것이라 메인보다 뒤처질 수 있다. runner 하나는 모델 하나다.
  머지·송신·분류 판단이 있는 회차와 순수 기계적인 회차의 모델을 나누려면 runner를 둘 만든다. janitor는 automation이
  도는 worktree를 정리 대상으로 잡지 않는다.
- precheck는 할 일이 없으면 exit 1이다. 지난 회차와 같은 결과도 할 일이 없는 것으로 친다. 그래야 모델 호출이
  바뀐 것이 있을 때만 일어난다. 같은 계획이 회차마다 실행되지 않고 쌓이면(거부) 정해 둔 횟수 뒤부터 건너뛰고,
  하루 한 번만 다시 시도하며, inbox로 한 번 알린다(reap.py는 `reap.plan_repeat` 기본 2회, `reap.inbox_to`).
- 다른 automation이 이미 하는 감시는 겹쳐 걸지 않는다. 예를 들어 PR 감시 automation이 따로 있으면, 정리
  작업은 PR을 감시하지 않고 scan할 때 `gh`로 PR 상태를 직접 읽는다.
- 실제 플래그는 `orca automations create --help`로 확인한다.

## 정기 정리(janitor)

`skills/reap-resources`의 `reap.py`가 맡는다. Orca 카드 전부(`card`, 규칙은 `references/cards.md`), 에이전트가 만든 것(ledger에 올린 것과 끝난
오케스트레이션 워커 터미널)과, `reap.user_folders`에 지정한 폴더에서 7일 넘게 안 바뀐 항목을 정리한다. 회차에는 모델이
없다. Orca는 precheck를 Claude 세션 밖에서 직접 실행하므로 거기서 돈 정리는 권한 프롬프트나 auto mode 분류기를 만나지
않는다. precheck `reap.py precheck --act`가 계획을 만들고, 같은 프로세스에서 `reap --plan`과 같은 경로(다시 scan, 같은
identity만, 대상 잠금 안에서 guard 재확인)로 정리한다. 이 명령은 위의 공용 실행기 `orca_job.py run janitor -- …` 안에서
돌아 그 결과(exit 0, 돌지 못한 회차는 2)가 `jobs/janitor.json`에 남고, 실행기가 늘 exit 1로 끝나서 Orca가 에이전트를 띄우지
않는다(실행 기록은 `skipped_precheck`). 480초 뒤에는 새 대상을 시작하지 않고, 570초가 되면 진행 중인 대상을 끊어 실패로
기록한다(실행기의 시간 제한 580초, precheck 600초보다 앞). 실제로 한 일은 `<state>/janitor-runs.jsonl`에 회차마다 한 줄 남는다. 실패한 대상은 그 대상만 하루
쉬고(`janitor-fails.json`), chrome-window는 브라우저에서 URL을 확인해야 하므로 손대지 않는다. 사람 결정 카드, chrome-window,
쉬는 대상은 `reap.inbox_to`로 한 통에 묶어 보내고, 지난번에 보낸 목록에 없던 것이 생겼을 때만 다시 보낸다. 도구는 영구 삭제도
휴지통 비우기도 하지 않지만, OS 설정(Finder의 "30일 후 휴지통에서 항목 제거")이 켜져 있으면 휴지통으로 옮긴 것은
30일 뒤 지워진다. scan과 precheck는 이 설정을 `## 경보`로 알리고, 그동안 지정한 개인 폴더 항목은
`$AGENT_SKILLS_TRASH`(일반 폴더)를 주지 않는 한 옮기지 않고 남긴다.

```bash
python3 <reap.py 절대 경로> precheck --act   # 예약 회차: 계획 → 정리 → exit 0(돌지 못하면 2). 다른 회차가 돌고 있으면 바로 exit 0
python3 <reap.py 절대 경로> precheck     # 계획만(에이전트 회차용): 대상이 있으면 exit 0. 지난 reap 이 실패한 대상과 같으면 하루 동안, 같은 계획이 reap 없이 plan_repeat 번 쌓이면 하루 한 번만 exit 0
skills-sync setup enable janitor        # 등록·갱신은 이 명령으로 한다. 아래 schedule 은 그 내부와 runner 용
python3 <reap.py 절대 경로> schedule     # 같은 precheck·trigger 로 만들 `orca automations create …` 명령을 출력만 한다(prompt 는 Run now 용). 스킬을 로드하는 체크아웃에서 돌린다
python3 <reap.py 절대 경로> schedule --workspace path:<runner>   # runner worktree 에서 돌린다(위 "지킬 것"의 모델 항목)
python3 <reap.py 절대 경로> schedule --write   # 실제로 만든다
orca automations edit <id> --prompt "<schedule 이 출력한 --prompt 값>"   # 이미 있는 automation 의 프롬프트만 바꾼다
```

주기는 30분(`*/30 * * * *`, precheck timeout 600초)이다. 모델을 부르지 않으니 회차 비용은 scan이다(대상이 있으면 계획과 실행 직전 재확인, 두 번). 에이전트 회차로 등록한 PC(옛 `7,37 * * * *`, precheck에 `orca_job.py`가 없는 것)는 `skills-sync setup`이 `등록된 내용이 이 체크아웃과 다름: <필드> (enable 로 맞춤)`으로 보여 주고, 오너가 메인 체크아웃에서 `skills-sync setup enable janitor`를 실행하면 이 값으로 고쳐진다. 남는 `--model` 인자는 에이전트가 뜨지 않으니 쓰이지 않는다. 등록된 실제 주기는 `orca automations list --json`의 `rrule`로 확인한다. 그 사이에 PC 부하가 높아지면 두 번째 automation `janitor-load`(`27,57 * * * *`, `precheck --load --act`)가 같은 회차를 한 번 더 돈다(30분 janitor 옆에서는 아래 쿨다운 때문에 거의 돌지 않는다). janitor를 꺼 두었거나 지운 PC에는 만들지 않고, janitor가 옮겨 간 worktree에 만든다. 이 precheck는 5분 load만 읽고, load가 `reap.load_limit`(기본 CPU 수) 이상이며 janitor가 등록돼 켜져 있고(Orca 앱에서 끄거나 지운 것도, automation 목록을 못 읽은 것도 꺼짐으로 본다) 두 job 어느 쪽도 `reap.load_cooldown_minutes`(기본 60분) 안에 precheck를 돌리지 않았을 때만 전체 precheck로 넘어간다. 쿨다운은 부하 job만 멈추고, 30분 job은 늘 제 시각에 돈다. 부하가 높을 때가 끝난 카드와 compose 스택(`compose-stack`: `orch heavy`가 등록한 것)이 가장 비싼 때이기 때문이다. pressure-watch LaunchAgent가 없는 PC에서도 Orca가 돌린다. 두 job은 `<state>/janitor.lock`을 함께 써서 한 번에 한 회차만 돈다. 규칙과 ledger 형식은 [reap-resources SKILL.md](../skills/reap-resources/SKILL.md).

## 세션 순찰 (`orch patrol`)

모델 없이 도는 한 라운드다. reap-hard LaunchAgent가 회차를 마칠 때마다 `orch patrol --if-due`를 별도 프로세스로 띄우고, 순찰은 마지막 라운드가 25분 넘게 지났을 때만 돈다(그래서 15분 박자에서 두 번에 한 번, 30분). pressure 회차가 경고나 위험으로 바뀌면 그 회차도 같은 명령을 띄워 바로 한 번 더 돈다. 한 라운드는 120초가 지나면 그 자리에서 멈춘다. 그때까지 쓴 발견 기록은 남고, 대시보드 Needs you 항목은 다음 라운드가 기록에 맞춘다. 새로 등록할 LaunchAgent는 없다. reap-hard와 pressure가 메인 체크아웃의 코드를 그대로 읽기 때문이다.

읽는 순서는 Orca 명령이 먼저다(`orca terminal list`, `orca worktree ps --agent-wait`, 모든 Run의 `orca orchestration worker-list`). 그다음 collector의 `state.json`(카드의 부모, 연결 PR, 보드), 카드별 세션 기록 끝부분, `orch sent` 기록, 직전 pressure 회차를 읽는다. PR은 collector가 모은 것을 쓰고 `gh`를 새로 부르지 않는다. 화면은 직전 라운드 뒤로 출력이 생긴 에이전트 터미널만 읽는다.

| 코드 | 경우 |
|---|---|
| I | Run 밖 카드의 턴이 15분 넘게 끝나 있고 스스로 깨어날 장치(ScheduleWakeup, Monitor, 백그라운드 명령)가 없다. 사람 대기 프롬프트는 `orch watch-cards`가 맡는다 |
| X | Orca가 `exited`로 본 진행 중 워커, 또는 에이전트가 사라진 Run 밖 카드의 세션 기록이 API 오류나 오류 알림으로 끝났다 |
| S | `orch sent add --kind supersede`로 대체를 기록한 지 30분이 지났는데 그 카드에 에이전트가 남아 있다 |
| O | 에이전트 없는 카드의 PR이 24시간 열려 있고 리뷰 요청도 진행 중인 CI도 없다 |
| D | 보드 doing 항목이 PR도 카드도 가리키지 않은 채 24시간 그대로다. 보드에 ❔로 보인다 |
| E | 같은 에러 줄이 출력이 새로 생긴 라운드 세 번 연속으로 보인다. ❔ 표시만 한다 |
| R | Run 밖에서 사람을 기다리지 않는 카드의 세션 기록이, 끝난 턴 3개 이상(30분 이상)에 걸쳐 같은 말만 하고 끝난다. 같은 에러, 같은 마지막 메시지, 같은 입력(nudge 같은 것), `/reload-*`만 반복한 경우이고, 그 사이 도구 호출·입력·결과가 바뀌었으면 세지 않는다. 카드는 working이 아니라 stalled로 판정한다(스스로 깨어날 장치가 있으면 그대로 둔다). 부모에게 한 번만 보여 주고, 같은 세션의 같은 문구로는 다시 올리지 않는다. ❔ 표시만 한다 |
| H | 사람을 기다리지 않는 에이전트 카드(Run 워커 포함)의 턴이 끝나 있고, 화면이 그 세션이 바뀌기 전 것으로 돌고 있다고 말한다. 마지막 턴의 hook 오류 중 자기 파일이 없다고 말하는 것(`hook error`와 `can't open file`·`No such file or directory` 같은 문구: 갱신이 지운 스크립트를 옛 hooks.json이 부를 때)이나 Claude Code의 `/restart to apply` 안내다. guard hook의 거부도 화면에는 `hook error`로 보이므로 그것만으로는 세지 않는다. 이 문구가 없는 화면은 판정하지 않는다. 카드는 stalled로 판정하고(Run 워커는 run 그대로), brief의 그 카드 행은 ❔ `확인 필요: <이유>`가 된다. 고치는 입력은 `skills-sync broadcast`가 그 세션이 idle일 때 한 번 친다 |
| B | 사람을 기다리지 않는 에이전트 카드(Run 워커 포함)의 턴이 15분 넘게 끝나 있고, 마지막 메시지는 백그라운드 작업의 완료 알림을 기다린다고 말하는데 깨울 것이 없다. 세션 기록에 결과를 기다리는 백그라운드 명령이나 Monitor가 없고(parked 아님), Claude Code 상태줄과 입력칸 아래 에이전트 패널에도 도는 shell·Monitor·agent·작업이 없다. 상태줄의 `← N agent`는 다른 세션의 대기 수라서 세지 않는다. 이 문구가 없는 화면은 판정하지 않는다. brief의 그 카드 행은 ❔ `에이전트 조치 필요: 백그라운드 작업을 기다리지만 깨울 작업이 없음`이 된다 |
| L | 경고·위험 회차에 상위 소비자가 I·X·S·O·R·H로 판정된 카드에 속한다 |
| W | 오케스트레이터의 `orch watch-cards`가 1시간 넘게 회차를 마치지 않았다. 대시보드 Needs you에는 collector가 올린다 |
| T | `~/.claude/agent-skills.json`의 `term_` 값이 `orca terminal list`에 없다. 발견은 키, 값의 앞 12자, 권장 주소 `run:<id>`를 적어 오케스트레이터에게 간다. Orca 목록이나 설정 파일을 못 읽거나 목록이 잘렸으면 발견 없이 note 한 줄만 남기고 이미 있던 발견은 유지한다. 경고만 하고 값을 거부하지는 않는다 |
| M | 살아 있는 Claude 세션의 실제 모델(transcript 마지막 assistant 줄의 `model`)이 그 역할의 `orchestrate.models`와 다르다. 역할은 `hooks/roles.py`의 판정을 그대로 쓰고(ROLE 기록·Dispatch preamble·CLOSE/KEEP 행), orchestrator와 lead는 `lead` 키, worker는 `worker` 키를 읽는다. 사람이 연 세션과 patrol 자신의 터미널은 보지 않고, 키가 없는 역할은 판정하지 않으며 note 한 줄만 남긴다. 발견은 ❔이고 brief 행에는 `모델 불일치: <실제> (설정 <alias>)`가 붙는다. 기본 설정에서 고치는 방법은 발견에 적힌 `claude --resume <세션 id> --model <alias>`로 그 세션의 소유자가 재기동하는 것뿐이다(아래 `orchestrate.model_autoswitch`가 켜진 worker는 예외). `/model`은 patrol도 치지 않는다: 새 세션의 사용자 기본값까지 바꾼다. 재기동한 세션은 첫 답을 하기 전까지 transcript에 옛 모델이 남아 있다. `orchestrate.model_autoswitch: true`이면 patrol이 그 재기동을 직접 한다(기본은 꺼짐). 대상은 Orca가 idle로 보고하고 화면이 빈 프롬프트인 worker뿐이고, orchestrator와 lead는 표시만 한다. 열린 대화상자·진행 중인 턴·사용자가 쓰던 초안·화면에 보이는 background 작업(셸, 서브에이전트, Monitor)이 있으면 건드리지 않고 다음 회차에 다시 본다. 순서는 `/exit`, 셸 프롬프트 확인, 같은 터미널에 `claude --resume <세션 id> --model <alias>`이고, 셸 프롬프트가 안 보이면 재기동 줄을 치지 않는다. 한 세션에 한 번만 시도하며(`$AGENT_SKILLS_STATE/model-switch/<세션 id>.json`을 키 입력 전에 만든다) 실패하면 재시도 없이 사람의 Needs you에 올리고 발견은 수동 안내로 남는다. 한 회차에 한 세션만 바꾸고, `--dry` 회차는 바꾸지 않는다. 터미널 핸들, Run 결속, 대화 맥락은 이어지고 background 셸·서브에이전트·`orch wait`는 끊긴다(그래서 있으면 건너뛴다) |

결과는 `<state>/patrol.jsonl`(발견, 해소, ack), `<state>/patrol-verdicts.json`에 남는다. 이 파일의 `cards`는 카드별 활동 판정(run·waiting·working·idle·exited·stalled), `hints`는 발견이 그 카드에 대해 한 말(superseded 등), `check`는 H·B·M과 R(스스로 깨어날 장치가 없는 것)이 걸린 카드와 brief가 그 행의 ❔에 붙일 이유, `failed`는 그 라운드에 못 읽은 소스다. brief는 1시간이 넘은 라운드의 `check`는 쓰지 않고, 그 라운드 뒤에 세션이 움직였거나 지금 작업 중인 카드에도 붙이지 않는다. 발견은 run·waiting·working 판정을 덮어쓰지 않는다. X·E·L·O는 대시보드 Needs you에 올라가고 조건이 풀리거나 ack하면 빠진다. `orch inbox`와 `orch wait`는 그 카드를 띄운 세션(없으면 오케스트레이터)에 ack되지 않은 발견을 30분마다 다시 보여 준다(R은 처음 한 번만). 처리한 세션은 `orch patrol ack <id>`로 닫는다. 순찰은 화면 입력, nudge, 터미널 닫기, 워커 종료, 카드 삭제를 하지 않는다(`orchestrate.model_autoswitch`를 켠 경우의 M 전환 하나만 예외다). reap-hard와 pressure는 판정을 카드를 남기는 쪽으로만 쓴다(작업 중, 사람 대기, Run 워커). 판정에 없는 카드, 특히 `failed`가 있는 라운드에서 빠진 카드를 안전하다는 근거로 쓰지 않는다.

`orch watch-cards`와 순찰은 `claude agents --json`의 세션 상태(busy, idle, blocked)를 두 번째 신호로 함께 기록한다. 터미널과 세션은 SessionStart 기록(`hooks/roles.py`)으로만 잇는다. 화면 검사는 줄지 않았고, 이 신호는 관찰만 한다. 어떤 판정, 발견, `cards`, `failed`, `sources`도 이 신호로 바뀌지 않고 어긋나도 아무도 깨우지 않는다. 조회 실패, 모르는 상태, 목록에 없음, 기록 없음은 `신호 없음`으로 적고, Codex 카드는 화면만 본다. 한 라운드가 못 본 것이 있으면 머리말이 `OK`가 아니라 `PARTIAL`이고 무엇을 못 봤는지 적는다. busy는 턴이 끝났어도 백그라운드 셸이 돌면 나오고, 권한 프롬프트·선택 메뉴·중단을 구분하지 못한다. 양쪽 판정은 `<state>/agent-signal.jsonl`에 남는다. `round`는 회차마다, `card`는 판정이 바뀔 때와 새 파일의 기준 행, `gone`은 터미널이 사라질 때 쓴다. 32 MB에서 `.1`로 한 세대 옮긴다. 1–2주 뒤 `differs`와 `finding`을 읽어 어느 쪽이 무엇을 놓쳤는지 비교한다. 순찰은 같은 값을 `patrol-verdicts.json`의 `signals`에도 쓴다.

같은 파일에는 정리 체계 v2의 그림자 필드도 함께 쓴다. 아직 아무도 읽지 않고, 위 필드의 이름과 타입은 그대로다. `round`는 회차 id, `sources`는 소스별 ok·failed·skipped, `activity`는 `cards`가 담지 못하는 상태(parked, unknown)다. `candidates`는 지금 정리 주체들(janitor의 `reap.py scan`, reap-hard, pressure의 안전 종류)이 이번 회차에 대상으로 판정한 것을 규칙 이름(`card:landed`, `card:strict`, `stack:gone` 같은 것)과 함께 옮긴 목록이다. 새로 판정하지 않으므로 지금 대상보다 많아지지 않는다. 기댄 소스를 이번 회차에 읽지 못한 후보와 모델이 닫던 Chrome 창은 `reports`(보고만)로, 사람이 정할 카드는 `alerts`로 간다. 후보 단계는 본 회차가 잠금을 놓은 뒤 240초 예산으로 돌고, 끝까지 가야 `complete: true`가 된다. 스캔을 시작한 시각은 `candidates_t`에, 후보마다 그 후보를 낸 스캔이 시작한 시각은 `seen`에 남는다. 위험 단계에서는 건너뛴다. 회차마다 소스별 시간이 `<state>/patrol-rounds.jsonl`에 남고 `orch patrol timing`이 p50·p95를 보여 준다.

```bash
orch patrol --once --dry-run   # 한 라운드를 판정만 하고 아무것도 쓰지 않는다
orch patrol list               # 이 터미널로 온(그리고 주인 없는) 미처리 발견
orch patrol ack <id>...        # 처리한 발견을 닫는다
orch patrol timing             # 최근 회차의 소스별 시간 p50·p95
```
