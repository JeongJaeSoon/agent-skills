# PC 사용량 감시(pressure-watch)

`skills/reap-resources/scripts/pressure.py`가 모델 없이 3분마다 머신을 재고, 높으면 reap-resources가 정리 대상으로 올린 것부터 정리한다. 새 무거운 작업은 `hooks/pressure_gate.py`가 시작 전에 막는다. 규칙은 [reap-resources SKILL.md](../skills/reap-resources/SKILL.md)의 "Pressure" 절에 있다.

## 왜 Orca automation이 아니라 LaunchAgent인가

[automation.md](automation.md)의 기본은 Orca automation + precheck다. 이 감시는 다음 이유로 preview-watch처럼 LaunchAgent로 돌린다.

- 할 일이 모두 결정적이다. 측정, `reap.py scan`·`reap --plan`, `orca orchestration send` 한 통이면 끝나고 모델이 판단할 것이 없다.
- 모델 세션을 띄우는 것 자체가 부하다. 위험 단계에서 Claude 세션을 하나 더 띄우면 막으려던 일을 키운다.
- 주기가 짧다. 3분 주기 automation은 정상인 회차마다 "건너뜀" 기록을 하루 480개 남긴다.

## 판정 규칙

`check`, hook, `round`는 같은 판정 함수(`pressure.judge`)와 같은 입력(`pressure.snapshot`)을 쓴다. 입력은 그 자리에서 읽는 값(load, 메모리 여유, 압박 레벨, swap 사용량, 디스크, 컨테이너 수)과, 직전 `round`가 회차 사이 카운터 차이로 잰 값(swapout 속도, CPU idle, Docker 메모리·CPU, swap 증가율)이다. CPU idle만은 hook·`check`·`queue wait`가 판정하는 그 자리에서 `idle_window_seconds`(2초) 동안 CPU tick 차이로 다시 재서 회차 값 대신 쓴다. `round`는 회차 사이 값이 없을 때(첫 회차, 늦어진 회차)만 그 자리에서 잰다. idle 측정 창은 `docker ps`·`orca` 같은 다른 측정과 겹쳐 돌므로, hook 전체는 `docker ps`(최대 5초)와 턴 수(최대 5초)를 더해도 hook 제한 20초 안에 끝난다. 회차 샘플은 `sample_fresh_minutes`(7분) 안의 것만 판정에 넣고, 더 오래되면 빼고 측정 줄에 `샘플 N분 전`으로 보인다. hook이 `docker stats`(약 2.5초)나 속도를 직접 재지 않아도 되고, hook이 막은 것을 `check`가 풀어 주는 시점과 hook이 통과시키는 시점이 같다.

예전에는 hook만 swap과 디스크를 경고로 낮춰서, 차단 메시지가 시킨 "`check`가 0이나 1이 될 때까지 대기"가 누적 swap 때문에 끝나지 않았다(2026-10-30).

## 기본 임계값과 근거

기준 머신은 Apple M5 Max 18코어(Super 6 + Performance 12, 효율 코어 없음), 메모리 64GB, 내장 디스크 1TB, Docker는 OrbStack(VM 18 vCPU, 메모리 한도 15.66GiB)이다. 18코어가 모두 고성능 코어이므로 load는 18로 나눈다. 아래 값은 2026-10-25(카드 10여 개 동시 작업), 10-25~30의 `pressure.log`, 10-30 13:55~14:06의 30초 간격 실측에서 왔다.

