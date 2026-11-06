---
name: secure-fill
description: "Use when a browser auth screen needs a secret the agent must not see — \"토큰 입력해줘\", \"비밀번호 칸 채워줘\", \"secure-fill\". The value goes from the Keychain to the page after the owner's Touch ID. Never read, echo or paste the secret, and never edit the allowlist."
---

# Secure fill

It works like a password manager. The agent only requests the fill, a human approves each time in a window the agent cannot operate (Touch ID / device owner authentication), and the tool puts the value into the browser directly. An item the owner marked `"approval": "none"` in the allowlist is filled without the approval window. The value appears nowhere: not in argv, stdout or logs.

## What the agent does

1. Navigate to the auth screen in the browser.
2. Click the input field that takes the value, to give it focus.
3. Run:

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/scripts/secure_fill.py" --item <name> --origin https://dev.example.test --target chrome   # or --target orca
   ```

   `bin/secure-fill` is a wrapper that does the same. Once the owner has run `chmod 755 bin/secure-fill`, it can be called as `secure-fill` from PATH.

4. Tell the human "Touch ID 승인 대기 중". The command does not finish until they approve. An item with `"approval": "none"` finishes at once without waiting.

The result comes out as one line and is also distinguished by exit code. The value is never printed.

| Exit code | Result | Next action |
|---|---|---|
| 0 | `filled` | A human or the agent submits the form (the tool submits only when given `--submit`) |
| 10 | `denied` | The human declined. Ask before trying again |
| 11 | `origin-mismatch` | The active tab differs from `--origin`. Navigate first |
| 12 | `not-found` | No value in the Keychain or in env. Ask the human to register it |
| 13 | `not-allowed` | The allowlist has no such item·origin pair. Ask the human |
| 14 | `no-field` | No input field has focus |
| 15 | `fill-failed` | Missing permission or similar. The reason appears in parentheses |
| 16 | `config-error` | Malformed allowlist |

Do not: edit the allowlist (`~/.config/secure-fill/allow.json`), run `security find-generic-password` directly, read the field's value after filling (`orca eval`, DevTools), or retry over and over without approval.

## Order of operations

1. Check that `--origin` has the form `scheme://host[:port]`, and that it is in the item's origins in the allowlist.
2. Read the active tab URL of the target browser and check that the origin matches exactly. For Chrome it is the AppleScript `URL of active tab of front window`; for Orca, `orca get --what url`.
3. Show the Touch ID window. Its reason text carries the item name and the origin. Without approval the value is not read. If the item is `"approval": "none"`, skip this step.
4. Read the value. The Keychain generic password (service `secure-fill`, account = item name) comes first; if absent, the item's `env` variable.
5. Fill.
   - Chrome, with "Allow JavaScript from Apple Events" on: check `location.origin` again inside the page and set the value in the same script (so the tab cannot change between the check and the input). The script goes to `osascript` on stdin.
   - Otherwise the clipboard: a Swift helper takes the value on stdin, writes it to the clipboard with the `org.nspasteboard.ConcealedType` and `TransientType` markers, brings the browser to the front and sends Cmd+V, then after 3 seconds restores the original string if nobody changed the clipboard in the meantime. It reads the origin again just before pasting. For Orca it also checks that the page and the input field have focus. Otherwise Cmd+V could land in the agent's own terminal.
6. Record time, item, origin, target, approval mode (`touch-id` or `none`), result and method in `~/.local/state/secure-fill/log.jsonl` (directory 0700, file 0600).

## Owner setup

1. Create the Keychain entry. Leaving `-w` last with no value makes it ask for the value interactively (so it stays out of argv and shell history).

   ```bash
   security add-generic-password -s secure-fill -a dev-token -T "" -w
   ```

   `-T ""` empties the trusted-app list. By the macOS documentation, the Keychain then shows a confirmation window whenever any process reads the value afterwards. That catches an agent that tries to read it directly with `security`, bypassing this tool. The cost is that every fill goes through both Touch ID and the Keychain confirmation (an item with `"approval": "none"` goes through the Keychain confirmation only). This behavior has not been measured yet, so on first use check yourself that the confirmation window appears. Without `-T ""`, `security` becomes a trusted app and any process of the same user can read the value without asking.

   In the confirmation window press only **Allow**. Pressing **Always Allow** even once puts `/usr/bin/security` into that item's trust list for good and this layer is gone. To undo it, delete the item and create it again.

