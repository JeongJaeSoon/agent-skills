# agent-skills 설치

[English](install.md) · [日本語](install.ja.md) · 한국어

사람이 직접 설치할 때 보는 안내다. 에이전트에게 맡기려면 [INSTALL.md](../INSTALL.md)를 건넨다. 같은 단계에 에이전트가 지킬 규칙을 붙인 파일이다. 설정 하나하나의 뜻과 옵션 전체는 [new-pc.md](new-pc.md)에 있다.

설치 방법은 셋이다.

- **A. 마켓플레이스만.** 공개된 플러그인을 그대로 쓴다. 스킬을 고치지 않고 쓰기만 할 때.
- **B. 세션에서 전체 설정.** 이 저장소의 체크아웃을 직접 읽고, 권장 사용자 설정, 동기화 작업, 기본 자동화까지 넣는다. A로 시작해 다음 세션에서 끝난다.
- **C. 셸에서 전체 설정.** B와 같은 결과를 Claude Code 세션 없이.

보이는 출력은 `HOME`, `CLAUDE_CONFIG_DIR`, `CLAUDE_CODE_PLUGIN_DIRS`를 임시 디렉터리로 돌린 환경에서 Claude Code 2.1.314로 돌린 것이다. 돌린 범위는 A, 마켓플레이스 사본 제거, C의 `--settings-only --apply`까지다. 동기화 작업과 자동화를 넣는 단계(`--actions-only`, 전체 `--apply`)와 제거 표의 `launchctl`·자동화 행은 돌리지 않았다. launchd는 실제 사용자와 같이 쓰기 때문이다.

## 준비물

| | 무엇 | 왜 |
|---|---|---|
| 필수 | Claude Code (2.1.314에서 확인) | 플러그인은 여기서만 로드된다 |
| 필수 | `git` | 설치, 체크아웃, 동기화 |
| 필수 | `python3` 3.9 이상 | 모든 스크립트. macOS의 `/usr/bin/python3`(3.9.6)로 돈다 |
| B·C에 필수 | github.com에 로그인한 `gh` | 내 저장소에 관한 권장 규칙, private 개인 설정 저장소 clone |
| B·C | macOS | 동기화 작업과 일부 자동화가 LaunchAgent다. Linux에서는 setup이 crontab 줄을 출력하고, 그 자동화는 `unsupported`로 나온다 |
| 선택 | Orca | 여러 세션을 다루는 스킬(`orchestrate`, `dispatch-card`, `handoff-ticket`)과 정기 회차 |
| 선택 | Linear 또는 Jira | 트래커 스킬. `tracker.adapter`가 없으면 Linear |
| 선택 | Obsidian 또는 Markdown 폴더 | 노트 스킬. `notes.adapter`를 정하기 전에는 꺼져 있다 |
| 선택 | tailscale, caddy | `preview-watch`, `share-server`. 둘 다 기본 꺼짐 |

## A. 마켓플레이스만

Claude Code 세션 안에서:

```
/plugin marketplace add JeongJaeSoon/agent-skills
/plugin install agent-skills@jeongjaesoon
```

설치 창이 값 세 개를 묻는다. 모두 비워도 된다.

- `language`: 응답 언어.
- `checkout`: B에서 이 저장소를 clone할 곳(기본 `~/src/agent-skills`).
- `personal_repo`: 내 `agent-skills.json`을 둔 private 저장소. URL에 토큰을 넣지 않는다. 이 값은 `settings.json`에 그대로 남는다.

셸에서는 같은 값을 `--config`로 준다.

```bash
claude plugin marketplace add JeongJaeSoon/agent-skills
claude plugin install agent-skills@jeongjaesoon --config language=Korean --config checkout=$HOME/src/agent-skills
```

```
✔ Successfully added marketplace: jeongjaesoon (declared in user settings)
✔ Successfully installed plugin: agent-skills@jeongjaesoon (scope: user)
1 userConfig option not yet set — run /plugin configure agent-skills@jeongjaesoon in Claude Code, or pass --config KEY=VALUE.
```