| 항목 | 실측 | 경고 | 위험 | 근거 |
|---|---|---|---|---|
| `load_per_cpu`(5분 load ÷ 18) | 평소 1.0~1.6, 10-27 과부하 때 최고 4.75 | 1.3 | 2.0이고 직전 회차 CPU idle ≤ `load_idle_pct`(10%) | 10-30 실측에서 load가 1.3~1.6일 때 CPU idle은 19~55%였다. load에는 OrbStack VM의 vCPU 스레드와 대기 중인 스레드가 섞여 실제 CPU 포화보다 높게 나온다. idle은 30초 순간값이 0.2%까지 튀었으므로(2회) 회차 간 CPU tick 차이(약 3분 평균)로 잰다. hook·`check`·`queue wait`는 판정하는 자리에서 2초 동안 잰 idle을 쓴다. macOS의 CPU tick 합계는 1초에 한 번 몰아서 오르므로 1초보다 짧은 창은 변화가 없어 1초로 올린다. 부하가 클수록 회차가 밀려(11-02에 3분 주기가 16분까지) 샘플이 만료되고 load만으로 판정되던 구멍이 있었기 때문이다. tick을 못 읽는 OS에서만 load만으로 위험을 낸다 |
| `mem_free_pct`(`kern.memorystatus_level`) | 평소 78~92%, swap 67GB 폭주 때도 최저 35% | 20% 이하 | 10% 이하 | 값은 "사용 중이 아닌 메모리"가 아니라 커널이 압박 없이 되찾을 수 있다고 보는 비율이다. 파일 캐시와 압축 가능한 페이지가 여유로 잡혀서 사용량 약 34GiB(64GiB의 53%)인데 87%로 읽힌다. 20·10%는 폭주에서도 닿은 적이 없는 안전망이고, 실제로 시작을 막은 지표는 `swapout_pps`다. 아래 "메모리 임계 검토" |
| `vm_pressure`(`kern.memorystatus_vm_pressure_level`) | 10-28부터 거의 늘 2, fseventsd 재시작 직후 1. 4는 기록 없음 | 2 | 4 | 2는 누수가 압축·swap 공간을 채운 동안 며칠씩 이어져 구분력이 없다. 4(critical)는 커널이 프로세스를 정리하기 시작하는 단계다 |
| `swapout_pps`(회차 간 swapout 페이지/초, 16KiB 페이지) | 대부분 0. 11일 평균 19, fseventsd 재시작 순간 30초 1792 | 300(약 4.7MB/s) | 1500(약 23MB/s) | 지금 메모리가 모자라 디스크로 밀어내는지를 바로 보여 준다. 누수로 swap이 시간당 4.5GB(약 78/s) 늘 때도 체감 둔화는 없었으므로 경고는 그 4배다. 3분 평균이라 순간 급등은 낮아져 잡히므로 한 회차로 위험을 낸다. 스래싱을 실측한 적은 없어 보정 대상이다 |
| `swap_gb`(사용 중) | 1.7(10-25), 누수로 56.9(10-30), 재시작 뒤 9.0 | 16 | 없음 | 과거 압박이나 누수가 남긴 값이라 지금 부하가 아니다. 메모리의 1/4을 넘으면 경고(정리 회차)만 한다 |
| `swap_growth_gb_h`(30~60분 순증가) | 누수 때 4.5 | 2 | 없음 | 누수 신호. 막지 않고 `renotify_minutes`마다 한 번 누수 알림을 보낸다 |
| `disk_free_gb`(`$HOME` 볼륨) | 264~428 | 50 이하 | 20 이하 | 이미지 빌드 한 번에 수 GB를 쓴다. 20GB 아래면 macOS 업데이트와 swap 확장이 막힌다. 기다려도 풀리지 않으므로 hook은 대기 대신 디스크 정리를 요청하라고 안내한다 |
| `docker_mem_pct`(컨테이너 메모리 합 ÷ VM MemTotal) | 25~40% (컨테이너 21~49개) | 70 | 90 | VM 한도를 넘으면 VM 안에서 OOM이 나고 호스트 지표에는 보이지 않는다. VM 커널·데몬 몫은 빠지므로 VM 전체 사용량보다 작다. 낮다고 여유가 확정되지는 않는다 |
| `docker_cpu_pct`(컨테이너 CPU 합 ÷ VM CPU 수) | 11-02 VM 84% idle일 때 약 16% | 50 | 없음 | 컨테이너 수 대신 Docker VM이 실제로 쓰는 CPU다. VM의 vCPU는 호스트 스레드라 호스트 CPU idle에 이미 잡히므로 위험은 내지 않고, 절반을 넘으면 정리 회차(끝난 카드의 스택)를 돌린다 |
| `containers`(실행 중) | 21~99 | 판정 안 함 | 판정 안 함 | 개수는 무게를 나타내지 못한다. 49개가 CPU 306%, 메모리 5.08GiB(VM의 32%)였고, 11-02에는 몇 분 사이에 21↔81로 흔들렸다. 측정 줄과 기록에만 남긴다. 예전 설정의 `containers` 키는 읽기는 하지만 판정에 쓰지 않는다 |

