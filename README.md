# agent-skills

HITL과 HOTL을 함께 고려한 Claude Code 에이전트 스킬 모음.

[![License: MIT](https://img.shields.io/github/license/JeongJaeSoon/agent-skills)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/JeongJaeSoon/agent-skills)](https://github.com/JeongJaeSoon/agent-skills/releases/latest)

사람이 단계마다 승인하는 방식(Human-in-the-loop, HITL)과, 에이전트가 진행하고 사람은 지켜보다 예외에만 끼어드는 방식(Human-on-the-loop, HOTL)을 둘 다 쓸 수 있게 만든 Claude Code 플러그인이다. 티켓 하나를 첫 수정부터 머지·완료 기록까지 끌고 가는 흐름과, Orca 워커 여러 개로 프로젝트 하나를 끝내는 흐름을 스킬 40개와 hook으로 만든다. Claude Code로 GitHub 저장소의 티켓 작업을 맡기려는 개발자를 위한 것이다. 사용자 설정 쓰기나 되돌릴 수 없는 행동은 늘 사람이 승인하고, 머지 같은 지점은 사람이 승인할지 맡길지를 설정으로 고른다([HITL과 HOTL](#hitl과-hotl)).

## 특징

- **티켓 하나를 끝까지**: 계획, 테스트, 작성자 외 독립 리뷰, PR, CI 확인, 머지, 완료 기준별 근거를 단 티켓 닫기까지.
- **여러 워커로 프로젝트 하나**: Orca 워커마다 티켓 하나, PR은 `orch land` 게이트를 통과하거나 사람이 승인한 일반 머지로만 들어가고, 공유 파일을 고친 PR은 하나씩 들어간다.
- **사람이 보는 곳**: 현황 보고(`brief`), 대시보드(`orch-dash`)의 결정·권한 창·받은편지함, 트래커와 노트.
- **거부된 행동은 다시 하지 않는다**: 권한 창·auto mode 분류기·hook·사람이 거부한 행동은 형태를 바꿔 재시도하지 않고 보고한다.
- **설정이 없으면 끄거나 묻는다**: 노트(Obsidian·Markdown), 회고 원장, 개인 폴더, 채팅 채널은 설정에 적힌 곳만 쓰고 추정하지 않는다. 트래커 어댑터(Linear·Jira)는 설정이 없으면 Linear다.

## 설치

Claude Code 세션 안에서:

```
/plugin marketplace add JeongJaeSoon/agent-skills
/plugin install agent-skills@jeongjaesoon
```

설치 창이 응답 언어, 체크아웃 경로(기본 `~/src/agent-skills`), 개인 설정 저장소 URL을 묻는다. 모두 비워도 된다.

1. 새 세션을 열면 `setup-pc`를 제안한다. 받아들이면 에이전트가 체크아웃을 clone하고 설정 파일별 diff를 보인다.
2. `gh`가 로그인돼 있지 않으면 `! gh auth login`.
3. diff를 읽고 에이전트가 건넨 `! python3 -I <체크아웃>/scripts/setup.py --settings-only … --apply <해시>` 한 줄을 붙여 넣는다. 에이전트는 사용자 설정을 직접 쓰지 않는다.
4. 동기화 작업과 정기 자동화를 켜는 권한 창을 승인하고, 세션을 재시작한다.

준비물(GitHub `gh` 로그인, 트래커, 노트, Orca·macOS가 필요한 기능), 셸에서 직접 설치, 갱신은 [docs/new-pc.md](docs/new-pc.md)와 [docs/operations.md](docs/operations.md#준비물)에 있다.

## 사용 예

스킬은 description에 적힌 상황이 오면 모델이 스스로 부른다.

- 「이 티켓 끝내줘」(티켓 키 `ABC-1`과 함께) → `deliver-ticket`이 계획부터 머지·티켓 닫기까지 한다.
- 「현황 보고해줘」 → `brief`가 진행 중, 다른 사람 대기, 결정 필요, 지금 오너가 할 일, 보류로 나눠 보고한다. `/brief`는 Claude Code 내장 명령이라 직접 부를 때는 `/agent-skills:brief`.

시나리오 다섯 개는 [docs/operations.md](docs/operations.md#사용-시나리오).

## HITL과 HOTL

지점마다 기본이 HITL인지 HOTL인지와, 설정으로 바꾸는 법이다.

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
| `janitor`, `pressure-watch` | HOTL: 에이전트가 남긴 끝난 리소스를 정기적으로 정리한다 | `skills-sync setup disable <이름>` |

HOTL에서 사람이 보는 곳은 `brief`의 현황 보고와 대시보드의 결정·권한 창·받은편지함이다. 대시보드·`janitor`·watch 회차는 Orca를 전제로 한다. 세부는 [docs/operations.md](docs/operations.md#안전-경계).

## 스킬

스킬별 호출 시점, 내용, 동봉 파일은 [스킬 카탈로그](docs/skills.md)(English: [docs/skills.en.md](docs/skills.en.md), 日本語: [docs/skills.ja.md](docs/skills.ja.md)).

| 묶음 | 스킬 | 하는 일 |
|---|---|---|
| [티켓 하나](docs/skills.md#티켓-하나) | `write-ticket`, `deliver-ticket`, `ui-review`, `handoff-ticket`, `dispatch-card`, `end-session`, `record-progress` | 티켓 쓰기부터 완료·다음 티켓 넘기기·세션 정리·진행 기록까지 |
| [프로젝트 하나](docs/skills.md#프로젝트-하나-orca-워커-여러-개) | `orchestrate`, `measure-delivery` | 여러 티켓을 Orca 워커로 병렬 착지시키고, 끝난 뒤 실제로 어땠는지 잰다 |
| [머신 하나](docs/skills.md#머신-하나) | `reap-resources`, `secure-fill`, `tune-automode`, `skills-config`, `setup-pc` | 끝난 세션의 잔여물 정리, 에이전트가 보지 않는 비밀값 입력, autoMode 규칙 초안, agent-skills.json 키·permissions.allow 변경, PC 설치·갱신 |
| [채팅·GitHub](docs/skills.md#채팅) | `watch-mentions`, `watch-prs` | 나와 관련된 채팅과 PR을 작업으로 모은다 |
| [어댑터](docs/skills.md#어댑터) | `use-tracker`, `use-notes` | Linear·Jira 티켓, Obsidian·Markdown 노트 |
| [글쓰기](docs/skills.md#글쓰기) | `brief`, `stage`, `map-features`, `write-plainly`, `prune-comments`, `write-skill` | 현황 보고, 단계표, 제품 기능 지도, 문체, diff 주석 정리, 스킬 작성 점검 |
| [pstack](docs/skills.md#pstack-스킬-lauren-tan-mit) | `architect`, `arena`, `blast-radius`, `create-verification-skill`, `figure-it-out`, `how`, `interrogate`, `maintain-verification-skill`, `principles`, `recall`, `reflect`, `show-me-your-work`, `swarm`, `tdd`, `teach`, `why` | 설계, 비교, 영향 범위, 검증, 설명, 회고. 원본 이름을 유지해 upstream을 따라간다 |

여러 세션이 함께 일할 때 워커 위의 층은 오케스트레이터(최상위 세션 하나)와 리드(프로젝트 하나를 맡은 세션) 둘이다. 이름·시작 명령·`ROLE` 줄은 [orchestrate의 Roles 표](skills/orchestrate/SKILL.md#roles), Orca의 "coordinator"와의 대응도 거기에 있다.

pstack 묶음은 Lauren Tan의 [pstack](https://github.com/cursor/plugins)(MIT)에서 가져왔다.

## 문서

- [docs/new-pc.md](docs/new-pc.md): 새 PC 설치, 셸에서 직접, 갱신
- [docs/operations.md](docs/operations.md): 용어, 정기 자동화, 사용 시나리오, 안전 경계와 hook, 스킬 고치기
- [docs/automation.md](docs/automation.md): 어떤 일을 어떤 장치로 돌리나
- [docs/configuration-migration.md](docs/configuration-migration.md): 설정 키와 전환
- [docs/platform.md](docs/platform.md): 체크아웃 로드와 동기화 설계
- 한눈에 보기: [스킬 카탈로그](https://claude.ai/artifact/16394ZGS3RX9sTU6h2ME8d), [사용 안내서](https://claude.ai/artifact/1ifoY8ykKHBqcZMrKN9ACR). 오른쪽 위에서 한국어·English·日本語를 고르고, 링크 끝에 `#en`이나 `#ja`를 붙이면 그 언어로 열린다.

## 테스트

```bash
for t in skills/orchestrate/scripts/test_*.py skills/use-tracker/scripts/test_tracker.py skills/measure-delivery/scripts/test_measure.py skills/reap-resources/scripts/test_reap.py skills/reap-resources/scripts/test_pressure.py skills/watch-mentions/scripts/test_mentions.py skills/watch-prs/scripts/test_prs.py skills/watch-prs/scripts/test_review_wait.py skills/watch-prs/scripts/test_notify.py skills/secure-fill/scripts/test_secure_fill.py skills/tune-automode/scripts/test_automode_rule.py skills/skills-config/scripts/test_config_emit.py skills/setup-pc/scripts/test_setup_check.py scripts/test_setup.py skills/brief/scripts/test_table_width.py skills/reflect/scripts/test_lesson_ids.py skills/deliver-ticket/scripts/test_shots.py skills/ui-review/scripts/test_ui_measure.py hooks/test_*.py scripts/test_sync.py scripts/test_preview_watch.py scripts/test_automations.py scripts/test_share_server.py scripts/test_reap_hard.py scripts/test_public_shapes.py; do python3 "$t"; done
bash scripts/pstack-sync-test.sh
```

스킬을 고치는 절차(worktree, 동기화, `claude plugin eval`)는 [docs/operations.md](docs/operations.md#스킬-고치기)와 [AGENTS.md](AGENTS.md).

## 라이선스

MIT. `vendor/pstack`과 거기서 가져온 스킬은 원저작자의 MIT 라이선스를 따른다(`vendor/pstack/LICENSE`).
