# 새 PC: 설치와 승인 창만으로 끝내기 (설계)

목표: 새 PC에서 사람이 하는 일을 "설치"와 "첫 세션에서 뜨는 창에 답하기"로 줄인다. 셸 명령을 직접 치지 않는다.

이 문서는 2026-10-26의 설계와 그 근거다. 쓰는 법은 [new-pc.md](new-pc.md).

## 확인한 사실

Claude Code 2.1.310과 공식 문서(code.claude.com/docs, 2026-10-26 확인)에서 확인했다.

| 사실 | 근거 |
|---|---|
| 플러그인이 싣는 `settings.json`은 `agent`, `subagentStatusLine` 두 키만 적용되고 나머지는 버려진다. env, `permissions`, `autoMode`, `language`, `outputStyle`은 플러그인으로 실을 수 없다 | plugins/components.md, manifest-reference.md |
| `autoMode`는 사용자 설정, 조직 관리 설정, `--settings` 플래그에서만 읽힌다. 프로젝트 설정의 것은 읽히지 않는다 | auto-mode-config.md |
| `userConfig`를 manifest에 선언하면 `/plugin install`(세션 안)이나 Installed 탭에서 켤 때 값을 묻는 창이 뜬다. 셸의 `claude plugin install`은 묻지 않는다. 값은 `settings.json`의 `pluginConfigs`에 Claude Code가 쓰고, hook 프로세스에는 `CLAUDE_PLUGIN_OPTION_<KEY>`로 들어간다. 다시 묻게 하려면 `/plugin configure` | plugins/components.md |
| PreToolUse hook의 `ask`는 auto mode에서도 권한 창을 띄운다. 분류기는 여전히 거부할 수 있지만 조용히 승인하지는 못한다 | hooks.md |
| 플러그인 SessionStart hook은 그 플러그인을 로드한 세션부터 돈다. 설치한 바로 그 세션에서는 첫 SessionStart가 이미 지나갔다 | hooks.md |
| 사용자 설정에 쓰는 공식 동의 창은 `/permissions`의 Auto mode 탭, `/auto-mode-setup`(초안 통째로 수락), 권한 창의 "다시 묻지 않기"(저장소의 `.claude/settings.local.json`)뿐이다. 설치할 때 env·`permissions.allow`·`language`를 사용자 설정에 쓰는 창은 없다 | permissions.md, auto-mode-config.md |
| 디렉터리 소스 마켓플레이스로 체크아웃을 설치해도 cache에는 커밋 단위 복사본(일반 파일)이 생긴다. 실시간 반영은 여전히 `CLAUDE_CODE_PLUGIN_DIRS`가 있어야 한다 | 임시 `CLAUDE_CONFIG_DIR`에서 실측, [platform.md](platform.md) §1과 같다 |
| 에이전트가 사용자 설정을 쓰면 auto mode 분류기가 자기수정으로 거부한다 | tune-automode "에이전트가 하지 않는 것" |

## 결론

사용자 설정을 쓰는 일은 사람에게 남는다. 분류기가 막는 쓰기를 다른 도구·스크립트·hook으로 감싸 에이전트가 하게 만드는 것은 경계를 우회하는 것이라 하지 않는다. hook `ask`로 `setup.py --apply`를 승인받는 것도 같다. 사람이 보는 것은 명령 줄이지 diff가 아니고, 묶음의 규칙을 승인하는 근거는 사람이 diff를 읽는 것이다.

그 대신 사용자 설정에 쓰지 않아도 되는 일을 모두 에이전트 쪽으로 옮긴다. 남는 사람 행동은 설정 쓰기 한 줄(`!`로 붙여 넣기)과 창 클릭이다.

## 단계별 담당