### 메모리 임계 검토 (2026-11-05)

질문: 대시보드 메모리 여유 92%와 호스트 지표의 사용량 33.5GiB(active+wired)가 다르다. 임계를 바꿔야 하는가.

**측정은 맞고 정의가 다르다.** `mem_free_pct`는 `kern.memorystatus_level`이다. 파일 캐시와 압축 가능한 페이지를 여유로 세므로 사용량이 64GiB의 절반을 넘어도 80%대로 읽힌다. 화면 라벨은 "Memory free"에서 "Memory available"로 바꾸고, 사용량은 `vm_stat`의 active+wired를 GiB로 따로 보인다(`mem_active_wired_gb`). 판정에는 쓰지 않는다. anonymous+wired(`mem_anon_wired_gb`)도 같이 기록한다. 대안 지표를 재현하려면 기록이 필요한데 지금까지는 없었기 때문이다.

**무엇이 시작을 막고 여는가** (코드에서 찾은 전부)

| 소비처 | 읽는 값 | 효과 |
|---|---|---|
| `hooks/pressure_gate.py`, `card.py`의 카드 생성 | `judge`가 모든 지표 중 최악 수준 | 전체 테스트·E2E·브라우저 실행이 `orch heavy` 밖이면 수준과 무관하게 거절하고 슬롯으로 감싸라고 한다. 수준 게이트는 `orch heavy`·compose·새 카드의 시작에 걸린다. ok는 통과, warn은 `round` 한 번 뒤 15분(`gate_fresh_minutes`) 동안 통과, danger는 대기열에 넣고 `queue wait`로 기다린다. 디스크만 위험이면 대기하지 않는다 |
| 같은 hook, 새 카드 | 진행 중인 턴 수 ≥ `active_turns_per_cpu`×CPU | 수준과 무관하게 새 카드만 막는다 |
| `orch heavy` | 위 hook이 같은 수준으로 먼저 판정하고, 통과하면 슬롯 수(기본 2) | 슬롯은 수준과 무관하다 |
| `pressure.py round` | 같은 수준 | warn·danger에서 reap 안전 대상을 정리하고, warn·danger로 수준이 바뀔 때(warn↔danger 포함) patrol을 부른다. danger는 알림과 Needs you |
| `patrol` | 마지막 수준 | danger에서는 정리 스캔을 건너뛴다 |
| `swap_growth_gb_h`, `swap_gb`, `docker_cpu_pct` | 위험이 없는 지표 | danger와 대기열을 만들지 않는다. 경고 수준이 되면 최근 스캔이 없는 동안 hook이 `round` 한 번을 요구한다 |
| janitor, 대시보드 | 수준을 읽지 않는다 | janitor는 일정으로 돌고, 대시보드는 기록만 그린다 |

수준을 정하는 지표 중 `mem_free_pct`, `vm_pressure`, `swapout_pps`, `load_per_cpu`(idle 조건 있음), `disk_free_gb`, `docker_mem_pct`가 danger를 낼 수 있다.

**기록 24시간 재현** (487회차, 3분 간격, 폭주 3번)

