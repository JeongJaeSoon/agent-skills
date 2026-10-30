# PC 사용량 감시(pressure-watch)

`skills/reap-resources/scripts/pressure.py`가 모델 없이 3분마다 머신을 재고, 높으면 reap-resources가 정리 대상으로 올린 것부터 정리한다. 새 무거운 작업은 `hooks/pressure_gate.py`가 시작 전에 막는다. 규칙은 [reap-resources SKILL.md](../skills/reap-resources/SKILL.md)의 "Pressure" 절에 있다.

## 왜 Orca automation이 아니라 LaunchAgent인가

[automation.md](automation.md)의 기본은 Orca automation + precheck다. 이 감시는 다음 이유로 preview-watch처럼 LaunchAgent로 돌린다.

- 할 일이 모두 결정적이다. 측정, `reap.py scan`·`reap --plan`, `orca orchestration send` 한 통이면 끝나고 모델이 판단할 것이 없다.
- 모델 세션을 띄우는 것 자체가 부하다. 위험 단계에서 Claude 세션을 하나 더 띄우면 막으려던 일을 키운다.
- 주기가 짧다. 3분 주기 automation은 정상인 회차마다 "건너뜀" 기록을 하루 480개 남긴다.

## 판정 규칙

`check`, hook, `round`는 같은 판정 함수(`pressure.judge`)와 같은 입력(`pressure.snapshot`)을 쓴다. 입력은 그 자리에서 읽는 값(load, 메모리 여유, 압박 레벨, swap 사용량, 디스크, 컨테이너 수)과, 직전 `round`가 회차 사이 카운터 차이로 잰 값(swapout 속도, CPU idle, Docker 메모리, swap 증가율)이다. 회차 샘플은 `sample_fresh_minutes`(7분) 안의 것만 판정에 넣고, 더 오래되면 빼고 측정 줄에 `샘플 N분 전`으로 보인다. hook(10초 제한)이 `docker stats`(약 2.5초)나 속도를 직접 재지 않아도 되고, hook이 막은 것을 `check`가 풀어 주는 시점과 hook이 통과시키는 시점이 같다.

예전에는 hook만 swap과 디스크를 경고로 낮춰서, 차단 메시지가 시킨 "`check`가 0이나 1이 될 때까지 대기"가 누적 swap 때문에 끝나지 않았다(2026-10-30).

## 기본 임계값과 근거

기준 머신은 Apple M5 Max 18코어(Super 6 + Performance 12, 효율 코어 없음), 메모리 64GB, 내장 디스크 1TB, Docker는 OrbStack(VM 18 vCPU, 메모리 한도 15.66GiB)이다. 18코어가 모두 고성능 코어이므로 load는 18로 나눈다. 아래 값은 2026-10-25(카드 10여 개 동시 작업), 10-25~30의 `pressure.log`, 10-30 13:55~14:06의 30초 간격 실측에서 왔다.

| 항목 | 실측 | 경고 | 위험 | 근거 |
|---|---|---|---|---|
| `load_per_cpu`(5분 load ÷ 18) | 평소 1.0~1.6, 10-27 과부하 때 최고 4.75 | 1.3 | 2.0이고 직전 회차 CPU idle ≤ `load_idle_pct`(10%) | 10-30 실측에서 load가 1.3~1.6일 때 CPU idle은 19~55%였다. load에는 OrbStack VM의 vCPU 스레드와 대기 중인 스레드가 섞여 실제 CPU 포화보다 높게 나온다. idle은 30초 순간값이 0.2%까지 튀었으므로(2회) 회차 간 CPU tick 차이(약 3분 평균)로 잰다. idle을 모르면(첫 회차, 샘플 만료) 예전처럼 load만으로 위험을 낸다 |
| `mem_free_pct`(`kern.memorystatus_level`) | 43~82% | 20% 이하 | 10% 이하 | 64GB의 20%는 약 13GB로 compose 스택(2~3GB) 몇 개를 더 띄울 여유다. 10% 아래는 커널이 압축·swap을 늘리는 구간이다 |
| `vm_pressure`(`kern.memorystatus_vm_pressure_level`) | 10-28부터 거의 늘 2, fseventsd 재시작 직후 1. 4는 기록 없음 | 2 | 4 | 2는 누수가 압축·swap 공간을 채운 동안 며칠씩 이어져 구분력이 없다. 4(critical)는 커널이 프로세스를 정리하기 시작하는 단계다 |
| `swapout_pps`(회차 간 swapout 페이지/초, 16KiB 페이지) | 대부분 0. 11일 평균 19, fseventsd 재시작 순간 30초 1792 | 300(약 4.7MB/s) | 1500(약 23MB/s) | 지금 메모리가 모자라 디스크로 밀어내는지를 바로 보여 준다. 누수로 swap이 시간당 4.5GB(약 78/s) 늘 때도 체감 둔화는 없었으므로 경고는 그 4배다. 3분 평균이라 순간 급등은 낮아져 잡히므로 한 회차로 위험을 낸다. 스래싱을 실측한 적은 없어 보정 대상이다 |
| `swap_gb`(사용 중) | 1.7(10-25), 누수로 56.9(10-30), 재시작 뒤 9.0 | 16 | 없음 | 과거 압박이나 누수가 남긴 값이라 지금 부하가 아니다. 메모리의 1/4을 넘으면 경고(정리 회차)만 한다 |
| `swap_growth_gb_h`(30~60분 순증가) | 누수 때 4.5 | 2 | 없음 | 누수 신호. 막지 않고 `renotify_minutes`마다 한 번 누수 알림을 보낸다 |
| `disk_free_gb`(`$HOME` 볼륨) | 264~428 | 50 이하 | 20 이하 | 이미지 빌드 한 번에 수 GB를 쓴다. 20GB 아래면 macOS 업데이트와 swap 확장이 막힌다. 기다려도 풀리지 않으므로 hook은 대기 대신 디스크 정리를 요청하라고 안내한다 |
| `docker_mem_pct`(컨테이너 메모리 합 ÷ VM MemTotal) | 25~40% (컨테이너 21~49개) | 70 | 90 | VM 한도를 넘으면 VM 안에서 OOM이 나고 호스트 지표에는 보이지 않는다. VM 커널·데몬 몫은 빠지므로 VM 전체 사용량보다 작다. 낮다고 여유가 확정되지는 않는다 |
| `containers`(실행 중) | 21~99 | 40 | 없음 | 개수는 무게를 나타내지 못한다. 49개가 CPU 306%, 메모리 5.08GiB(VM의 32%)였다. 경고(정리 회차)만 한다 |

