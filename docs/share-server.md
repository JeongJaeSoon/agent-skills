# share-server: 공유 폴더 서버와 이미지 갤러리

공유 폴더(기본 `~/share`)를 `127.0.0.1`에서 서빙한다. `python3 -m http.server`와 같은 자리에 들어가되, 이미지가 든 폴더는 파일 목록 대신 썸네일 갤러리로 보여 준다. 표준 라이브러리만 쓰고 모델·네트워크 API는 부르지 않는다.

기본은 꺼져 있다. 공유 폴더를 `tailscale serve` 같은 것으로 열어 쓰는 머신에서만 `skills-sync setup enable share-server`로 켜고 `disable`로 끈다([automation.md](automation.md#처음-설치할-때)). tailnet 공개 설정은 건드리지 않는다.

```bash
bin/share-server serve [--root DIR] [--port N]   # 앞에서 한 번 띄운다
bin/share-server install                         # 메인 체크아웃에서. KeepAlive LaunchAgent
bin/share-server uninstall
```

## 폴더를 열면

- `index.html`이 있으면 그 페이지를 그대로 준다.
- 없으면 목록을 만든다. 이미지(`png`, `jpg`, `jpeg`, `webp`, `gif`)가 있으면 맨 위에 썸네일 그리드를 두고, 하위 폴더와 다른 파일은 그 아래 목록으로 둔다. 이미지가 없으면 목록만 있다.
- 그리드는 파일명 순이고, 끝의 크기·테마 접미사(`-1280`, `-390`, `-dark`, `-light`, `-mobile`, `@2x` 같은 것)를 뗀 이름이 같은 파일끼리 한 묶음이다. 캡션은 파일명이다.
- 썸네일을 누르면 원본을 화면 가득 연다. 좌우 화살표 키나 버튼으로 앞뒤 이미지, Esc로 닫는다. Cmd·Ctrl 클릭은 새 탭에서 원본을 연다.

## 보안

- `127.0.0.1`에만 바인딩한다. 바꾸는 설정은 없다.
- `..`가 든 경로, 숨김 항목(`.`으로 시작하는 경로 조각), 실제 경로가 루트 밖으로 나가는 심볼릭 링크는 목록에도 없고 요청하면 404다. 루트 안을 가리키는 심볼릭 링크는 그대로 쓴다.
- 목록의 파일명은 HTML 이스케이프하고 링크는 URL 인코딩한다. 생성한 목록 페이지에는 `Content-Security-Policy`(같은 출처 이미지와 인라인 스크립트만)를, 모든 응답에 `X-Content-Type-Options: nosniff`를 붙인다.

## 설정

`~/.claude/agent-skills.json`의 `"share-server"` 키. 없으면 기본값을 쓴다.

| 키 | 기본 | 뜻 |
|---|---|---|
| `root` | `~/share` | 서빙할 폴더 |
| `port` | `13201` | loopback 포트 |
| `label` | `io.github.jeongjaesoon.share-server` | LaunchAgent 라벨. 이미 같은 폴더를 서빙하는 LaunchAgent가 있으면 그 라벨을 적어 같은 자리에서 교체한다 |

`install`은 같은 라벨의 plist가 이미 있고 내용이 다르면 `<라벨>.plist.bak-<시각>`으로 옆에 남긴 뒤 덮어쓴다. 로그는 `~/.local/state/agent-skills/share-server.log`.