| 단계 | 지금 사람이 하는 일 | 바뀐 뒤 담당 | 근거·제약 |
|---|---|---|---|
| 플러그인 받기 | `claude plugin marketplace add`·`install` 또는 clone | 사람: 세션 안에서 `/plugin marketplace add JeongJaeSoon/agent-skills`, `/plugin install agent-skills@jeongjaesoon` | 셸 `claude plugin install`은 `userConfig`를 묻지 않으므로 세션 안 설치를 쓴다 |
| 언어·체크아웃 경로·개인 저장소 | `--language`를 치거나 손으로 옮김 | 설치 창(`userConfig`): `language`, `checkout`, `personal_repo`. 모두 비워도 된다 | Claude Code가 `pluginConfigs`에 쓴다. 에이전트의 쓰기가 아니다 |
| 미설정 감지 | 사람이 `setup-pc`를 떠올림 | 플러그인 SessionStart hook(`hooks/onboard.py`): 이 프로필의 사용자 설정도 세션의 환경 변수도 체크아웃을 로드하지 않으면(`CLAUDE_CODE_PLUGIN_DIRS`) `setup-pc`를 하라는 문맥을 넣는다. 답할 사람이 없는 `-p`·SDK 세션(`CLAUDE_CODE_ENTRYPOINT`가 `sdk-`로 시작)에서는 넣지 않고, 첫 요청이 브리프·자동화 프롬프트인 세션은 문맥 안의 지시로 무시한다. 거절하면 `onboard.py --decline`으로 그 프로필을 기록해 다시 묻지 않는다 | 설치 뒤 새 세션 하나가 필요하다(사람). 상태 폴더는 Orca 프로필끼리 공유하므로 적용 기록으로는 판단하지 않고, 거절 기록은 프로필의 settings 경로로 나눈다 |
| 체크아웃 clone | `git clone` | `setup-pc` 스킬(에이전트): `checkout`이 있으면 그 경로로 clone | 사용자 설정이 아닌 일반 파일 쓰기 |
| 개인 설정 가져오기 | baseline export/check, 손 복사 | `setup-pc`(에이전트): `personal_repo`를 `~/.config/agent-skills/personal`로 clone·fast-forward. URL의 인증 정보는 hook이 지우고, 인증은 `gh` 로그인으로 받는다. 반영은 아래 설정 줄의 diff에 들어간다 | 공개 저장소에는 넣지 않는다. 아래 "개인 설정의 출처" |
| dry run과 diff | 사람이 `setup.py` 실행 | `setup-pc`(에이전트): `setup.py --settings-only`로 diff를 보이고 줄 하나를 건넨다 | dry run은 아무것도 쓰지 않는다 |
| 사용자 설정 쓰기(`settings.json`, `agent-skills.json`) | `--apply <해시>`를 침 | 사람: 건네받은 `! … --settings-only --apply <해시>` 한 줄을 붙여 넣는다 | 분류기 경계. 남는 유일한 명령 줄 |
| 동기화 작업·정기 자동화(janitor, pressure-watch, watch-prs, watch-mentions)·Orca Run | `--apply`가 같이 함, watch-*는 따로 `schedule --write` | `setup-pc`(에이전트)가 `setup.py --actions-only`를 실행하고, guard hook이 `ask`로 권한 창을 띄운다. 사람은 창에서 승인한다 | 사용자 설정을 쓰지 않는다. watch-*는 개인 설정에 `prs`·`mentions` 키가 있을 때만 켠다 |
| `gh auth login` | 사람 | 사람(브라우저 승인). 로그인돼 있지 않으면 `setup-pc`가 먼저 멈추고 알린다 | 자격 증명은 에이전트가 다루지 않는다 |
| 세션 재시작 | 사람 | 사람 | `CLAUDE_CODE_PLUGIN_DIRS`와 설정은 기동할 때만 읽힌다 |
| 점검 표 | 사람이 `setup_check.py check` | `setup-pc`(에이전트), `--actions-only`의 마지막 단계 | 읽기 전용 |

Orca가 프로필마다 `CLAUDE_CONFIG_DIR`을 따로 쓰면 프로필마다 설치·설치 창·설정 줄을 한 번씩 한다. `setup.py`는 `settings.json`에서만 `CLAUDE_CONFIG_DIR`을 따른다. `agent-skills.json`은 스킬의 스크립트가 읽는 한 파일(`~/.claude/agent-skills.json`)이라 프로필끼리 같이 쓴다.

### 남는 사람 행동

1. 세션 안에서 마켓플레이스 추가와 설치, 설치 창에 값 입력(비워도 됨).
2. 새 세션 하나를 연다.
3. `gh`가 로그인돼 있지 않으면 `! gh auth login`.
4. 에이전트가 보인 diff를 읽고 `! … --settings-only --apply <해시>` 한 줄을 붙여 넣는다.
5. 권한 창(동기화 작업·자동화)에서 승인한다.
6. 세션을 재시작한다.

## 개인 설정의 출처

