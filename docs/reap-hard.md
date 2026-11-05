# reap-hard: 모델 없이 끝난 카드·스택·포트를 닫는 정리

`bin/reap-hard`는 규칙이 코드로 고정된 정리 스크립트다. Orca automation(에이전트 없음)이 15분마다 돌리고, 모델이나 카드 세션의 판단을 기다리지 않는다.

## 왜 따로 두는가

janitor automation(`reap.py precheck`)은 당시 30분마다 돌았지만(지금은 3시간마다, 부하가 높으면 그 사이에도 한 번) 하루 넘게 대상 0개를 냈고, 그동안 머지된 카드가 남았다. 실측으로 확인한 원인은 다음과 같다.

1. **등록된 것만 본다.** janitor는 ledger에 있거나, 오케스트레이션 워커가 만들었거나, lineage가 `cli`인 카드만 본다. Orca 앱이나 다른 경로로 만든 카드(lineage `manual`·없음)는 판정 대상에 오르지 않았다. 측정 시점에 Orca 카드 42개 중 13개가 그랬고, 리드 카드 대부분이 여기에 들었다. dispatch-card의 `ledger add`는 2주 동안 5번 불렸다.
2. **닫기 요청이 한 번도 나가지 않았다.** 세션이 떠 있는 카드는 `done-card`로 그 세션에게 닫아 달라고 한 번 묻는다. 그런데 이 요청은 PR 머지, 워커 턴 없음, `cli` 카드면 6시간 조용, 그 뒤 2시간 더 조용이라는 조건을 모두 채워야 나간다. ledger의 `done-card` 기록은 0건이었다. 머지 뒤에도 워커 행이 `in_progress`로 남은 카드는 이 조건을 영영 채우지 못한다.
3. **대상이 0이면 아무도 깨지 않는다.** precheck가 대상 0으로 끝나면 automation은 `skipped_precheck`로 끝난다. 최근 100회 중 74회가 그랬고, 정리가 멈춘 것이 어디에도 드러나지 않았다.
4. **스택은 소유 카드의 PR을 기다린다.** `orch heavy`가 등록한 verify 스택은 카드 PR이 머지되거나 worktree가 없어져야 내려간다. 그래서 1~2가 막히면 스택도 함께 남는다. 등록된 스택 38개 중 janitor가 내린 것은 0개였다.

reap-hard는 이 네 가지를 우회하지 않고 대체한다. 등록 여부를 묻지 않고 `~/orca/workspaces` 아래 Orca 카드 전부를 같은 규칙으로 보고, 세션에게 묻지 않고 직접 닫는다. 마지막 실행 시각은 정기 보고(`orch brief`)에 나온다.

세션은 자기가 띄운 스택과 자기 터미널까지 정리하고, 끝난 카드의 worktree와 나머지는 reap-hard와 janitor가 거둔다(`end-session` "Who cleans up").

## 규칙

한 회차는 아래 순서로 돈다. 뒤 단계는 앞 단계에서 닫은 것을 반영해 판정한다.

`orca worktree rm`이 권한 계층에 거부돼 `rm_refused.py add`로 기록된 카드는 이 표의 어느 조건을 채워도 닫지 않는다. 판정 이유에 `사람에게 넘김`과 사람이 실행할 `orca worktree rm` 한 줄이 나온다. 기록을 읽지 못하면 카드를 남긴다.

