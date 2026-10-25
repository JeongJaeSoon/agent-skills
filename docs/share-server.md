# share-server: 공유 폴더 읽기 전용 뷰어

공유 폴더(기본 `~/share`)를 `127.0.0.1`에서 [copyparty](https://github.com/9001/copyparty)로 읽기 전용 서빙한다. 이미지 썸네일과 확대 보기, 마크다운 렌더링, 파일명·본문 검색이 된다. 모델·네트워크 API는 부르지 않는다.

기본은 꺼져 있다. 공유 폴더를 `tailscale serve` 같은 것으로 열어 쓰는 머신에서만 `skills-sync setup enable share-server`로 켜고 `disable`로 끈다([automation.md](automation.md#처음-설치할-때)). tailnet 공개 설정은 건드리지 않는다.

```bash
bin/share-server serve [--root DIR] [--port N]   # 앞에서 한 번 띄운다
bin/share-server install                         # 메인 체크아웃에서. venv 를 만들고 KeepAlive LaunchAgent 로 등록
bin/share-server uninstall
```

## 설치되는 것

`share_server.py`의 `PINS`에 고정한 copyparty와 의존성(Jinja2, MarkupSafe, Pillow, mutagen)을 `~/.local/share/agent-skills/copyparty-venv`에 wheel로만 설치한다. 고정 버전은 채택 시점에 릴리스 후 14일이 지난 것만 쓴다. `PINS`가 바뀌면 다음 `install`이나 `serve`가 venv를 새로 만든다. `install`은 venv를 먼저 만들고, 실패하면 돌던 LaunchAgent를 내리지 않고 멈춘다.

| 경로 | 내용 |
|---|---|
| `~/.local/share/agent-skills/copyparty-venv` | copyparty venv |
| `~/.cache/agent-skills/copyparty` | 검색 색인(`up2k.db`)과 썸네일 캐시. 지워도 다시 만든다 |
| `~/.local/state/agent-skills/copyparty` | copyparty의 salt와 세션 DB |
| `~/.local/state/agent-skills/share-server.log` | 로그 |

공유 폴더 안에는 아무것도 쓰지 않는다.

## 폴더를 열면

- 목록은 썸네일 그리드로 연다(`g` 키나 `田` 버튼으로 표 보기). 이미지를 누르면 확대 보기가 열리고 좌우 키로 넘긴다.
- `index.html`이 있는 폴더는 브라우저가 그 페이지로 바로 연다. 목록을 보려면 주소 끝에 `?v`를 붙인다.
- `.md`는 렌더링해서 보여 준다. 편집기는 쓰기 권한이 없어 열리지 않는다.
- 🔎 탭에서 파일명·경로로 찾는다. `tags` 칸은 텍스트 파일(`md`, `txt`, `html`, `json`, `csv`, `log`, `yaml` 등) 앞 64 KiB의 본문도 찾는다(`scripts/share_text_tag.py`가 색인). 새 파일은 60초 안에 색인된다.

## 보안

- `127.0.0.1`에만 바인딩한다. 바꾸는 설정은 없다.
- 계정이 없고 모두에게 읽기 권한만 준다. 업로드·폴더 만들기·삭제·이동·복사·WebDAV·폼 메시지 기록은 모두 거부되고 폴더는 그대로다.
- 숨김 항목(`.`으로 시작)은 목록·검색·zip 내려받기에서 빠진다. 다만 copyparty는 정확한 주소로 요청하면 숨김 파일도 내준다. 비밀값은 공유 폴더에 두지 않는다.
- 루트 밖으로 나가는 심볼릭 링크와 `..` 경로는 거부한다(`--xvol`).
- 페이지에 호스트명, 디스크 사용량, 다른 방문자의 내려받기 기록을 보이지 않는다.

## 설정

`~/.claude/agent-skills.json`의 `"share-server"` 키. 없으면 기본값을 쓴다.

| 키 | 기본 | 뜻 |
|---|---|---|
| `root` | `~/share` | 서빙할 폴더. 경로에 `:`가 있으면 거부한다 |
| `port` | `13201` | loopback 포트 |
| `label` | `io.github.jeongjaesoon.share-server` | LaunchAgent 라벨. 이미 같은 폴더를 서빙하는 LaunchAgent가 있으면 그 라벨을 적어 같은 자리에서 교체한다 |

`install`은 같은 라벨의 plist가 이미 있고 내용이 다르면 `<라벨>.plist.bak-<시각>`으로 옆에 남긴 뒤 덮어쓴다. 되돌릴 때는 `uninstall` 뒤 백업 plist를 원래 이름으로 복사해 `launchctl bootstrap gui/$(id -u) <plist>`로 올린다. venv·캐시·상태 폴더는 `uninstall`이 지우지 않는다.

## 테스트

`scripts/test_share_server.py`. `Live` 테스트는 venv가 있을 때만 임시 폴더에 copyparty를 띄워 검색·숨김·쓰기 거부를 확인하고, 없으면 건너뛴다.