옮길 개인 설정은 `agent-skills.json`의 개인 값(`tracker`, `notes`, `prs`, `mentions` 등)과, `tracker.rules_file`이 가리키는 트래커 사이트 규칙 Markdown이다. 공개 저장소에는 넣을 수 없다. 트래커·노트 쓰기 agent는 플러그인의 `task-tracker`·`notes-writer`가 맡으므로 개인 에이전트는 옮기지 않는다. 개인 autoMode 규칙도 옮기지 않고 PC마다 둔다. agent-skills를 쓰는 데 필요한 autoMode 규칙은 저장소 기본값(`scripts/recommended-settings.json`)에 있어 설정 줄이 없는 것만 더한다.

| 안 | 방식 | 장점 | 단점 |
|---|---|---|---|
| A. 개인 private git 저장소 (추천) | 설치 창의 `personal_repo`에 URL. 에이전트가 `~/.config/agent-skills/personal`로 clone하고, `setup.py`가 그 안의 파일을 diff에 넣는다 | 이력이 남고, 새 PC에서 `gh` 로그인만 있으면 된다. 원래 PC가 없어도 된다 | private 저장소 하나를 만들어 관리해야 한다 |
| B. 동기화 폴더(iCloud Drive 등) | 설치 창에 폴더 경로. 내용은 A와 같다 | 인증이 필요 없다 | 동기화 서비스에 평문으로 남고 이력이 없다. 동기화 중인 파일을 읽을 수 있다 |
| C. 원래 PC에서 baseline export (지금) | `setup_check.py export`를 복사해 `check --baseline` | 추가 저장소가 없다 | 원래 PC와 손 복사가 필요하다. 셸 명령이 남는다 |

A로 정했다(2026-10-26). B는 같은 파일 형식이라 경로만 바꿔 쓸 수 있다(`personal_repo` 대신 디렉터리를 직접 두면 된다). C는 "다른 PC와 비교" 용도로 남긴다.

개인 디렉터리의 형식:

```
agent-skills.json   # ~/.claude/agent-skills.json 에 없는 키만 더한다. 권장 기본값과 겹치면 이쪽이 이긴다
tracker-rules.md    # 이름은 자유. agent-skills.json 의 tracker.rules_file 에 상대 경로로 적으면 이 폴더 기준 절대 경로가 된다
```

있는 값은 덮지 않는다. 권장 묶음과 같은 규칙으로, 한 번 제안한 키는 기록되어 지운 뒤 다시 제안하지 않는다.

## 이미 설치한 PC의 갱신

같은 코드 경로를 탄다. `setup-pc`가 먼저 개인 설정 폴더를 `git pull --ff-only`로 당긴다(폴더가 있을 때만. `setup.py`도 15분 동기화 작업도 체크아웃만 다루고 이 폴더는 당기지 않는다). 이어서 `setup.py --update`의 dry run이 새 권장값과 개인 저장소에 새로 생긴 키를 diff에 넣고, 사람은 같은 `--apply` 줄을 친다. 당기기가 dry run 앞인 것은 dry run이 그 폴더를 읽어 해시를 만들기 때문이다. `--actions-only`도 그대로 쓴다. SessionStart hook은 이미 체크아웃을 로드하는 세션에서는 아무것도 넣지 않는다.

## 고르지 않은 것

- **hook `ask`로 `setup.py --apply`를 에이전트가 실행.** 분류기 경계를 다른 창으로 돌리는 것이다. 사람이 승인하는 것이 diff가 아니라 명령 줄이 된다.
- **플러그인 hook이 사용자 설정을 직접 씀(설치 창 체크박스로 동의).** 에이전트가 아니어도 같은 경계를 우회하고, diff를 보지 않은 동의가 된다. `!` 한 줄을 남기기로 했다(2026-10-26).
- **개인 에이전트와 개인 autoMode 규칙을 개인 저장소로 옮기기.** 쓰기 agent는 플러그인에 있고, 개인 autoMode 규칙은 PC마다 다르게 두기로 했다(2026-10-26).
- **저장소에 `.claude/settings.json`(디렉터리 마켓플레이스와 `enabledPlugins`)을 두어 clone만으로 로드.** 이 저장소의 worktree 세션마다 마켓플레이스 사본이 켜져 `CLAUDE_CODE_PLUGIN_DIRS`의 체크아웃과 hook이 두 번 돈다. 프로젝트 설정이 사용자 설정의 `false`를 덮는다.
- **`agent-skills.json`을 개인 디렉터리에서 겹쳐 읽기(overlay).** 이 파일을 읽는 곳이 30곳이 넘는다. 한 번 합쳐 쓰는 편이 작다.
- **`/auto-mode-setup`으로 autoMode 묶음 받기.** 초안은 Claude가 새로 쓰므로 저장소의 묶음과 같은 문장이 들어간다는 보장이 없다.