| 대상 | 닫는 조건 | 동작 |
|---|---|---|
| 카드 | 그 브랜치의 자기 PR(포크 제외)이 MERGED/CLOSED이고 PR head가 worktree HEAD와 같다. 열린 PR이 없다. 변경 없음(추적 안 되는 파일 포함). 세션 기록이 `idle_minutes`(30분) 넘게 조용하고, Orca 터미널 출력도 그만큼 없다. 세션 기록이 있는 Claude 터미널은 출력 기준이 5분이다. 사람이 띄운 프로세스가 없다. 카드 안 Claude 세션이 돌리는 셸이 없다 | 카드 잠금(janitor와 공유)을 잡고 다시 판정한 뒤 공용 가드(`cardguard.guard`)를 통과하면, 판정 때 본 터미널만 `orca terminal close --terminal H`로 닫는다. 가드를 한 번 더 통과하면 ignored 파일은 휴지통, `orca worktree rm --run-hooks` |
| 스택 | compose가 만든 컨테이너의 작업 폴더(`com.docker.compose.project.working_dir`)가 모두 없거나 이번 회차에 닫은 카드다 | `docker compose -p P down`. `-v`·prune·볼륨·이미지 삭제는 하지 않는다 |
| 리스너 | ppid 1인 node·bun·deno·vite가 TCP LISTEN 중이고, `idle_minutes`보다 오래됐고, cwd가 지워졌거나 cwd의 카드가 없다 | 그 pid에만 SIGTERM. pid와 시작 시각을 다시 확인한다 |
| serve | `tailscale serve` 항목의 로컬 포트가 미리보기 범위(`serve_ranges`, 기본 4500–4599·32768–60999)에 있고 두 회차 연속 리스너가 없다 | `tailscale serve --https=<port> off`(또는 `--tcp`) |
| 컨테이너 | 이름이 `reap.stopped_containers.prefixes`로 시작하고, compose가 만들지 않았고, `exited`·`created`이고, 멈춘 지 `reap.hours`(6시간)가 지났다 | `docker rm <전체 ID>`. `-f`·`-v` 없음 |
| 볼륨 | `<namespace>.origin` 라벨 값이 `reap.labeled_volumes.origins`에 있고, 어떤 상태의 컨테이너도 참조하지 않고, 그 compose 프로젝트의 컨테이너가 없고, 만든 지 `reap.hours`가 지났고, 소유 카드가 닫혔다 | `docker volume rm <이름>`. `-f` 없음 |

컨테이너와 볼륨 규칙은 설정 키가 없으면 꺼진다. 판정은 janitor의 `stopped-container`·`labeled-volume`과 같은 코드(`reap.leftovers`)이고, 상세와 라벨 규약은 `reap-resources` 스킬에 있다. 볼륨은 소유 스택의 ledger 기록이 있고, 기록에 나온 worktree가 디스크와 `orca worktree list`에서 모두 사라지고, 연결된 PR이 열려 있거나 읽지 못하는 상태가 아닐 때만 지운다. 카드 목록을 읽지 못하거나 기록이 없으면 지우지 않고 이유와 함께 남긴다. reap-hard는 회차마다(dry-run 제외) 스택 컨테이너의 작업 폴더가 속한 카드를 ledger에 `stack-seen` 줄로 적는다. 이미 기록된 이름을 다른 카드가 회차 사이에 띄웠다가 내리면 기록이 남지 않아, 첫 카드가 닫혔을 때 볼륨이 지워질 수 있다. 처리 직전에 모든 컨테이너를 다시 읽어 나이를 뺀 규칙을 다시 돌리고, 대상을 하나씩 전체 ID나 이름으로 지운다. prune, `down -v`, 필터·라벨·패턴 단위 삭제는 없다. 라벨 없는 볼륨은 어떤 규칙의 대상도 아니다.

PR head와 HEAD가 같아야 한다는 조건은 리드가 머지된 브랜치 이름을 다시 쓰는 경우를 거른다. PR이 없는 카드는 닫지 않는다. 리드, 조사 카드, 막 만든 카드도 PR이 없기 때문이다. 카드 안에 살아 있는 Claude 세션이 리드나 오케스트레이터면, 얼마나 조용했든 닫지 않는다. 리드는 inbox를 기다리며 몇 시간씩 조용한 것이 정상이기 때문이다. 판별 근거는 둘 중 하나다. `--agent agent-skills:lead|orchestrator`(또는 `--name lead:<slug>`)로 시작했거나, 그 프로세스가 시작된 뒤 이 카드의 세션 기록을 쓴 세션이 `ROLE: lead|orchestrator`를 선언했다(reorient hook의 `roles/<session>.json`). 판별에 필요한 것을 못 읽어도 남긴다. 예를 들어 카드 안 프로세스의 행이 없거나, role 기록이 손상됐거나, role 기록이나 세션 기록 폴더에 권한이 없는 경우다. role 기록 파일이 아예 없는 경우만 "역할 없음"으로 본다. Orca에 하위 카드가 남아 있는 카드(리드)도 닫지 않는다.

