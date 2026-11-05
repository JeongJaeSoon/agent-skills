# 운영 안내

용어, 준비물, 설치 보충, 정기 자동화, 사용 시나리오, 안전 경계와 hook, 스킬 고치기를 다룬다. 설치 순서는 [new-pc.md](new-pc.md), 스킬별 세부는 [skills.md](skills.md)에 있다.

## 이 문서에 나오는 말

- **Orca**: 여러 Claude Code 세션을 worktree별 터미널로 띄우는 멀티 에이전트 IDE(`orca` CLI). Orca에서 일 하나를 맡은 세션을 카드, 프로젝트 안에서 티켓 하나를 맡은 카드를 워커라고 부른다.
- **오케스트레이터·리드**: 워커 위의 층은 둘뿐이다. 오케스트레이터는 사람이 말을 거는 최상위 세션 하나로, 요청을 나누고 리드를 띄운다. 리드는 프로젝트 하나를 워커로 끝까지 끌고 가는 세션이다(예전 문서의 "프로그램 코디네이터"). Orca 문서와 출력의 "coordinator"는 Run에 묶인 세션을 가리키며, 여기서는 늘 오케스트레이터나 리드다. 이름·시작 명령·ROLE 줄 표는 [orchestrate의 Roles](../skills/orchestrate/SKILL.md#roles).
- **착지(land)**: 검증된 PR을 `orch land`로 머지하는 것. 공유 파일을 고친 PR은 독점 레인에서 하나씩, 나머지는 준비되는 대로 들어간다.
- **auto mode 분류기**: Claude Code의 auto permission mode에서 행동마다 허용·거부를 판정하는 모델. `autoMode` 규칙이 그 기준이다.
- **`!` 한 줄**: Claude Code 입력칸에서 `!`로 시작하는 줄은 사람이 그 세션에서 셸 명령을 직접 실행한다.

## 준비물

- Claude Code와 GitHub(`gh` 로그인).
- 티켓 트래커: Linear 또는 Jira 어댑터(`use-tracker`). `tracker.adapter`가 없으면 Linear다.
- 노트: Obsidian vault 또는 일반 Markdown 폴더(`use-notes`). 설정하지 않으면 노트를 쓰지 않고 멈추거나 묻는다([설정 전환 안내](configuration-migration.md)).
- Orca: `orchestrate`, `handoff-ticket`, `dispatch-card`, Orca automation(`janitor`, `watch-prs`·`watch-mentions` 회차)이 쓴다. `deliver-ticket`은 Orca 카드 밖에서도 돈다. 스킬마다 Orca를 어디에 쓰는지는 [스킬 카탈로그](skills.md)의 그 스킬 절에 있다.
- 동기화 작업과 `pressure-watch`·`reap-hard`·`orch-dash`·`land-executor`는 Orca automation으로 돌므로 Orca 앱이 켜져 있는 동안만 돈다. macOS에서 `preview-watch`·`share-server`만 launchd를 쓴다. 예전 setup이 만든 LaunchAgent가 남아 있으면 `setup-pc` 점검이 알리고, 제거는 오너가 직접 한다([automation.md](automation.md#처음-설치할-때)).
- 독립 리뷰와 브라우저 검증은 사용 가능한 리뷰어와 브라우저 자동화 도구로 한다. 특정 모델이나 별도 모델 플러그인은 필요하지 않다.

## 설치 보충

**확인**: `setup.py --apply`가 끝에 낸 점검 표(또는 `python3 skills/setup-pc/scripts/setup_check.py check`)에 `missing`·`differs`·`warn`이 없고, 새 세션의 `claude plugin list`에 `agent-skills@inline`이 체크아웃 경로로 보이면 된다. `@inline`은 `CLAUDE_CODE_PLUGIN_DIRS`로 체크아웃을 직접 읽는다는 뜻이고, 이때 마켓플레이스 사본 `agent-skills@jeongjaesoon`은 `setup.py`가 꺼 둔다(둘 다 켜면 hook이 두 번 돈다).

Orca가 프로필마다 `CLAUDE_CONFIG_DIR`을 따로 쓰면 프로필마다 설치 절차를 한 번씩 한다. 설정 제안을 거절하면 그 프로필에서는 다시 묻지 않고(`onboard.py --decline`), 모든 프로필에서 끄려면 `AGENT_SKILLS_ONBOARD=0`.

적용 전부터 떠 있던 세션에서 `/reload-plugins`를 치면 마켓플레이스 사본만 빠지고 체크아웃은 붙지 않아, 재시작할 때까지 agent-skills가 통째로 없다. 체크아웃 로드와 동기화만 원하면 `scripts/bootstrap.py`(dry run, `--write`)를 쓴다. 근거는 [platform.md](platform.md) §5.

고치지 않고 쓰기만 하는 PC는 마켓플레이스 설치본만으로도 스킬과 hook이 돈다. 설치본은 커밋 단위 복사본이라 갱신하기 전까지 그대로이고, 권장 autoMode 규칙·정기 자동화·실시간 반영은 없다. 필요해지면 `setup-pc`("새 PC 세팅해줘")를 부른다.

```bash
claude plugin marketplace add JeongJaeSoon/agent-skills
claude plugin install agent-skills@jeongjaesoon
```

마켓플레이스 설치본의 갱신은 `claude plugin marketplace update jeongjaesoon` 뒤 `claude plugin update agent-skills@jeongjaesoon`이다. 업그레이드 전에 [설정 전환 안내](configuration-migration.md)를 확인한다. 노트·회고·개인 폴더 정리는 명시 설정이 필요하고, 성과 보고 시간대 기본값은 UTC다.

예전 `scripts/install.py`(symlink와 settings 병합)로 설치했다면, 그 설치로 시작한 프로그램이 모두 끝난 뒤 `python3 scripts/migrate.py`(바뀔 내용 보기), `--write`(symlink·옛 규칙·옛 hook 제거 뒤 `bootstrap.py --write`) 순서로 옮긴다. orch-panel mod는 제거됐다. 설치해 두었다면 `claude plugin uninstall orch-panel@jeongjaesoon`으로 지우고, 같은 내용은 대시보드(`orch-dash`)에서 본다.

## 정기 자동화

`skills-sync setup`이 이 체크아웃의 정기 자동화를 한곳에서 켜고 끈다. 같은 명령을 다시 돌려도 결과는 같다. 메인 체크아웃에서 돌린다.

```bash
skills-sync setup                        # 목록: 이름, 지금 상태, 기본값, 하는 일
skills-sync setup apply                  # 기본 on 중 한 번도 설정하지 않은 것만 켠다(setup.py --apply 와 bootstrap --write 가 부른다)
skills-sync setup enable preview-watch   # 하나 켜기
skills-sync setup disable janitor        # 하나 끄기. 어느 것이든 apply 를 다시 돌려도 꺼진 채로 남는다
```

| 이름 | 기본 | 장치 | 하는 일 |
|---|---|---|---|
| `janitor` | on | Orca automation(Orca 필요) | 에이전트가 만든 끝난 리소스를 30분마다 모델 없이 정리한다(에이전트 없는 주기 작업: precheck `orca_job.py run janitor -- reap.py precheck --act`가 정리까지 하고 결과를 `jobs/janitor.json`에 남긴다. 에이전트 회차로 등록된 것은 `skills-sync setup enable janitor`로 맞춘다). compose는 down까지만, `-v`·prune·볼륨/이미지 삭제는 하지 않는다 |
| `janitor-load` | on | Orca automation(Orca 필요) | 30분마다 5분 load만 재고, CPU 수 이상이면 janitor와 같은 정리를 한 번 더 돈다(1시간 안에 돈 회차가 있거나 janitor가 꺼져 있으면 건너뜀) |
| `preview-watch` | off | macOS LaunchAgent | loopback 웹페이지를 tailnet HTTPS로 자동 공개한다. tailscale CLI가 없으면 켜지 않는다 |
| `sync` | on | Orca automation(에이전트 없음) | 15분마다 체크아웃을 origin에 맞춘다(`skills-sync sync`) |
| `pressure-watch` | on | Orca automation(에이전트 없음) | 3분마다 PC 사용량을 재고, 높으면 reap이 올린 안전한 대상만 정리해 알린다. 아무것도 종료하지 않는다([pressure.md](pressure.md)) |
| `reap-hard` | on(보호 목록 파일이 있을 때) | Orca automation(에이전트 없음) | 15분마다 끝난 세션이 남긴 프로세스·창을 규칙으로 정리한다. `patrol`이 꺼져 있으면 세션 순찰도 이 회차가 띄운다([reap-hard.md](reap-hard.md)) |
| `orch-dash` | on | Orca automation(에이전트 없음) | 10분마다 대시보드 서버를 확인해 내려갔으면 올린다 |
| `patrol` | off | Orca automation(에이전트 없음) | 30분마다 모든 에이전트 카드를 모델 없이 순찰한다(`orch patrol --if-due`). 꺼져 있으면 reap-hard가 대신 돈다 |
| `share-server` | off | macOS LaunchAgent | 공유 폴더를 127.0.0.1에서 Caddy로 읽기 전용 서빙한다. tailnet 공개는 하지 않는다([share-server.md](share-server.md)) |
| `land-executor` | off | Orca automation(에이전트 없음) | `orchestrate.land_executor`가 켜진 repo에서 `orch land`가 남긴 머지 요청을 1분마다 모델 없이 다시 검사해 머지한다. agent 턴에서는 머지하지 않는다([landing.md](../skills/orchestrate/references/landing.md#land-executor)) |
| `watch-prs` | on(`prs` 키가 있을 때) | Orca automation | `watch-prs` 스킬의 회차. 설정 키가 없으면 켜지 않는다 |
| `watch-mentions` | on(`mentions` 키가 있을 때) | Orca automation | `watch-mentions` 스킬의 회차. 설정 키가 없으면 켜지 않는다 |

켜고 끄는 세부 동작은 [automation.md](automation.md#처음-설치할-때).

## 선택 설정

- `recall`이 지난 세션을 빨리 찾게 하려면 Orca 앱에서 Settings → Agent Session Search → Search inside sessions의 이 컴퓨터(Local Mac) 스위치를 켠다. CLI로는 켤 수 없고, 켜기 전에는 `recall`이 `~/.claude/projects`의 transcript를 grep한다. 켜졌는지는 `orca search --index-status`의 `enabled`로 확인한다.
- 대시보드는 `setup.py --apply`(또는 `skills-sync setup apply`)가 켠다. 손으로 하려면 메인 체크아웃에서 `skills-sync setup enable orch-dash`를 한다. 10분마다 `ensure`를 돌리는 Orca automation이 걸린다(`orch init`·`orch status`도 ensure를 돌린다). 예전 `orch-dash install-agent` LaunchAgent는 setup이 더 만들지 않는다. 자세한 동작은 `skills/orchestrate/references/dashboard.md`의 "Start it".

## 사용 시나리오

스킬은 description에 적힌 상황이 오면 모델이 스스로 부른다. 직접 부를 때는 `/agent-skills:<이름>`을 쓰고, 내장 명령이나 다른 플러그인과 이름이 겹치지 않으면 `/<이름>`도 된다. `/brief`는 Claude Code 내장 명령이라 `/agent-skills:brief`로 부른다. 아래 따옴표 안은 각 스킬 description에 있는 트리거 문구다.

**1. 티켓 하나를 끝까지: 「이 티켓 끝내줘」, 「머지까지 진행해줘」 → `deliver-ticket`**

티켓 키(예: `ABC-1`)나 내용을 함께 건넨다. 에이전트가 계획, 테스트, 작성자 외 독립 리뷰, PR, CI 확인, 머지, 머지 뒤 남은 일(릴리스·배포 확인)까지 한다. 완료는 PR이 아니라 티켓의 완료 기준을 모두 채운 것이고, 기준마다 근거를 달아 티켓을 닫는다. 혼자 도는 카드는 준비된 PR을 묻지 않고 머지한다. 사람이 hold를 걸었거나 직접 보겠다고 했거나, 계속 flaky한 체크·되돌릴 수 없는 migration 같은 예외 상황이면 머지 전에 묻는다.

**2. 지금 어디까지 왔는지: 「현황 보고해줘」, 「브리핑해줘」 → `brief`**

완료·진행·결정 필요·사람만 할 일로 나눈 짧은 보고가 나온다. 결정 필요에는 승인 범위 밖의 선택만 추천과 함께 올리고, 사람만 할 수 있는 행동은 따로 모은다. 지난 세션 맥락을 되살리는 「어디까지 했지」는 `recall`이다.

**3. 프로젝트 하나를 여러 워커로: 「PM 겸 오케스트레이터로 끝까지」 → `orchestrate`**

이렇게 부탁받은 세션은 이름과 달리 리드다(`ROLE: lead <slug>`). 리드가 티켓마다 Orca 워커를 띄우고, 워커는 `deliver-ticket`으로 일한 뒤 자기 PR을 `orch land`로 착지시킨다. migration·CI 설정 같은 공유 파일은 독점 레인에서 하나씩 머지된다. 사람은 `orch-dash serve` 대시보드에서 목표·완료 조건·착지 순서·워커·토큰을 보고, 결정과 권한 창에 거기서 답한다. 머지 정책 기본값은 `autonomous`(워커가 착지)다. `human-gate`로 정하면 워커는 READY에서 멈추고 사람이 게이트를 풀어야 착지한다. 끝나면 `measure-delivery`가 후속 티켓 증가·재작업·토큰을 잰다.

**4. auto mode가 막았을 때: 「auto mode 가 막았어」, 「이거 허용되게 규칙 추가해줘」 → `tune-automode`**

에이전트는 거부된 행동을 우회하지 않는다. 대신 그 행동 부류만 덮는 가장 작은 autoMode 규칙 초안과 적용 스크립트를 만들고, 사람이 diff를 보고 `! python3 -I <경로> --apply <spec 해시>`로 적용한다. 에이전트는 `settings.json`을 고치지 않는다.

**5. 나를 기다리는 PR: 「리뷰 요청 온 PR 정리해줘」, 「내 PR 리뷰 리마인드」 → `watch-prs`**

`agent-skills.json`에 `prs` 키가 있어야 돈다. 기본(`prs.kinds`가 `["review_request"]`)은 나를 직접 지명한 리뷰 요청만 우선순위대로 한 통으로 모은다. `prs.kinds`에 더한 것만 더 한다: `review_wait`는 리뷰를 기다리는 내 PR의 리마인드, `ready_to_merge`는 머지 허용 목록(`merge_repos`) 저장소의 내 PR 중 모든 게이트를 통과한 것의 머지, `ownergate`는 내 계정만 할 수 있는 봇 PR 승인을 링크·diff 요약·`!` 실행 줄과 함께 사람에게 넘기기다.

## 안전 경계

### 사람만 하는 일

- **사용자 설정 쓰기.** 에이전트가 `~/.claude/settings.json`을 쓰면 auto mode 분류기가 자기수정으로 거부한다. `setup-pc`와 `tune-automode`는 diff와 `!` 한 줄만 만들고, 적용은 사람이 한다([zero-command-setup.md](zero-command-setup.md#결론), `skills/tune-automode/SKILL.md`).
- **되돌릴 수 없는 행동과 사람 몫의 행동.** 파괴적 migration, 공유 브랜치 force push, 바깥으로 보내기, 저장소·브랜치 보호 변경, 프로덕션 배포, 리뷰 규칙이 사람에게 요구하는 승인, 다른 사람의 티켓 앞에서는 멈추고 묻는다(`skills/orchestrate/SKILL.md` "Merge policy"). 권장 autoMode 묶음은 기본 브랜치 force push와 요청받지 않은 프로덕션 단계를 막는다(`scripts/recommended-settings.json`).
- **자격 증명.** `gh auth login`은 사람이 한다. 브라우저 인증 칸의 비밀값은 `secure-fill`이 소유자의 Touch ID 승인 뒤 Keychain에서 페이지로 바로 넣고, 에이전트는 값을 보지 않는다.

### 거부된 행동의 재시도는 한 번이다

권한 창, auto mode 분류기, hook, 사람이 거부한 행동은 다시 하지 않는다. 예외는 한 번뿐이고, 거부된 세션이 같은 형태로 한다. 사람이 그 세션 채팅에서 허락했거나 규칙을 넣었을 때, 또는 분류기나 hook이 말한 사유에 맞는 근거를 보강했을 때만이고, 보고에 `재시도 1회: <보강한 것>`을 적는다. 다시 거부되면 멈춘다. 명령을 바꿔 쓰기, 스크립트로 감싸기, 다른 세션·워커·서브에이전트에게 시키기가 모두 우회다. 거부된 세션은 행동, 목적, 거부 종류, 정확한 명령을 보고한다. 계속 허용하려면 `tune-automode`가 규칙 초안을 만들고 사람이 적용한다(`skills/orchestrate/SKILL.md` "Refused acts", `skills/tune-automode/SKILL.md`).

### hook

| hook | 언제 | 하는 일 |
|---|---|---|
| `guard.py` | Bash·Skill·Edit·Write·MultiEdit·NotebookEdit·AskUserQuestion 호출 전 | 이 플러그인 스킬과 `orch`(`heavy`·`set`·`init`·`backfill` 제외)·`orca orchestration`(reset·worker-abandon·gate-resolve 제외) 명령을 허용하고, 다른 터미널의 Orca 메일함 읽기와 foreground sleep 폴링 루프를 거부한다. `setup.py --actions-only`는 권한 창. 그보다 먼저 패턴 kill·prune·`down -v`·강제 worktree 삭제·사용자 설정 쓰기 같은 위험 명령과, 역할 세션의 AskUserQuestion·foreground 대기·`/dev/null` 전송을 거부한다. 규칙마다 `agent-skills.json` 키로 끄거나 warn으로 낮춘다. 나머지는 평소 권한 흐름을 탄다([권한 결정](skills.md#권한-결정), [위험 명령과 역할 세션 규칙](skills.md#위험-명령과-역할-세션-규칙)) |
| `delegate_gate.py` | 모든 도구 호출 전 | 오케스트레이터 세션(`--agent agent-skills:orchestrator` 또는 `ROLE: orchestrator`)의 본 대화가 조정 밖의 도구를 부르면 `[gate:delegate]` 사유로 거부하고 서브에이전트·카드·`orca orchestration send`로 넘기라고 알린다. 허용: `orch`(`heavy` 제외)·`skills-sync`·`orca` 조정 명령, `gh pr merge`·`ready`, `gh … --json` 읽기, Agent·SendMessage·TaskStop, 상태·출력 Read, 브리프·메모리 Write. 서브에이전트 호출(`agent_id` 있음)과 다른 세션은 그대로 둔다. `~/.claude/agent-skills.json`의 `orchestrate.delegate_gate`를 `false`로 하면 끈다 |
| `pressure_gate.py` | Bash 호출 전·후 | `orch heavy`·`docker compose up`·`orca worktree create`를 PC 사용량이 위험이거나, 경고인데 최근 15분 안에 정리 회차가 없으면 거부하고 정리·대기 방법을 알린다. 위험으로 거부한 명령은 대기열과 대시보드에 남기고, 같은 세션의 재시도 결과를 기록한다. 전체 테스트·E2E·headless Chrome은 `orch heavy` 밖이면 감싼 명령을 알려 주며 거부하고, 새 카드는 진행 중 에이전트 턴이 상한(`reap.pressure.active_turns_per_cpu`)에 닿으면 거부한다. `AGENT_SKILLS_PRESSURE_GATE=0`으로 끈다 |
| `self_land_gate.py` | Bash 호출 전 | 머지를 맡은 프로그램 카드가 자기 PR이 열린 채 `worker_done`이나 `orca worktree rm`을 부르면 알린다. `orchestrate.self_land`를 `deny`로 올리면 막는다. 읽지 못한 것은 통과시키고 한 줄로 알린다 |
| `prose_check.py` | PR·커밋·Linear·Jira 저장 전 | 본문의 write-plainly 금지 패턴을 알린다. 막지 않는다 |
| `stack_gate.py` | `gh pr create` 전 | 브리프의 `Lands after`나 브랜치 이력으로 의존하는 열린 PR이 있는데 base가 그 위가 아니면 알린다. `orchestrate.stack_dependent: deny`면 막는다 |
| `claim_check.py` | 멈출 때(Stop, SubagentStop) | 코드를 고치고 아무것도 돌리지 않은 채 "고쳤다"고 하면 한 번 막는다. `AGENT_SKILLS_CLAIM_CHECK=0`으로 끈다 |
| `owner_check.py` | 멈출 때(Stop, SubagentStop) | 최종 응답에 🙋·"오너 할 일"·오너에게 묻는 표현이 있는데 이번 턴에 `orch decide add`·`orch board now`·`orch board set --owner`·`orch gate`·`orch-dash inbox add`가 없으면 "기록 안 된 오너 항목 N"을 사람에게 한 줄 보이고 `owner-check.jsonl`에 남긴다. 모델에 전달하는 block은 `guard.owner_check: "deny"`로만 켠다([오너 항목 기록 검사](skills.md#오너-항목-기록-검사)) |
| `record_check.py` | Bash 호출 전·후, 멈출 때 | `gh pr merge`·`gh pr create`·`orch land` 뒤 기록을 요구하고, 보고·`worker_done` 본문에 `기록:`·`기록 못 함:` 줄이 나올 때까지 그 세션에 의무를 남긴다. 기록 줄 없는 `worker_done`, 의무가 남은 카드 닫기, 의무를 남긴 채 끝나는 턴을 `guard.record_check`(기본 warn)로 알리거나 막는다. `tracker`·`notes` 설정이 없으면 꺼진다([기록 줄 검사](skills.md#기록-줄-검사)) |
| `artifact_archive.py` | Artifact 게시 뒤 | 게시한 페이지와 `files`로 올린 첨부를 `artifact-backup.public_dir`(기본 `~/share/artifacts`)에 버전별로 복사하고 index.html을 다시 만든다. `<meta name="backup" content="no">`, agent-guard 검출·실패·부재, 스캔 시간 초과면 `private_dir`로 보낸다. 스캔은 게시 한 번에 폴더 단위 한 번이다. 시작 기록을 남겨 두어, hook 시간 제한으로 죽은 실행은 다음 실행이 errors.log에 적고 다시 백업한다. `file_path` 없이 `files`만으로 한 게시도 그 안의 페이지를 백업한다. index.jsonl·errors.log는 `$AGENT_SKILLS_STATE/artifact-backup/`에 두어 서빙하지 않는다. `artifact-backup.base_url`이 없으면 복사는 하되 백업 링크를 만들지 않고 errors.log에 한 줄 남긴다. `artifact-backup.categories`(`[[이름, 정규식], …]`)로 index 분류를 정한다 |
| `artifact_links_stop.py` | 멈출 때(Stop) | 이번 턴에 게시한 지 6시간이 안 된 Artifact의 claude.ai·백업 링크가 최종 응답에 없으면 `guard.artifact_links`와 무관하게 한 번 막는다(`off`로도 끌 수 없다. 게시 뒤 성공한 Telegram·Slack 전송 도구 호출에 링크가 있으면 이 검사는 최종 응답에 없어도 통과하고 그때는 아래 두 번째 검사가 이어서 돌며, 막은 뒤 다시 멈추면 경고만 하고 넘어간다). '새 guard는 warn으로 시작'하는 원칙의 예외다. 게시 직후라 요구할 링크를 이미 알고 있어 오탐이 작다고 보기 때문이다(비공개 페이지는 claude.ai 링크만 요구하고, 다른 세션에서 비공개로 바뀐 페이지는 백업 링크 요구가 빠진다). 이 세션이 게시하지 않았어도, 응답이 공개 백업이 있는 Artifact를 가리키면서 그 최신 백업 링크가 없으면 알린다. 이 두 번째 검사만 `guard.artifact_links`를 따른다(기본 warn, 한 줄. `deny`일 때만 막고 `off`로 끈다). 서브에이전트는 보지 않는다 |
| `artifact_send_check.py` | Telegram 전송 도구와 `orca terminal send`·`orca orchestration send/reply`(worker_done 포함) 호출 전 | 보내려는 글이 공개 백업이 있는 Artifact를 가리키면서 최신 백업 링크가 없으면 알린다(기본 warn, 한 줄). 세션을 건너가며 백업 링크가 빠지는 길이다. `guard.artifact_links: "deny"`일 때만 거부하고 `off`로 끈다. 본문을 읽지 못하면 통과시킨다 |
| `onboard.py` | 세션 시작 | 설정되지 않은 PC에서 `setup-pc`를 제안한다. 거절하면 그 프로필에서는 다시 묻지 않고(`onboard.py --decline`), `AGENT_SKILLS_ONBOARD=0`으로 모두 끈다 |
| `reorient.py` | 세션 시작, 프롬프트 | `--agent agent-skills:orchestrator`·`lead`나 `ROLE:` 줄로 선언한 세션에 다시 읽을 규칙과 매 프롬프트 한 줄을 넣는다 |
| `decision.py` | 프롬프트 | 터미널에서 답한 대시보드 결정을 닫는다 |
| `decision_relay.py` | Bash 호출 전 | 오케스트레이터나 리드가 오너 말을 인용해 전달하는데 결정 기록이 없으면 알린다. 막지 않는다. `guard.decision_relay: off`로 끈다 |
| `permission.py` | 권한 창 | 대시보드에서 답할 수 있게 권한 창을 기록한다. 결정은 내리지 않는다 |

hook마다의 판정 세부는 [skills.md](skills.md#hook).

### 설정이 없으면 끄거나 멈추고 묻는다

스킬은 개인 vault·폴더·채널을 추정하지 않는다. 노트는 `notes` 설정이나 사용자가 명시한 목적지가 있어야 쓰고, 회고는 `reflect.ledger`, 개인 폴더 정리는 `reap.user_folders`에 적은 폴더만 다룬다. `prs`·`mentions` 키가 없으면 watch 자동화를 켜지 않는다. 키별 동작은 [configuration-migration.md](configuration-migration.md).

### 공개 저장소 규칙

이 저장소는 public이다. 파일, 커밋 메시지, PR 본문·코멘트에 회사·조직 이름, 사내 저장소·채널·인명·티켓·URL·로그를 쓰지 않는다. 사내 전용 값은 저장소 밖 `~/.claude/agent-skills.json`과 `~/.config/agent-skills/`에서 읽고, 예시는 `acme/app#1`, `ABC-1`, `example.test` 같은 자리표시자만 쓴다. push 전에 로컬의 사내 이름 목록으로 diff를 grep한다([AGENTS.md](../AGENTS.md)).

## 스킬 고치기

- 고치는 곳은 worktree 브랜치다. 메인 체크아웃은 세션들이 직접 읽으므로 fast-forward로만 바꾼다. 메인 체크아웃에 커밋 안 한 변경이 있으면 자동 동기화가 멈춘다.
- worktree의 스킬로 세션 하나를 띄워 보려면 `claude --settings '{"env":{"CLAUDE_CODE_PLUGIN_DIRS":"<worktree 절대 경로>"}}'`. `--plugin-dir`을 더하면 메인 체크아웃과 두 벌이 함께 올라온다.
- 메인 체크아웃에 들어간 뒤에는 `skills-sync sync`(push까지)와 `skills-sync broadcast`를 돌린다. 브로드캐스트는 `SKILL.md`가 바뀌었으면 `/reload-skills`, hook 연결(`hooks/hooks.json`)이나 manifest가 바뀌었으면 `/reload-plugins`를, 빈 입력칸에서 쉬고 있는 세션에만 보낸다. 권한 창이나 질문 창이 뜬 세션에 Enter가 가면 그 창에 답해 버리기 때문이다. 못 보낸 세션은 남겨 두었다가 다음 동기화 때 다시 시도한다.
- description이나 트리거 문구를 바꿨으면 Claude Code 내장 `claude plugin eval`로 고치기 전 체크아웃과 나란히 발동을 본다. 케이스는 `evals/<스킬>/`에 있다. `prompt.md`에는 사용자가 칠 문장을 넣고, `graders/`는 그 스킬이 떴는지(`tool_used`, `tool: Skill`)와 헷갈리기 쉬운 이웃 스킬이 뜨지 않았는지(`min: 0`, `max: 0`)를 본다. Skill grader는 플러그인 없는 비교 arm을 같이 돌면 점수에서 빠지므로 모두 `arm: both`를 단다. 사용자 설정·다른 플러그인·메모리 없이 격리된 홈에서 읽기 도구만으로 돌기 때문에 다른 곳을 건드리지 않고, 스킬 사용 횟수도 올리지 않는다. 케이스 하나에 0.15달러 안팎이 든다. 바꾼 스킬의 케이스가 없으면, 그 스킬이 떠야 할 문장 하나와 이웃 스킬을 가르는 grader 하나로 케이스를 더한다.

```bash
claude plugin eval <체크아웃> --ablation none --no-publish --trust-plugin --max-cost-usd 5   # 전체, 케이스마다 3회
claude plugin eval <체크아웃> --case recall --runs 1 --ablation none --no-publish --trust-plugin
```

`--no-publish`를 빼면 claude.ai 계정에서는 보고서가 artifact로 게시된다. `--trust-plugin`은 터미널 없이 돌 때 첫 실행의 신뢰 확인을 대신한다. 결과는 `evals/results/`(git 제외)에 남는다.

카탈로그 페이지는 `python3 scripts/catalog/build.py <out.html>`이 `docs/skills.md`와 번역본 `docs/skills.{en,ja}.md`로 만든다. 번역본 첫 줄의 `translated-from` 커밋 뒤에 `docs/skills.md`가 바뀌었으면 경고하고, 그 언어 하단에 원문보다 오래됐다고 표시한다. 안내서는 손으로 쓴 한 장짜리 `scripts/catalog/guide.html`이라, 스킬이 늘거나 흐름이 바뀌면 세 언어를 함께 고친다.

명령은 `bin/`에 있다: `orch`(프로그램 원장과 착지 게이트), `orch-dash`(대시보드), `skills-sync`(동기화와 reload 브로드캐스트), `secure-fill`, 그리고 LaunchAgent 도구 `preview-watch`(열린 링크는 `preview-links`)·`share-server`·`reap-hard`(문서: [preview-watch](preview-watch.md), [share-server](share-server.md), [reap-hard](reap-hard.md)). 대시보드 페이지 구성은 `skills/orchestrate/references/dashboard.md`.

pstack upstream은 이렇게 따라간다. 출처와 수정 내용은 `vendor/pstack/NOTICE.md`, 고정 커밋은 `vendor/pstack/manifest.json`.

```bash
python3 scripts/pstack-sync.py            # upstream 변경 확인 (쓰지 않음)
python3 scripts/pstack-sync.py --write    # 충돌이 없을 때만 반영하고 pin 갱신
```
