# PC 사용량 감시(pressure-watch)

`skills/reap-resources/scripts/pressure.py`가 모델 없이 3분마다 머신을 재고, 높으면 reap-resources가 정리 대상으로 올린 것부터 정리한다. 새 무거운 작업은 `hooks/pressure_gate.py`가 시작 전에 막는다. 규칙은 [reap-resources SKILL.md](../skills/reap-resources/SKILL.md)의 "Pressure" 절에 있다.

## 왜 Orca automation이 아니라 LaunchAgent인가

[automation.md](automation.md)의 기본은 Orca automation + precheck다. 이 감시는 다음 이유로 preview-watch처럼 LaunchAgent로 돌린다.

- 할 일이 모두 결정적이다. 측정, `reap.py scan`·`reap --plan`, `orca orchestration send` 한 통이면 끝나고 모델이 판단할 것이 없다.
- 모델 세션을 띄우는 것 자체가 부하다. 위험 단계에서 Claude 세션을 하나 더 띄우면 막으려던 일을 키운다.
- 주기가 짧다. 3분 주기 automation은 정상인 회차마다 "건너뜀" 기록을 하루 480개 남긴다.

## 기본 임계값과 근거

기준 머신은 Apple Silicon 18코어(성능 코어 6 + 효율 코어 12), 메모리 64GB, 내장 디스크 1TB, Docker는 OrbStack(VM 메모리 한도 15.66GiB)이다. 2026-10-25 오후, 카드 10여 개가 동시에 일하던 때 잰 값이다.

| 항목 | 그때 값 | 경고 | 위험 | 근거 |
|---|---|---|---|---|
| `load_per_cpu`(5분 부하 ÷ 논리 코어) | 25~28 ÷ 18 = 1.4~1.57 | 1.0 | 2.0 | 경고는 reap `load`의 기존 한도(CPU 수)와 같다. 그 시점에 Orca UI가 느려졌다. 유휴 세션 8개를 닫아 19.5에서 11로 내린 기록이 있어, 코어 수의 두 배면 정리 없이는 돌아오지 않는 수준으로 본다 |
| `mem_free_pct`(`kern.memorystatus_level`) | 66~69% | 20% 이하 | 10% 이하 | 64GB의 20%는 약 13GB로, compose 스택 하나(2~3GB) 몇 개를 더 띄울 여유다. 10% 아래는 커널이 압축·swap을 늘리는 구간이다 |
| `vm_pressure`(`kern.memorystatus_vm_pressure_level`) | 1(정상) | 2 | 4 | 커널 자체 판정. 2는 warn, 4는 critical이다 |
| `swap_gb` | 1.74 | 8 | 16 | macOS는 swap을 필요할 때 늘린다. 메모리의 1/8, 1/4을 넘으면 스래싱이 체감된다. swap은 천천히 줄어서 경고가 오래 남을 수 있다 |
| `disk_free_gb`(`$HOME` 볼륨) | 264~277 | 50 이하 | 20 이하 | Docker 이미지 빌드 한 번에 수 GB를 쓴다. 20GB 아래면 macOS 업데이트와 swap 확장이 막힌다 |
| `containers`(실행 중) | 56~59 | 40 | 80 | 카드 하나의 스택이 컨테이너 11~19개였다. 경고는 스택 약 3개, 위험은 약 6개다. 그때 59개에서 OrbStack VM이 CPU 413%를 썼다 |

`containers`는 `docker ps`의 줄 수가 아니다. OrbStack k8s를 켜면 pod마다 pause 컨테이너(`k8s_POD_*`)가 하나씩 더 뜨고, `kube-system`(coredns·traefik svclb·local-path-provisioner)이 늘 돈다. 그래서 k8s 컨테이너는 pod 하나(`io.kubernetes.pod.uid`)를 1로 세고 `kube-system`은 세지 않는다. 레인 하나를 namespace에 올리면 pod 10개 안팎이라 compose 스택 하나(11~19개)와 같은 무게로 잡힌다. 2026-10-25 k8s를 상시 켠 뒤 `docker ps`는 51~52개였지만 그중 35개가 compose 라벨이 없는 k8s 컨테이너와 상시 컨테이너여서, 레인 스택이 없어도 경고선 40에 걸렸다.

사용자가 늘 켜 두는 서비스(관측 스택, 채팅 UI 같은 것)는 `always_on`에 compose 프로젝트, 컨테이너 이름, k8s namespace로 적어 상한 계산에서 뺀다. 판정 줄에는 `컨테이너 N개(상시 M개 제외)`로 따로 보인다. restart policy로 고르지 않는 이유는 카드의 레인 스택도 `unless-stopped`를 쓰기 때문이다. 이름은 머신마다 다르므로 저장소 기본값은 빈 목록이다.

상한 40·80은 그대로 둔다. 같은 머신(64GB, 18코어, OrbStack VM 15.66GiB)이고, 새로 잰 근거가 없다. 위 셈법으로 바꾼 뒤에도 경고선은 레인 스택 약 3개다.

기준 머신은 평소 load가 경고선 위에 있었다. 그래서 경고 단계는 알리지 않고 정리만 하는 것이 기본이다. 정리한 것이 있을 때만 한 줄을 보내고, 같은 경고가 이어지면 10분(`rescan_minutes`)마다만 다시 스캔한다.

hook은 swap과 디스크를 경고까지만 센다. 둘 다 몇 시간씩 높게 남아, 위험으로 세면 그동안 모든 무거운 시작이 막힌다.

다른 머신에서는 `~/.claude/agent-skills.json`의 `reap.pressure`에서 항목별 `[경고, 위험]`을 바꾼다.

```json
{"reap": {"pressure": {"load_per_cpu": [1.0, 2.0], "containers": [20, 40], "always_on": ["<compose-project>"], "notify_to": "run:<id>"}}}
```

`notify_to`가 없으면 `reap.inbox_to`로 보내고, 둘 다 없으면 로그(`~/.local/state/agent-skills/pressure.log`)에만 남긴다. 터미널 handle은 터미널을 다시 열면 바뀌므로, 가능하면 run 주소(`run:<id>`)를 쓴다.

## 설치와 확인

`skills-sync setup`의 `pressure-watch` 항목(기본 on)이다. `bootstrap.py --write`의 `apply`가 켜고, `skills-sync setup disable pressure-watch`로 끈다. 직접 부를 때:

```bash
python3 <체크아웃>/skills/reap-resources/scripts/pressure.py install    # 메인 체크아웃에서. worktree면 거부한다
python3 <체크아웃>/skills/reap-resources/scripts/pressure.py status
python3 <체크아웃>/skills/reap-resources/scripts/pressure.py round --dry-run --set load_per_cpu=0.5,1 --set containers=10,20
```

`--dry-run`은 정리 계획과 보낼 알림을 출력만 한다. reap도 전송도 하지 않고, 상태 파일도 바꾸지 않는다.
