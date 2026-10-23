# 오케스트레이터가 오케스트레이터로 남게 하기

## 문제

최상위 오케스트레이터는 라우팅·판단·보고만 하고 조사, 브라우저 읽기, 코드 읽기, PR 확인, 메시지 작성은 서브에이전트·리드·카드로 넘긴다는 규칙이 있다. 이 규칙은 `orchestrate`의 "Stay answerable", reorient hook이 매 프롬프트 앞에 넣는 한 줄, 메모리에 모두 적혀 있었다. 그래도 운영 중에 이런 일이 반복됐다.

- 관리 화면 하나를 내장 브라우저로 직접 열어 10번 가까이 호출하며 읽었다.
- `gh`와 채팅 도구로 담당자를 직접 찾아 정리하고, 채팅 글을 직접 써서 보냈다.
- 멈춘 프롬프트를 찾는 감시 스크립트를 `| tail`로 묶어 배경에서 돌렸다. 감시자는 끝나지 않으므로 찾은 줄이 출력 파일에 한 번도 나오지 않았다.

매번 메모리에 한 줄을 더했지만 지켜지지 않았다. 글로 된 규칙은 모델이 "잠깐 보기만" 하는 순간을 막지 못한다. 그래서 도구 호출 자체에서 막는다.

## 근거로 쓴 동작 (Claude Code 문서)

- PreToolUse hook 입력의 `agent_type`은 `claude --agent <이름>`으로 띄운 메인 스레드면 그 이름이고, 서브에이전트 안에서 부른 도구면 서브에이전트의 타입이 우선한다. 서브에이전트 호출에는 `agent_id`도 붙는다. 그래서 hook이 "오케스트레이터 본인의 손"과 "위임받은 서브에이전트"를 구별할 수 있다.
- 에이전트 frontmatter의 `tools`/`disallowedTools`는 `--agent` 메인 스레드에도 걸린다. 하지만 `disallowedTools`에 `Bash(git push *)`처럼 지정자를 붙여도 Bash 전체가 빠진다. 명령 단위로 좁히려면 권한 규칙이나 PreToolUse hook을 써야 한다.
- 에이전트 본문이 비어 있지 않으면 기본 시스템 프롬프트를 통째로 대체한다. `agents/orchestrator.md`의 본문이 비어 있는 이유다.
- 플러그인 에이전트는 frontmatter의 `hooks`·`permissionMode`를 무시한다. hook은 플러그인의 `hooks/hooks.json`에 둔다.
- `/rename <이름>`은 세션 이름을 바꾸고 프롬프트 바에 보여 준다. `orca terminal rename --terminal <handle> --title <text>`는 Orca 탭 제목을 바꾼다.

## 비교한 안

### a. `agents/orchestrator.md`의 `tools:`를 좁힌다

오케스트레이터에 필요한 것은 Agent, SendMessage, AskUserQuestion, Skill, Cron*, Monitor, 자기 상태 디렉터리 Read, `orca`·`orch`·`skills-sync`용 Bash다.

깨지는 것:

- Bash를 명령 단위로 좁힐 수 없다. Bash를 남기면 `gh`·`curl`이 그대로 열리고, 빼면 `orca`·`orch`로 하는 라우팅과 사람이 그 세션에서 승인한 운영 작업(배포 트리거 등)까지 막힌다.
- `ROLE: orchestrator` 줄로 편입한 세션에는 걸리지 않는다. 문서에 적힌 두 번째 시작 경로가 통째로 빠진다.
- 메인 스레드의 도구 풀을 줄이면 거기서 띄운 서브에이전트가 물려받는 풀도 줄어드는지 문서로 확인하지 못했다. 그렇다면 위임 자체가 깨진다.
- Read를 상태 디렉터리로 한정하는 방법이 없다.

채택하지 않는다.

### b. 역할을 한정한 PreToolUse hook

오케스트레이터 메인 스레드일 때만 판정하는 hook이다. 판정 대상인지 먼저 가르고, 아니면 아무 출력 없이 끝난다.

- 대상: `agent_type == agent-skills:orchestrator`, 또는 `ROLE: orchestrator` 기록이 있는 세션에서 `agent_id`·`agent_type`이 없는 호출. 서브에이전트는 세션 id를 공유하므로 `agent_id`로 가른다. `ROLE: none`이면 끈다.
- 거부: 브라우저·데스크톱·채팅 전송 도구, WebFetch·WebSearch. 상태 디렉터리 밖 Edit·Write·NotebookEdit.
- 턴당 직접 작업 호출 수: 상태 디렉터리 밖 Read, Grep, Glob, 위에서 막지 않은 MCP 도구, 라우팅 목록 밖 Bash를 센다. 기본 3번까지 두고 네 번째부터 거부한다. `orch status`, `gh … --json` 한 번 같은 몇 초짜리 확인은 남는다.
- 거부 사유는 위임 경로(Agent를 배경으로, `orca worktree create`·`dispatch-card`, 리드에게 `orca orchestration send`, `orch …`)를 적고, 다른 도구로 같은 결과를 얻는 것은 거부를 세탁하는 것이라고 덧붙인다.