2. Create the allowlist, `~/.config/secure-fill/allow.json`:

   ```json
   {"items": {
     "dev-token": {"origins": ["https://dev.example.test"]},
     "staging-token": {"origins": ["https://staging.example.test:8443"], "env": "STAGING_TOKEN",
                       "keychain": {"service": "secure-fill", "account": "staging-token"}}
   }}
   ```

   For an item to be filled without Touch ID, write `"approval": "none"` (the default is `"touch-id"`). Example: `"dev-token": {"origins": ["https://dev.example.test"], "approval": "none"}`. This setting is a convenience, not a security boundary (see "Limits" below).

   Write an origin as `scheme://host[:port]` only, with no path. The default ports (443, 80) are the same whether written or left out. `http` and `https`, and subdomains, are different from each other.

3. Allowlist ownership. The agent runs as the same user, so it can edit a file the user owns. Owned by root, the agent cannot change it without sudo. The tool prints an `allowlist-writable-by-this-user` warning when the file is writable by the current user.

   ```bash
   sudo chown root:wheel ~/.config/secure-fill/allow.json && sudo chmod 644 ~/.config/secure-fill/allow.json
   ```

4. macOS permissions. Grant them to the terminal app that runs the agent (e.g. Orca, Terminal, iTerm).
   - System Settings → Privacy & Security (개인정보 보호 및 보안) → **Accessibility (손쉬운 사용)**: needed for clipboard pasting (sending Cmd+V). Without it the run ends with `fill-failed (the terminal app lacks Accessibility permission)` and the value goes nowhere.
   - **Automation (자동화)** → Google Chrome: needed to read the active tab URL. macOS asks on the first run.
   - (Optional) Chrome menu View → Developer → **Allow JavaScript from Apple Events**: when on, the value goes into the DOM directly without the clipboard and `--selector` can be used. Turn it on knowing that this setting lets every process that can use AppleScript (the agent included) run JavaScript in the page.

5. `xcrun swiftc` (Xcode Command Line Tools) is required. The first run compiles `scripts/sfhelper.swift` into `~/.cache/secure-fill/`.

## Limits

- This tool keeps the value from passing through the agent's argv, output and logs. Once the value is in the page, an agent that can drive the page (`orca eval`, DevTools, Apple Events JavaScript) can read it as `input.value`. What stops that is the rules and the Keychain ACL.
- The tool, the helper and the allowlist all sit under the same user's permissions as the agent. The Touch ID window shows the human what they are approving, but it cannot stop an agent that tries to get around the tool (it can import the module directly and drop the gate, or swap the helper in the cache). The layers against a tampered tool are the Keychain's `-T ""` ACL and a root-owned allowlist.
- `"approval": "none"` reduces human clicks within these limits. With this setting the value still does not appear in argv, output or logs, and the origin is still checked. If you create the item without `-T ""` to drop even the Keychain confirmation window, any process of the same user can read the value with `security`, and only the rules and the shell guard are left. Keep items to be filled without approval to development and test values for which that much protection is enough.
- When the JavaScript path is off in Chrome, the tool cannot check input-field focus. If the address bar has focus, the value goes into the address bar (nothing is submitted, but a search-suggestion request may go out). Click the input field first.
- The clipboard restores strings only. Other formats such as images are not restored; the clipboard is left empty.

## Considered and left out

- **Putting the value in through Orca `fill`/`type`/`eval`:** the value rides in argv and any process of the same user can see it with `ps`. Orca is used only to read the URL and focus.
- **Chrome remote debugging (CDP):** Chrome has to be relaunched with a flag, and while it is on, the agent can drive the browser through the same port.
- **Approval through an osascript dialog:** an agent with computer-use can click it.
- **A test flag or environment variable that skips the gate:** it becomes a hole the agent can use. Tests inject a fake gate, source and browser as Python objects.
- **Cmd+V through System Events:** needs one more permission, Automation (System Events). When the helper sends it with CGEvent, Accessibility alone is enough.

## Tests

```bash
python3 skills/secure-fill/scripts/test_secure_fill.py
```

It covers origin normalization (userinfo rejected, default ports, scheme, port and subdomain differences), allowlist parsing, that the value is not read on a denial or an origin mismatch, and that on every path the value is absent from argv, output and logs. The manual demo record is [references/demo.md](references/demo.md).