idle 상태의 Claude 화면은 새 내용이 없어도 약 30분마다 터미널에 출력한다. 그래서 Claude 터미널은 세션 기록으로 조용한지를 보고, 터미널 출력은 일하는 중인지만 가린다. 일하는 Claude는 스피너 때문에 초 단위로 출력하므로 5분 창으로 충분하다. 세션 기록을 모르거나 Claude가 아닌 터미널은 출력 30분 기준을 그대로 쓴다. idle 세션은 시각 없는 줄도 세션 기록에 덧붙여 파일 수정 시각을 바꾼다. 그래서 세션 기록이 조용한지는 끝부분 256KB에서 가장 최근의 `timestamp`로 본다. 시각이 있는 줄이 없으면 수정 시각을 쓴다. 세션 기록이나 그 폴더를 못 읽거나 가장 최근 `timestamp`를 해석하지 못하면 남긴다. 권한이 없어 목록을 못 읽는 폴더도 "세션 기록 없음"이 아니라 못 읽음으로 본다. 더 오래된 줄로 내려가면 실제보다 조용하게 읽히기 때문이다.

조용해도 자기 백그라운드 작업을 기다리는 세션은 끝난 것이 아니다. Bash 호출, 백그라운드 셸, Monitor는 모두 Claude 프로세스의 자식 셸이다. 그래서 카드 안 Claude 프로세스에 자식 셸이 하나라도 있으면 남긴다. MCP 서버나 caffeinate 같은 셸 아닌 자식은 세션이 떠 있는 동안 늘 있으므로 보지 않는다. 서브에이전트는 프로세스가 아니라서 이 검사로는 안 보인다. 하지만 일하는 동안 `subagents/` 아래 세션 기록에 시각이 있는 줄을 쓰므로 세션 기록 기준에서 걸린다. 화면의 'N shells still running' 같은 표시는 읽지 않는다. Orca의 터미널 미리보기는 화면 일부가 뒤섞인 조각이라 믿을 수 없다.

카드는 닫기 직전에 판정을 처음부터 다시 한다. 스캔 뒤 PR이 다시 열렸거나, 세션이 움직였거나, 사람이 명령을 띄웠거나, 판정 때 없던 터미널이 열렸으면 남긴다. 스택도 내리기 직전에 컨테이너를 다시 읽어 같은 기준으로 판정하고, 그 사이 스택이 없어졌거나 작업 폴더가 다시 생겼거나 보호 대상이 됐으면 남긴다. 스택과 리스너는 실제로 닫힌 카드만 "없어진 카드"로 친다. 그래서 건너뛰거나 실패한 카드의 스택은 내리지 않는다. 스택은 작업 폴더 라벨이 없는 컨테이너가 하나라도 있거나, 라벨이 상대 경로이거나, 보호 경로 아래에 있으면 남긴다. serve는 `공개 포트 → 로컬 포트` 짝이 두 회차 연속 그대로이고 그 로컬 포트에 연결이 안 될 때만 끈다. 연결 확인은 소유자와 상관없이 실제로 접속해 본다. 끄기 직전에 설정과 연결을 다시 확인한다.

## 대상 잠금과 공통 검사

reap-hard, janitor의 `reap.py reap --plan`, `cards.py apply`, preview-watch의 serve off는 대상 하나를 처리하기 전에 같은 절차를 거친다.

