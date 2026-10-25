# share-server: 공유 폴더 서버

공유 폴더(기본 `~/share`)를 `127.0.0.1`에서 읽기 전용으로 서빙한다. 기본 백엔드는 Caddy의 `file_server`이고, 저장소의 [`scripts/share-caddy/Caddyfile`](../scripts/share-caddy/Caddyfile)을 그대로 띄운다. 예전 표준 라이브러리 갤러리 서버는 `backend: "python"`으로 남겨 두었다. 모델·네트워크 API는 부르지 않는다.

기본은 꺼져 있다. 공유 폴더를 `tailscale serve` 같은 것으로 열어 쓰는 머신에서만 `skills-sync setup enable share-server`로 켜고 `disable`로 끈다([automation.md](automation.md#처음-설치할-때)). tailnet 공개 설정은 건드리지 않는다.

```bash
brew install caddy                               # caddy 백엔드에 필요. 공식 릴리스 바이너리면 sha256을 확인하고 설정 caddy에 경로를 적는다
bin/share-server serve [--root DIR] [--port N] [--backend caddy|python]   # 앞에서 한 번 띄운다
bin/share-server install                         # 메인 체크아웃에서. KeepAlive LaunchAgent
bin/share-server uninstall
```

## caddy 백엔드 (기본)

- 폴더에 `index.html`(또는 `index.htm`)이 있으면 그 페이지를 그대로 준다. HTML 파일은 템플릿을 거치지 않고 원본 그대로 나간다.
- 없으면 Caddy 목록이다. 최근 수정 순이고, 위의 Grid 버튼(`?layout=grid`)을 누르면 이미지가 썸네일로 보인다. 다크 모드는 시스템 설정을 따른다.
- `.md`는 HTML로 렌더한다(Caddy `templates`의 `markdown`). `?raw`(또는 `?raw=1`)를 붙이면 `text/plain; charset=utf-8` 원문이다. 문서 안의 HTML 태그는 그대로 렌더되지만, 렌더한 페이지에는 스크립트를 전혀 허용하지 않는 `Content-Security-Policy`가 붙는다.
- `.txt`, `.log`, `.csv`, `.jsonl`, 소스 파일 같은 텍스트는 `text/plain; charset=utf-8`, `.json`은 `application/json; charset=utf-8`로 준다. 한글이 깨지지 않고 다운로드 대신 브라우저에 보인다.

## python 백엔드 (`backend: "python"`)

- 폴더에 `index.html`이 있으면 그 페이지를 준다. 없으면 목록을 만들고, 이미지(`png`, `jpg`, `jpeg`, `webp`, `gif`)가 있으면 맨 위에 썸네일 그리드를 둔다. 끝의 크기·테마 접미사(`-1280`, `-390`, `-dark`, `@2x` 같은 것)를 뗀 이름이 같은 파일끼리 한 묶음이고, 썸네일을 누르면 원본을 화면 가득 연다(좌우 화살표로 이동, Esc로 닫기).
- `.md`는 HTML로 렌더한 페이지를 준다. 라이트·다크 모드를 따르고, 넓은 표는 표 안에서 가로로 스크롤한다. 렌더는 서버의 표준 라이브러리 코드가 하고 외부 스크립트는 없다. 제목, 문단, 목록(체크박스 포함), 인용, 코드 블록, 표, 구분선, 굵게·기울임·취소선, 인라인 코드, 링크, 이미지, 맨 URL 자동 링크를 지원한다. 문서 안의 HTML 태그는 렌더하지 않고 글자로 보인다.
- 원문은 `?raw`를 붙여 연다(렌더 페이지 위의 "원문" 링크). `text/markdown; charset=utf-8`로 나간다.
- 텍스트 파일(`text/*`, `.json`, `.log`, `.jsonl`)은 `charset=utf-8`을 붙여 보낸다. `.html`은 문서 안의 `<meta charset>`을 따르도록 붙이지 않는다.
- 1 MB가 넘는 `.md`는 렌더하지 않고 `?raw`처럼 원문을 준다. 32 KB가 넘는 문단·셀은 서식 없이, 16단계보다 깊은 인용·목록은 글자로 보여 준다. 악의적인 문서가 서버를 붙잡지 못하게 하는 상한이다.

## 보안

- 두 백엔드 모두 `127.0.0.1`에만 바인딩한다. 바꾸는 설정은 없다. 모든 응답에 `X-Content-Type-Options: nosniff`를 붙이고, 목록과 제목의 파일명은 이스케이프한다.
- caddy: GET·HEAD 말고는 405다. `.`으로 시작하는 경로 조각이 하나라도 있으면 404이고 목록에도 나오지 않는다. Caddy는 심볼릭 링크를 따라가므로 [`sandbox.sb`](../scripts/share-caddy/sandbox.sb) 아래에서 띄운다(`sandbox-exec`). caddy는 공유 폴더, 설정 폴더, 자기 상태 폴더, 자기 실행 파일, 시스템 라이브러리만 읽을 수 있고 공유 폴더 안의 숨김 항목은 못 읽는다. 그래서 루트 밖(`/etc`, Homebrew 폴더, 홈 포함)이나 숨김 파일로 가는 링크는 403이다. python 백엔드와 달리 그런 링크의 **이름**과 대상의 크기·수정 시각은 목록에 남는다(파일 메타데이터 읽기는 막지 않는다). 렌더한 md 페이지의 CSP는 스크립트·폼 전송·`<base>`를 막지만, 문서 안의 `<meta http-equiv="refresh">` 같은 HTML은 그대로 렌더된다.
- python: `..`가 든 경로, 숨김 항목, 실제 경로가 루트 밖이나 숨김 항목으로 가는 심볼릭 링크는 목록에도 없고 요청하면 404다. 폴더의 `index.html`이 그런 링크여도 404다. 생성한 목록 페이지에는 `Content-Security-Policy`(같은 출처 이미지와 인라인 스크립트만)를, 렌더한 `.md` 페이지에는 스크립트를 전혀 허용하지 않는 CSP를 붙이고 `http`·`https`·`mailto`·상대 경로가 아닌 링크는 `#`으로 바꾼다.

## 설정

`~/.claude/agent-skills.json`의 `"share-server"` 키. 없으면 기본값을 쓴다.

| 키 | 기본 | 뜻 |
|---|---|---|
| `root` | `~/share` | 서빙할 폴더 |
| `port` | `13201` | loopback 포트 |
| `label` | `io.github.jeongjaesoon.share-server` | LaunchAgent 라벨. 이미 같은 폴더를 서빙하는 LaunchAgent가 있으면 그 라벨을 적어 같은 자리에서 교체한다 |
| `backend` | `caddy` | `caddy` 또는 `python` |
| `caddy` | (PATH의 `caddy`) | caddy 실행 파일 경로 |

`install`은 같은 라벨의 plist가 이미 있고 내용이 다르면 `<라벨>.plist.bak-<시각>`으로 옆에 남긴 뒤 덮어쓴다. caddy(설정 경로, PATH, `/opt/homebrew/bin`, `/usr/local/bin` 순으로 찾는다)나 `sandbox-exec`가 없거나, 공유 폴더 경로에 Caddy가 glob으로 읽는 `* ? [ ] \`가 있거나, `backend`가 모르는 값이면 아무것도 바꾸지 않고 멈춘다. LaunchAgent는 `share_server.py serve`를 띄우고, `serve`가 시작할 때마다 caddy 경로를 찾아 sandbox-exec로 넘긴다. 그래서 `brew upgrade caddy` 뒤에는 재시작만 하면 된다. 로그는 `~/.local/state/agent-skills/share-server.log`, caddy 상태는 `~/.local/state/agent-skills/share-caddy/`.

## 되돌리기

- python 백엔드로: 설정에 `"backend": "python"`을 넣고 `bin/share-server install`.
- 이전 plist 그대로: `install`이 남긴 `~/Library/LaunchAgents/<라벨>.plist.bak-<시각>`을 `<라벨>.plist`로 복사한 뒤 `launchctl bootout gui/$(id -u)/<라벨>`, `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<라벨>.plist`.
