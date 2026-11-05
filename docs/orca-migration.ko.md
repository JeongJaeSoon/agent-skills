# 오케스트레이션 상태를 Orca로 옮기기 (2026-11-01)

이 릴리스부터 카드의 상태를 모델이 손으로 적은 줄이 아니라 Orca에서 읽는다. 무엇이 옮겨 갔는지, 릴리스 전에 시작한 세션이 어떻게 계속 도는지, 전환 순서와 되돌리는 법을 적는다.

## 옮겨 간 것

| 전 | 이제 |
|---|---|
| 카드마다 오케스트레이터가 적는 board doing 줄 | 카드의 Orca worktree 칸(`workspaceStatus`, `linkedPR`, `linkedItems`)과, 있으면 Run의 Task. doing 줄은 카드가 없는 대기에만 쓴다 |
| 리드마다 `orch board lead add` | 리드 행은 세션에서 만든다. fleet이 못 보는 리드에만 `lead add`를 쓰고, fleet이 보는 리드는 거부한다(2026-11-04) |
| 카드마다 `orch record spawned` | 원장은 Orca의 Task·Dispatch를 읽는다. `spawned`는 하위 단위 티켓에만 쓴다 |
| 카드마다 `orch sent add` | Sent 표는 Run의 Task에서 만든다. `orch sent`는 Orca 밖 경로만 적는다 |
| `orca orchestration send`·`reply`마다 `orch sent add` (2026-11-02) | Sent 표가 Orca inbox를 읽는다. heartbeat만 뺀다 |
| 화면으로 대기 판정 | Orca `agentWait`를 먼저 보고, Orca가 보지 않았을 때만 화면을 본다 |
| 감독 카드도 일반 worktree | `REPORT`가 `run:<id>`인 카드는 `skills-sync card`가 `worker-start`로 띄운다 |
| 카드 코멘트에 적던 티켓·PR 링크 | `--reference <URL>`로 Orca 링크에 저장한다 |
| 같은 질문에 결정 기록과 gate 둘 다 | Task를 막는 결정이면 gate, 아니면 `orch decide` |

무거운 일, 보류, 요청, 단계표, 할 일·지금 목록, 브리프 렌더러, heartbeat, 수집기의 부수 효과는 orch에 남는다.

## 이전 세션과 함께 돌 때

- **릴리스 전에 띄운 카드**는 자기가 읽어 들인 코드로 계속 돈다. 그 카드의 doing 줄은 `orch migrate --apply`나 tidy가 닫을 때까지 열려 있다. tidy는 카드를 표시 이름이나 worktree 디렉터리 이름으로 대소문자 없이 찾고, Orca의 `linkedPR`이 merged인 것도 머지 근거로 받는다. 티켓 ID만, 브랜치 이름은 키가 아니어서 부모 티켓을 적은 후속 줄은 열린 채 남는다.
- **릴리스 전에 띄운 리드**는 여전히 `orch sent add`를 할 수 있다. 보드가 `agent-skills.db`로 옮겨 간 뒤에는 `orch board lead add`가 거부되고, 메시지가 대신 행을 잇는 `orch board set` 줄을 알려 준다.
- **`term_` 주소로 보고하는 브리프**는 새 `skills-sync card`가 거부한다(종료 코드 2). Run 밖 터미널로는 `worker_done`이 가지 않기 때문이다. 그 줄을 `run:<id>`로 바꾸거나, 빼서 카드 자기 대화창에 보고하게 한다.
- **`agentWait`를 주지 않는 Orca**에서는 예전처럼 화면을 읽는다.
- 옛 코드와 새 코드는 같은 파일을 읽고 쓴다. `orch migrate`는 보통의 tidy 줄만 더하므로 옛 코드도 그 줄을 읽는다.

## 전환 순서

머지나 카드 시작이 진행 중이지 않을 때 한다. 아래 어느 단계도 돌고 있는 카드를 건드리지 않는다.

