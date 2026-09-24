# agent-skills

Claude Code 개인 스킬 저장소. 티켓 하나를 끝까지 끌고 가는 흐름과, Orca 워커 여러 개로 프로젝트 하나를 끝내는 흐름을 스킬로 만든다. 일부 스킬은 Lauren Tan의 [pstack](https://github.com/cursor/plugins)(MIT)에서 가져왔다.

전제는 Orca(멀티 에이전트 IDE, `orca` CLI), GitHub, 티켓 트래커(Linear, Jira 어댑터 준비)다. 노트는 Obsidian vault 또는 일반 Markdown 폴더를 쓴다.

선택 도구: 교차 검토는 [Codex 플러그인](https://github.com/openai/codex-plugin-cc)의 companion 스크립트, 브라우저 검증은 Aside를 쓴다. 없으면 그 단계만 다른 도구로 바꾼다.

작업 방식은 pstack이 기준이다. superpowers 플러그인과 함께 쓰면 두 흐름이 겹치므로 끄고 쓴다(`enabledPlugins`에서 `superpowers@claude-plugins-official: false`).

## 설치

```bash
git clone https://github.com/JeongJaeSoon/agent-skills.git ~/workspace/project/agent-skills
cd ~/workspace/project/agent-skills
python3 scripts/install.py                       # 무엇이 바뀌는지 보기
python3 scripts/install.py --write               # ~/.claude/skills 에 symlink
python3 scripts/install.py --settings --write    # orchestrate용 권한 규칙과 hook (백업 후 병합)
```

- 설치본은 이 체크아웃의 `skills/<name>` symlink다. 메인 브랜치를 fast-forward하면 바로 반영된다. 작업은 worktree 브랜치에서 한다.
- `~/.claude/skills`에 실제 폴더로 있는 스킬(다른 도구가 관리)은 건드리지 않는다. 설치하지 않을 스킬은 `.install-ignore`에 한 줄씩 적는다.
- 이름이 바뀐 스킬(`ship-pr` → `deliver-ticket`, `dispatch-work` → `dispatch-card`, `use-obsidian` → `use-notes`)은 `legacy/`의 안내용 별칭으로 남아 있다.

`--settings`가 넣는 것은 여섯 규칙과 hook 하나다. 나머지는 auto mode 분류기가 판단한다.

| 규칙 | 이유 |
|---|---|
| allow `orca orchestration:*` | 워커가 메시지 명령마다 멈추지 않게 |
| allow `prog.py land:*` | 검토 판정·CI·착지 순서를 확인하고 머지하는 관문. 워커가 직접 머지하면 분류기가 거절한다 |
| deny `orca orchestration reset`, `worker-abandon` | 되돌릴 수 없는 Run 조작 |
| ask `orca terminal send`, `orca orchestration gate-resolve` | 다른 에이전트 입력, 사람이 내려야 할 결정 |
| hook `mailbox_guard.py` | 다른 터미널의 Orca 메일함을 읽어 소비하는 것을 막는다(자기 메일함은 허용) |

설정 파일(`~/.claude/agent-skills.json`)로 트래커와 노트 저장소를 고른다. 형식은 `use-tracker`, `use-notes` 스킬에 있다.

## 스킬

**티켓 하나**

| 스킬 | 언제 |
|---|---|
| `write-ticket` | 티켓을 쓸 때. 후속 티켓 형식, 템플릿 |
| `deliver-ticket` | 첫 수정부터 완료까지: 계획, 테스트, Codex 교차 검토, GitHub stack, E2E, 머지, 완료 기준 |
| `handoff-ticket` | 끝난 뒤 다음 티켓을 새 Orca 카드로 |
| `dispatch-card` | 이 세션은 계속하면서 다른 저장소나 곁가지를 카드로 |
| `end-session` | 세션·카드·worktree 정리 |

**프로젝트 하나 (Orca 여러 워커)**

| 스킬 | 언제 |
|---|---|
| `orchestrate` | 코디네이터가 여러 티켓을 병렬로 착지시킬 때. 착지 순서, 독점 레인, main 가디언, QA 리드, 대시보드 |
| `measure-delivery` | 프로젝트가 실제로 어땠는지: 후속 티켓 증가, 재작업, 토큰 |

**어댑터**: `use-tracker`(Linear, Jira), `use-notes`(Obsidian, Markdown).

**참고용**: `obsidian-cli`(Obsidian CLI 명령 안내). `.install-ignore`에 있어 기본 설치에서 빠진다.

**pstack**: `architect`, `blast-radius`, `create-verification-skill`, `how`, `interrogate`, `maintain-verification-skill`, `principles`, `reflect`, `show-me-your-work`, `swarm`, `tdd`. 원본 이름을 유지해 upstream을 따라간다.

```bash
python3 scripts/pstack-sync.py            # upstream 변경 확인 (쓰지 않음)
python3 scripts/pstack-sync.py --write    # 충돌이 없을 때만 반영하고 pin 갱신
```

출처와 수정 내용은 `vendor/pstack/NOTICE.md`, 고정 커밋은 `vendor/pstack/manifest.json`.

## orchestrate 한눈에

- 워커가 자기 PR을 `prog.py land`로 착지시킨다. 준비된 PR은 base보다 뒤처져 있어도 병렬로 머지되고, migration·CI·Dockerfile·compose 같은 공유 파일은 독점 레인에서 base당 하나씩 최신 base 위에서 머지된다.
- 의존 관계는 Orca task deps가 정본이다. 순서대로 들어가야 하는 체인은 GitHub stack으로 한 번에 머지한다.
- main이 red가 되면 main 가디언이 flake 여부부터 보고 hotfix나 revert를 고른다. QA 리드가 티켓 검증, 주기 E2E, 설계·코드 정합성 감사를 맡는다.
- `dash.py serve`: 목표, 완료 조건, 착지 순서, 의존 그래프, 워커, 토큰을 보여주는 대시보드(라이트·다크, 창 크기에 맞춤). 모델 호출 없이 JSON으로 갱신한다.

## 테스트

```bash
for t in skills/orchestrate/scripts/test_*.py skills/use-tracker/scripts/test_tracker.py; do python3 "$t"; done
bash scripts/pstack-sync-test.sh
```

## 라이선스

MIT. `vendor/pstack`과 거기서 가져온 스킬은 원저작자의 MIT 라이선스를 따른다(`vendor/pstack/LICENSE`).
