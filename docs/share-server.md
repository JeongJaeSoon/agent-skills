# share-server: 공유 폴더 읽기 전용 뷰어

공유 폴더(기본 `~/share`)를 `127.0.0.1`에서 [copyparty](https://github.com/9001/copyparty)로 읽기 전용 서빙한다. 이미지 썸네일과 확대 보기, 마크다운 렌더링, 파일명·본문 검색이 된다. 모델·네트워크 API는 부르지 않는다.

기본은 꺼져 있다. 공유 폴더를 `tailscale serve` 같은 것으로 열어 쓰는 머신에서만 `skills-sync setup enable share-server`로 켜고 `disable`로 끈다([automation.md](automation.md#처음-설치할-때)). tailnet 공개 설정은 건드리지 않는다.

```bash
bin/share-server serve [--root DIR] [--port N]   # 앞에서 한 번 띄운다
bin/share-server install                         # 메인 체크아웃에서. venv 를 만들고 KeepAlive LaunchAgent 로 등록
bin/share-server uninstall
```

## 설치되는 것

`share_server.py`의 `PINS`에 고정한 copyparty와 의존성(Jinja2, MarkupSafe, Pillow, mutagen)을 `~/.local/share/agent-skills/copyparty-<키>`에 wheel로만 설치한다. 키는 Python 마이너 버전과 `PINS`로 정해지므로, 고정 버전이나 Python이 바뀌면 새 venv를 옆에 만든다. 고정 버전은 채택 시점에 릴리스 후 14일이 지난 것만 쓴다.

`install`은 새 venv를 먼저 만들고, 실패하면 돌던 LaunchAgent와 그 venv를 건드리지 않고 멈춘다. 성공하면 plist에 그 venv 경로(`--venv`)를 적고, 새 LaunchAgent가 올라온 뒤에 이전 venv를 지운다. LaunchAgent가 띄우는 `serve`는 pip를 돌리지 않는다. venv가 없거나 깨졌으면 로그에 `install`을 다시 하라고 남기고 끝난다. `PINS`가 바뀌면 plist 내용도 달라지므로 `skills-sync setup status`가 차이를 보여 주고 `enable`이 다시 설치한다.

| 경로 | 내용 |
|---|---|
| `~/.local/share/agent-skills/copyparty-<키>` | copyparty venv |
| `~/.cache/agent-skills/copyparty` | 검색 색인(`up2k.db`)과 썸네일 캐시. 지워도 다시 만든다. 이미 색인한 파일의 본문은 다시 읽지 않으므로 `share_text_tag.py`를 바꾸면 이 폴더를 지우고 재시작한다 |
| `~/.local/state/agent-skills/copyparty` | copyparty의 salt와 세션 DB |
| `~/.local/state/agent-skills/share-server.log` | 로그 |

공유 폴더 안에는 아무것도 쓰지 않는다.

## 폴더를 열면

- 목록은 썸네일 그리드로 연다(`g` 키나 `田` 버튼으로 표 보기). 이미지를 누르면 확대 보기가 열리고 좌우 키로 넘긴다.
- `index.html`이 있는 폴더는 브라우저가 그 페이지로 바로 연다. 목록을 보려면 주소 끝에 `?v`를 붙인다.
- `.md`는 렌더링해서 보여 준다. 목록에서 눌러도, 브라우저로 `report.md` 주소를 바로 열어도 문서로 보인다(`scripts/share_copyparty.py`). 원문은 뷰어의 `view raw`(`report.md?`)나 curl 같은 비브라우저 요청으로 받는다. 뷰어의 edit 링크는 보이지만 저장은 거부된다.
- `text/*` 응답(마크다운·텍스트·HTML·목록)은 `charset=utf-8`을 붙인다. `ts`, `tsx`, `py`, `yaml`, `log` 같은 소스·설정 파일은 내려받기 대신 `text/plain`으로 보여 준다.
- 🔎 탭에서 파일명·경로로 찾는다. `tags` 칸은 텍스트 파일(`md`, `txt`, `html`, `json`, `csv`, `log`, `yaml` 등) 본문 앞 4 KiB도 찾는다(`scripts/share_text_tag.py`가 색인, HTML은 보이는 글자만). copyparty는 이 값을 목록과 검색 결과에 함께 실어 보내므로 길게 두지 않는다. 새 파일은 60초 안에 색인된다.

## 보안

- `127.0.0.1`에만 바인딩한다. 바꾸는 설정은 없다.
- 앞단 프록시는 TLS를 끝내고 `X-Forwarded-For`만 보낸다고 본다(`tailscale serve`). 그래서 프록시를 거친 요청은 https로 다룬다. 그래야 검색·선택 zip POST가 같은 출처로 통과하고, 다른 출처의 POST는 거부된다.
- 계정이 없고 모두에게 읽기 권한만 준다. 업로드·폴더 만들기·삭제·이동·복사·WebDAV·폼 메시지 기록은 모두 거부되고 폴더는 그대로다.
- 숨김 항목(`.`으로 시작하는 경로 조각)은 목록·검색·zip에서 빠지고, 주소를 알아도 404다. copyparty 자체는 숨김 파일·폴더를 정확한 주소로 내주므로 `scripts/share_copyparty.py`가 GET·POST와 선택 zip에서 막는다. 숨김 경로를 가리키는 심볼릭 링크도 그 주소와 선택 zip으로는 404지만, 링크 이름은 목록·검색에 보이고 그 링크가 든 폴더를 통째로 zip하면 딸려 나간다. 폴더 썸네일은 copyparty가 숨김 커버 이미지(`.folder.png` 등)로도 만든다. 그런 링크·이미지는 만들지 않고, 비밀값은 공유 폴더에 두지 않는다.
- 루트 밖으로 나가는 심볼릭 링크와 `..` 경로는 거부한다(`--xvol`).
- 페이지에 호스트명, 디스크 사용량, 다른 방문자의 내려받기 기록을 보이지 않는다.

## 설정

`~/.claude/agent-skills.json`의 `"share-server"` 키. 없으면 기본값을 쓴다.

| 키 | 기본 | 뜻 |
|---|---|---|
| `root` | `~/share` | 서빙할 폴더. 경로에 `:`가 있으면 거부한다 |
| `port` | `13201` | loopback 포트 |
| `label` | `io.github.jeongjaesoon.share-server` | LaunchAgent 라벨. 이미 같은 폴더를 서빙하는 LaunchAgent가 있으면 그 라벨을 적어 같은 자리에서 교체한다 |

`install`은 같은 라벨의 plist가 이미 있고 내용이 다르면 `<라벨>.plist.bak-<시각>`으로 옆에 남긴 뒤 덮어쓴다. 되돌릴 때는 `uninstall` 뒤 백업 plist를 원래 이름으로 복사해 `launchctl bootstrap gui/$(id -u) <plist>`로 올린다. 백업이 없으면(같은 내용이었으면) 이 저장소를 되돌린 커밋에서 `install`을 다시 한다. venv·캐시·상태 폴더는 `uninstall`이 지우지 않는다.

copyparty 로그는 `-q`로 끈다. 켜면 색인한 본문이 로그에 남는다.

## 테스트

`scripts/test_share_server.py`. `Live` 테스트는 지금 `PINS`의 venv가 있을 때만 임시 폴더에 copyparty를 띄워 검색·숨김 경로 404·마크다운 렌더링·charset·쓰기 거부를 확인하고, 없으면 건너뛴다(`share_server.py serve`를 한 번 띄우면 생긴다).
