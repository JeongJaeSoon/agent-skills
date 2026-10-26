# 새 PC에서 같은 방식으로 쓰기

일하는 방식(보고 모양, 위임, 결정, 리뷰 의뢰, 검증)은 스킬이 정하므로 설치만으로 따라온다. 스킬이 실을 수 없는 사용자 설정·최소 설정 키·정기 자동화는 `scripts/setup.py` 한 번으로 넣는다. 원래 PC에서 옮겨 오는 것을 전제로 하지 않고, 저장소가 가진 권장 기본값(`scripts/recommended-settings.json`)만 쓴다.

## 한 줄 실행

```bash
git clone https://github.com/JeongJaeSoon/agent-skills <체크아웃>
python3 <체크아웃>/scripts/setup.py --language Korean     # 바뀔 내용: 파일별 diff, 마지막 줄에 --apply 줄
python3 <체크아웃>/scripts/setup.py --language Korean --apply <해시>
```

dry run은 아무것도 쓰지 않는다. 두 번째 줄은 첫 줄이 출력한 것을 그대로 친다. 해시는 diff에 나온 계획 전체(쓸 파일 내용과 이어서 할 일)의 해시라서, 그 사이에 무엇이 바뀌면 거절하고 dry run을 다시 하라고 한다. 이미 설치한 PC에서 Claude Code를 쓰고 있다면 `setup-pc` 스킬("새 PC 세팅해줘")이 dry run을 돌려 diff를 보이고 `!` 한 줄을 건넨다.

`--apply`가 하는 일:

1. `~/.claude/settings.json`, `~/.claude/agent-skills.json`을 각각 타임스탬프 백업한다.
2. 없는 값만 더한다. 이미 있는 값은 덮지 않는다(`kept` 줄로 보인다). 예외는 하나다: 마켓플레이스 사본 `agent-skills@jeongjaesoon`이 켜져 있으면 끈다(`enabledPlugins`의 `true`→`false`, `changed` 줄로 보인다). 둘 다 켜면 hook이 두 번 돈다.
   - `env.CLAUDE_CODE_PLUGIN_DIRS`에 체크아웃 경로.
   - `language`(`--language`, 없으면 시스템 locale), `outputStyle`, `permissions.defaultMode`(`auto`).
   - `permissions.allow` 묶음: PR 보기·checks, `skills-sync status`·`broadcast`, `orch-dash`의 조회·서버 하위 명령(`ensure`·`serve`·`collect`·`fleet`·`skills`·`prstats`). allow는 분류기를 건너뛰어 저장소 한정이 걸리지 않으므로, PR 만들기·고치기는 넣지 않고 아래 autoMode의 사용자 저장소 규칙이 맡는다. launchd 상주 설치(`install-agent`)와 받은편지함 쓰기(`inbox add`·`resolve`)도 넣지 않는다. `orca orchestration`과 `orch`는 플러그인 hook이 이미 허용한다.
   - autoMode 규칙 묶음: 체크아웃 안 편집, 요청받은 로컬 정리, 사용자 저장소의 PR 작업, 사용자가 지시한 머지, 오케스트레이터의 워커 지시·받은편지함, 워커의 자기 작업 공간, janitor 회차, 그리고 "사용자 본인의 채팅만 지시로 친다". 막는 쪽은 기본 브랜치 force push와 요청받지 않은 프로덕션 단계. 규칙마다 `Not covered:`로 한계를 적는다. 체크아웃 경로와 `gh` 로그인 이름은 실행할 때 채운다.
   - `agent-skills.json`: `reflect.skill_repository`(체크아웃), `reap.user_folders`(빈 배열 = 끔).
3. 임시 파일에 써서 원자적으로 바꾸고, 다시 읽어 같은지 확인한 뒤 파일마다 되돌리는 `cp` 줄을 출력한다.
4. 15분마다 `skills-sync sync`를 돌리는 launchd 작업을 설치하고(Linux면 crontab 줄을 출력), 한 번 동기화하고, 기본 on 정기 자동화를 켠다(`skills-sync setup apply`, README "정기 자동화").
5. `--orca-run`을 주면 이 터미널에 묶인 Orca Run이 없을 때 하나 만든다. 오케스트레이터의 메일 알림은 Run에 묶인 터미널에만 뜬다(`orchestrate` references/top-level.md "Your Run").
6. `setup-pc` 점검 표를 출력한다. `missing`·`differs`·`warn` 행이 남은 일이다(`gh auth login`, git 커밋 서명 등은 사람이 한다).

