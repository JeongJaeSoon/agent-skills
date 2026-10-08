# 어떤 일을 어떤 장치로 돌리나

정기 작업이나 사건에 반응하는 작업을 어디에 걸지 정하는 표다. 근거는 Claude Code 2.1.292와 Orca 1.4.220에서
잰 결과다.

## 장치별 성질

| 장치 | 세션이 끝나도 남나 | 로컬 파일·프로세스 접근 | 모델 없이 거르나 |
|---|---|---|---|
| Orca automation + `--precheck` | 남는다(Orca 앱이 돌린다) | 된다 | 된다. precheck가 0이 아니면 그 회차는 모델을 부르지 않고 건너뛴다 |
| 클라우드 routine | 남는다 | 안 된다(로컬 파일을 못 본다) | 안 된다 |
| `CronCreate`, `ScheduleWakeup` | 세션과 함께 사라진다 | 된다 | 안 된다 |
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
| 세션 안 사건 반응 | `Monitor` 또는 `run_in_background`. 결정적인 반응은 hooks |

## Orca automation을 걸 때 지킬 것

- precheck의 cwd는 `--workspace`로 준 worktree가 아니라 저장소의 메인 체크아웃이다. precheck 스크립트는 절대
  경로와 `git -C`를 쓴다.
- precheck가 `schedule`을 실행한 셸의 환경 변수를 물려받는다고 가정하지 않는다. 상태 폴더 같은 경로는 precheck 명령에
  `env NAME=value`로 넣는다(reap.py `schedule`은 `AGENT_SKILLS_STATE`와 `AGENT_SKILLS_LEDGER`를 넣는다).
- `orca automations run`(수동 실행)은 precheck를 건너뛴다. precheck가 맞게 거르는지는 스크립트를 직접 돌려서
  exit code로 확인한다. 지난 결과를 기억하는 precheck라면 직접 돌린 회차도 기억되므로, 검증할 때는 상태
  폴더를 따로 둔다(reap.py는 `AGENT_SKILLS_STATE`).
- `--workspace-mode existing`을 쓴다. `new-per-run`은 회차마다 worktree를 만들어 치울 것을 늘린다.
- precheck는 할 일이 없으면 exit 1이다. 지난 회차와 같은 결과도 할 일이 없는 것으로 친다. 그래야 모델 호출이
  바뀐 것이 있을 때만 일어난다.
- 다른 automation이 이미 하는 감시는 겹쳐 걸지 않는다. 예를 들어 PR 감시 automation이 따로 있으면, 정리
  작업은 PR을 감시하지 않고 scan할 때 `gh`로 PR 상태를 직접 읽는다.
- 실제 플래그는 `orca automations create --help`로 확인한다.

## 정기 정리(janitor)

`skills/reap-resources`의 `reap.py`가 맡는다. 에이전트가 만든 것(ledger에 올린 것과 Orca 오케스트레이션
워커가 만든 worktree)만 보고, report-only다.

```bash
python3 <reap.py 절대 경로> precheck     # 대상 집합이 지난 보고 이후 바뀌었고 비어 있지 않을 때만 exit 0
python3 <reap.py 절대 경로> schedule     # 만들 `orca automations create …` 명령을 출력만 한다. 스킬을 로드하는 체크아웃에서 돌린다
python3 <reap.py 절대 경로> schedule --write   # 실제로 만든다
```

주기는 3시간(`17 */3 * * *`)이다. 규칙과 ledger 형식은 [reap-resources SKILL.md](../skills/reap-resources/SKILL.md).