마지막 줄은 값 하나를 비워 뒀다는 뜻일 뿐이다. 플러그인은 세션이 시작될 때 로드되므로 새 세션을 연다. 새 세션이 `setup-pc`를 한 번 제안한다. B로 갈 거면 받아들이고, 거절하면 그 프로필에서는 다시 묻지 않는다.

## B. 세션에서 전체 설정

1. A를 한 뒤 새 세션을 열고 `setup-pc` 제안을 받아들인다("새 PC 세팅해줘"라고 해도 된다).
2. `gh`가 로그인돼 있지 않으면 `! gh auth login`. 에이전트가 대신 로그인하지 않는다.
3. 에이전트가 체크아웃과 개인 설정 저장소를 clone하고 dry run으로 파일별 diff를 보인다. 아직 아무것도 쓰지 않았다. diff를 읽는다.
4. 에이전트가 건넨 한 줄 `! python3 -I <체크아웃>/scripts/setup.py --settings-only … --apply <해시>`를 붙여 넣는다. 해시는 계획 전체의 해시라서 diff 뒤에 무엇이 바뀌었으면 거절하고, 그때는 dry run을 다시 한다. 끝에 파일마다 되돌리는 줄을 출력한다.
5. 에이전트가 `setup.py --actions-only`를 돌리면 권한 창이 뜬다. 동기화 작업을 설치하고 기본 자동화를 켜는 단계이고 Claude 설정은 쓰지 않는다. 승인한다.
6. 세션을 재시작한다. 여기서 `/reload-plugins`를 치지 않는다. 적용 전부터 떠 있던 세션에서는 마켓플레이스 사본만 빠지고 체크아웃은 붙지 않아서, 재시작할 때까지 agent-skills가 통째로 없다.

## C. 셸에서 전체 설정

```bash
git clone https://github.com/JeongJaeSoon/agent-skills ~/src/agent-skills
python3 ~/src/agent-skills/scripts/setup.py --language Korean       # dry run: 파일별 diff와 --apply 줄
python3 -I ~/src/agent-skills/scripts/setup.py --language Korean --apply <해시>
```

- 체크아웃은 `main` 브랜치의 메인 체크아웃이어야 한다. worktree나 다른 브랜치면 거절한다.
- dry run은 아무것도 쓰지 않는다. 첫 줄 `target <경로>`가 쓸 `settings.json`이고, 마지막 줄이 칠 `--apply` 명령이다. 이 줄은 Claude Code 세션에 붙여 넣도록 `! `로 시작하므로, 셸에서는 `!`를 뺀다.
- `--settings-only`가 없으면 `--apply`가 동기화 작업 설치, 한 번 동기화, 기본 자동화 켜기까지 하고 점검 표를 출력한다.
- 마켓플레이스 사본이 설치돼 있으면 diff가 그것을 끈다(`enabledPlugins`를 `false`로). 둘 다 켜져 있으면 hook이 두 번 돈다. setup이 이미 있는 값을 바꾸는 경우는 이것 하나다.

## 최소 설정

필요한 설정은 없다. `~/.claude/agent-skills.json`이 없으면 기능마다 꺼져 있거나 처음 필요할 때 묻고, 목적지를 추정하지 않는다. B·C는 키 두 개(`reflect.skill_repository`, `reap.user_folders: []`)를 넣는다.

트래커와 노트 폴더를 정하는 예:

```json
{
  "tracker": {"adapter": "jira"},
  "notes": {"adapter": "markdown", "markdown": {"root": "~/notes"}}
}
```

`tracker.adapter`가 없으면 트래커 스킬은 Linear를 쓴다. 점검 표는 미설정인 `tracker.adapter`를 `info`로 보이는데, 오류가 아니다. 키 전체와 기본 동작은 [configuration-migration.md](configuration-migration.md)와 각 스킬의 설정 절에 있고, 바꾸는 일은 `skills-config` 스킬이 한다.

## 확인

A 뒤의 `claude plugin list`:

```
  ❯ agent-skills@jeongjaesoon
    Version: <커밋>
    Scope: user
    Status: ✔ enabled
```

B·C 뒤에는 체크아웃이 로드되고 마켓플레이스 사본은 꺼진다.

```
  ❯ agent-skills@jeongjaesoon
    Status: ✘ disabled

Session-only plugins (--plugin-dir / --plugin-url):
  ❯ agent-skills@inline
    Path: <체크아웃>
    Status: ✔ loaded
```

이어서 `python3 <체크아웃>/skills/setup-pc/scripts/setup_check.py check`. 읽기만 한다. `missing`·`differs`·`warn` 행이 남은 일이고 마지막 칸이 방법이다. 커밋 서명과 `gh auth login`은 늘 사람이 한다.

세션에서 `/agent-skills:brief`가 현황 보고로 답하면 스킬이 로드된 것이다. 그냥 `/brief`는 Claude Code 내장 명령이다.

## 갱신

- **마켓플레이스 사본:** `claude plugin marketplace update jeongjaesoon`, `claude plugin update agent-skills@jeongjaesoon`, 그다음 떠 있는 세션에서 `/reload-plugins`.
- **체크아웃:** 동기화 작업이 15분마다 fast-forward하므로 스킬은 저절로 최신이 된다. 새 권장 설정은 `python3 <체크아웃>/scripts/setup.py --update`(dry run)가 출력한 `--apply` 줄로 넣거나, 세션에 "agent-skills 업데이트해줘"라고 한다. 개인 설정 저장소가 있으면 먼저 `git -C ~/.config/agent-skills/personal pull --ff-only`. 다른 무엇도 그 폴더를 당기지 않는다.

## 안전 장치

지점마다 기본이 사람이 승인하는 HITL(Human-in-the-loop)인지, 에이전트가 진행하고 사람은 지켜보는 HOTL(Human-on-the-loop)인지와, 바꾸는 법이다.

| 지점 | 기본 | 바꾸는 법 |
|---|---|---|
| 사용자 설정 적용(`setup-pc`, `tune-automode`, `skills-config`) | HITL: 에이전트는 diff와 `!` 한 줄만 만들고 사람이 실행한다 | 없음 |
| `setup.py --actions-only`(동기화·자동화 켜기) | HITL: `guard.py`가 auto mode에서도 권한 창을 띄운다 | 없음 |
| 그 밖의 권한 창 | HITL: 사람이 승인한다 | 사람이 `permissions.allow` 항목(사용자 설정의 memory 경로 규칙·MCP 도구 이름은 `skills-config`, Bash 규칙과 프로젝트 설정은 `update-config`)을 더하면 그 명령은 묻지 않고 진행한다 |
| auto mode 분류기의 거부 | HITL: 에이전트가 보고하고 멈춘다 | `tune-automode`가 만든 autoMode 규칙을 사람이 적용하면 그 부류는 진행한다. 판정 없는 거부(`no verdict`)는 규칙으로 바뀌지 않는다 |
| 되돌릴 수 없는 행동(파괴적 migration, 공유 브랜치 force push, 프로덕션 배포, 바깥으로 보내기) | HITL: 멈추고 묻는다 | 없음 |
| `orchestrate` 머지 정책 | HOTL: `autonomous`, 워커가 `orch land`로 머지(그 저장소의 머지를 허용하는 autoMode 규칙이 있을 때. 없으면 묻는다) | `human-gate`면 워커는 READY에서 멈추고 사람이 게이트를 푼다 |
| `deliver-ticket` 혼자 도는 카드의 머지 | HOTL: 준비된 PR을 묻지 않고 머지(그 머지를 허용하는 autoMode 규칙이 있을 때. 없으면 묻는다) | hold를 걸거나 직접 보겠다고 하면 머지 전에 묻는다. 계속 flaky한 체크, 되돌릴 수 없는 migration 같은 예외에서도 묻는다 |
| `watch-prs` `ownergate`(봇 PR 승인) | 꺼짐. `prs.kinds`에 넣고 `prs.ownergate`에 게이트 항목을 적으면 HITL: 사람이 `!` 줄로 승인한다 | 프로덕션이 아니고 autoMode allow 규칙이 덮으면 회차가 실행한다(HOTL) |
| `watch-prs` `ready_to_merge` | 꺼짐 | `prs.kinds`에 더하면 `merge_repos`의 내 PR 중 게이트를 모두 통과한 것을 머지한다(HOTL) |
| `secure-fill` 비밀값 입력 | HITL: 매번 Touch ID 승인 | 허용 목록 항목을 `"approval": "none"`으로 두면 Touch ID 없이 채운다(`-T ""`로 만든 항목이면 Keychain 확인은 남는다) |
| `janitor`, `pressure-watch` | HOTL: 에이전트가 남긴 끝난 리소스를 정기적으로 정리한다 | `<체크아웃>/bin/skills-sync setup disable <이름>` |