1. 로드되는 메인 체크아웃을 릴리스까지 fast-forward 한다([platform.md](platform.md)).
2. 오케스트레이터, 그다음 각 리드에서 턴이 끝난 때 `/reload-plugins`. 돌고 있는 카드는 할 일이 없다.
3. 오케스트레이터에서 `orch doctor`. `이 터미널은 Run에 묶여 있지 않다`고 답할 때만 이전 브리프들이 보고하던 Run으로 `orca orchestration run-use --id <id>`. Run이 하나도 없을 때만 새로 만든다. `orca orchestration run-current --json`을 직접 읽을 때는 `result.runId`나 `result.run.id`가 있으면 묶여 있는 것이다.
4. `orch migrate`(dry-run). 첫 목록(카드 하나를 가리키는 doing 줄)을 확인한다. 사람을 기다리는 줄, 오너의 줄, 오너가 다시 연 줄, 카드는 머지됐지만 남은 단계(릴리스, 배포)를 적은 줄은 들어 있지 않다. 마지막 것은 따로 나열되고 그대로 남는다.
5. `orch migrate --apply`. 출력되는 백업 디렉터리와 `orch board reopen` 줄을 적어 둔다.
6. 대시보드를 확인한다. Sessions 표에 Orca에서 온 Ticket·PR 칸이 보이고, board의 열린 doing 목록에는 남은 대기만 있다.

## 되돌리기

- **상태:** `orch migrate --restore <백업 디렉터리>`는 그 실행이 닫은 줄을 다시 열고, 그 뒤 오너가 바꾼 것은 그대로 둔다. 옛 코드가 로드된 상태라면 `--apply`가 출력한 `orch board reopen` 줄을 대신 돌린다. 백업에는 실행 전의 `agent-skills.db` 사본이 0600 파일로 있다.
- **코드:** 로드되는 체크아웃을 이전 릴리스 태그로 돌리고 다시 `/reload-plugins`.
- **Orca 값:** `workspaceStatus`는 카드가 자기 칸에만 쓰고(PR을 열면 `in-review`, 닫을 때 `completed`), 링크는 새 코드가 띄운 카드에만 붙는다. 상태를 되돌리려면 `orca worktree set --worktree <sel> --workspace-status in-progress`.

# 결정을 agent-skills.db로 옮기기 (2026-11-04)

이 릴리스부터 `orch decide`와 대시보드의 답은 결정을 `decisions.json`이 아니라 fleet 상태 디렉터리(`$ORCH_FLEET_STATE`)의 SQLite 파일 `agent-skills.db`에 둔다. 보드와 tidy 파일은 다음 절에서 같은 파일로 옮긴다.

## 바뀌는 것

- 닫힌 결정을 7일 뒤에 지우지 않는다. 테이블에 남고, 읽는 쪽이 상태로 거른다.
- 결정을 바꿀 때마다 바뀌기 전후 값을 같은 트랜잭션에서 `event` 테이블에 남긴다.
- 파일은 `orch db import`만 만든다. 그 전에는 `orch decide`가 `orch db cutover-plan 4a`를 안내하고 멈추며, decision hook은 아무것도 하지 않고, brief headline에 `이행 대기: 결정 DB`가 보인다.
- 표를 볼 때는 `orch db dump [table]`(JSON 출력)를 쓴다. 파일을 직접 열 때는 `sqlite3 -readonly`로만 연다.
- DB 사본은 `cp`가 아니라 `orch db backup <file>`로 만든다. `-wal` 없이 복사한 파일은 마지막 쓰기를 잃을 수 있다. 상태 디렉터리는 동기화 폴더가 아니라 로컬 디스크에 둔다.

## 전환 순서

옛 코드는 여전히 `decisions.json`에 쓰므로 데이터를 옮기는 동안 돌면 안 된다. `orch db cutover-plan 4a`가 아래 단계를 이 PC에서 칠 명령으로 출력한다. 그 명령은 아무것도 실행하지 않는다.

