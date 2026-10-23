# preview-watch: tailnet 미리보기 자동 공개

이 머신의 loopback에서 뜬 웹페이지(문서 정적 서버, 개발 서버, 검증 스택 웹 화면)를 tailnet HTTPS로 자동으로 열고, 서버가 내려가면 자기가 연 항목만 닫는다. 모델·API는 부르지 않는다. launchd가 `StartInterval`마다 한 번 실행하고 끝낸다.

```bash
bin/preview-watch install     # 메인 체크아웃에서. LaunchAgent io.github.jeongjaesoon.preview-watch
bin/preview-links             # 지금 열린 tailnet 링크. watcher가 연 것은 (watch)
bin/preview-watch run         # 한 번만 돌린다
bin/preview-watch uninstall   # LaunchAgent를 내리고 watcher가 연 항목을 끈다
```

## 한 번의 실행

1. `lsof -nP -F pcn -iTCP@127.0.0.1 -iTCP@[::1] -sTCP:LISTEN` 한 번으로 loopback LISTEN 포트와 프로세스(이름, PID)를 얻는다. `*`(0.0.0.0)이나 LAN 주소에 묶인 포트는 대상이 아니다.
2. `exclude`에 없고, 허용 범위나 `include`에 든 포트만 후보다.
3. 새 후보는 웹페이지인지 한 번 확인한다. `GET /`를 보내 같은 호스트·포트 안의 리다이렉트를 따라가고(최대 5번, 요청당 2초), 2xx이고 `Content-Type`이 `text/html`이고 본문 앞 4KB에 `<html` 또는 `<!doctype html`이 있을 때만 웹페이지다. JSON API, 빈 `text/plain`, S3 같은 오브젝트 스토어, DB는 열지 않는다. `include` 포트는 확인하지 않고 연다.
   - 판정은 상태 파일에 포트와 PID 기준으로 남는다. 웹페이지 판정은 그 프로세스가 포트를 쥐는 동안 유지되고, 아니라는 판정은 10분 뒤 한 번 다시 확인한다(OrbStack은 모든 컨테이너 포트를 한 프로세스로 쥐어서, 같은 포트를 다른 컨테이너가 다시 쓰면 PID가 같다). 그래서 변화가 없는 실행은 HTTP 요청도 tailscale 호출도 하지 않는다.
   - 연결 자체가 거부되면 120초 뒤 다시 확인한다.
4. 새 웹페이지도, 내려야 할 자기 항목도 없으면 여기서 끝난다. 있으면 `tailscale serve status --json`을 한 번 읽는다.
   - 새 웹페이지 포트에 이미 serve 항목이 있으면 남의 것이다. 그 포트가 LISTEN을 멈출 때까지 다시 보지 않는다.
   - 항목이 없으면 `tailscale serve --bg --https=<p> http://127.0.0.1:<p>`(`::1`에만 묶였으면 `http://[::1]:<p>`)를 건다.
   - 자기가 연 항목은 LISTEN이 사라졌거나, 범위·`exclude` 설정으로 후보에서 빠졌거나, 웹페이지가 아니라고 판정되면 내린다. 판정이 없는 자기 항목(이 판정이 생기기 전에 연 것)은 그 실행에서 한 번 확인한다. 지금 항목이 자기가 건 대상을 그대로 가리킬 때만 `serve --https=<p> off`. 누가 바꿨으면 기록에서만 지우고 항목은 그대로 둔다.
   - 항목을 걸거나 끄기 직전에 serve 설정을 다시 읽어, 그 사이 누가 그 포트를 잡거나 바꿨으면 손대지 않는다.
5. funnel(tailnet 밖 공개)은 어떤 경로로도 부르지 않는다. 열린 링크에 누가 닿는지는 tailnet ACL과 노드 공유 설정이 정한다.

## 허용 범위

`~/.claude/agent-skills.json`의 `"preview-watch"` 키로 바꾼다. 없으면 기본값을 쓴다.

```json
{"preview-watch": {
  "interval": 15,
  "ranges": [
    {"from": 4500, "to": 4599},
    {"from": 32768, "to": 60999, "commands": ["OrbStack", "com.docker"]}
  ],
  "exclude": [4570],
  "include": [],
  "tailscale": null
}}
```

- 4500–4599: 손으로 띄우는 미리보기 서버.
- 32768–60999: 검증 스택은 호스트 포트를 비운 `127.0.0.1::<port>`로 compose 포트를 열고, Docker는 Linux ephemeral 범위(`ip_local_port_range` 기본값 32768–60999)에서 포트를 고른다. 이 범위에는 데스크톱 앱이 혼자 쓰는 loopback 포트도 있으므로 OrbStack·Docker Desktop 데몬이 쥔 포트만 받는다(`commands`는 lsof 프로세스 이름의 접두어).
- `exclude`: 웹페이지여도 열지 않는 포트. 이미 열어 둔 자기 항목은 다음 실행에서 닫는다. 기본값은 4570. 키를 적으면 기본값을 대체하므로 4570도 함께 적는다.
- `include`: 범위·웹페이지 판정 없이 여는 포트(루트가 JSON인 앱처럼 판정이 놓치는 화면). 남의 serve 항목이 있는 포트는 그래도 건드리지 않는다.
- `interval`을 바꾸면 `install`을 다시 돌린다. `tailscale`을 비우면 PATH의 `tailscale`, 없으면 `/Applications/Tailscale.app/Contents/MacOS/Tailscale`. launchd의 PATH는 `/usr/sbin:/usr/bin:/bin`뿐이라, Homebrew 등으로 설치한 CLI는 이 키에 경로를 적는다. CLI를 못 찾으면 로그에 한 줄 남긴다.

## 상태와 안전 동작

- 상태 파일 `~/.local/state/agent-skills/preview-watch.json`: `owned`(watcher가 연 포트 → 대상), `skip`(남의 항목이거나 연결이 거부된 포트), `pages`(포트 → [PID, 웹페이지 여부, 확인 시각]). 원자적으로 쓰고, serve 항목을 하나 걸 때마다 바로 기록한다.
- 상태 파일이 깨졌으면 `preview-watch.json.corrupt-<시각>`으로 옮기고 소유 항목 없이 시작한다. 자기 것임을 증명할 수 없는 항목은 끄지 않으므로, 그때 열려 있던 항목은 손으로 끈다.
- loopback 리스너가 하나도 안 보이는 스캔은 깨진 스캔으로 보고 아무것도 끄지 않는다. Mac에는 늘 loopback 리스너가 있어서, 빈 결과를 믿으면 열어 둔 항목이 한꺼번에 닫힌다.
- 실행 로그는 열고 닫은 일과 실패만 `~/.local/state/agent-skills/preview-watch.log`에 한 줄씩 남는다. 같은 실패가 이어지면 한 줄만 남긴다.
- `serve status`가 빈 출력이나 JSON 객체가 아닌 것을 내면 실패로 본다. 빈 설정으로 읽으면 남의 항목이 빈 포트처럼 보이기 때문이다.
- 소유 판정은 `/` handler의 대상만 본다. watcher가 연 포트에 다른 경로 handler를 덧붙이면 끌 때 같이 사라질 수 있다.
- `uninstall`은 끄지 못한 항목을 상태 파일에 남기고 0이 아닌 코드로 끝난다. 다시 돌리면 그 항목만 재시도한다.
- `install`은 worktree에서 거부한다. worktree가 지워지면 launchd 작업이 조용히 실패하기 때문이다. 검증 목적으로만 `--here`를 붙인다.