1. **대상 잠금.** `<state>/locks/` 아래 대상별 파일을 flock으로 잡는다. 기다리지 않는다. 다른 정리 주체가 잡고 있으면 그 대상은 이번에 건너뛴다. 여러 개를 잡을 때는 늘 카드 → compose 프로젝트 → 컨테이너 → 볼륨 → 프로세스 → serve 포트 → 그 밖의 순서로 잡아서 서로 기다리며 멈추지 않는다. 하나라도 못 잡으면 이미 잡은 것도 바로 놓는다.
2. **소유권.** `<state>/sweep-owner.json`(`{"generation": N, "rules": {"<규칙>": "sweep"}, "interval": 초}`)을 다시 읽는다. 파일이 없으면 모든 규칙이 지금 주체의 것이다. sweep이 가진 규칙은 옛 주체가 건너뛴다. 단 `<state>/sweep-last.json`의 마지막 정상 회차가 세 주기보다 오래됐으면 옛 주체가 대신 처리하고 메모를 남긴다. 파일을 읽지 못하면 그 회차의 옛 주체는 아무것도 하지 않는다.
3. **공통 검사.** 남길 이유만 찾고, 못 읽은 것도 남길 이유다. 프로젝트·포트·경로·pid는 보호 목록과 상시 서비스를 보고, 프로세스는 같은 pid·시작 시각인지와 트리에 사람이 띄운 셸이 있는지를 본다. 카드는 각 주체가 처리하면서 `cardguard.guard()`를 거친다. 이 검사는 순찰 판정(작업 중·사람 대기·Run 워커, `activity`의 parked·unknown), HEAD, 판정 뒤 열린 터미널, Orca agent-wait, 에이전트 화면의 질문이나 못 읽은 화면, 스스로 깨어날 장치를 건 세션을 본다. 카드 잠금은 `cardguard.card_lock()`과 같은 파일이다. 같은 프로세스가 이미 잡았으면 다시 잡지 않고 넘어간다.

잠금은 두 주체가 같은 순간에 처리하는 것만 막는다. 이미 처리된 대상을 다시 처리하지 않게 하는 것은 잠금 안에서 하는 재판정이다. janitor와 pressure는 보호 목록 파일이 없으면 지금처럼 빈 목록으로 보고, 못 읽는 줄이 있으면 멈춘다.

## sweep 그림자 실행

정리 v2의 정리 주체 `skills/reap-resources/scripts/sweep.py`는 지금 그림자 단계다. reap-hard가 dry-run이 아닌 회차를 마칠 때마다 `sweep.py run --shadow`를 따로 띄운다. 한 번에 하나만 돌고(`<state>/sweep.lock`), 10분 안에 못 끝나면 그만둔다.

sweep은 순찰 판정 파일의 가장 최근 회차만 읽는다. 그 회차가 끝났고(`complete`), 한 시간 안의 것이고, 아직 판정하지 않았을 때만이다. 거부한 회차는 판정한 것으로 치지 않으므로 다음 실행이 다시 본다. 그 회차의 `candidates`는 다음 순서로 거른다.

1. 기댄 소스를 이번 회차에 읽었는지
2. pressure 회차 전용 후보인지
3. 연속 실패로 쉬는 중인지
4. 회차 상한(`sweep.max_per_round`, 기본 50)을 넘었는지
5. 공통 검사에 걸리는지

결과로 "처리하겠다"와 "남긴다"를 `<state>/sweep-shadow.jsonl`에 남긴다. 아무것도 닫거나 지우지 않는다.

- 대상 잠금은 실제 모드에서만 잡는다. 그림자는 잠금을 잡지 않으므로 옛 주체가 그 때문에 대상을 건너뛰는 일이 없다.
- 그림자는 모든 규칙을 자기가 가진 것처럼 판정하고, 지금 누가 가졌는지를 함께 적는다.
- 보호 목록 파일이 없거나 못 읽으면 reap-hard처럼 회차 전체를 거부한다.
- `run`에 `--shadow`가 없으면 거부한다.