1. 대시보드 서버를 멈춘다(페이지의 답을 전달하는 곳이다). macOS에서는 그 LaunchAgent를 `launchctl bootout`한다.
2. 로드되는 메인 체크아웃을 fast-forward한다.
3. 살아 있는 Claude 세션을 모두 끝내고 새 프로세스로 다시 띄운다(`/reload-plugins`와 `/restart`는 pid와 시작 시각이 그대로라 통과하지 못한다). 세션마다 자기가 로드한 decision hook을 부르기 때문이다.
4. `orch db import --dry-run`으로 옮길 결정 수와 `--apply`가 거부할지를 본다.
5. `orch db import --apply`. 다음 중 하나라도 있으면 거부한다. 다른 체크아웃의 스크립트·래퍼·hook을 실행 중이거나 fast-forward 전부터 그것들을 실행해 온 프로세스, 어느 Claude home에서든 그 전에 시작한 살아 있는 세션, 다른 커밋의 사본을 로드하는 plugin이나 `--plugin-dir`로 다른 체크아웃을 로드한 세션, 아직 로드된 LaunchAgent(pid가 없어도)로서 이 셸의 label을 쓰는 대시보드이거나 `~/Library/LaunchAgents`에서 agent-skills의 스크립트·래퍼·hook을 실행하는 것, 그것들을 실행하는 주석 처리되지 않은 crontab 줄(갱신된 체크아웃을 가리켜도 해당하니 전환하는 동안 주석 처리한다). 확인하지 못할 때도 거부한다: `ps`나 `crontab -l`이 실패했을 때(crontab이 없는 사용자나 `crontab` 명령이 없는 경우는 괜찮다), 프로세스의 디렉터리나 체크아웃의 갱신 시각을 읽지 못했을 때. 통과하면 board·tidy·decision 잠금을 잡고, `decisions.json`을 sha256과 함께 `backup-db-<시각>/`(0700, 파일 0600)에 복사하고, 임시 이름으로 DB를 만들어 검사한 뒤 제자리로 rename하고, `decisions.json` 자리에 빈 디렉터리를 남겨 옛 코드의 쓰기가 실패하게 한다.
6. 대시보드 서버를 다시 올린다(macOS는 `orch-dash install-agent`).

`--apply`는 다시 돌려도 안전하다. import가 커밋됐지만 빈 디렉터리를 아직 놓지 못했다면, 다시 돌렸을 때 `decisions.json`의 sha256이 import 때와 같을 때만 빈 디렉터리를 놓는다. 그 사이 파일이 바뀌었으면 충돌을 보이고 멈추며 아무것도 바꾸지 않는다. 전환 뒤 빈 디렉터리 자리에 파일이 생기면 brief에 `옛 코드가 쓴 흔적`이 보인다.

## 되돌리기 (결정)

1. 전환 순서 1단계처럼 writer를 멈추고 `orch db backup <file>`. 옛 형식에 자리가 없는 이벤트 이력까지 모두 남는다.
2. `orch db export --legacy --target pre-4a <dir>`가 옛 코드가 읽는 `decisions.json`을 쓰고, WAL checkpoint가 됐는지 알린다. busy라고 나오면 아직 열린 연결이 있으니 먼저 멈춘다.
3. `agent-skills.db`를 `-wal`·`-shm`과 함께 상태 디렉터리 밖으로 옮기고, 빈 `decisions.json` 디렉터리를 지운 뒤 `<dir>/decisions.json`을 그 자리에 둔다.
4. 이전 릴리스로 되돌리고 fast-forward한 뒤 writer를 다시 올린다.

다른 PC로 옮길 때는 원래 PC에서 `orch db backup <file>`, 새 PC에서 `orch db restore <file>`. `restore`도 `--apply`와 같은 검사를 하고, 새 상태 디렉터리에 `decisions.json`·`board.json`·`tidy.jsonl` 파일이 있으면 거부한다.

# 보드를 agent-skills.db로 옮기기 (2026-11-04)

이 릴리스부터 보드, 할 일·보류 목록, 리드, 모든 닫기 기록도 `board.json`·`tidy.jsonl`이 아니라 `agent-skills.db`에 둔다. 결정을 아직 옮기지 않은 PC는 한 번의 실행으로 두 단계를 함께 한다.

