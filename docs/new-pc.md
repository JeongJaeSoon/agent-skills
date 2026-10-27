# 새 PC에서 같은 방식으로 쓰기

일하는 방식(보고 모양, 위임, 결정, 리뷰 의뢰, 검증)은 스킬이 정하므로 설치만으로 따라온다. 스킬이 실을 수 없는 사용자 설정·최소 설정 키·정기 자동화는 `scripts/setup.py`가 넣는다. 원래 PC에서 옮겨 오는 것을 전제로 하지 않고, 저장소가 가진 권장 기본값(`scripts/recommended-settings.json`)과 사용자의 개인 설정 저장소(있으면)만 쓴다.

## 설치와 승인 창으로

사람이 직접 치는 셸 명령은 설정 쓰기 한 줄뿐이다. 왜 그 한 줄이 남는지는 [zero-command-setup.md](zero-command-setup.md).

1. Claude Code 세션 안에서 `/plugin marketplace add JeongJaeSoon/agent-skills`, `/plugin install agent-skills@jeongjaesoon`. 설치 창이 응답 언어, 체크아웃 경로(기본 `~/src/agent-skills`), 개인 설정 저장소 URL을 묻는다. 모두 비워도 된다. 셸의 `claude plugin install`은 이 창을 띄우지 않는다.
2. 새 세션을 연다. 플러그인의 SessionStart hook이 설정되지 않은 PC임을 알아채고 `setup-pc`를 제안한다. 받아들이면 에이전트가 체크아웃과 개인 설정 저장소를 clone하고 dry run으로 diff를 보인다.
3. `gh`가 로그인돼 있지 않으면 `! gh auth login`.
4. diff를 읽고 에이전트가 건넨 `! python3 -I <체크아웃>/scripts/setup.py --settings-only … --apply <해시>` 한 줄을 붙여 넣는다.
5. 에이전트가 `setup.py --actions-only`를 실행하면 권한 창이 뜬다. 동기화 작업과 정기 자동화를 켜는 단계이고 Claude 설정은 쓰지 않는다. 승인한다.
6. 세션을 재시작한다.

Orca가 프로필마다 `CLAUDE_CONFIG_DIR`을 따로 쓰면 프로필마다 1~6을 한 번씩 한다. 프로필마다 따로인 것은 `settings.json`뿐이고, `agent-skills.json`은 스킬의 스크립트가 읽는 한 파일(`~/.claude/agent-skills.json`)이라 프로필끼리 같이 쓴다.

2의 제안을 거절하면 에이전트가 `hooks/onboard.py --decline`을 돌려 그 프로필에서는 다시 묻지 않는다(기록은 `~/.local/state/agent-skills/onboard-declined.json`, 그 줄을 지우면 다시 묻는다). 모든 프로필에서 끄려면 `AGENT_SKILLS_ONBOARD=0`.

### 개인 설정 저장소

private git 저장소 하나에 `agent-skills.json`을 둔다. 에이전트가 `~/.config/agent-skills/personal`로 clone하고 다음 dry run부터 diff에 넣는다. URL에 토큰을 넣지 않는다. hook은 https URL의 인증 정보를 지운 URL로 clone하게 하지만, 설치 창에 넣은 값 그대로는 Claude Code의 `pluginConfigs`에 남는다. 인증은 `gh` 로그인(`gh auth setup-git`)으로 받는다. 있는 값은 덮지 않고(이 PC의 값과 다르면 `kept` 줄로 보인다), 권장 묶음처럼 한 번 제안한 키는 지운 뒤 다시 제안하지 않는다. 동기화 폴더를 쓰려면 같은 형식의 폴더를 그 경로에 두면 된다.

- `agent-skills.json`: `~/.claude/agent-skills.json`에 더할 키(`tracker`, `notes`, `prs`, `mentions` 등). 같은 키가 권장 기본값에도 있으면 이쪽이 이긴다.
- 트래커 사이트 규칙 Markdown: 같은 폴더에 두고 `tracker.rules_file`에 상대 경로로 적으면 그 폴더 기준 절대 경로로 들어간다.

개인 에이전트와 개인 autoMode 규칙은 옮기지 않는다. 쓰기 agent는 플러그인에 있고, agent-skills에 필요한 autoMode 규칙은 저장소 기본값에 있다.

`prs`·`mentions` 키가 들어오면 그다음 `--actions-only`가 watch-prs·watch-mentions automation을 만든다.

## 셸에서 직접