Docker CPU는 따로 재지 않는다. OrbStack VM의 vCPU는 호스트 스레드라서 Docker의 CPU 사용은 호스트 CPU idle에 그대로 잡힌다.

`containers`는 `docker ps`의 줄 수가 아니다. OrbStack k8s를 켜면 pod마다 pause 컨테이너(`k8s_POD_*`)가 하나씩 더 뜨고, `kube-system`(coredns·traefik svclb·local-path-provisioner)이 늘 돈다. 그래서 k8s 컨테이너는 pod 하나(`io.kubernetes.pod.uid`)를 1로 세고 `kube-system`은 세지 않는다.

사용자가 늘 켜 두는 서비스(관측 스택, 채팅 UI 같은 것)는 `always_on`에 compose 프로젝트, 컨테이너 이름, k8s namespace로 적어 개수에서 뺀다. 판정 줄에는 `컨테이너 N개(상시 M개 제외)`로 따로 보인다. restart policy로 고르지 않는 이유는 카드의 레인 스택도 `unless-stopped`를 쓰기 때문이다. 이름은 머신마다 다르므로 저장소 기본값은 빈 목록이다. `docker_mem_pct`에는 상시 서비스도 들어간다. 정리 대상에서 빼는 것과 자원 사용량에서 빼는 것은 다르다.

경고 단계는 알리지 않고 정리만 하는 것이 기본이다. 정리한 것이 있을 때만 한 줄을 보내고, 같은 경고가 이어지면 10분(`rescan_minutes`)마다만 다시 스캔한다. 스캔이 실패한 회차도 hook의 "최근 회차"로 센다. 경고만으로는 막을 이유가 없고, 스캔이 매번 실패하면 경고 단계의 모든 무거운 시작이 막히기 때문이다.

수동 `round`가 직전 회차 뒤 `interval`의 절반이 안 돼 실행되면 샘플을 새로 재지 않고 직전 3분 샘플을 그대로 쓴다. 10초짜리 순간값(idle 0.2% 같은 급등)이 7분 동안 판정을 바꾸지 않게 하려는 것이다.

다른 머신에서는 `~/.claude/agent-skills.json`의 `reap.pressure`에서 항목별 `[경고, 위험]`을 바꾼다.

```json
{"reap": {"pressure": {"load_per_cpu": [1.3, 2.0], "docker_mem_pct": [60, 85], "swap_gb": [8, null], "always_on": ["<compose-project>"], "notify_to": "run:<id>"}}}
```

위험을 끄려면 위험 값에 `null`을 쓴다(`--set`에서는 `none`). 예전 기본값(`swap_gb: [8, 16]`, `containers: [40, 80]`)을 설정 파일에 그대로 적어 둔 머신은 그 값이 이기므로 여전히 위험을 낸다. 새 규칙을 쓰려면 그 줄을 지운다.

`notify_to`가 없으면 `reap.inbox_to`로 보낸다. 다만 LaunchAgent에는 Orca 송신 터미널(`ORCA_TERMINAL_HANDLE`)이 없어 `orca orchestration send`가 `no_active_sender_terminal`로 실패한다. `term_`과 `run:` 주소 모두 그렇다. 그래서 전송에 실패하면(받는 곳이 없을 때도) 위험과 누수 알림을 orchestrate 대시보드의 Needs you(`~/.local/state/agent-skills/dashboard/inbox-external.jsonl`, 키 `pressure:danger`·`pressure:swap-growth`)에 올린다. 위험 줄은 위험에서 벗어나면, 누수 줄은 증가율이 경고선 아래로 내려가면 닫힌다. 같은 알림은 `renotify_minutes`(30분) 안에 다시 시도하지 않는다. 예전에는 실패 때 `danger_t`를 0으로 돌려 매 회차 재시도했다. 대시보드가 없는 머신에서는 로그(`~/.local/state/agent-skills/pressure.log`)에만 남는다.

## 설치와 확인

`skills-sync setup`의 `pressure-watch` 항목(기본 on)이다. `bootstrap.py --write`의 `apply`가 켜고, `skills-sync setup disable pressure-watch`로 끈다. 직접 부를 때:

```bash
python3 <체크아웃>/skills/reap-resources/scripts/pressure.py install    # 메인 체크아웃에서. worktree면 거부한다
python3 <체크아웃>/skills/reap-resources/scripts/pressure.py status
python3 <체크아웃>/skills/reap-resources/scripts/pressure.py round --dry-run --set load_per_cpu=0.5,1 --set containers=10,20
```

`--dry-run`은 정리 계획과 보낼 알림을 출력만 한다. reap도 전송도 하지 않고, 상태 파일도 바꾸지 않는다.