다시 돌려도 이미 된 것은 건너뛴다. 묶음에서 빼고 싶은 규칙은 적용 뒤 지우면 된다. 한 번 제안한 항목은 `~/.local/state/agent-skills/setup-applied.json`에 남아 다시 제안하지 않는다. 새 설정은 새 세션부터 적용되므로 떠 있던 세션은 재시작한다. 체크아웃 폴더에서 `claude`를 처음 띄우면 폴더 신뢰 창이 한 번 뜬다.

옵션: `--settings-only`(파일만 쓰고 launchd·동기화·자동화·점검은 건너뜀), `--update`(아래).

## 갱신

```bash
python3 <체크아웃>/scripts/setup.py --update     # 지금 체크아웃 기준으로 새로 생긴 권장값만 diff
```

dry run은 여기서도 아무것도 쓰지 않고 fetch도 하지 않는다. fast-forward는 `first` 줄로만 보인다. 출력한 `--apply` 줄을 그대로 치면 먼저 체크아웃을 fast-forward하고(`skills-sync sync`, 더러운 체크아웃이나 갈라진 체크아웃이면 멈춘다) 계획을 다시 맞춰 본다. 새 릴리스가 권장값을 더했으면 해시가 맞지 않아 거절하는데, 그때는 이미 fast-forward된 상태이니 dry run을 다시 한다. 파일에 바뀔 것이 없으면 `no change`이고 그 줄은 fast-forward·launchd 작업·자동화만 돌린다.

## 옮기지 않는 것

PC나 회사에 매인 것은 옮기지 않는다. 없으면 그 기능은 꺼지거나 처음 쓸 때 묻는다.

- 사내 이름 목록(`~/.config/agent-skills/internal-names.txt`, 공개 저장소에 쓰는 PC만), 채팅 채널, 트래커 프로젝트, 노트 저장소 같은 `agent-skills.json`의 나머지 키. 키와 기본 동작은 각 스킬의 설정 절과 [configuration-migration.md](configuration-migration.md).
- 조직 저장소를 신뢰 범위에 넣는 autoMode 문장. 묶음의 "사용자 저장소"는 `gh` 로그인 계정 아래만이다. 조직은 `tune-automode`로 더한다.
- 개인 지침과 스킬(`~/.claude/CLAUDE.md`, `skills/`, `agents/`)과 자동 메모리. 어느 PC에서나 지켜야 할 작업 방식이 여기에만 있으면 `reflect`나 `write-skill`로 스킬에 올린다. 트래커·노트 쓰기 agent는 플러그인의 `task-tracker`·`notes-writer`가 기본이라 옮길 필요가 없고, 트래커 사이트 고유 규칙만 `tracker.rules_file`이 가리키는 파일로 옮긴다(키를 두고 파일을 빠뜨리면 `task-tracker`가 쓰지 않고 멈춘다). Obsidian vault를 쓰면 그 PC의 접근 경로를 `notes.obsidian.access`에 적는다.
- `env`의 다른 값과 토큰. PC마다 다시 만든다.

특정 PC와 똑같이 맞추고 싶으면 `setup-pc`의 "Compare with another PC"(원래 PC에서 `setup_check.py export`, 여기서 `check --baseline`)를 쓴다. 기본 경로는 아니다.

## 확인

1. `setup.py --apply`가 끝에 낸 표(또는 `python3 skills/setup-pc/scripts/setup_check.py check`)에 `missing`·`differs`·`warn`이 없다.
2. 새 세션에서 `claude plugin list`에 `agent-skills@inline`이 보이고 Path가 체크아웃이다. `command -v orch`가 체크아웃의 `bin/orch`다.
3. 일상 작업(카드 시작, 받은편지함 대기, `orch land`)이 확인 창 없이 돈다. 하나가 막히면 `tune-automode`가 그 행동만 덮는 규칙을 만든다.
