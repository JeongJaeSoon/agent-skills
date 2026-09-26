# 수동 데모 기록 (2026-09-27, macOS 26.5)

실제 원격 서비스와 실제 토큰은 쓰지 않았다. 출력은 기록한 그대로다. 그 뒤 안내 문구의 `README`는 `SKILL.md owner setup`으로 바뀌었다. 값은 가짜이고, 소유자의 login keychain과 `~/.config`는 건드리지 않았다.

## 준비

```bash
mkdir -p /tmp/sf-demo/site
# 입력칸 하나(type=password, autofocus)와 값 길이만 보여 주는 페이지
python3 -m http.server 8765 --bind 127.0.0.1 --directory /tmp/sf-demo/site

# 버리는 keychain. 데모에서만 값을 argv로 넣었다(가짜 값)
security create-keychain -p <demo-pass> /tmp/sf-demo/demo.keychain-db
security add-generic-password -s secure-fill -a demo-token -w <FAKE> -T /usr/bin/security /tmp/sf-demo/demo.keychain-db

# 설정·로그·캐시를 /tmp 로 돌린다
export XDG_CONFIG_HOME=/tmp/sf-demo/cfg XDG_STATE_HOME=/tmp/sf-demo/state XDG_CACHE_HOME=/tmp/sf-demo/cache
cat /tmp/sf-demo/cfg/secure-fill/allow.json
{"items": {
  "demo-token": {"origins": ["http://127.0.0.1:8765", "http://localhost:8765"],
                 "keychain": {"path": "/tmp/sf-demo/demo.keychain-db"}}
}}
```

Chrome에서 `http://127.0.0.1:8765/`를 활성 탭으로 연다.

## 1. 허용 목록에 없는 origin

```text
$ secure-fill --item demo-token --origin https://dev.example.test --target chrome
secure-fill: not-allowed item=demo-token origin=https://dev.example.test target=chrome (item/origin not in allow.json)
  item/origin pair is not in the owner's allowlist; ask the owner, never edit it yourself
  warning: allowlist-writable-by-this-user (owner: see README 'allowlist ownership')
exit=13
```

## 2. 허용됐지만 활성 탭과 다른 origin

```text
$ secure-fill --item demo-token --origin http://localhost:8765 --target chrome
secure-fill: origin-mismatch item=demo-token origin=http://localhost:8765 target=chrome (active tab is http://127.0.0.1:8765)
  the active tab is not on the requested origin; navigate there first
exit=11
```

Touch ID 창은 뜨지 않았고 값도 읽지 않았다.

## 3. 게이트와 채우기

```text
$ secure-fill --item demo-token --origin http://127.0.0.1:8765 --target chrome
secure-fill: fill-failed item=demo-token origin=http://127.0.0.1:8765 target=chrome (the terminal app lacks Accessibility permission (README setup))
  could not deliver the value to the browser; see the reason and README permissions
```

- Touch ID 창이 떴고 소유자가 승인했다(소유자 확인). 이유 문구는 `fill "demo-token" into http://127.0.0.1:8765 (chrome)`.
- Chrome의 "Apple Events의 JavaScript 허용"이 꺼져 있어 DOM 경로가 실패했고, 클립보드 경로로 넘어갔다.
  같은 AppleScript 문장을 따로 돌리면 문법 오류가 아니라 `Executing JavaScript through AppleScript is turned off` 오류가 난다. 설정만 켜면 DOM 경로가 실행된다.
- 터미널 앱에 Accessibility 권한이 없어 헬퍼가 붙여넣기 전에 멈췄다(`AXIsProcessTrusted() == false`, 종료 코드 3). 클립보드에 값을 쓰기 전에 확인하므로 값은 어디에도 가지 않았다.
- 이 데모에서는 소유자가 권한을 주지 않은 상태라 입력칸에 실제로 값이 들어가는 것까지는 보지 못했다. Accessibility를 주거나 JavaScript 허용을 켠 뒤 같은 명령을 다시 돌리면 확인할 수 있다.

## 감사 로그

```text
{"ts": "2026-09-26T16:56:43+00:00", "item": "demo-token", "origin": "https://dev.example.test", "target": "chrome", "result": "not-allowed", ...}
{"ts": "2026-09-26T16:56:43+00:00", "item": "demo-token", "origin": "http://localhost:8765", "target": "chrome", "result": "origin-mismatch", "reason": "active tab is http://127.0.0.1:8765", ...}
{"ts": "2026-09-26T16:56:57+00:00", "item": "demo-token", "origin": "http://127.0.0.1:8765", "target": "chrome", "result": "fill-failed", "reason": "the terminal app lacks Accessibility permission (README setup)", ...}
```

출력 파일과 로그에서 가짜 값 문자열을 grep한 결과 0건.

## 덤: 에이전트 측 가드

에이전트 세션에서 `security find-generic-password ... -w`를 직접 실행하려 하자, 설치된 셸 가드 훅이 deny 패턴으로 막았다. 이 도구는 Python 프로세스 안에서 읽으므로 그 가드와 부딪히지 않는다. 가드는 보조 수단이고, 에이전트가 우회하지 못하게 하는 층은 Keychain의 `-T ""` ACL이다.
