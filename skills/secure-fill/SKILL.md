---
name: secure-fill
description: Use when a browser auth screen needs a secret (dev API token, test account password) and the agent must not see the value — "토큰 입력해줘", "비밀번호 칸 채워줘", "로그인 화면에 키 넣어줘", "secure-fill". The agent focuses the field and runs secure-fill; the owner approves each fill with Touch ID; the tool reads the value from the Keychain and puts it into the page itself. Never read, echo, or paste the secret yourself, and never edit the allowlist.
---

# Secure fill

비밀번호 관리자와 같은 방식이다. 에이전트는 채우기를 요청만 하고, 사람은 에이전트가 조작할 수 없는 창(Touch ID / 기기 소유자 인증)에서 매번 승인하고, 값은 도구가 브라우저에 직접 넣는다. 값은 argv, 표준 출력, 로그 어디에도 나오지 않는다.

## 에이전트가 할 일

1. 브라우저에서 인증 화면으로 이동한다.
2. 값을 넣을 입력칸을 클릭해 포커스를 준다.
3. 실행한다.

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/secure_fill.py" --item <이름> --origin https://dev.example.test --target chrome   # 또는 --target orca
   ```

   `bin/secure-fill`은 같은 일을 하는 래퍼다. 소유자가 `chmod 755 bin/secure-fill`을 해 두면 PATH에서 `secure-fill`로 부를 수 있다.

4. 사람에게 "Touch ID 승인 대기 중"이라고 알린다. 승인할 때까지 명령이 끝나지 않는다.

결과는 한 줄로 나오고 종료 코드로도 구분된다. 값은 나오지 않는다.

| 종료 코드 | 결과 | 다음 행동 |
|---|---|---|
| 0 | `filled` | 폼 제출은 사람이나 에이전트가 한다(`--submit`을 줬을 때만 도구가 제출) |
| 10 | `denied` | 사람이 거절했다. 다시 시도하기 전에 묻는다 |
| 11 | `origin-mismatch` | 활성 탭이 `--origin`과 다르다. 이동부터 한다 |
| 12 | `not-found` | Keychain에도 env에도 값이 없다. 사람에게 등록을 부탁한다 |
| 13 | `not-allowed` | 허용 목록에 그 item·origin 쌍이 없다. 사람에게 부탁한다 |
| 14 | `no-field` | 포커스된 입력칸이 없다 |
| 15 | `fill-failed` | 권한 부족 등. 이유가 괄호 안에 나온다 |
| 16 | `config-error` | 허용 목록 형식 오류 |

하지 않는 것: 허용 목록(`~/.config/secure-fill/allow.json`) 수정, `security find-generic-password` 직접 실행, 채운 뒤 입력칸 값 읽기(`orca eval`, DevTools), 승인 안 된 채로 재시도 반복.

## 동작 순서

1. `--origin`이 `scheme://host[:port]` 꼴인지 보고, 허용 목록에서 item의 origins에 있는지 본다.
2. 대상 브라우저의 활성 탭 URL을 읽어 origin이 정확히 같은지 본다. Chrome은 AppleScript `URL of active tab of front window`, Orca는 `orca get --what url`.
3. Touch ID 창을 띄운다. 이유 문구에 item 이름과 origin이 나온다. 승인이 없으면 값을 읽지 않는다.
4. 값을 읽는다. Keychain generic password(service `secure-fill`, account = item 이름)가 먼저, 없으면 item의 `env` 변수.
5. 채운다.
   - Chrome, "Allow JavaScript from Apple Events"가 켜져 있으면: 페이지 안에서 `location.origin`을 다시 확인하고 같은 스크립트에서 값을 넣는다(확인과 입력 사이에 탭이 바뀔 틈이 없다). 스크립트는 `osascript`의 stdin으로 간다.
   - 그 밖에는 클립보드: Swift 헬퍼가 stdin으로 값을 받아 `org.nspasteboard.ConcealedType`·`TransientType` 표시를 붙여 클립보드에 쓰고, 브라우저를 앞으로 가져와 Cmd+V를 보내고, 3초 뒤 그동안 아무도 클립보드를 바꾸지 않았으면 원래 문자열로 되돌린다. 붙여넣기 직전에 origin을 다시 읽는다. Orca는 페이지에 포커스와 입력칸 포커스가 있는지도 확인한다. 그렇지 않으면 Cmd+V가 에이전트 자신의 터미널에 들어갈 수 있다.
6. `~/.local/state/secure-fill/log.jsonl`(디렉터리 0700, 파일 0600)에 시각·item·origin·대상·결과·방법을 남긴다.

## 소유자 설정

1. Keychain 항목을 만든다. `-w`를 마지막에 값 없이 두면 값을 대화형으로 묻는다(argv와 셸 히스토리에 남지 않는다).

   ```bash
   security add-generic-password -s secure-fill -a dev-token -T "" -w
   ```

   `-T ""`는 신뢰 앱 목록을 비운다. macOS 문서대로라면 이후 어떤 프로세스든 값을 읽을 때 Keychain이 확인 창을 띄운다. 에이전트가 이 도구를 거치지 않고 `security`로 직접 읽으려 해도 걸린다. 대신 채울 때마다 Touch ID와 Keychain 확인을 둘 다 거친다. 데모에서는 이 동작을 확인하지 못했다(에이전트 세션의 셸 가드가 `find-generic-password -w` 실행을 막았다). 첫 사용 때 확인 창이 뜨는지 직접 본다. `-T ""`를 빼면 `security`가 신뢰 앱으로 들어가 같은 사용자의 어떤 프로세스든 묻지 않고 읽을 수 있다.

   확인 창에서는 **허용(Allow)**만 누른다. **항상 허용(Always Allow)**을 한 번 누르면 `/usr/bin/security`가 그 항목의 신뢰 목록에 영구히 들어가 이 층이 사라진다. 되돌리려면 항목을 지우고 다시 만든다.

