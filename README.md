# agent-skills

개인 Claude Code 스킬의 소스 저장소. 설치 위치는 `~/.claude/skills`.

## 설치

`~/.claude/skills/<name>` → 이 저장소 메인 체크아웃의 `skills/<name>` symlink다(이 저장소의 스킬 전부). 메인 브랜치가 fast-forward될 때만 설치본이 바뀐다. 작업은 worktree 브랜치에서 하고, 검토가 끝나면 메인에 fast-forward한다.

이 저장소에 없는 설치본도 있다. `~/.claude/skills/synced/`는 claude.ai 계정 동기화가, `aside-browser`는 Aside가 관리하므로 여기서 다루지 않는다.

## pstack

일부 스킬은 [cursor/plugins](https://github.com/cursor/plugins)의 pstack(Lauren Tan, MIT)에서 가져왔다. 출처와 고친 내용은 `vendor/pstack/NOTICE.md`, 고정 커밋과 파일 목록은 `vendor/pstack/manifest.json`에 있다.

```bash
python3 scripts/pstack-sync.py            # upstream 변경 확인 (쓰지 않음)
python3 scripts/pstack-sync.py --write    # 충돌이 없을 때만 반영하고 pin 갱신
bash scripts/pstack-sync-test.sh          # sync 스크립트 자체 테스트
```

## 조사 자료

`docs/research/2026-09-23-agent-platform-alpha/`: agent-platform alpha 자율 개발 루프 분석의 원자료와 pstack 도입 방식 비교(`06-pstack-adoption.md`).