깨질 수 있는 것과 대비:

- 턴 경계. hook 입력의 `prompt_id`로 턴을 가르지만, 배경 작업 알림이나 cron으로 깨어난 턴이 새 `prompt_id`를 받는지는 확인하지 못했다. 그래서 마지막 작업 호출 뒤 10분이 지나면 새 턴으로 보고, `prompt_id`가 없는 호출은 세지 않는다. 그리고 라우팅 도구는 세지도 막지도 않는다. 셈이 틀려도 결과는 "위임해야 한다"이지 "아무것도 못 한다"가 아니다.
- 운영 작업. 사람이 그 세션에서 지시한 배포 같은 작업은 Bash 작업 호출로 세므로 한 턴에 세 번 안에 끝내야 한다. 그 앞의 조사는 원래 위임 대상이다.
- 비용. 모든 도구 호출마다 Python 하나가 뜬다. 대상이 아니면 역할 파일 하나만 읽고 끝난다.
- 우회. 라우팅 명령 안의 `$(…)`나 백틱(작은따옴표 밖), 상태 디렉터리 밖으로의 리다이렉트는 작업으로 센다. 입력·상태·설정이 깨져 있으면 막지 않고 통과시킨다. 사고를 막는 장치이지 샌드박스는 아니다.

### c. 세션 이름

`claude --agent agent-skills:orchestrator --name orchestrator`가 이미 시작 방법이다. 이름이 빠진 채 시작했거나 `ROLE:` 줄로 편입한 세션은 목록에서 역할이 안 보인다. reorient hook이 SessionStart에서 `session_title`이 `orchestrator`가 아니면 안내 끝에 한 줄을 붙인다. 사람에게 `/rename orchestrator`를 청하고, Orca 안이면 자기 탭 제목을 `orca terminal rename`으로 바꾸라는 줄이다. 매 프롬프트 한 줄 안내에는 넣지 않는다. 프롬프트 이벤트에 세션 이름이 늘 들어오는지 확인하지 못했고, 없을 때 매번 잘못 알리게 된다.

상태 줄(statusline)은 `~/.claude/settings.json`을 바꿔야 하는 일이고, `/rename`만으로 프롬프트 바에 이름이 보이므로 이번에는 다루지 않는다.

### d. 감시자를 `tail`로 묶지 않는다

배경 명령을 `| tail`이나 `| head`로 파이프하면 그 명령이 끝날 때까지 아무 줄도 출력 파일에 나오지 않는다. 끝나지 않는 감시자라면 영영 안 나온다. b의 hook이 오케스트레이터 본인 스레드에서 이런 호출(`tail -f` 제외)을 거부한다. 워커가 `make test 2>&1 | tail -40`을 배경으로 돌리는 것은 정상이라 전역 규칙으로 두지 않는다.

## 추천과 이유

b + c + d. 모두 `hooks/` 아래 두 파일과 `orchestrate` 문서 두 줄이다.

- 규칙이 글로는 지켜지지 않았으므로 도구 호출에서 막아야 한다. 그 방법 중 Bash를 명령 단위로 보고, `ROLE:` 편입 세션까지 덮고, 서브에이전트를 건드리지 않는 것은 b뿐이다.
- a는 막는 범위가 너무 넓거나(Bash 제거) 너무 좁고(Bash 유지), 위임까지 깨뜨릴 위험이 있다.
- 되돌리기 쉽다. `ROLE: none`(그 세션), `AGENT_SKILLS_ORCHESTRATOR_GUARD=0`(전체), `orchestrator_guard.work_calls`로 상한 조정. 셈 기준은 실제 운영에서 너무 빡빡하면 숫자만 바꾼다.

## 설정

`~/.claude/agent-skills.json`:

```json
{"orchestrator_guard": {"work_calls": 3, "write_dirs": ["~/notes/inbox"], "route_commands": ["my-router"], "deny_tools": ["^mcp__acme_chat__post"]}}
```

목록은 기본값에 더해진다. 셈 상태는 `$AGENT_SKILLS_STATE/orchestrator-guard/<session_id>.json`.

## 반영 방법

hook 연결(`hooks/hooks.json`)이 바뀌었으므로 떠 있는 세션은 `/reload-plugins`(또는 `skills-sync broadcast --plugins`) 뒤부터 적용된다.