- danger 16회차는 모두 `swapout_pps ≥ 1500`이 잡았다. 그 회차의 `mem_free_pct`는 35~61, 88이 2회차였고 20 이하는 0회다. swap은 최대 67.0GB였다.
- 폭주는 한 회차 안에서 시작됐다(직전 회차 `mem_free_pct` 82·88·79·53, swapout 0). 그래서 `mem_free_pct` 임계를 올려도 `swapout_pps`와 같은 회차에 같은 행을 잡을 뿐 미리 알려 주지 않는다.
- `mem_free_pct ≤ X`를 한 지표로 놓고 센 결과(폭주 = swapout ≥ 1500인 16회차, 평시 = 나머지 471회차): X=20·10은 0/16, 평시 0. X=40은 11/16, 평시 0. X=50은 13/16, 평시 0. X=60은 13/16, 평시 1. X=80은 14/16, 평시 5.
- `swap_gb ≥ 16`은 12/16이고 평시 0이지만 폭주가 지나간 뒤에도 한참 남는 값이라 게이트로는 틀리다(위 표의 판단 그대로 경고만).

시간 차이도 있다. 판정은 `mem_free_pct`를 그 자리에서 읽고 `swapout_pps`는 회차 샘플을 `sample_fresh_minutes`(7분)까지만 쓴다. 부하로 회차가 밀려 샘플이 만료되면 swapout은 판정에서 빠지고 그 자리에서 읽는 값(`mem_free_pct`, `vm_pressure`, load, swap, 디스크, hook이 다시 재는 CPU idle)이 남는다. 40~50 같은 높은 `mem_free_pct` 임계는 그때의 독립 방어선이다. 다만 이 기록에서는 그런 회차가 없었고, 폭주를 swapout으로 정의했으므로 위의 포착 수는 두 지표가 같은 현상을 보는지만 말해 줄 뿐 독립 검증이 아니다.

**결론**: 기본값은 바꾸지 않는다. `mem_free_pct`의 20·10은 이 기록에서 한 번도 닿지 않아 오탐도 미탐 개선도 없다. 40~50으로 올려도 이 기록에서는 새로 잡는 행이 없다. 시작을 여닫는 실질 지표는 `swapout_pps`이고, 20·10은 정의가 다른 이 값으로 "13GB 여유"를 약속하지 않는 안전망으로 남긴다. 위의 근거 문장은 이 검토로 고쳤다. 한계: 기록이 24시간뿐이고 폭주가 3번이며 anonymous+wired와 swap 증가율 대안은 재현할 값이 없었다. 새 필드가 쌓이면(일주일 이상, 폭주 여러 번) 다시 본다. 다시 볼 때 바꾸려면 `reap.pressure.mem_free_pct`에 `[50, 40]`처럼 적는다. 기본값은 건드리지 않는다.

### 테스트 슬롯과 새 카드 턴 상한

2026-11-02 18시 이후 CPU idle 0~1%, 실행 대기 58~67, 초당 새 프로세스 약 1,100개가 이어졌다. 끝난 카드의 찌꺼기는 거의 없었고, 살아 있는 카드들이 각자 전체 테스트 스위트·`bun test`·`tsc`·headless Chrome을 동시에 돌렸다. 게이트는 `orch heavy`·compose·새 카드 같은 시작만 보고 있었으므로 이 실행들은 슬롯 2개를 거치지 않았다.