`python3 skills/reap-resources/scripts/sweep.py diff --hours 48`은 그림자가 회차마다 처리하겠다고 한 대상을 옛 주체의 실제 처리와 맞춘다.

- 옛 주체 기록: `reap-hard.jsonl`(처리 시각 `t`와 identity가 새로 붙는다), 그리고 `reap --plan`·`cards.py apply`가 새로 남기는 `reap-actions.jsonl`(규칙, identity, 실제로 뭔가 했는지).
- 일치 조건: 그 후보를 낸 스캔이 시작한 때(후보의 `seen`)부터 회차 뒤 규칙별 지연까지 끝난 처리 중에, 같은 효과의 처리가 같은 대상에 있어야 한다. 그보다 먼저 끝난 처리는 치지 않는다. 스캔이 그 뒤에 대상을 봤으므로, 먼저 끝난 처리는 그 전에 있던 것(같은 경로에 다시 만든 카드라면 옛 카드)에 한 것이다. `seen`이 없는 옛 판정 파일은 후보 스캔 시작(`candidates_t`)이나 회차 시작부터 센다. 지연은 reap-hard 규칙이 reap-hard 주기(`interval`)의 두 배에 10분을 더한 값이고, janitor 규칙은 4시간이다.
  - 같은 효과: 처리 결과 문장으로 실제로 한 일을 가른다. 카드 삭제(`card:strict`·`card:landed`), 카드 터미널 닫기(`card:close-idle`, 삭제를 건너뛰고 터미널만 닫은 경우 포함), 그 밖에는 같은 규칙이다. 터미널 닫기는 그림자가 닫으려던 터미널이 옛 주체가 닫을 수 있던 목록에 모두 있어야 한다.
  - 같은 대상: 그림자 계획이 지닌 identity(HEAD와 카드 폴더 inode, 프로그램 폴더의 실제 경로, pid·시작 시각, serve의 포트 짝, state-file의 파일 목록 등)가 처리 기록에 모두 있고 같아야 한다. identity가 없는 옛 기록은 증거가 되지 않는다. 처리가 도중에 실패해도 실제로 닫은 터미널은 닫기 증거로 친다.
- 일치하지 않는 경우: 지연이 지나도 처리가 없는 회차가 하나라도 있으면 그 대상은 넘침이다. 아직 지연 안이면 대기다.
- 한계: 카드가 아닌 경로 대상(tmpdir, 증거 폴더 같은 것)은 identity가 경로뿐이다. 그래서 한 스캔이 도는 몇 초 사이에 다른 주체가 같은 경로를 지우고 다시 만들면, 옛 것을 지운 처리가 새 것의 계획과 일치할 수 있다. 이 비교는 보고일 뿐이라 정리 동작에는 영향이 없다. 이 경우는 실제 모드 전환(PR D)에서 처리 직전 재판정이 막는다.

판정한 회차가 없거나, 판정한 회차 없이 2시간 넘게 빈 구간이 있으면 `아직 판정 못 함`(exit 3)이다. 넘침이 있으면 exit 1, 통과면 exit 0이다. 전환(PR D) 전에는 48시간 넘게 넘침이 0이어야 한다.

## 보호

- 회차마다 `~/.config/agent-skills/reap-protect.txt`를 먼저 읽는다. 파일이 없거나 못 읽는 줄이 있으면 아무것도 하지 않고 exit 2로 끝나며, 정기 보고에 "멈춤"으로 나온다.

  ```text
  # port N | pid N | path P (카드나 상위 경로) | project NAME (compose 프로젝트)
  port 4999
  pid 12345
  path /Users/me/orca/workspaces/app/lead-x
  project my-always-on-stack
  ```

