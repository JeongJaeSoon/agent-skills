# share-server: 공유 폴더 서버

공유 폴더(기본 `~/share`)를 `127.0.0.1`에서 읽기 전용으로 서빙한다. Caddy의 `file_server`로 저장소의 [`scripts/share-caddy/Caddyfile`](../scripts/share-caddy/Caddyfile)을 그대로 띄운다. 모델·네트워크 API는 부르지 않는다.

기본은 꺼져 있다. 공유 폴더를 `tailscale serve` 같은 것으로 열어 쓰는 머신에서만 `skills-sync setup enable share-server`로 켜고 `disable`로 끈다([automation.md](automation.md#처음-설치할-때)). tailnet 공개 설정은 건드리지 않는다.

```bash
brew install caddy                               # 필수. 공식 릴리스 바이너리면 sha256을 확인하고 설정 caddy에 경로를 적는다
bin/share-server serve [--root DIR] [--port N]  # 앞에서 한 번 띄운다
bin/share-server install                         # 메인 체크아웃에서. KeepAlive LaunchAgent
bin/share-server uninstall
```

## 동작

- 폴더에 `index.html`(또는 `index.htm`)이 있으면 그 페이지를 그대로 준다. HTML 파일은 템플릿을 거치지 않고 원본 그대로 나간다.
- 없으면 Caddy 목록이다. 최근 수정 순이고, 위의 Grid 버튼(`?layout=grid`)을 누르면 이미지가 썸네일로 보인다. 다크 모드는 시스템 설정을 따른다.
- `.md`는 HTML로 렌더한다(Caddy `templates`의 `markdown`). `?raw`(또는 `?raw=1`)를 붙이면 `text/plain; charset=utf-8` 원문이다. 문서 안의 HTML 태그는 그대로 렌더되지만, 렌더한 페이지에는 스크립트를 전혀 허용하지 않는 `Content-Security-Policy`가 붙는다.
- `.txt`, `.log`, `.csv`, `.jsonl`, 소스 파일 같은 텍스트는 `text/plain; charset=utf-8`, `.json`은 `application/json; charset=utf-8`로 준다. 한글이 깨지지 않고 다운로드 대신 브라우저에 보인다.

## 보안

- `127.0.0.1`에만 바인딩한다. 바꾸는 설정은 없다. 모든 응답에 `X-Content-Type-Options: nosniff`를 붙이고, 목록과 제목의 파일명은 이스케이프한다.
- GET·HEAD 말고는 405다. `.`으로 시작하는 경로 조각이 하나라도 있으면 404이고 목록에도 나오지 않는다. Caddy는 심볼릭 링크를 따라가므로 [`sandbox.sb`](../scripts/share-caddy/sandbox.sb) 아래에서 띄운다(`sandbox-exec`). caddy는 공유 폴더, 설정 폴더, 자기 상태 폴더, 자기 실행 파일, 시스템 라이브러리만 읽을 수 있고 공유 폴더 안의 숨김 항목은 못 읽는다. 그래서 루트 밖(`/etc`, Homebrew 폴더, 홈 포함)이나 숨김 파일로 가는 링크는 403이다. 그런 링크의 **이름**과 대상의 크기·수정 시각은 목록에 남는다(파일 메타데이터 읽기는 막지 않는다). 렌더한 md 페이지의 CSP는 스크립트·폼 전송·`<base>`를 막지만, 문서 안의 `<meta http-equiv="refresh">` 같은 HTML은 그대로 렌더된다.

## 설정

`~/.claude/agent-skills.json`의 `"share-server"` 키. 없으면 기본값을 쓴다.

| 키 | 기본 | 뜻 |
|---|---|---|
| `root` | `~/share` | 서빙할 폴더 |
| `port` | `13201` | loopback 포트 |
| `label` | `io.github.jeongjaesoon.share-server` | LaunchAgent 라벨. 이미 같은 폴더를 서빙하는 LaunchAgent가 있으면 그 라벨을 적어 같은 자리에서 교체한다 |
| `caddy` | (PATH의 `caddy`) | caddy 실행 파일 경로 |

`install`은 같은 라벨의 plist가 이미 있고 내용이 다르면 `<라벨>.plist.bak-<시각>`으로 옆에 남긴 뒤 덮어쓴다. caddy(설정 경로, PATH, `/opt/homebrew/bin`, `/usr/local/bin` 순으로 찾는다)나 `sandbox-exec`가 없거나, 공유 폴더 경로(실제 경로 포함)에 Caddy가 glob·placeholder로 읽는 `* ? [ ] \ { } "`가 있으면 아무것도 바꾸지 않고 멈춘다. LaunchAgent는 `share_server.py serve`를 띄우고, `serve`가 시작할 때마다 caddy 경로를 찾아 sandbox-exec로 넘긴다. 그래서 `brew upgrade caddy` 뒤에는 재시작만 하면 된다. 로그는 `~/.local/state/agent-skills/share-server.log`, caddy 상태는 `~/.local/state/agent-skills/share-caddy/`.

## 되돌리기

- 이전 plist 그대로: `install`이 남긴 `~/Library/LaunchAgents/<라벨>.plist.bak-<시각>`을 `<라벨>.plist`로 복사한 뒤 `launchctl bootout gui/$(id -u)/<라벨>`, `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<라벨>.plist`.