```bash
git clone https://github.com/JeongJaeSoon/agent-skills <체크아웃>
python3 <체크아웃>/scripts/setup.py --language Korean     # 바뀔 내용: 파일별 diff, 마지막 줄에 --apply 줄
python3 -I <체크아웃>/scripts/setup.py --language Korean --apply <해시>
```

dry run은 아무것도 쓰지 않는다. 두 번째 줄은 첫 줄이 출력한 것을 그대로 친다. 해시는 diff에 나온 계획 전체(쓸 파일 내용과 이어서 할 일)의 해시라서, 그 사이에 무엇이 바뀌면 거절하고 dry run을 다시 하라고 한다. 이미 설치한 PC에서 Claude Code를 쓰고 있다면 `setup-pc` 스킬("새 PC 세팅해줘")이 dry run을 돌려 diff를 보이고 `!` 한 줄을 건넨다.

`--apply`가 하는 일:

1. `~/.claude/settings.json`, `~/.claude/agent-skills.json`을 각각 타임스탬프 백업한다.
2. 없는 값만 더한다. 이미 있는 값은 덮지 않는다(`kept` 줄로 보인다). 예외는 하나다: 마켓플레이스 사본 `agent-skills@jeongjaesoon`이 켜져 있으면 끈다(`enabledPlugins`의 `true`→`false`, `changed` 줄로 보인다). 둘 다 켜면 hook이 두 번 돈다.
   - `env.CLAUDE_CODE_PLUGIN_DIRS`에 체크아웃 경로.
   - `language`(`--language`, 없으면 시스템 locale), `outputStyle`, `permissions.defaultMode`(`auto`).
   - `permissions.allow` 묶음: PR 보기·checks, `skills-sync status`·`broadcast`, `orch-dash`의 조회·서버 하위 명령(`ensure`·`serve`·`collect`·`fleet`·`skills`·`prstats`). allow는 분류기를 건너뛰어 저장소 한정이 걸리지 않으므로, PR 만들기·고치기는 넣지 않고 아래 autoMode의 사용자 저장소 규칙이 맡는다. launchd 상주 설치(`install-agent`)와 받은편지함 쓰기(`inbox add`·`resolve`)도 넣지 않는다. `orca orchestration`과 `orch`는 플러그인 hook이 이미 허용한다.
   - autoMode 규칙 묶음: 체크아웃 안 편집, 요청받은 로컬 정리, 사용자 저장소의 PR 작업, 사용자가 지시한 머지, 오케스트레이터의 워커 지시·받은편지함, 워커의 자기 작업 공간, janitor 회차, 저장소가 승인을 요구하지 않을 때 자기가 연 PR·번호로 지명된 PR의 머지(워커는 브리프 APPROVED가 머지를 허락할 때만)와 그 앞의 별도 리뷰어·리뷰봇 지적 해제(통과한 체크가 하나 이상이고 실패·대기 없음, 오케스트레이션 프로그램의 PR 제외), 검증 증거 첨부, 로컬·개발 환경 VERIFY, 리드의 자기 카드 권한 창 응답, 그리고 "사용자 본인의 채팅만 지시로 친다". 막는 쪽은 기본 브랜치 force push와 요청받지 않은 프로덕션 단계. 규칙마다 `Not covered:`로 한계를 적는다. 체크아웃 경로와 `gh` 로그인 이름은 실행할 때 채운다.
   - `agent-skills.json`: `reflect.skill_repository`(체크아웃), `reap.user_folders`(빈 배열 = 끔).
