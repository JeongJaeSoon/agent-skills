# 오케스트레이션 상태를 Orca로 옮기기 (2026-11-01)

이 릴리스부터 카드의 상태를 모델이 손으로 적은 줄이 아니라 Orca에서 읽는다. 무엇이 옮겨 갔는지, 릴리스 전에 시작한 세션이 어떻게 계속 도는지, 전환 순서와 되돌리는 법을 적는다.

## 옮겨 간 것

| 전 | 이제 |
|---|---|
| 카드마다 오케스트레이터가 적는 board doing 줄 | 카드의 Orca worktree 칸(`workspaceStatus`, `linkedPR`, `linkedItems`)과, 있으면 Run의 Task. doing 줄은 카드가 없는 대기에만 쓴다 |
| 리드마다 `orch board lead add` | 리드 행은 세션에서 만든다. fleet이 못 보는 리드에만 `lead add`를 쓴다 |
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
- **릴리스 전에 띄운 리드**는 여전히 `orch board lead add`와 `orch sent add`를 할 수 있다. fleet은 board 리드와 그 세션을 터미널 handle로 합치므로 리드는 한 번만 보인다.
- **`term_` 주소로 보고하는 브리프**는 새 `skills-sync card`가 거부한다(종료 코드 2). Run 밖 터미널로는 `worker_done`이 가지 않기 때문이다. 그 줄을 `run:<id>`로 바꾸거나, 빼서 카드 자기 대화창에 보고하게 한다.
- **`agentWait`를 주지 않는 Orca**에서는 예전처럼 화면을 읽는다.
- 옛 코드와 새 코드는 같은 파일을 읽고 쓴다. `orch migrate`는 보통의 tidy 줄만 더하므로 옛 코드도 그 줄을 읽는다.

## 전환 순서

머지나 카드 시작이 진행 중이지 않을 때 한다. 아래 어느 단계도 돌고 있는 카드를 건드리지 않는다.

1. 로드되는 메인 체크아웃을 릴리스까지 fast-forward 한다([platform.md](platform.md)).
2. 오케스트레이터, 그다음 각 리드에서 턴이 끝난 때 `/reload-plugins`. 돌고 있는 카드는 할 일이 없다.
3. 오케스트레이터에서 `orca orchestration run-current --json`. null이면 이전 브리프들이 보고하던 Run으로 `orca orchestration run-use <id>`. Run이 하나도 없을 때만 새로 만든다.
4. `orch migrate`(dry-run). 첫 목록(카드 하나를 가리키는 doing 줄)을 확인한다. 사람을 기다리는 줄, 오너의 줄, 오너가 다시 연 줄, 카드는 머지됐지만 남은 단계(릴리스, 배포)를 적은 줄은 들어 있지 않다. 마지막 것은 따로 나열되고 그대로 남는다.
5. `orch migrate --apply`. 출력되는 백업 디렉터리와 `orch board reopen` 줄을 적어 둔다.
6. 대시보드를 확인한다. Sessions 표에 Orca에서 온 Ticket·PR 칸이 보이고, board의 열린 doing 목록에는 남은 대기만 있다.

## 되돌리기

- **상태:** `orch migrate --restore <백업 디렉터리>`는 그 실행이 닫은 줄을 다시 열고, 그 뒤 오너가 바꾼 것은 그대로 둔다. 옛 코드가 로드된 상태라면 `--apply`가 출력한 `orch board reopen` 줄을 대신 돌린다. 백업에는 실행 전의 `board.json`, `tidy.jsonl`, `decisions.json`이 0600 파일로 있다.
- **코드:** 로드되는 체크아웃을 이전 릴리스 태그로 돌리고 다시 `/reload-plugins`.
- **Orca 값:** `workspaceStatus`는 카드가 자기 칸에만 쓰고(PR을 열면 `in-review`, 닫을 때 `completed`), 링크는 새 코드가 띄운 카드에만 붙는다. 상태를 되돌리려면 `orca worktree set --worktree <sel> --workspace-status in-progress`.
