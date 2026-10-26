# agent-skills

구조와 흐름, 사람이 끼어드는 지점을 한 장으로: [랜딩 페이지](https://jeongjaesoon.github.io/agent-skills/)(`docs/index.html`).

Claude Code 개인 스킬 저장소. 티켓 하나를 끝까지 끌고 가는 흐름과, Orca 워커 여러 개로 프로젝트 하나를 끝내는 흐름을 스킬로 만든다. 일부 스킬은 Lauren Tan의 [pstack](https://github.com/cursor/plugins)(MIT)에서 가져왔다.

한눈에 보기: [스킬 카탈로그](https://claude.ai/artifact/16394ZGS3RX9sTU6h2ME8d)(스킬별 언제·무엇, `docs/skills.md`에서 생성), [사용 안내서](https://claude.ai/artifact/1ifoY8ykKHBqcZMrKN9ACR)(흐름과 `orch` 착지 과정 그림).

두 페이지는 한국어·English·日本語를 오른쪽 위에서 고른다. 링크 끝에 `#en`이나 `#ja`를 붙이면 그 언어로 열린다. 카탈로그는 `python3 scripts/catalog/build.py <out.html>`이 `docs/skills.md`와 번역본 `docs/skills.{en,ja}.md`로 만든다. 번역본 첫 줄의 `translated-from` 커밋 뒤에 `docs/skills.md`가 바뀌었으면 경고하고, 그 언어 하단에 원문보다 오래됐다고 표시한다. 안내서는 손으로 쓴 한 장짜리 `scripts/catalog/guide.html`이라, 스킬이 늘거나 흐름이 바뀌면 세 언어를 함께 고친다.

전제는 Orca(멀티 에이전트 IDE, `orca` CLI), GitHub, 티켓 트래커(Linear, Jira 어댑터 준비)다. 노트는 Obsidian vault 또는 일반 Markdown 폴더를 쓴다.

선택 도구: 독립 리뷰는 사용 가능한 리뷰어와 도구로, 브라우저 검증은 브라우저 자동화 도구로 수행한다. 특정 모델이나 별도 모델 플러그인은 필요하지 않다.

## 설치

새 PC는 Claude Code 세션 안에서 `/plugin marketplace add JeongJaeSoon/agent-skills`, `/plugin install agent-skills@jeongjaesoon`을 하고 새 세션을 열면 된다. 플러그인이 설정되지 않은 PC임을 알아채고 `setup-pc`를 제안하고, 받아들이면 체크아웃·개인 설정 저장소 clone, diff, 동기화·자동화(권한 창)까지 에이전트가 한다. 사람이 치는 셸 명령은 Claude 설정을 쓰는 `!` 한 줄뿐이다. 순서는 [docs/new-pc.md](docs/new-pc.md), 그 한 줄이 남는 이유는 [docs/zero-command-setup.md](docs/zero-command-setup.md).

셸에서 직접 할 때. 이 저장소를 고쳐 가며 쓰는 PC는 체크아웃을 Claude Code가 직접 읽게 한다. 스킬을 고치면 떠 있는 세션에도 reload 한 번으로 반영되고, 원격 변경은 15분마다 자동으로 받는다. 설계와 실측 근거는 [docs/platform.md](docs/platform.md).

체크아웃은 어느 경로에 두어도 된다. 스크립트는 자기 파일 위치로 체크아웃 경로를 정한다. 아래 `<체크아웃>`은 clone한 경로로 바꾼다(예시: `~/src/agent-skills`).

```bash
git clone https://github.com/JeongJaeSoon/agent-skills <체크아웃>
python3 <체크아웃>/scripts/setup.py                 # 바뀔 내용: 파일별 diff 와 마지막의 --apply 줄
python3 <체크아웃>/scripts/setup.py --apply <해시>  # 위가 출력한 줄 그대로
```

`setup.py --apply`는 `~/.claude/settings.json`과 `agent-skills.json`을 백업하고, 저장소의 권장 기본값(`scripts/recommended-settings.json`: 체크아웃 로드, `language`·`outputStyle`·기본 permission mode, autoMode·`permissions.allow` 묶음, 최소 설정 키) 중 없는 것만 더한 뒤, 15분마다 도는 동기화 작업과 기본 on 정기 자동화를 켜고 `setup-pc` 점검 표를 낸다. 이미 있는 값은 덮지 않고(켜져 있는 마켓플레이스 사본을 끄는 것만 예외), 다시 돌리면 바뀌는 것이 없다. 하는 일·옮기지 않는 것·갱신(`--update`)은 [docs/new-pc.md](docs/new-pc.md). 새 설정은 새 세션부터 적용되므로 떠 있는 세션은 재시작한다. 거기서 `/reload-plugins`를 치면 마켓플레이스 사본만 빠지고 체크아웃은 붙지 않아, 재시작할 때까지 agent-skills가 통째로 없다. 체크아웃 폴더에서 `claude`를 처음 띄우면 폴더 신뢰 창이 한 번 뜬다.

체크아웃 로드와 동기화만 원하면 `scripts/bootstrap.py`(dry run, `--write`)를 쓴다. 설계와 실측 근거는 [docs/platform.md](docs/platform.md) §5.

### 정기 자동화

`skills-sync setup`이 이 체크아웃의 정기 자동화를 한곳에서 켜고 끈다. 같은 명령을 다시 돌려도 결과는 같다. 메인 체크아웃에서 돌린다.

```bash
skills-sync setup                        # 목록: 이름, 지금 상태, 기본값, 하는 일
skills-sync setup apply                  # 기본 on 중 한 번도 설정하지 않은 것만 켠다(setup.py --apply 와 bootstrap --write 가 부른다)
skills-sync setup enable preview-watch   # 하나 켜기
skills-sync setup disable janitor        # 하나 끄기. apply 를 다시 돌려도 꺼진 채로 남는다
```

| 이름 | 기본 | 장치 | 하는 일 |
|---|---|---|---|
| `janitor` | on | Orca automation(Orca 필요) | 에이전트가 만든 끝난 리소스를 3시간마다 정리한다. compose는 down까지만, `-v`·prune·볼륨/이미지 삭제는 하지 않는다 |
| `janitor-load` | on | Orca automation(Orca 필요) | 30분마다 5분 load만 재고, CPU 수 이상이면 janitor와 같은 정리를 한 번 더 돈다(1시간 안에 돈 회차가 있거나 janitor가 꺼져 있으면 건너뜀) |
| `preview-watch` | off | macOS LaunchAgent | loopback 웹페이지를 tailnet HTTPS로 자동 공개한다. tailscale CLI가 없으면 켜지 않는다 |
| `pressure-watch` | on | macOS LaunchAgent | 3분마다 PC 사용량을 재고, 높으면 reap이 올린 안전한 대상만 정리해 알린다. 아무것도 종료하지 않는다([docs/pressure.md](docs/pressure.md)) |
| `watch-prs` | on(`prs` 키가 있을 때) | Orca automation | `watch-prs` 스킬의 회차. 설정 키가 없으면 켜지 않는다 |
| `watch-mentions` | on(`mentions` 키가 있을 때) | Orca automation | `watch-mentions` 스킬의 회차. 설정 키가 없으면 켜지 않는다 |

자세한 동작과 다른 OS의 대안은 [docs/automation.md](docs/automation.md#처음-설치할-때).

고치지 않고 쓰기만 하는 PC는 마켓플레이스로 설치해도 된다. 설치본은 커밋 단위 복사본이라 갱신은 `claude plugin marketplace update jeongjaesoon` 뒤 `claude plugin update agent-skills@jeongjaesoon`이다.

```bash
claude plugin marketplace add JeongJaeSoon/agent-skills
claude plugin install agent-skills@jeongjaesoon
```

- 스킬 이름은 `/agent-skills:<이름>`. 다른 플러그인과 겹치지 않으면 `/<이름>`도 된다.
- 권한: 플러그인은 권한 규칙을 설정으로 실을 수 없어 `hooks/guard.py`가 대신 결정한다. 이 플러그인의 스킬, `orca orchestration` 명령(reset·worker-abandon·gate-resolve 제외), `orch` 명령(`heavy`·`set`·`init`·`backfill` 제외)을 허용하고, 다른 터미널의 Orca 메일함 읽기와 foreground sleep 폴링 루프를 거부한다. 나머지는 평소 권한 흐름을 탄다. 자세한 규칙은 [스킬 카탈로그](docs/skills.md#권한-결정).

예전 `scripts/install.py`(symlink와 settings 병합)로 설치했다면, 그 설치로 시작한 프로그램이 모두 끝난 뒤 `python3 scripts/migrate.py`(바뀔 내용 보기), `--write`(symlink·옛 규칙·옛 hook 제거 뒤 `bootstrap.py --write`) 순서로 옮긴다.

### 다른 PC에서 같은 방식으로 쓰려면

일하는 방식(보고 모양, 위임, 결정, 리뷰 의뢰, 검증)은 스킬이 정하므로 설치만으로 따라온다. 스킬이 실을 수 없는 사용자 설정과 자동화는 위의 `setup.py`가 넣는다. 원래 PC에서 옮기는 것을 전제로 하지 않는다. 채널·트래커 같은 개인 설정(`agent-skills.json`과 트래커 사이트 규칙)은 설치 창에 적은 개인 private 저장소에서만 가져오고, 사내 이름 목록, 개인 지침, 개인 autoMode 규칙은 옮기지 않는다. 자세한 내용은 [docs/new-pc.md](docs/new-pc.md). Claude Code 안에서는 `setup-pc` 스킬("새 PC 세팅해줘")이 dry run을 보이고 `!` 한 줄을 건넨다.

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

트래커는 설정된 어댑터를 사용한다(기존 기본값 Linear). 노트는 `~/.claude/agent-skills.json`의 `notes` 설정이나 사용자가 명시한 목적지가 있어야 쓴다. `reflect.ledger`·`reflect.skill_repository`로 회고 위치와 편집할 원본을 지정하고, `reap.user_folders`에 명시한 개인 폴더만 정리한다. 설정이 없으면 개인 vault나 폴더를 추정하지 않는다. **업그레이드 전에 [설정 전환 안내](docs/configuration-migration.md)를 확인한다.** 기존 노트·회고·개인 폴더 정리는 명시 설정이 필요하며, 성과 보고 시간대 기본값은 UTC로 바뀌었다. 기존 설정 파일을 통째로 덮어쓰거나 원장을 옮기지 않는다.

`recall`이 지난 세션을 빨리 찾게 하려면 Orca 앱에서 Settings → Agent Session Search → Search inside sessions의 이 컴퓨터(Local Mac) 스위치를 켠다. CLI로는 켤 수 없고, 켜기 전에는 `recall`이 `~/.claude/projects`의 transcript를 grep한다. 켜졌는지는 `orca search --index-status`의 `enabled`로 확인한다.

## 스킬

스킬별 호출 시점, 실제 내용, 동봉 파일, `orch` 명령, hook, pstack과의 차이는 [스킬 카탈로그](docs/skills.md)에 있다.

**티켓 하나**

| 스킬 | 언제 |
|---|---|
| `write-ticket` | 티켓을 쓸 때. 후속 티켓 형식, 템플릿 |
| `deliver-ticket` | 첫 수정부터 완료까지: 계획, 테스트, 독립 검토, GitHub stack, E2E, 머지, 완료 기준 |
| `ui-review` | 시안·화면·UI PR을 사람에게 보이기 전 관문. 1280·390에서 행 높이·버튼 폭·정렬·여백·밑줄·색을 headless Chrome으로 재고, 시나리오 워크스루로 시스템 단계 노출·중복 옵션·구성을 본다 |
| `handoff-ticket` | 끝난 뒤 다음 티켓을 새 Orca 카드로 |
| `dispatch-card` | 이 세션은 계속하면서 다른 저장소나 곁가지를 카드로 |
| `end-session` | 세션·카드·worktree 정리. Orca 워커와 브리프로 띄운 카드는 일을 마치면 스스로 닫는다 |
| `record-progress` | PR·리뷰·머지·조사 결론마다 묻지 않고 기록: 트래커에는 근거 있는 상태, 노트에는 판단의 이유 |

**프로젝트 하나 (Orca 여러 워커)**

| 스킬 | 언제 |
|---|---|
| `orchestrate` | 코디네이터가 여러 티켓을 병렬로 착지시킬 때. 착지 순서, 독점 레인, main 가디언, QA 리드, 대시보드 |
| `measure-delivery` | 프로젝트가 실제로 어땠는지: 후속 티켓 증가, 재작업, 토큰 |

**머신 하나**

| 스킬 | 언제 |
|---|---|
| `reap-resources` | 죽은 세션이 남긴 고아 테스트 프로세스, Docker 잔여물, 머지된 브랜치를 점검하고 목록에 올린 것만 정리할 때. 에이전트가 만든 끝난 리소스와 명시적으로 설정한 개인 폴더의 기준 기간이 지난 항목은 3시간 주기 automation이 휴지통으로 정리한다. 닫힌 지 7일 지난 프로그램 상태는 `_archive/`로 보관하고(재개 노트가 있으면 남김), hook 세션 파일과 `sync.log`도 정리한다. `pressure.py`는 모델 없이 3분마다 load·메모리 압박·swap·디스크·컨테이너 수를 재고, 높으면 안전한 대상부터 정리해 오케스트레이터에 한 줄 알린다([docs/pressure.md](docs/pressure.md)) |
| `secure-fill` | 브라우저 인증 화면에 개발용 토큰 같은 비밀값을 넣을 때. 소유자가 매번 Touch ID로 승인하고(항목별로 승인 없이 채우게 둘 수 있다), 값은 에이전트를 거치지 않고 Keychain에서 페이지로 간다 |
| `tune-automode` | auto mode 분류기 거부 하나를 그 행동 부류만 덮는 가장 작은 autoMode 규칙으로 바꿀 때. diff와 적용 스크립트를 만들고, 적용은 사용자가 `! python3 <경로> --apply <spec 해시>`로 한다 |
| `setup-pc` | 다른 PC에 들이거나 설치된 PC를 최신으로 맞출 때. 설치 뒤 첫 세션에 플러그인 hook이 제안한다. 체크아웃·개인 설정 저장소를 clone하고, `scripts/setup.py`의 dry run으로 설정 파일별 diff를 보여 사람이 칠 `--apply <해시>` 한 줄을 건넨다. 동기화·정기 자동화와 점검 표는 `--actions-only`로 권한 창 뒤에서 직접 돌린다. 갱신은 `--update`. 원래 PC의 기준 파일과 비교하는 점검은 선택 |

**도구**: `preview-watch`(loopback에서 뜬 웹페이지(`GET /`가 HTML 문서)를 tailnet HTTPS로 자동으로 열고 내려가면 자기가 연 항목만 닫는 LaunchAgent. API·DB는 열지 않는다. 모델 없이 15초마다 `lsof` 한 번, 판정은 포트·프로세스별로 캐시. 기본 off이고 `skills-sync setup enable preview-watch`로 켠다. `bin/preview-links`로 열린 링크를 본다. [docs/preview-watch.md](docs/preview-watch.md)). `share-server`(공유 폴더를 127.0.0.1에서 Caddy `file_server`로 읽기 전용 서빙한다. 최근 순 목록·Grid 썸네일, `.md` 렌더(`?raw`는 원문), 텍스트는 UTF-8. 숨김 경로는 404, 루트 밖 심볼릭 링크는 sandbox-exec로 막는다. 기본 off이고 `skills-sync setup enable share-server`로 켠다. [docs/share-server.md](docs/share-server.md)).

`reap-hard`(끝난 카드·스택·미리보기 포트·tailscale serve 항목을 모델 없이 고정 규칙으로 닫는 LaunchAgent. 15분마다, 등록 여부와 상관없이 `~/orca/workspaces` 아래 카드 전부를 본다. 보호 목록 파일이 없으면 아무것도 하지 않고, 사람이 띄운 셸의 프로세스는 건드리지 않는다. 마지막 실행은 정기 보고에 나온다. [docs/reap-hard.md](docs/reap-hard.md)).

**채팅**: `watch-mentions`(공개 채널의 멘션·이름 언급·참여 스레드 새 답글을 분류해 코디네이터에게 한 통으로 보내고, 작업 의뢰는 기표 초안으로).

**GitHub**: `watch-prs`(팀·CODEOWNERS가 아니라 나를 지명한 리뷰 요청만 골라 우선순위대로 코디네이터에게 한 통으로 보낸다. 리뷰한 PR에 재요청 없이 새 커밋이 오면 한 번 다시 알린다. dev 경로를 선언한 저장소에서 머지된 내 PR은 저장소마다 릴리스 워커 하나를 띄워 dev 반영·동작·로그를 확인하게 한다. 내 PR 중 리뷰를 기다리는 것은 송신 로그 규칙에 따라 Slack 리뷰 의뢰·리마인드와 리뷰 봇 재트리거로 바꾼다. 설정하면 허용 목록 저장소의 내 PR 중 승인·green·CLEAN이거나 리뷰가 필요 없는 저장소에서 head 리뷰 통과 기록이 있는 CLEAN인 것을 머지하고 기본 브랜치 CI까지 보고한다. 내 계정만 할 수 있는 봇 PR 승인은 링크·diff 요약·프로덕션 여부·`!` 실행 줄과 함께 묶어 보내고 대시보드 inbox에 올린다. 30분 주기 automation, 모델 없는 precheck. 세션은 백그라운드 셸로 GitHub 알림을 폴링하다 새 항목이 오면 깨어나며, 리뷰 요청은 나를 직접 지명한 것만 한 번 알린다).

**어댑터**: `use-tracker`(Linear, Jira), `use-notes`(Obsidian, Markdown).

**글쓰기**: `write-plainly`(한국어·영어 문체), `map-features`(PRD·현황 문서용 제품 기능 지도: 커밋 하나를 기준으로 앱 표와 기능별 상태·근거), `brief`(현황 보고: 완료·진행·결정·사람만, Telegram이면 markdown 없는 짧은 줄, Claude Code에서는 `/agent-skills:brief`로도), `prune-comments`(diff의 주석 정리), `write-skill`(스킬을 쓰거나 고친 뒤의 점검: 겹침 확인, prompt audit, 사내 이름 grep, 테스트, reload).

**pstack**: `architect`, `arena`, `blast-radius`, `create-verification-skill`, `figure-it-out`, `how`, `interrogate`, `maintain-verification-skill`, `principles`, `recall`, `reflect`, `show-me-your-work`, `swarm`, `tdd`, `teach`, `why`. 원본 이름을 유지해 upstream을 따라간다.

```bash
python3 scripts/pstack-sync.py            # upstream 변경 확인 (쓰지 않음)
python3 scripts/pstack-sync.py --write    # 충돌이 없을 때만 반영하고 pin 갱신
```

출처와 수정 내용은 `vendor/pstack/NOTICE.md`, 고정 커밋은 `vendor/pstack/manifest.json`.

**hook**

| hook | 언제 | 하는 일 |
|---|---|---|
| `guard.py` | Bash·Skill 호출 전 | 이 플러그인 스킬과 `orch`·`orca orchestration` 명령 허용, 남의 메일함 읽기·sleep 폴링 거부, `setup.py --actions-only`는 권한 창 |
| `orchestrator_guard.py` | 모든 도구 호출 전 | 최상위 오케스트레이터 본인 스레드에서만 브라우저·채팅 전송·웹 도구, 상태 디렉터리 밖 편집, `tail`로 파이프한 배경 감시자, 턴당 네 번째 직접 작업 호출을 거부하고 위임 경로를 알린다. `AGENT_SKILLS_ORCHESTRATOR_GUARD=0`으로 끈다 |
| `pressure_gate.py` | Bash 호출 전 | `orch heavy`·`docker compose up`·`orca worktree create`를 PC 사용량이 위험이거나, 경고인데 최근 15분 안에 정리 회차가 없으면 거부하고 정리·대기 방법을 알린다. `AGENT_SKILLS_PRESSURE_GATE=0`으로 끈다 |
| `prose_check.py` | PR·커밋·Linear·Jira 저장 전 | 본문의 write-plainly 금지 패턴을 알린다. 막지 않는다 |
| `claim_check.py` | 멈출 때(Stop, SubagentStop) | 코드를 고치고 아무것도 돌리지 않은 채 "고쳤다"고 하면 한 번 막는다. `AGENT_SKILLS_CLAIM_CHECK=0`으로 끈다 |
| `onboard.py` | 세션 시작 | 설정되지 않은 PC에서 `setup-pc`를 제안한다. 거절하면 그 프로필에서는 다시 묻지 않고(`onboard.py --decline`), `AGENT_SKILLS_ONBOARD=0`으로 모두 끈다 |
| `reorient.py` | 세션 시작, 프롬프트 | `--agent agent-skills:orchestrator`·`lead`나 `ROLE:` 줄로 선언한 세션에 다시 읽을 규칙과 매 프롬프트 한 줄을 넣는다 |
| `decision.py` | 프롬프트 | 터미널에서 답한 대시보드 결정을 닫는다 |
| `permission.py` | 권한 창 | 대시보드에서 답할 수 있게 권한 창을 기록한다 |

## orchestrate 한눈에

- 워커가 자기 PR을 `orch land`로 착지시킨다. 준비된 PR은 base보다 뒤처져 있어도 병렬로 머지되고, migration·CI·Dockerfile·compose 같은 공유 파일은 독점 레인에서 base당 하나씩 최신 base 위에서 머지된다.
- 의존 관계는 Orca task deps가 정본이다. 순서대로 들어가야 하는 체인은 GitHub stack으로 한 번에 머지한다.
- main이 red가 되면 main 가디언이 flake 여부부터 보고 hotfix나 revert를 고른다. QA 리드가 티켓 검증, 주기 E2E, 설계·코드 정합성 감사를 맡는다.
- `orch-dash serve`: 목표, 완료 조건, 착지 순서, 의존 그래프, 워커, 토큰을 보여주는 대시보드(라이트·다크, 창 크기에 맞춤). 모델 호출 없이 JSON으로 갱신한다. fleet의 Work 페이지는 `orch stage` 기록(`stages.json`)을 기능/epic·작업·PR·머지·dev·prod 표로 보이고, 기록이 바뀌면 다음 5초 poll에 반영한다. Automations 페이지는 Orca automation의 주기·다음/마지막 실행·최근 6회 결과와 `▲` 경고를, Monitors 페이지는 살아 있는 Claude Code 세션이 띄운 백그라운드 명령·Monitor의 실행 여부와 죽은 것을, Sent 페이지는 세션이 다른 세션·카드·서브에이전트·inbox·Slack·트래커로 보낸 지시의 원문을 `orch sent add` 기록·대시보드 입력·살아 있는 세션 트랜스크립트에서 모아, PR stats 페이지는 내가 연 PR과 에이전트가 나 대신 연 PR을 월·주·요일×시간·저장소별로 세어 추세선과 함께, Sessions 페이지는 착지 lane과 `orch hold` 자원의 보유자와 시간을 보인다. Skill changes 페이지는 agent-skills 가 스스로 바꾼 것(개선 signal·열린 PR·커밋)을 스킬별·시간순으로, Data flow 페이지는 로컬 상태 파일마다 누가 쓰고 누가 읽는지와 마지막 변경·크기·줄 수, 오래된 파일·지도에 없는 파일·입력이 없는 소비자를 stat 만으로 보인다.

macOS에서는 메인 체크아웃에서 `orch-dash install-agent`를 한 번 실행하면 launchd가 대시보드를 로그인·크래시·코드 갱신 뒤에도 띄워 둔다(`uninstall-agent`로 제거). 자세한 동작은 `skills/orchestrate/references/dashboard.md`의 "Start it".

orch-panel mod는 제거됐다. 설치해 두었다면 `claude plugin uninstall orch-panel@jeongjaesoon`으로 지우고, 같은 내용은 대시보드(`orch-dash`)에서 본다.

## 테스트

```bash
for t in skills/orchestrate/scripts/test_*.py skills/use-tracker/scripts/test_tracker.py skills/measure-delivery/scripts/test_measure.py skills/reap-resources/scripts/test_reap.py skills/reap-resources/scripts/test_pressure.py skills/watch-mentions/scripts/test_mentions.py skills/watch-prs/scripts/test_prs.py skills/watch-prs/scripts/test_review_wait.py skills/watch-prs/scripts/test_notify.py skills/secure-fill/scripts/test_secure_fill.py skills/tune-automode/scripts/test_automode_rule.py skills/setup-pc/scripts/test_setup_check.py scripts/test_setup.py skills/brief/scripts/test_table_width.py skills/deliver-ticket/scripts/test_shots.py skills/ui-review/scripts/test_ui_measure.py hooks/test_*.py scripts/test_sync.py scripts/test_preview_watch.py scripts/test_automations.py scripts/test_share_server.py scripts/test_reap_hard.py scripts/test_public_shapes.py; do python3 "$t"; done
bash scripts/pstack-sync-test.sh
```

## 라이선스

MIT. `vendor/pstack`과 거기서 가져온 스킬은 원저작자의 MIT 라이선스를 따른다(`vendor/pstack/LICENSE`).