## 바뀌는 것

- doing·보류·할 일·지금 목록의 항목마다 안정 id `b<N>`이 붙는다. 문구를 고치거나 목록을 옮겨도 그대로이고, 다른 일에 다시 쓰이지 않는다. 옛 `doing:`·`later:`·`do:`·`todo:` id는 import 때 가리키던 항목을 계속 찾는다.
- 목록에서 뺀 항목은 행이 `dropped`로 남고, 마지막 값은 `event`에 있다.
- `board.prev.json`과 `tidy.jsonl.1`은 없어진다. 바뀌기 전후 값은 `event`에 있고, 두 파일은 import가 백업으로 옮긴다.
- 살아 있는 카드나 subagent를 가리키는 제목의 `orch board doing add`는 거부한다. 그것들은 자기 행이 있기 때문이다. `--force --why <이유>`를 주면 추가하고 이유를 이벤트에 남긴다.
- 카드에 이어진 행의 `orch board set --pr`와 다른 카드를 가리키는 `--worktree`는 거부한다. 행은 링크를 유지하고 Orca의 PR을 보여 준다. 이어지지 않은 행은 `--worktree`로 한 번 이을 수 있다.
- Orca가 아는 리드의 `orch board lead add`는 거부한다. 행을 이으려면 `orch board set <lead> --kind lead --repo <repo> --worktree <worktree>`를 쓴다. Orca가 못 보는 리드에는 `lead add`를 그대로 쓴다.
- 인박스 항목을 닫아도 `inbox-external.jsonl`에 쓰지 않는다. 닫기 기록이 본 줄을 적어 두므로 그 키의 새 줄이 생기면 그 줄이 결정하고, 다시 열 때 새 줄이 필요 없다. 파일이 다시 쓰여 그 줄이 없어지면 마지막 줄이 결정하고 brief headline에 `인박스 기록 어긋남`이 보인다.
- `orch db import` 전에는 보드 명령이 `orch db migrate`(메인 체크아웃 절대 경로)를 안내하고 멈추며, `orch board open`도 같은 줄을 stderr에 보이고, brief headline에 `이행 대기: 보드·결정 DB`가 보이고 행은 모두 ❔로 나온다.

## 한 줄로 전환 (`orch db migrate`)

오너가 일반 터미널에서 메인 체크아웃의 `bin/orch db migrate`를 친다(경로는 `orch db cutover-plan 4b`와 보드 명령의 안내 줄에 절대 경로로 나온다). Claude 세션 안에서는 거부한다. 멈추고 켜는 일은 오너의 몫이다. 흐름은 이렇다.

1. 이미 이행됐으면 "already migrated"라고 하고 아무것도 바꾸지 않는다. 몇 번을 돌려도 같다. 이행 뒤 옛 코드가 파일을 다시 만들었으면 그렇게 말하고 멈춘다.
2. 서비스를 멈춘다. 대시보드 LaunchAgent, agent-skills-sync, `ensure`로 뜬 대시보드 서버, janitor와 watch-prs 자동화다. 멈춘 것은 `cutover-recover.json`에 먼저 적어 둔다. LaunchAgent plist의 `ORCH_*`·`PROGRAMS_HOME` 값은 읽기만 하고(셸에 없을 때 이행 중 env로 쓴다, plist는 고치지 않는다), 셸의 값과 다르면 거부한다.
3. 거부 사유가 남아 있으면(옛 코드를 도는 세션·프로세스·cron 줄) 표로 보이고, 아무것도 이행하지 않은 채 멈춘 서비스를 되살린다. 세션은 `/reload-plugins`나 `/restart`로는 통과하지 못한다. 끝내고 새로 시작한 세션만 통과한다.
4. `orch db import`와 같은 이행을 하고, 결과를 `migration` 단계와 `orch board show`로 확인한다(`orch board open`은 이행 전에도 정상 종료해서 확인이 되지 않는다).
5. 끝나거나 실패하거나 중간에 죽어도 멈춘 것을 되살린다. LaunchAgent는 `install-agent`로 plist를 다시 쓰지 않고 있던 plist를 그대로 `launchctl bootstrap`하고, 꺼져 있던 자동화는 켜지 않는다. 되살리지 못한 것은 `cutover-recover.json`에 남아 다음 실행이 먼저 되살린다.
6. 이행 중에는 `cutover.lock`이 잡혀 있어, 재시작한 세션의 `orch wait`이나 `ensure`가 대시보드 서버를 되살리지 못한다.

