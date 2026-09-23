# agent-skills

개인 Claude Code 스킬의 소스 저장소. 설치 위치는 `~/.claude/skills`.

## 설치

- 2026-09-24에 추가한 스킬 8개(`run-program`, `measure-delivery`, `create-verification-skill`, `maintain-verification-skill`, `interrogate`, `show-me-your-work`, `blast-radius`, `pstack-principles`)는 `~/.claude/skills/<name>` → 이 저장소 메인 체크아웃의 `skills/<name>` symlink다. 메인 브랜치가 fast-forward될 때만 설치본이 바뀐다.
- 그 전부터 있던 7개(`aside-browser`, `use-obsidian`, `dispatch-work`, `end-session`, `handoff-ticket`, `ship-pr`, `write-ticket`)는 `~/.claude/skills`에 실제 디렉터리로 설치돼 있다. 여기 사본은 그 원문이다.

## pstack

일부 스킬은 [cursor/plugins](https://github.com/cursor/plugins)의 pstack(Lauren Tan, MIT)에서 가져왔다. 출처와 고친 내용은 `vendor/pstack/NOTICE.md`, 고정 커밋과 파일 목록은 `vendor/pstack/manifest.json`에 있다.

```bash
python3 scripts/pstack-sync.py            # upstream 변경 확인 (쓰지 않음)
python3 scripts/pstack-sync.py --write    # 충돌이 없을 때만 반영하고 pin 갱신
bash scripts/pstack-sync-test.sh          # sync 스크립트 자체 테스트
```

## 조사 자료

`docs/research/2026-09-23-agent-platform-alpha/`: agent-platform alpha 자율 개발 루프 분석의 원자료와 pstack 도입 방식 비교(`06-pstack-adoption.md`).