HOTL에서 사람이 보는 곳은 `brief`의 현황 보고와 대시보드의 결정·권한 창·받은편지함이다. 대시보드·`janitor`·watch 회차는 Orca를 전제로 한다. 세부는 [operations.md](operations.md#안전-경계).

## 제거

마켓플레이스 사본:

```bash
claude plugin uninstall agent-skills@jeongjaesoon
claude plugin marketplace remove jeongjaesoon
```

`enabledPlugins`, `extraKnownMarketplaces`, `pluginConfigs`의 해당 항목도 함께 지워진다.

전체 설정(B·C)은 이것도 되돌린다.

| setup이 바꾼 것 | 되돌리는 법 |
|---|---|
| `settings.json`의 `env.CLAUDE_CODE_PLUGIN_DIRS`와 다른 권장 설정 | `--apply`가 출력한 되돌리기 줄(`cp settings.json.bak-setup-<시각> settings.json`)을 치거나 원하지 않는 키를 지운다. 떠 있는 세션은 재시작한다 |
| 동기화 작업(macOS) | `launchctl bootout gui/$(id -u)/io.github.jeongjaesoon.agent-skills-sync` 뒤 `~/Library/LaunchAgents/io.github.jeongjaesoon.agent-skills-sync.plist` 삭제 |
| 자동화 | 하나씩 `<체크아웃>/bin/skills-sync setup disable <이름>`. LaunchAgent인 것은 그 agent도 내린다. 손으로 설치한 `bin/reap-hard`와 대시보드 agent는 `<체크아웃>/bin/reap-hard uninstall`, `<체크아웃>/bin/orch-dash uninstall-agent` |
| 상태 파일 | `~/.local/state/agent-skills/` 삭제 |

남는 것이 둘 있다. Orca automation 등록은 `disable`이 끄기만 하고 지우지 않는다. `~/.claude/agent-skills.json`은 아무것도 지우지 않는다.

## 문제 해결

- **`/reload-plugins` 뒤 스킬이 사라졌다.** 전체 설정을 적용하기 전부터 떠 있던 세션이다. 재시작한다.
- **setup이 내 세션이 읽는 것과 다른 `settings.json`에 썼다.** Claude Code는 `CLAUDE_CONFIG_DIR`이 있으면 `$CLAUDE_CONFIG_DIR/settings.json`을, 없으면 `~/.claude/settings.json`을 읽는다(Orca는 프로필마다 따로 준다). dry run 첫 줄 `target <경로>`가 setup이 계획하는 파일이고, `--settings <파일>`로 다른 것을 고른다. 프로필이 여럿이면 프로필마다 한 번씩 설정한다. `agent-skills.json`은 모든 프로필이 함께 쓰는 한 파일이다.
- **`refused: … is a worktree`, `… is on 'feature'`.** `main`의 메인 체크아웃에서 돌린다.
- **hook이 두 번 돈다.** 마켓플레이스 사본과 체크아웃이 둘 다 켜져 있다. setup dry run을 돌리면 그 diff가 마켓플레이스 사본을 끈다.
- **Linux.** setup이 출력한 crontab 줄을 더한다. LaunchAgent 자동화는 지원하지 않는다.
