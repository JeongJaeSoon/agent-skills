# agent-skills

[English](README.md) · [日本語](README.ja.md) · 한국어

티켓 하나를 머지된 PR까지 끌고 가는 Claude Code 스킬 모음. 사람이 어디서 승인할지는 직접 고른다.

[![License: MIT](https://img.shields.io/github/license/JeongJaeSoon/agent-skills)](LICENSE)
[![Latest release](https://img.shields.io/github/v/release/JeongJaeSoon/agent-skills)](https://github.com/JeongJaeSoon/agent-skills/releases/latest)

## 무엇인가

agent-skills는 Claude Code 플러그인이다. 스킬 41개, 에이전트 정의 4개, hook 10개와 명령 몇 개로 이뤄져 있다. 흐름은 둘이다. 하나는 티켓 하나를 첫 수정부터 머지와 티켓 닫기까지 끌고 간다. 다른 하나는 Orca 워커 여러 개를 동시에 돌려 워커마다 티켓 하나를 맡기고, 그 PR들을 순서대로 머지한다.

GitHub 저장소의 티켓 작업을 Claude Code에 맡기려는 개발자를 위한 것이다. 단계마다 사람이 승인하는 방식(Human-in-the-loop, HITL)과, 에이전트가 진행하고 사람은 지켜보다 예외에만 끼어드는 방식(Human-on-the-loop, HOTL)을 지점마다 고른다. 사용자 설정 쓰기와 되돌릴 수 없는 행동은 늘 사람의 승인을 기다린다.

주 환경은 macOS와 [Orca](https://github.com/stablyai/orca)다. 티켓 하나짜리 스킬은 Claude Code가 도는 곳이면 어디서나 쓴다. 여러 세션을 다루는 스킬, 대시보드, 정기 회차는 Orca가 있어야 하고, 자동화 일부는 macOS가 있어야 한다.

## 설명 영상

구성 요소, 두 흐름, 사람이 결정권을 쥐는 지점을 4분 남짓으로 소개한다.

https://github.com/user-attachments/assets/4989f7f2-54d3-493a-8692-e0a4157199b0

자막: [한국어 (.srt)](docs/video/agent-skills-explainer-ko.srt). [영어판](https://github.com/user-attachments/assets/1e240200-0d28-4f8b-b13c-ae25a68984f9)과 [일본어판](https://github.com/user-attachments/assets/206c4624-dca8-448d-8179-69c08ecc511c)도 있다.

## 무엇을 얻나

- **티켓 하나를 끝까지.** `deliver-ticket`이 계획, 테스트, 작성자 외 독립 리뷰, PR, CI 확인, 머지, 완료 기준별 근거를 단 티켓 닫기까지 한다.
- **워커 여러 개로 프로젝트 하나.** `orchestrate`가 Orca 워커마다 티켓 하나를 맡기고 프로그램 원장을 둔다. PR은 `orch land` 게이트를 통과하거나 사람이 승인해야만 머지되고, 공유 파일을 고친 PR은 하나씩 머지된다.
- **사람이 보는 곳.** `brief`가 진행 중, 다른 사람 대기, 내 결정 대기, 보류로 나눠 보고한다. 대시보드 `orch-dash`에는 열린 결정, 권한 창, 받은편지함이 뜬다.
- **거부된 행동은 다시 하지 않는다.** 권한 창, auto mode 분류기, hook, 사람이 거부한 행동은 형태를 바꿔 재시도하지 않고 보고한다.
- **설정이 없으면 추정하지 않는다.** 노트, 회고 원장, 개인 폴더, 채팅 채널은 설정에 적힌 곳만 쓴다. 키가 없으면 그 기능은 꺼지거나 스킬이 먼저 묻는다.
- **머신 정리.** `reap-resources`가 끝난 세션이 남긴 프로세스, worktree, 컨테이너를 목록으로 뽑고, 목록에 오른 것만 치운다.

## 준비물

필수:

- Claude Code
- `git`
- `python3` 3.9 이상(macOS의 `/usr/bin/python3`도 된다)

전체 설정에만 필요:

- `gh`, github.com 로그인

선택:

- [Orca](https://github.com/stablyai/orca): 여러 세션을 다루는 스킬과 정기 회차
- Linear 또는 Jira: 트래커 스킬(설정이 없으면 Linear)
- Obsidian 또는 평범한 Markdown 폴더: 노트 스킬
- tailscale, caddy: `preview-watch`와 `share-server`

macOS에서는 전체 설정이 동기화 작업과 기본 자동화를 LaunchAgent로 설치한다. Linux에서는 대신 crontab 줄을 출력하니 직접 넣는다.

## 설치

### 에이전트에게 맡기기

Claude Code 세션에 아래 의뢰문을 붙여 넣는다.

```text
https://github.com/JeongJaeSoon/agent-skills 의 Claude Code 플러그인을 설정해 줘. https://raw.githubusercontent.com/JeongJaeSoon/agent-skills/main/INSTALL.md 를 읽고 그대로 따라 줘. 먼저 내 환경을 확인하고, 고를 것이 있으면 그때마다 묻고, 내가 직접 쳐야 하는 명령은 그대로 건네줘. 설정 파일은 직접 고치지 마.
```

에이전트가 따르는 절차는 [INSTALL.md](INSTALL.md)(영어)다. 환경을 확인한 뒤 마켓플레이스 플러그인만 설치할지 전체 설정까지 할지, 넣을 값(응답 언어, 체크아웃 경로, 개인 설정 저장소)을 묻고, 실행할 명령 두 개에 대해 승인을 받는다.

### 직접 하기

1. Claude Code 세션에서 마켓플레이스를 더하고 플러그인을 설치한다.

   ```text
   /plugin marketplace add JeongJaeSoon/agent-skills
   /plugin install agent-skills@jeongjaesoon
   ```

   설치 창이 응답 언어, 체크아웃 경로(기본 `~/src/agent-skills`), 개인 설정 저장소 URL을 묻는다. 셋 다 비워도 된다.
2. 새 세션을 연다. 플러그인은 세션이 시작될 때 읽히므로 여기서는 `/reload-plugins`로 부족하다.
3. 새 세션은 받아들이거나 거절할 때까지 매번 `setup-pc`를 제안한다. 거절하면 그 프로필에서는 더 제안하지 않는다. 전체 설정을 하려면 받아들인다. 에이전트가 체크아웃을 clone하고 설정 파일별 diff를 보인다. 설정 파일은 에이전트가 직접 쓰지 않는다. diff를 읽고 에이전트가 건넨 `! python3 ... --apply <해시>` 한 줄을 붙여 넣는다.
4. 동기화 작업과 자동화를 켜는 권한 창을 승인하고 세션을 재시작한다.

셸에서만 설치하는 법, 갱신, 승인 지점 전체 표, 제거는 [docs/install.ko.md](docs/install.ko.md)에 있다.

## 첫걸음

스킬은 description에 적힌 상황이 오면 모델이 스스로 부른다. `/agent-skills:<이름>`으로 직접 부를 수도 있다.

- 「ABC-1 끝내줘」라고 하면 `deliver-ticket`이 계획부터 머지, 티켓 닫기까지 한다. 트래커가 있어야 한다(기본은 Linear, 바꾸려면 `tracker.adapter`, [설정](#설정) 참고).
- 「현황 보고해줘」라고 하면 `brief`가 돈다. Claude Code에 내장 `/brief`가 있어서 직접 부를 때는 `/agent-skills:brief`다.
- Orca 안에서 `claude --agent agent-skills:orchestrator --name orchestrator`로 최상위 세션을 띄우고 모든 세션을 맡긴다. 오케스트레이터가 프로젝트마다 리드 세션을 띄우고, 리드가 워커를 돌린다.

## 사람이 결정권을 쥐는 지점

- **내 설정.** `setup-pc`, `tune-automode`, `skills-config`는 diff와 `!` 줄만 만들고, 실행은 사람이 한다. 이 동작은 바꿀 수 없다.
- **권한 창과 거부.** 권한 창은 사람의 응답을 기다린다. auto mode 분류기가 행동을 거부하면 에이전트는 보고하고 멈춘다. 어떤 부류의 행동을 이후 통과시키려면 사람이 `permissions.allow` 항목이나 `tune-automode`가 초안을 쓴 autoMode 규칙을 더한다.
- **되돌릴 수 없는 행동.** 파괴적 migration, 공유 브랜치 force push, 프로덕션 배포, 바깥으로 보내기는 늘 멈추고 묻는다.
- **머지.** `orchestrate`와 `deliver-ticket`이 준비된 PR을 스스로 머지하는 것은 그 저장소의 머지를 허용하는 autoMode 규칙이 있을 때뿐이다. 없으면 묻는다. `human-gate` 정책으로 두거나 직접 보겠다고 하면 머지마다 그 앞에서 멈춘다.
- **정기 정리.** `janitor`와 `pressure-watch`가 끝난 에이전트의 잔여물을 정기적으로 치운다. 끄려면 `skills-sync setup disable <이름>`. `watch-prs`의 내 PR 자동 승인·자동 머지는 설정하기 전까지 꺼져 있다.

지점마다 기본값과 바꾸는 법은 [docs/install.ko.md](docs/install.ko.md#안전-장치)에 있다.

## 스킬

스킬별 호출 시점, 내용, 동봉 파일은 [스킬 카탈로그](docs/skills.md)에 있다.

| 묶음 | 스킬 | 하는 일 |
|---|---|---|
| [티켓 하나](docs/skills.md#티켓-하나) | `write-ticket`, `deliver-ticket`, `ui-review`, `handoff-ticket`, `dispatch-card`, `end-session`, `record-progress` | 티켓 쓰기부터 완료, 다음 티켓 넘기기, 세션 정리, 진행 기록까지 |
| [프로젝트 하나](docs/skills.md#프로젝트-하나-orca-워커-여러-개) | `orchestrate`, `measure-delivery` | 여러 티켓을 Orca 워커로 병렬 처리해 머지하고, 끝난 뒤 실제로 어땠는지 잰다 |
| [머신 하나](docs/skills.md#머신-하나) | `reap-resources`, `secure-fill`, `tune-automode`, `skills-config`, `setup-pc` | 끝난 세션의 잔여물 정리, 에이전트가 보지 않는 비밀값 입력, autoMode 규칙 초안, 설정 키 변경, PC 설치·갱신 |
| [채팅·GitHub](docs/skills.md#채팅) | `watch-mentions`, `watch-prs` | 나와 관련된 채팅과 PR을 작업으로 모은다 |
| [어댑터](docs/skills.md#어댑터) | `use-tracker`, `use-notes` | Linear·Jira 티켓, Obsidian·Markdown 노트 |
| [글쓰기](docs/skills.md#글쓰기) | `brief`, `stage`, `todo`, `map-features`, `write-plainly`, `prune-comments`, `write-skill` | 현황 보고, 단계표, 할 일·보류 큐, 제품 기능 지도, 문체, diff 주석 정리, 스킬 작성 점검 |
| [pstack](docs/skills.md#pstack-스킬-lauren-tan-mit) | `architect`, `arena`, `blast-radius`, `create-verification-skill`, `figure-it-out`, `how`, `interrogate`, `maintain-verification-skill`, `principles`, `recall`, `reflect`, `show-me-your-work`, `swarm`, `tdd`, `teach`, `why` | 설계, 비교, 영향 범위, 검증, 설명, 회고 |

pstack 묶음은 Lauren Tan의 [pstack](https://github.com/cursor/plugins)(MIT)에서 가져왔고, upstream을 따라가려고 원본 이름을 유지한다. 여러 세션이 함께 일할 때 워커 위의 층은 오케스트레이터(최상위 세션 하나)와 리드(프로젝트 하나를 맡은 세션) 둘이다. 자세한 것은 [orchestrate의 Roles 표](skills/orchestrate/SKILL.md#roles)(영어).

## 설정

필수 설정은 없다. 스킬은 `~/.claude/agent-skills.json`의 스킬별 섹션에서 키를 읽고, 목록과 비밀값은 `~/.config/agent-skills/`에서 읽는다. 키가 없으면 그 기능은 꺼지거나 스킬이 행동 전에 묻는다. 트래커 어댑터는 `tracker.adapter`가 없으면 Linear다. `skills-config` 스킬에 맡기면 키를 쓰는 스크립트를 만들어 주고, 실행은 사람이 한다. 키와 기본값은 [docs/configuration-migration.md](docs/configuration-migration.md).

## 문서

- [docs/install.ko.md](docs/install.ko.md): 설치, 갱신, 승인 지점, 제거, 문제 해결
- [INSTALL.md](INSTALL.md)(영어): 에이전트가 따르는 설치 절차
- [docs/skills.md](docs/skills.md): 스킬 카탈로그
- [docs/operations.md](docs/operations.md): 용어, 정기 자동화, 사용 시나리오, 안전 장치와 hook, 스킬 고치기
- [docs/new-pc.md](docs/new-pc.md): 새 PC 설치의 세부
- [docs/automation.md](docs/automation.md): 어떤 일을 어떤 장치로 돌리나
- [docs/platform.md](docs/platform.md): 체크아웃 로드와 동기화 설계
- [docs/token-usage.md](docs/token-usage.md): 이 플러그인이 컨텍스트에 넣는 토큰과 기능별로 쓴 토큰 재기
- [소개 페이지](https://jeongjaesoon.github.io/agent-skills/): 한 장으로 보는 소개(GitHub Pages)
- 한눈에 보기: [스킬 카탈로그](https://claude.ai/artifact/16394ZGS3RX9sTU6h2ME8d), [사용 안내서](https://claude.ai/artifact/1ifoY8ykKHBqcZMrKN9ACR). 오른쪽 위에서 한국어·English·日本語를 고르고, 링크 끝에 `#en`이나 `#ja`를 붙이면 그 언어로 열린다.

## 기여와 보안

체크아웃 준비와 이 공개 저장소의 규칙은 [CONTRIBUTING.md](CONTRIBUTING.md)(영어)에 있다. 저장소에서 일하는 에이전트는 [AGENTS.md](AGENTS.md)도 읽는다. 취약점은 공개 issue가 아니라 [SECURITY.md](SECURITY.md)(영어)의 방법으로 비공개로 알린다.

## 테스트

CI가 없다. 변경을 보내기 전에 테스트 파일을 모두 돌린다.

```bash
for t in skills/orchestrate/scripts/test_*.py skills/use-tracker/scripts/test_tracker.py skills/measure-delivery/scripts/test_measure.py skills/reap-resources/scripts/test_reap.py skills/reap-resources/scripts/test_cards.py skills/reap-resources/scripts/test_pressure.py skills/watch-mentions/scripts/test_mentions.py skills/watch-prs/scripts/test_prs.py skills/watch-prs/scripts/test_review_wait.py skills/watch-prs/scripts/test_notify.py skills/secure-fill/scripts/test_secure_fill.py skills/tune-automode/scripts/test_automode_rule.py skills/skills-config/scripts/test_config_emit.py skills/setup-pc/scripts/test_setup_check.py scripts/test_setup.py skills/brief/scripts/test_table_width.py skills/reflect/scripts/test_lesson_ids.py skills/deliver-ticket/scripts/test_shots.py skills/ui-review/scripts/test_ui_measure.py hooks/test_*.py scripts/test_sync.py scripts/test_card.py scripts/test_preview_watch.py scripts/test_automations.py scripts/test_share_server.py scripts/test_reap_hard.py scripts/test_public_shapes.py scripts/test_skill_tokens.py; do python3 "$t"; done
bash scripts/pstack-sync-test.sh
```

## 라이선스

MIT. `vendor/pstack`과 거기서 가져온 스킬은 원저작자의 MIT 라이선스를 따른다(`vendor/pstack/LICENSE`).