`orch db migrate --check`는 무엇을 멈출지와 무엇이 막는지를 보이고 아무것도 쓰지 않는다. 다른 PC는 메인을 당기면(agent-skills-sync가 15분 안에 당긴다) 보드 명령과 brief가 이 한 줄을 안내한다. 새 PC는 `skills-sync setup`이 `orch db init`을 돌려 빈 DB를 만들므로 이행이 필요 없다. 이미 상태 파일이 있는 PC에서는 아무것도 하지 않는다.

## 손으로 전환 순서

`orch db cutover-plan 4b`가 아래 단계를 이 PC에서 칠 명령으로 출력한다. 그 명령은 아무것도 실행하지 않는다. 위 한 줄이 이 순서를 대신한다.

1. 대시보드 서버를 멈추고 janitor를 끈다. macOS에서는 LaunchAgent를 `launchctl bootout`하고 `skills-sync setup disable janitor`.
2. 로드되는 메인 체크아웃을 fast-forward한다.
3. 살아 있는 Claude 세션을 모두 끝내고 새 프로세스로 다시 띄운다. `/reload-plugins`와 `/restart`로는 통과하지 못한다.
4. `orch db import --dry-run`으로 옮길 내용을 본다. 목록별 항목 수, 닫기 기록, 행과 리드, Orca가 갖고 있어서 빠지는 리드와 행 PR, `--apply`가 거부할지.
5. `orch db import --apply`. 결정 단계와 같은 검사를 한다. 보드 백업은 `backup-db-*`로 가고 파일별 sha256은 `migration` 테이블에 남으며, `board.json`·`tidy.jsonl` 자리에 빈 디렉터리를 놓아 결정 릴리스보다 옛 코드의 쓰기가 실패하게 한다. 결정 릴리스의 코드는 `4b-board` 행을 보고 멈춘다.
6. 대시보드 서버와 janitor를 다시 올린다.

`--apply`는 결정 때와 마찬가지로 다시 돌려도 안전하다. 파일마다 import 때의 sha256이 그대로일 때만 빈 디렉터리를 놓고, 바뀐 파일이 있으면 충돌을 보이고 멈춘다. import가 실패하면 아무것도 바뀌지 않는다. 단계 전체가 한 트랜잭션이다.

## 되돌리기 (보드)

1. 전환 순서 1단계처럼 writer를 멈추고 `orch db backup <file>`.
2. `orch db export --legacy --target 4a <dir>`가 `board.json`, `tidy.jsonl`, 닫기 기록을 줄로 풀어 쓴 `inbox-external.jsonl`, `export.json`을 쓴다. 같은 문구가 두 상태에 있으면 옛 형식으로 나타낼 수 없어 export가 그 항목을 알리고 멈춘다. 한쪽을 닫거나 문구를 바꾼 뒤 다시 돌린다.
3. `orch db rollback 4b <dir>`가 export 뒤 DB와 인박스가 바뀌지 않았는지 확인하고, 빈 디렉터리 자리에 세 파일을 놓은 뒤 마지막으로 `4b-board` 행을 지운다. 중간에 죽으면 다시 돌리면 된다.
4. 이전 릴리스로 되돌리고 fast-forward한 뒤 writer를 다시 올린다.

나중에 import하면 파일을 다시 가져온다. 테이블은 교체되고 번호는 이어진다. 롤백 전의 `b<N>`은 다시 쓰이지 않는다. 결정 단계까지 되돌리려면 이 절을 끝낸 뒤 "되돌리기 (결정)"을 따른다.