- 전체 테스트·E2E·브라우저 실행은 수준과 상관없이 `orch heavy` 슬롯(기본 2개)을 거친다. 파일 하나짜리 테스트는 짧아서 그대로 둔다.
- 새 카드는 Orca가 `working`으로 보이는 에이전트 턴이 `active_turns_per_cpu` × 논리 CPU(기본 0.5, 18코어면 9개) 이상이면 기다린다. 18:42에 턴이 진행 중이던 카드 10개만으로 CPU가 포화됐고, 19:24에는 `working` 21개에 load5 104였다. 상한은 포화가 관측된 10개보다 낮게, 코어 수에 비례하게 잡았다. 이미 도는 턴은 끊지 않고 새 카드만 막으므로, 턴이 끝나는 대로 다시 열린다. Orca가 턴 수를 주지 못하면(응답 없음, 목록 잘림) 센 수가 없는 것이지 0이 아니므로 가득 찬 것과 같이 막는다. hook·`turns`·`turns --wait`가 `turn_hold` 하나로 판정하고, `turns --wait`는 셀 수 있을 때까지 기다리다 끝내 못 세면 "셀 수 없음"을 출력하고 3으로 끝난다.
- 턴이 끝났어도 백그라운드 셸, `orch wait`, orchestration `ask`, 서브에이전트를 기다리는 에이전트는 Orca가 `working`으로 둔다. 이런 에이전트는 `workingMode`가 `monitoring`이거나 `mainAgent.state`가 `working`이 아니며, 턴을 돌리지 않으므로 세지 않는다. 2026-11-03에는 `working` 9개 중 4개가 이 상태였는데도 9개가 상한 9에 닿아 새 카드가 막혔다. 두 필드가 모두 없는 Orca 출력은 예전처럼 `state`만 보고 센다.
- 판정을 부른 세션 자신의 턴은 세지 않는다. 리드가 `turns --wait`로 쉬는 동안에는 자기 턴이 끝나 8개로 나와 exit 0이 되지만, 깨어나 `worker-start`를 부르면 자기 턴이 돌고 있어 hook이 9개로 세고 거절했다(2026-11-05에 다섯 번 재현). 그러면 대기가 헛돌았다. 이제 `active_turns`가 `ORCA_TERMINAL_HANDLE`로 `orca terminal show`를 불러 얻은 pane key(`tabId:leafId`)와 같은 에이전트를 뺀다. 상한 값과 경계(`n >= 상한`)는 그대로다. 다른 세션의 턴은 그대로 센다. 호출 세션을 알아내지 못하면(핸들 없음, 조회 실패) 예전처럼 자기 턴도 세고, `turns` 출력과 hook 거절 사유에 경고 한 줄을 붙인다.

Docker CPU(`docker_cpu_pct`)는 `docker_mem_pct`와 같은 `docker stats` 한 번에서 읽는다. OrbStack VM의 vCPU는 호스트 스레드라서 Docker의 CPU 사용은 호스트 CPU idle에도 그대로 잡힌다. 그래서 위험 판정은 호스트 CPU가 맡고, Docker CPU는 부하가 Docker 쪽에 있다는 경고와 정리 회차에만 쓴다.

`containers`는 `docker ps`의 줄 수가 아니다. OrbStack k8s를 켜면 pod마다 pause 컨테이너(`k8s_POD_*`)가 하나씩 더 뜨고, `kube-system`(coredns·traefik svclb·local-path-provisioner)이 늘 돈다. 그래서 k8s 컨테이너는 pod 하나(`io.kubernetes.pod.uid`)를 1로 세고 `kube-system`은 세지 않는다.

사용자가 늘 켜 두는 서비스(관측 스택, 채팅 UI 같은 것)는 `always_on`에 compose 프로젝트, 컨테이너 이름, k8s namespace로 적어 개수에서 뺀다. 판정 줄에는 `컨테이너 N개(상시 M개 제외)`로 따로 보인다. restart policy로 고르지 않는 이유는 카드의 레인 스택도 `unless-stopped`를 쓰기 때문이다. 이름은 머신마다 다르므로 저장소 기본값은 빈 목록이다. `docker_mem_pct`에는 상시 서비스도 들어간다. 정리 대상에서 빼는 것과 자원 사용량에서 빼는 것은 다르다.

경고 단계는 알리지 않고 정리만 하는 것이 기본이다. 정리한 것이 있을 때만 한 줄을 보내고, 같은 경고가 이어지면 10분(`rescan_minutes`)마다만 다시 스캔한다. 스캔이 실패한 회차도 hook의 "최근 회차"로 센다. 경고만으로는 막을 이유가 없고, 스캔이 매번 실패하면 경고 단계의 모든 무거운 시작이 막히기 때문이다.

