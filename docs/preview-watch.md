# preview-watch: tailnet 미리보기 자동 공개

이 머신의 loopback에서 뜬 미리보기 서버(문서 정적 서버, 개발 서버, 검증 스택 웹)를 tailnet HTTPS로 자동으로 열고, 서버가 내려가면 자기가 연 항목만 닫는다. 모델·API는 부르지 않는다. launchd가 `StartInterval`마다 한 번 실행하고 끝낸다.

```bash
bin/preview-watch install     # 메인 체크아웃에서. LaunchAgent io.github.jeongjaesoon.preview-watch
bin/preview-links             # 지금 열린 tailnet 링크. watcher가 연 것은 (watch)
bin/preview-watch run         # 한 번만 돌린다
bin/preview-watch uninstall   # LaunchAgent를 내리고 watcher가 연 항목을 끈다
```

## 한 번의 실행

1. `lsof -nP -F cn -iTCP@127.0.0.1 -iTCP@[::1] -sTCP:LISTEN` 한 번으로 loopback LISTEN 포트와 프로세스 이름을 얻는다. `*`(0.0.0.0)이나 LAN 주소에 묶인 포트는 대상이 아니다.
2. 허용 범위에 든 포트만 후보다. 상태 파일과 비교해 새로 뜬 후보도, 사라진 자기 항목도 없으면 여기서 끝난다. tailscale은 부르지 않는다.
3. 변화가 있으면 `tailscale serve status --json`을 한 번 읽는다.
   - 새 후보 포트에 이미 serve 항목이 있으면 남의 것이다. 그 포트가 LISTEN을 멈출 때까지 다시 보지 않는다.
   - 항목이 없으면 `HEAD /`로 HTTP 응답을 확인하고 `tailscale serve --bg --https=<p> http://127.0.0.1:<p>`(`::1`에만 묶였으면 `http://[::1]:<p>`)를 건다. HTTP로 답하지 않는 포트(Postgres 등)는 걸지 않고 120초 뒤 다시 확인한다.
   - 자기가 연 포트의 LISTEN이 사라졌으면, 지금 항목이 자기가 건 대상을 그대로 가리킬 때만 `serve --https=<p> off`. 누가 바꿨으면 기록에서만 지우고 항목은 그대로 둔다.
4. funnel(tailnet 밖 공개)은 어떤 경로로도 부르지 않는다.

## 허용 범위

`~/.claude/agent-skills.json`의 `"preview-watch"` 키로 바꾼다. 없으면 기본값을 쓴다.

```json
{"preview-watch": {
  "interval": 15,
  "ranges": [
    {"from": 4500, "to": 4599},
    {"from": 32768, "to": 60999, "commands": ["OrbStack", "com.docker"]}
  ],
  "tailscale": null
}}
```

- 4500–4599: 손으로 띄우는 미리보기 서버.
- 32768–60999: 검증 스택은 호스트 포트를 비운 `127.0.0.1::<port>`로 compose 포트를 열고, Docker는 Linux ephemeral 범위(`ip_local_port_range` 기본값 32768–60999)에서 포트를 고른다. 이 범위에는 데스크톱 앱이 혼자 쓰는 loopback 포트도 있으므로 OrbStack·Docker Desktop 데몬이 쥔 포트만 받는다(`commands`는 lsof 프로세스 이름의 접두어).
- `interval`을 바꾸면 `install`을 다시 돌린다. `tailscale`을 비우면 PATH의 `tailscale`, 없으면 `/Applications/Tailscale.app/Contents/MacOS/Tailscale`.

## 상태와 안전 동작

- 상태 파일 `~/.local/state/agent-skills/preview-watch.json`: `owned`(watcher가 연 포트 → 대상), `skip`(남의 항목이거나 HTTP가 아닌 포트). 원자적으로 쓰고, serve 항목을 하나 걸 때마다 바로 기록한다.
- 상태 파일이 깨졌으면 `preview-watch.json.corrupt-<시각>`으로 옮기고 소유 항목 없이 시작한다. 자기 것임을 증명할 수 없는 항목은 끄지 않으므로, 그때 열려 있던 항목은 손으로 끈다.
- loopback 리스너가 하나도 안 보이는 스캔은 깨진 스캔으로 보고 아무것도 끄지 않는다. Mac에는 늘 loopback 리스너가 있어서, 빈 결과를 믿으면 열어 둔 항목이 한꺼번에 닫힌다.
- 실행 로그는 열고 닫은 일과 실패만 `~/.local/state/agent-skills/preview-watch.log`에 한 줄씩 남는다.
- `install`은 worktree에서 거부한다. worktree가 지워지면 launchd 작업이 조용히 실패하기 때문이다. 검증 목적으로만 `--here`를 붙인다.