- 보호 포트를 LISTEN하는 프로세스나 보호 pid가 들어 있는 카드, 보호 포트를 publish하는 스택은 남긴다. 고정(pin)된 카드, 메인 체크아웃, `reap.pressure.always_on`의 compose 프로젝트도 남긴다.
- **사람이 띄운 프로세스**: 프로세스에서 부모 쪽으로 올라가다 Claude 세션보다 로그인 셸(`-zsh`, Orca·Terminal이 띄우는 셸)이나 tmux·screen·sshd 바로 아래의 셸을 먼저 만나면 사람의 것이다. reap-hard는 그런 프로세스가 있는 카드를 닫지 않는다. 같은 규칙이 `reap.py`의 orphan(pressure 포함)·gui-app kill에도 들어 있어서, 트리에 사람의 셸이나 그 자식이 있는 orphan은 통째로 남는다. 판정 뒤 kill 직전에 트리를 다시 읽는다.
- 한계: 사람이 띄운 서버라도 셸이 먼저 끝나 pid 1로 넘어간 뒤에는 부모 체인이 남지 않아 사람의 것으로 알아볼 수 없다. 그런 서버는 cwd가 지워졌거나 cwd의 카드가 없어진 경우에만 SIGTERM 대상이 된다. 보호 목록의 `path`·`port`·`pid`로 남길 수 있다.

## 실행과 기록

```bash
bin/reap-hard run --dry-run   # 판정과 로그만. 아무것도 건드리지 않는다
bin/reap-hard run             # 한 회차(인자 없이 부르면 usage만 출력하고 exit 2)
bin/reap-hard last            # 마지막 회차 한 줄
bin/reap-hard install         # 메인 체크아웃에서. status, uninstall
```

- `skills-sync setup` 목록에는 기본 off로 있다. 보호 목록 파일을 쓴 사람이 `skills-sync setup enable reap-hard`로 직접 건다. `apply`가 새 PC에서 묻지 않고 카드를 지우는 정리를 켜지 않게 하려는 것이다([automation.md](automation.md#처음-설치할-때)). `reap-hard install`(LaunchAgent)은 남아 있지만 setup이 쓰지 않는다.

- 동작한 것(또는 dry-run에서 동작했을 것)과 실패는 `~/.local/state/agent-skills/reap-hard.jsonl`에 한 줄씩 남는다. 회차 요약은 `reap-hard-last.json`에 남고, `orch brief`가 이 파일을 읽어 `🧹 reap-hard 14:07(3분 전) 닫음 2 · 실패 0 · 남김 41`처럼 보여 준다. 주기의 세 배가 지나도록 새 회차가 없으면 경고를 붙인다.
- 같은 시각에 회차가 겹치면 뒤에 시작한 쪽이 바로 끝난다.
- 회차의 출력은 `reap-hard.log`에, 성공·실패 한 줄은 `jobs/reap-hard.json`에 남는다.

## 설정

`~/.claude/agent-skills.json`의 `"reap-hard"` 키에 둔다. 모두 선택이다. 컨테이너·볼륨 규칙의 키(`reap.stopped_containers`, `reap.labeled_volumes`, `reap.hours`)는 janitor와 같이 `"reap"` 아래에 있다.

```json
{"reap-hard": {"interval": 900, "idle_minutes": 30, "workspaces": "~/orca/workspaces",
               "protect_file": "~/.config/agent-skills/reap-protect.txt",
               "serve_ranges": [[4500, 4599], [32768, 60999]]}}
```

## 세션 순찰과의 연결

회차를 마치면(dry-run이 아닐 때) `orch patrol --if-due`를 별도 프로세스로 띄운다. 순찰이 한 시간 안에 작업 중·사람 대기·Run 워커로 판정한 카드는 정리 대상에서 뺀다. 순찰 판정만으로 새로 닫히는 카드는 없다. 자세한 규칙은 [automation.md](automation.md)의 "세션 순찰". `reap-hard run --no-patrol`은 순찰을 띄우지 않는다.