수동 `round`가 직전 회차 뒤 `interval`의 절반이 안 돼 실행되면 샘플을 새로 재지 않고 직전 3분 샘플을 그대로 쓴다. 10초짜리 순간값(idle 0.2% 같은 급등)이 7분 동안 판정을 바꾸지 않게 하려는 것이다.

다른 머신에서는 `~/.claude/agent-skills.json`의 `reap.pressure`에서 항목별 `[경고, 위험]`을 바꾼다.

```json
{"reap": {"pressure": {"load_per_cpu": [1.3, 2.0], "docker_mem_pct": [60, 85], "swap_gb": [8, null], "always_on": ["<compose-project>"], "notify_to": "run:<id>"}}}
```

위험을 끄려면 위험 값에 `null`을 쓴다(`--set`에서는 `none`). 예전 기본값(`swap_gb: [8, 16]`)을 설정 파일에 그대로 적어 둔 머신은 그 값이 이기므로 여전히 위험을 낸다. 새 규칙을 쓰려면 그 줄을 지운다. `containers` 값은 남아 있어도 판정에 쓰이지 않는다.

`notify_to`가 없으면 `reap.inbox_to`로 보낸다. 두 키에는 `run:<id>`를 쓴다. `term_` 핸들도 받지만 터미널이 재시작하면 바뀌고, Orca 목록에서 사라진 핸들은 `orch patrol`이 T 발견으로 알린다. 다만 LaunchAgent에는 Orca 송신 터미널(`ORCA_TERMINAL_HANDLE`)이 없어 `orca orchestration send`가 `no_active_sender_terminal`로 실패한다. `term_`과 `run:` 주소 모두 그렇다. 그래서 전송에 실패하면(받는 곳이 없을 때도) 위험과 누수 알림을 orchestrate 대시보드의 Needs you(`~/.local/state/agent-skills/dashboard/inbox-external.jsonl`, 키 `pressure:danger`·`pressure:swap-growth`)에 올린다. 위험 줄은 위험에서 벗어나면, 누수 줄은 증가율이 경고선 아래로 내려가면 닫힌다. 같은 알림은 `renotify_minutes`(30분) 안에 다시 시도하지 않는다. 예전에는 실패 때 `danger_t`를 0으로 돌려 매 회차 재시도했다. 대시보드가 없는 머신에서는 로그(`~/.local/state/agent-skills/pressure.log`)에만 남는다.

회차마다 수준과 주요 지표(CPU당 load, CPU idle, 메모리 여유, swap, swapout, 컨테이너 수, Docker 메모리·CPU, 디스크 여유), 대기열 건수를 한 줄씩 `~/.local/state/agent-skills/pressure-history.jsonl`에 남긴다. 가장 오래된 줄이 24시간보다 한 시간 넘게 지나면 24시간치만 남기고 다시 쓴다. 3분 간격이면 하루 480줄 남짓이다. orchestrate 대시보드는 Overview 맨 위 PC load 패널에서 `pressure.py history`로 이 기록을 읽어 최근 6시간(또는 24시간) 추이와 경고·위험선을 그린다. 대시보드는 파일만 읽고 직접 측정하지 않는다. 화면을 열 때마다 `docker stats`가 돌면 그것 자체가 부하가 되기 때문이다.

## 설치와 확인

`skills-sync setup`의 `pressure-watch` 항목(기본 on)이다. `bootstrap.py --write`의 `apply`가 켜고, `skills-sync setup disable pressure-watch`로 끈다. 직접 부를 때:

```bash
python3 <체크아웃>/skills/reap-resources/scripts/pressure.py install    # 메인 체크아웃에서. worktree면 거부한다
python3 <체크아웃>/skills/reap-resources/scripts/pressure.py status
python3 <체크아웃>/skills/reap-resources/scripts/pressure.py round --dry-run --set load_per_cpu=0.5,1 --set docker_cpu_pct=5,none
```

`--dry-run`은 정리 계획과 보낼 알림을 출력만 한다. reap도 전송도 하지 않고, 상태 파일도 바꾸지 않는다.