3. 임시 파일에 써서 원자적으로 바꾸고, 다시 읽어 같은지 확인한 뒤 파일마다 되돌리는 `cp` 줄을 출력한다.
4. 15분마다 `skills-sync sync`를 돌리는 launchd 작업을 설치하고(Linux면 crontab 줄을 출력), 한 번 동기화하고, 기본 on 정기 자동화를 켠다(`skills-sync setup apply`, [operations.md](operations.md#정기-자동화)).
5. `--orca-run`을 주면 이 터미널에 묶인 Orca Run이 없을 때 하나 만든다. 오케스트레이터의 메일 알림은 Run에 묶인 터미널에만 뜬다(`orchestrate` references/top-level.md "Your Run").
6. `setup-pc` 점검 표를 출력한다. `missing`·`differs`·`warn` 행이 남은 일이다(`gh auth login`, git 커밋 서명 등은 사람이 한다).

다시 돌려도 이미 된 것은 건너뛴다. 묶음에서 빼고 싶은 규칙은 적용 뒤 지우면 된다. 한 번 제안한 항목은 `~/.local/state/agent-skills/setup-applied.json`에 남아 다시 제안하지 않는다. 새 설정은 새 세션부터 적용되므로 떠 있던 세션은 재시작한다. 체크아웃 폴더에서 `claude`를 처음 띄우면 폴더 신뢰 창이 한 번 뜬다.

옵션: `--settings-only`(파일만 쓰고 launchd·동기화·자동화·점검은 건너뜀), `--actions-only`(그 반대: 파일은 쓰지 않고 4~6만, 해시 없음. `setup-pc`가 권한 창 뒤에서 돌린다), `--update`(아래). 기본 on 자동화에는 watch-prs·watch-mentions도 있는데, `agent-skills.json`에 `prs`·`mentions` 키가 있을 때만 켠다.

## 갱신

Claude Code 안에서는 `setup-pc`("agent-skills 업데이트해줘")가 갱신 dry run을 보이고 `!` 한 줄을 건넨다. 셸에서는 다음을 친다.

```bash
git -C ~/.config/agent-skills/personal pull --ff-only   # 개인 설정 폴더가 있을 때만. setup.py도 동기화 작업도 이 폴더는 당기지 않는다
python3 <체크아웃>/scripts/setup.py --update     # 지금 체크아웃과 개인 설정 폴더 기준으로 새로 생긴 값만 diff
```

dry run은 여기서도 아무것도 쓰지 않고 fetch도 하지 않는다. fast-forward는 `first` 줄로만 보인다. 출력한 `--apply` 줄을 그대로 치면 먼저 체크아웃을 fast-forward하고(`skills-sync sync`, 더러운 체크아웃이나 갈라진 체크아웃이면 멈춘다) 계획을 다시 맞춰 본다. 새 릴리스가 권장값을 더했으면 해시가 맞지 않아 거절하는데, 그때는 이미 fast-forward된 상태이니 dry run을 다시 한다. 파일에 바뀔 것이 없으면 `no change`이고 그 줄은 fast-forward·launchd 작업·자동화만 돌린다.

## 옮기지 않는 것

저장소는 PC나 회사에 매인 것을 싣지 않는다. 개인 설정 저장소에 넣은 것만 따라오고, 나머지는 그 기능이 꺼지거나 처음 쓸 때 묻는다.

- 사내 이름 목록(`~/.config/agent-skills/internal-names.txt`, 공개 저장소에 쓰는 PC만). 채팅 채널, 트래커 프로젝트, 노트 저장소 같은 `agent-skills.json`의 나머지 키는 개인 설정 저장소의 `agent-skills.json`으로 옮긴다. 키와 기본 동작은 각 스킬의 설정 절과 [configuration-migration.md](configuration-migration.md).
- 조직 저장소를 신뢰 범위에 넣는 autoMode 문장. 묶음의 "사용자 저장소"는 `gh` 로그인 계정 아래만이다. 조직은 그 PC에서 `tune-automode`로 더한다. 개인 autoMode 규칙은 PC마다 둔다.
- 개인 지침과 스킬·에이전트(`~/.claude/CLAUDE.md`, `skills/`, `agents/`)와 자동 메모리. 어느 PC에서나 지켜야 할 작업 방식이 여기에만 있으면 `reflect`나 `write-skill`로 스킬에 올린다. 트래커·노트 쓰기 agent는 플러그인의 `task-tracker`·`notes-writer`가 기본이라 옮길 필요가 없다. 트래커 사이트 고유 규칙은 개인 설정 저장소에 두고 `tracker.rules_file`로 가리킨다(키를 두고 파일을 빠뜨리면 `task-tracker`가 쓰지 않고 멈춘다).
- `notes.obsidian.access`(그 PC에서 vault에 닿는 경로). PC마다 달라서 개인 설정 저장소에 넣지 않고, Obsidian vault를 쓰는 PC마다 적는다.
- `env`의 다른 값과 토큰. PC마다 다시 만든다.

특정 PC와 똑같이 맞추고 싶으면 `setup-pc`의 "Compare with another PC"(원래 PC에서 `setup_check.py export`, 여기서 `check --baseline`)를 쓴다. 기본 경로는 아니다.

## 확인

1. `setup.py --apply`가 끝에 낸 표(또는 `python3 skills/setup-pc/scripts/setup_check.py check`)에 `missing`·`differs`·`warn`이 없다.
2. 새 세션에서 `claude plugin list`에 `agent-skills@inline`이 보이고 Path가 체크아웃이다. `command -v orch`가 체크아웃의 `bin/orch`다.
3. 일상 작업(카드 시작, 받은편지함 대기, `orch land`)이 확인 창 없이 돈다. 하나가 막히면 `tune-automode`가 그 행동만 덮는 규칙을 만든다.
