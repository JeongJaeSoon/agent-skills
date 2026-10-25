# reap-hard: 모델 없이 끝난 카드·스택·포트를 닫는 정리

`bin/reap-hard`는 규칙이 코드로 고정된 정리 스크립트다. LaunchAgent가 15분마다 돌리고, 모델이나 카드 세션의 판단을 기다리지 않는다.

## 왜 따로 두는가

janitor automation(`reap.py precheck`)은 30분마다 돌았지만 하루 넘게 대상 0개를 냈고, 그동안 머지된 카드가 남았다. 실측으로 확인한 원인은 다음과 같다.

1. **등록된 것만 본다.** janitor는 ledger에 있거나, 오케스트레이션 워커가 만들었거나, lineage가 `cli`인 카드만 본다. Orca 앱이나 다른 경로로 만든 카드(lineage `manual`·없음)는 판정 대상에 오르지 않았다. 측정 시점에 Orca 카드 42개 중 13개가 그랬고, 리드 카드 대부분이 여기에 들었다. dispatch-card의 `ledger add`는 2주 동안 5번 불렸다.
2. **닫기 요청이 한 번도 나가지 않았다.** 세션이 떠 있는 카드는 `done-card`로 그 세션에게 닫아 달라고 한 번 묻는다. 그런데 이 요청은 PR 머지, 워커 턴 없음, `cli` 카드면 6시간 조용, 그 뒤 2시간 더 조용이라는 조건을 모두 채워야 나간다. ledger의 `done-card` 기록은 0건이었다. 머지 뒤에도 워커 행이 `in_progress`로 남은 카드는 이 조건을 영영 채우지 못한다.
3. **대상이 0이면 아무도 깨지 않는다.** precheck가 대상 0으로 끝나면 automation은 `skipped_precheck`로 끝난다. 최근 100회 중 74회가 그랬고, 정리가 멈춘 것이 어디에도 드러나지 않았다.
4. **스택은 소유 카드의 PR을 기다린다.** `orch heavy`가 등록한 verify 스택은 카드 PR이 머지되거나 worktree가 없어져야 내려간다. 그래서 1~2가 막히면 스택도 함께 남는다. 등록된 스택 38개 중 janitor가 내린 것은 0개였다.

reap-hard는 이 네 가지를 우회하지 않고 대체한다. 등록 여부를 묻지 않고 `~/orca/workspaces` 아래 Orca 카드 전부를 같은 규칙으로 보고, 세션에게 묻지 않고 직접 닫는다. 마지막 실행 시각은 정기 보고(`orch brief`)에 나온다.

## 규칙

한 회차는 아래 순서로 돈다. 뒤 단계는 앞 단계에서 닫은 것을 반영해 판정한다.

| 대상 | 닫는 조건 | 동작 |
|---|---|---|
| 카드 | 그 브랜치의 자기 PR(포크 제외)이 MERGED/CLOSED이고 PR head가 worktree HEAD와 같다. 열린 PR이 없다. 변경 없음(추적 안 되는 파일 포함). 모든 Orca 터미널 출력과 세션 기록이 `idle_minutes`(30분) 넘게 조용하다. 사람이 띄운 프로세스가 없다 | `orca terminal close --worktree path:P --all`, ignored 파일은 휴지통, `orca worktree rm --run-hooks` |
| 스택 | compose가 만든 컨테이너의 작업 폴더(`com.docker.compose.project.working_dir`)가 모두 없거나 이번 회차에 닫은 카드다 | `docker compose -p P down`. `-v`·prune·볼륨·이미지 삭제는 하지 않는다 |
| 리스너 | ppid 1인 node·bun·deno·vite가 TCP LISTEN 중이고, `idle_minutes`보다 오래됐고, cwd가 지워졌거나 cwd의 카드가 없다 | 그 pid에만 SIGTERM. pid와 시작 시각을 다시 확인한다 |
| serve | `tailscale serve` 항목의 로컬 포트가 미리보기 범위(`serve_ranges`, 기본 4500–4599·32768–60999)에 있고 두 회차 연속 리스너가 없다 | `tailscale serve --https=<port> off`(또는 `--tcp`) |

PR head와 HEAD가 같아야 한다는 조건은 리드가 머지된 브랜치 이름을 다시 쓰는 경우를 거른다. PR이 없는 카드는 닫지 않는다. 리드, 조사 카드, 막 만든 카드도 PR이 없기 때문이다. 카드 안에 살아 있는 Claude 세션이 리드나 오케스트레이터면, 얼마나 조용했든 닫지 않는다. 리드는 inbox를 기다리며 몇 시간씩 조용한 것이 정상이기 때문이다. 판별 근거는 둘 중 하나다. `--agent agent-skills:lead|orchestrator`(또는 `--name lead:<slug>`)로 시작했거나, 그 프로세스가 시작된 뒤 이 카드의 세션 기록을 쓴 세션이 `ROLE: lead|orchestrator`를 선언했다(reorient hook의 `roles/<session>.json`). Orca에 하위 카드가 남아 있는 카드(리드)도 닫지 않는다.

카드는 닫기 직전에 판정을 처음부터 다시 한다. 스캔 뒤 PR이 다시 열렸거나, 세션이 움직였거나, 사람이 명령을 띄웠으면 남긴다. 스택과 리스너는 실제로 닫힌 카드만 "없어진 카드"로 친다. 그래서 건너뛰거나 실패한 카드의 스택은 내리지 않는다. 스택은 작업 폴더 라벨이 없는 컨테이너가 하나라도 있거나, 라벨이 상대 경로이거나, 보호 경로 아래에 있으면 남긴다. serve는 `공개 포트 → 로컬 포트` 짝이 두 회차 연속 그대로이고 그 로컬 포트에 연결이 안 될 때만 끈다. 연결 확인은 소유자와 상관없이 실제로 접속해 본다. 끄기 직전에 설정과 연결을 다시 확인한다.

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

- 동작한 것(또는 dry-run에서 동작했을 것)과 실패는 `~/.local/state/agent-skills/reap-hard.jsonl`에 한 줄씩 남는다. 회차 요약은 `reap-hard-last.json`에 남고, `orch brief`가 이 파일을 읽어 `🧹 reap-hard 14:07(3분 전) 닫음 2 · 실패 0 · 남김 41`처럼 보여 준다. 주기의 세 배가 지나도록 새 회차가 없으면 경고를 붙인다.
- 같은 시각에 회차가 겹치면 뒤에 시작한 쪽이 바로 끝난다.
- LaunchAgent의 출력은 `reap-hard.log`에 남는다.

## 설정

`~/.claude/agent-skills.json`의 `"reap-hard"` 키에 둔다. 모두 선택이다.

```json
{"reap-hard": {"interval": 900, "idle_minutes": 30, "workspaces": "~/orca/workspaces",
               "protect_file": "~/.config/agent-skills/reap-protect.txt",
               "serve_ranges": [[4500, 4599], [32768, 60999]]}}
```