2. 허용 목록을 만든다. `~/.config/secure-fill/allow.json`:

   ```json
   {"items": {
     "dev-token": {"origins": ["https://dev.example.test"]},
     "staging-token": {"origins": ["https://staging.example.test:8443"], "env": "STAGING_TOKEN",
                       "keychain": {"service": "secure-fill", "account": "staging-token"}}
   }}
   ```

   origin은 경로 없이 `scheme://host[:port]`만 적는다. 기본 포트(443, 80)는 적어도 빠진 것과 같다. `http`와 `https`, 하위 도메인은 서로 다르다.

3. 허용 목록 소유권. 에이전트는 같은 사용자로 돌기 때문에 사용자 소유 파일은 고칠 수 있다. root 소유로 두면 에이전트가 sudo 없이는 바꿀 수 없다. 도구는 파일이 현재 사용자에게 쓰기 가능하면 `allowlist-writable-by-this-user` 경고를 낸다.

   ```bash
   sudo chown root:wheel ~/.config/secure-fill/allow.json && sudo chmod 644 ~/.config/secure-fill/allow.json
   ```

4. macOS 권한. 에이전트를 돌리는 터미널 앱(예: Orca, Terminal, iTerm)에 준다.
   - 시스템 설정 → 개인정보 보호 및 보안 → **손쉬운 사용(Accessibility)**: 클립보드 붙여넣기(Cmd+V 전송)에 필요하다. 없으면 `fill-failed (the terminal app lacks Accessibility permission)`로 끝나고 값은 어디에도 가지 않는다.
   - **자동화(Automation)** → Google Chrome: 활성 탭 URL 읽기에 필요하다. 처음 실행할 때 macOS가 묻는다.
   - (선택) Chrome 메뉴 보기 → 개발자 → **Apple Events의 JavaScript 허용**: 켜면 클립보드를 거치지 않고 DOM에 직접 넣고 `--selector`를 쓸 수 있다. 이 설정은 AppleScript를 쓸 수 있는 모든 프로세스(에이전트 포함)에 페이지 JavaScript 실행을 허용한다는 점을 알고 켠다.

5. `xcrun swiftc`(Xcode Command Line Tools)가 필요하다. 첫 실행에서 `scripts/sfhelper.swift`를 `~/.cache/secure-fill/`에 컴파일한다.

## 한계

- 이 도구는 값이 에이전트의 argv·출력·로그를 거치지 않게 한다. 페이지에 들어간 값은 페이지를 조작할 수 있는 에이전트(`orca eval`, DevTools, Apple Events JavaScript)가 `input.value`로 읽을 수 있다. 막는 것은 규칙과 Keychain ACL이다.
- 도구, 헬퍼, 허용 목록은 모두 에이전트와 같은 사용자 권한 아래에 있다. Touch ID 창은 사람이 무엇을 승인하는지 보여 주지만, 조작된 도구에 맞서는 층은 Keychain의 `-T ""` ACL과 root 소유 허용 목록이다.
- Chrome에서 JavaScript 경로가 꺼져 있으면 도구는 입력칸 포커스를 확인하지 못한다. 주소창에 포커스가 있으면 값이 주소창에 들어간다(제출은 하지 않지만 검색 제안 요청이 나갈 수 있다). 입력칸을 먼저 클릭한다.
- 클립보드는 문자열만 되돌린다. 이미지 등 다른 형식은 복원하지 않고 비운다.

## 검토하고 뺀 것

- **Orca `fill`/`type`/`eval`로 값 넣기:** 값이 argv에 실려 `ps`로 같은 사용자의 모든 프로세스가 볼 수 있다. Orca는 URL과 포커스를 읽는 데만 쓴다.
- **Chrome 원격 디버깅(CDP):** Chrome을 플래그와 함께 다시 띄워야 하고, 켜진 동안 에이전트도 같은 포트로 브라우저를 조종할 수 있다.
- **osascript 대화상자로 승인:** computer-use를 가진 에이전트가 클릭할 수 있다.
- **게이트를 건너뛰는 테스트용 플래그·환경 변수:** 에이전트가 쓸 수 있는 구멍이 된다. 테스트는 Python 객체로 가짜 게이트·소스·브라우저를 주입한다.
- **System Events로 Cmd+V:** Automation(System Events) 권한이 하나 더 필요하다. 헬퍼가 CGEvent로 보내면 Accessibility 하나로 된다.

## 테스트

```bash
python3 skills/secure-fill/scripts/test_secure_fill.py
```

origin 정규화(userinfo 거부, 기본 포트, 스킴·포트·하위 도메인 차이), 허용 목록 파싱, 거절·origin 불일치 때 값을 읽지 않는지, 모든 경로에서 값이 argv·출력·로그에 없는지를 본다. 수동 데모 기록은 [references/demo.md](references/demo.md).
