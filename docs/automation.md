# 어떤 일을 어떤 장치로 돌리나

정기 작업이나 사건에 반응하는 작업을 어디에 걸지 정하는 표다. 근거는 Claude Code 2.1.292와 Orca 1.4.220에서
잰 결과다.

## 처음 설치할 때

`skills-sync setup`(`scripts/automations.py`)이 이 저장소가 거는 정기 자동화의 유일한 입구다. 자동화마다
기본값이 있고, `apply`는 기본 on 중 한 번도 설정하지 않은 것만 켠다. `bootstrap.py --write`가 마지막에 `apply`를
부르므로, 처음 설치한 사람은 따로 할 일이 없다. 바꾸고 싶으면 아래 명령을 메인 체크아웃에서 돌린다.

```bash
skills-sync setup [--json]               # 목록과 상태
skills-sync setup apply                  # 기본값 적용
skills-sync setup enable <이름>          # 켜기
skills-sync setup disable <이름>         # 끄기
```

| 이름 | 기본 | 장치 | 켜기 | 끄기 |
|---|---|---|---|---|
| `janitor` | on | Orca automation `agent-skills janitor` | 없으면 `reap.py schedule --write`로 만든다. 있으면 trigger·precheck·precheck timeout·prompt 중 `reap.py schedule`과 다른 것만 `orca automations edit`로 맞추고 켠다. workspace는 건드리지 않는다(runner worktree로 옮긴 것은 그대로) | `orca automations edit --disabled`. 등록은 남겨 `apply`가 다시 켜지 않는다 |
| `janitor-load` | on | Orca automation `agent-skills janitor load` | `janitor`와 같고, 만들 때는 `reap.py schedule --load --write` | `janitor`와 같다 |
| `preview-watch` | off | LaunchAgent `io.github.jeongjaesoon.preview-watch` | tailscale CLI가 없으면 거부한다. 있으면 `preview_watch.py install` | `preview_watch.py uninstall`(watcher가 연 serve 항목도 닫는다) |
| `pressure-watch` | on | LaunchAgent `io.github.jeongjaesoon.pressure-watch` | `pressure.py install`(3분마다 `round`) | `pressure.py uninstall` |
| `share-server` | off | LaunchAgent(라벨은 설정 `share-server.label`, 기본 `io.github.jeongjaesoon.share-server`) | `share_server.py install`(KeepAlive로 `serve`, 그 안에서 sandbox-exec 아래 caddy. caddy가 없으면 `brew install caddy`부터. 같은 라벨의 다른 plist는 백업 후 교체) | `share_server.py uninstall` |
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
- `agent-skills-sync` LaunchAgent(15분 동기화)는 자동화 목록에 없다. 체크아웃을 실시간으로 읽는 설치의 일부라
  `bootstrap.py`가 건다.

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
| PC 사용량 감시·선제 정리 | LaunchAgent(모델이 판단할 것이 없고, 부하 중에 세션을 띄우지 않으려고. [pressure.md](pressure.md)) |
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
  (reap.py `schedule`은 기본값과 다른 상태 폴더와 ledger만 `--state`, `--ledger`로 붙인다).
- `orca automations run`(수동 실행)은 precheck를 건너뛴다. precheck가 맞게 거르는지는 스크립트를 직접 돌려서
  exit code로 확인한다. 지난 결과를 기억하는 precheck라면 직접 돌린 회차도 기억되므로, 검증할 때는 상태
  폴더를 따로 둔다(reap.py는 `--state`).
- `--workspace-mode existing`을 쓴다. `new-per-run`은 회차마다 worktree를 만들어 치울 것을 늘린다.
- 모델은 `--workspace` 체크아웃의 Claude Code 설정을 따른다. `orca automations create`·`edit`에는 모델 옵션이 없고,
  메인 체크아웃의 `.claude/settings.local.json`에 `model`을 넣으면 그 체크아웃에서 여는 대화형 세션까지 바뀐다.
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

`skills/reap-resources`의 `reap.py`가 맡는다. 에이전트가 만든 것(ledger에 올린 것과 Orca 오케스트레이션
워커가 만든 worktree)과, `reap.user_folders`에 지정한 폴더에서 7일 넘게 안 바뀐 항목을 정리한다. automation의 에이전트는
`reap --plan`을 한 번 돌려 대상을 닫거나 휴지통으로 옮기고, 무엇을 했는지 보고한다. 도구는 영구 삭제도
휴지통 비우기도 하지 않지만, OS 설정(Finder의 "30일 후 휴지통에서 항목 제거")이 켜져 있으면 휴지통으로 옮긴 것은
30일 뒤 지워진다. scan과 precheck는 이 설정을 `## 경보`로 알리고, 그동안 지정한 개인 폴더 항목은
`$AGENT_SKILLS_TRASH`(일반 폴더)를 주지 않는 한 옮기지 않고 남긴다. reap이 거부되면 회차는 우회하지 않고 거부된
명령을 `reap.inbox_to`로 보낸다.

```bash
python3 <reap.py 절대 경로> precheck     # 대상이 있으면 exit 0. 지난 reap 이 실패한 대상과 같으면 하루 동안, 같은 계획이 reap 없이 plan_repeat 번 쌓이면 하루 한 번만 exit 0
skills-sync setup enable janitor        # 등록·갱신은 이 명령으로 한다. 아래 schedule 은 그 내부와 runner 용
python3 <reap.py 절대 경로> schedule     # 만들 `orca automations create …` 명령을 출력만 한다. 스킬을 로드하는 체크아웃에서 돌린다
python3 <reap.py 절대 경로> schedule --workspace path:<runner>   # runner worktree 에서 돌린다(위 "지킬 것"의 모델 항목)
python3 <reap.py 절대 경로> schedule --write   # 실제로 만든다
orca automations edit <id> --prompt "<schedule 이 출력한 --prompt 값>"   # 이미 있는 automation 의 프롬프트만 바꾼다
```

주기는 3시간(`17 */3 * * *`)이다. 그 사이에 PC 부하가 높아지면 두 번째 automation `janitor-load`(`27,57 * * * *`, `precheck --load`)가 같은 회차를 한 번 더 돈다. janitor를 꺼 둔 PC에는 만들지 않고, janitor가 옮겨 간 worktree에 만든다. 이 precheck는 5분 load만 읽고, load가 `reap.load_limit`(기본 CPU 수) 이상이며 두 job 어느 쪽도 `reap.load_cooldown_minutes`(기본 60분) 안에 precheck를 돌리지 않았을 때만 전체 precheck로 넘어간다. 부하가 높을 때가 끝난 카드와 compose 스택(`compose-stack`: `orch heavy`가 등록한 것)이 가장 비싼 때이기 때문이다. pressure-watch LaunchAgent가 없는 PC에서도 Orca가 돌린다. 같은 계획이 reap 없이 쌓이면 모델을 깨우지 않는 규칙(`reap.plan_repeat`)은 두 job이 함께 쓴다. 규칙과 ledger 형식은 [reap-resources SKILL.md](../skills/reap-resources/SKILL.md).
