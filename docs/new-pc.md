# 새 PC에서 같은 방식으로 쓰기

agent-skills를 설치하면 일하는 방식은 그대로 따라온다. 보고 모양(`brief`), 라우팅과 위임, 받은편지함 대기 하나(`orchestrate`), 카드의 `/goal`·Approved·`CLOSE`(`dispatch-card`), 범위 안의 결정, 리뷰 의뢰 묶기(`watch-prs`), 완료 전 검증(`deliver-ticket`)이 그렇다. 스킬이 실을 수 없는 것은 셋이다. 사용자 설정, 저장소 밖의 고유값, 그 PC에만 쌓인 기록. 이 문서는 그 셋을 옮기는 순서다.

## 1. 설치

README "설치"대로 clone 한 뒤 `bootstrap.py --write`를 돌린다. 하는 일과 실측 근거는 [platform.md](platform.md) §5에 있다. 그 전부터 떠 있던 세션은 재시작한다.

## 2. 사용자 설정 (`~/.claude/settings.json`)

플러그인은 아래 값을 설정으로 실을 수 없다. 에이전트도 이 파일을 고치지 않는다(분류기가 자기수정으로 막는다). 사람이 직접 넣는다. 원래 PC와 무엇이 다른지는 `setup-pc`가 표로 보인다(5절).

| 키 | 하는 일 | 없으면 | 옮기는 방법 |
|---|---|---|---|
| `language` | 답변 언어 | 보고는 그래도 사람이 쓰는 언어로 나온다(`brief`). 다른 답변은 모델 기본값을 따른다 | 원래 PC와 같은 값 |
| `outputStyle` | 답변에 설명 블록(예: ★ Insight)을 붙이는 style | 블록이 없다. 보고 구조는 같다 | `/config`에서 같은 style을 고른다. 켜져 있으면 블록의 자리(진행 중 뒤, 결정 필요 앞)는 `brief`가 정한다 |
| `autoMode` (`allow`, `soft_deny`, `environment`) | auto mode 분류기가 묻지 않고 허용하거나 막는 범위 | 기본 분류기만 돈다. 오케스트레이터의 머지, 받은편지함 명령, 카드 시작 같은 일상 작업이 확인 창이나 거부로 멈춘다 | 원래 PC의 규칙을 하나씩 옮긴다. 규칙 문장에 사내 이름이 있으면 그대로 둔다(로컬 파일이다). 새로 막힌 일은 `tune-automode`가 가장 작은 규칙과 적용 스크립트를 만든다. 처음부터 잡으려면 내장 `/auto-mode-setup` |
| `permissions.allow` | 확인 없이 도는 명령 | 명령마다 확인 창이 뜬다 | 원래 PC의 목록을 옮긴다. 규칙은 단독 명령에만 맞는다. `&&`나 파이프로 이은 명령은 분류기로 넘어간다. 바꾸는 일은 `update-config` |
| permission mode | 기본 모드(auto 등) | 시작 모드가 달라 확인 창 수가 달라진다 | 원래 PC와 같은 기본 모드 |
| `env.CLAUDE_CODE_PLUGIN_DIRS` | 체크아웃을 플러그인으로 읽기 | — | `bootstrap.py`가 넣는다. 손으로 옮기지 않는다(경로가 PC마다 다르다) |

`env`의 다른 값과 토큰은 PC마다 다시 만든다. 다른 PC에서 파일째 복사하지 않는다.

## 3. 저장소 밖의 고유값

스킬은 사람·채널·저장소·트래커 같은 고유값을 본문에 두지 않고, 실행할 때 아래에서 읽는다. 없으면 그 기능을 끄거나 멈추고 묻는다.

- `~/.claude/agent-skills.json`: 스킬별 키(`notes`, `tracker`, `prs`, `mentions`, `reap`, `reflect`, `orchestrate` 등). 키 목록과 기본 동작은 각 스킬의 설정 절과 [configuration-migration.md](configuration-migration.md)에 있다.
- `~/.config/agent-skills/`: 목록과 비밀. 예를 들어 공개 저장소에 쓰기 전에 grep하는 사내 이름 목록은 `internal-names.txt`다(`write-skill` §4).
- `skills-sync setup`: 정기 자동화(janitor 등)를 켠다. README "정기 자동화" 절.

## 4. 그 PC에만 있는 기록

- **개인 지침과 스킬**: `~/.claude/CLAUDE.md`, `~/.claude/skills/`, `~/.claude/agents/`는 git으로 동기화되지 않는다. 원래 PC에서 복사한다. 사내 채널이나 투고 규칙처럼 공개 저장소에 둘 수 없는 규칙이 여기에 있다.
- **자동 메모리**: 프로젝트 경로별로 PC 안에 쌓여 옮겨지지 않는다. 어느 PC에서나 지켜야 할 작업 방식이 메모리에만 있으면, 다른 PC에서는 그 방식이 사라진다. 그런 규칙은 `reflect`나 `write-skill`로 스킬에 올리고, 고유값은 2·3절로 보낸다.

## 5. 확인

새 세션에서 아래를 본다.

1. `/agent-skills:setup-pc`로 2~4절과 설치·동기화를 점검한다. 원래 PC에서 `setup_check.py export`로 뽑은 기준 파일이 있으면 `check --baseline`이 빠진 autoMode 규칙·`permissions.allow`·`agent-skills.json` 키·개인 파일을 짚는다. 스킬은 읽기만 하고 고치는 줄은 사람이 친다. 아무것도 설치되지 않았으면 clone한 체크아웃에서 `python3 skills/setup-pc/scripts/setup_check.py check`를 직접 돌린다.
2. `claude plugin list`에 `agent-skills@inline`이 보이고 Path가 체크아웃이다. `command -v orch`가 체크아웃의 `bin/orch`를 가리킨다.
3. 여러 항목에 걸친 상태 보고를 요청한다(`/agent-skills:brief`). 시각이 붙은 제목 줄, 완료 → 진행 중 → 결정 필요 → 지금 오너가 할 일 → 보류 순서로 나오고, 할 일이 없으면 `없음`으로 나오면 된다.
4. 오케스트레이터나 리드를 쓰는 PC라면, 그 세션 터미널에서 `orca orchestration run-current --json`이 Run을 돌려주는지 본다. Orca의 `You have N orchestration message(s)` 알림은 Run에 묶인 터미널에만 들어온다(`orchestrate` references/top-level.md "Your Run"). `null`이면 그 절대로 `run-use`나 `run-create`를 한다. `CLAUDE_CODE_DISABLE_TERMINAL_TITLE`이 켜져 있으면 Orca가 idle을 보지 못해 알림이 안 뜬다.
5. 같은 PC에서 일상 작업(카드 시작, 받은편지함 대기, `orch land`)이 확인 창 없이 도는지 본다. 멈추면 2절의 `autoMode`와 `permissions.allow`를 다시 본다.
