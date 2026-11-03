"""`sync.py` against throwaway git repos and a fake orca. Run: python3 scripts/test_sync.py"""
import json, os, pathlib, shutil, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import sync  # noqa: E402

RULE = "─" * 40
IDLE = ["✻ Worked for 2m 7s · done 5:48 AM", RULE, "❯\xa0", RULE, "  ~/x ⎇ main | Opus", "  ⏵⏵ auto mode on"]
FRESH = ["▝▜██████▀  Haiku 4.5", RULE, '❯\xa0Try "write a test for migrate.py"', RULE, "  ⏸ manual mode on"]
BUSY = ["⏺ Checking docker", "✶ Contemplating… (32s · ↓ 912 tokens)", "  ⎿  Tip: Send messages", RULE, "❯", RULE]
HOOKS = ["· Crunching… (running UserPromptSubmit hooks… 3/4 · 0s)", RULE, "❯", RULE]
PERMISSION = [" Bash command", "   command -v orch", " Do you want to proceed?", " ❯ 1. Yes", "   2. No",
              " Esc to cancel · Tab to amend"]
TRUST = [" Accessing workspace: /w", " ❯ No, exit", "   Yes, I trust this folder", " Enter to confirm · Esc to cancel"]
ASK = ["4. Type something.", RULE, "5. Chat about this", "Enter to select · ↑/↓ to navigate · Esc to cancel"]
TYPING = ["❯ earlier prompt", "⏺ answer", RULE, "❯ 아 그리고 지금 이 세션이", RULE]
SHELL = ["$ claude", "^[[200~You are working inside Orca", "$"]
COMPACTING = ["⏺ done", "✶ Compacting conversation… (12s · esc to interrupt)", RULE, "❯", RULE, "  ⏵⏵ auto mode on"]
CUSTOM_VERB = ["✻ Working hard… (3s)", RULE, "❯", RULE]
EXITED = ["⏺ bye", RULE, "❯", RULE, "  ⏵⏵ auto mode on", "", "Resume this session with: claude --resume 1234",
          "user@mac ~ % "]
EXITED_BARE = ["⏺ bye", RULE, "❯", RULE, "user@mac ~ % "]
PRESS_ENTER = ["Update installed.", "Press Enter to continue…", RULE, "❯", RULE]
# Real /reload-plugins then /reload-skills on 2.1.282, as `terminal read --screen` returned it.
RELOAD_DONE = [" ▝▝   ▝▝   ~/w", "❯ /reload-plugins",
               "  ⎿  Reloaded: 15 plugins · 75 skills · 13 agents · 22 hooks · 6 plugin MCP servers · 1 plugin LSP server",
               "❯ /reload-skills", "  ⎿  Reloaded skills: 91 skills available (no changes)", RULE, "❯", RULE,
               "  ~/w\xa0⎇\xa0main\xa0|\xa0Haiku\xa04.5", "  ⏸ manual mode on · ← for agents"]

# A session whose hooks.json names a script an update removed: every tool call fails on the hook.
HOOK_BLOCKED = ["❯ run the tests", "⏺ Bash(make test)",
                "  ⎿  PreToolUse:Bash hook error: python3: can't open file '/w/plugin/hooks/gone.py': [Errno 2] No such file",
                "⏺ Every tool call is blocked by a hook error, so I stop here.", RULE, "❯", RULE, "  ~/w ⎇ main"]
RESTART_NOTE = ["⏺ Goal achieved.", RULE, "❯", RULE, "  ~/w ⎇ main", "  ✓ Update installed · /restart to apply"]
OLD_HOOK_ERROR = ["❯ first", "  ⎿  PreToolUse:Bash hook error: can't open file x", "❯ second", "⏺ done", RULE, "❯", RULE]
# A guard hook's deny is shown with the same words, and is no stale plugin.
GUARD_DENY = ["❯ run the suite", "⏺ Bash(make test)", "  ⎿  PreToolUse:Bash hook error: [gate:x] run it in the slot",
              "⏺ Blocked by the gate.", RULE, "❯", RULE]
# Shaped like the screen that was stuck: the notice right-aligned under the turn's summary, above the box.
STUCK_SHOT = ["● Stopped here: a hook names a file that is not there.", "", "✔ Goal achieved (8d · 1 turn · 20.5k tokens)", "",
              "✻ Brewed for 26s · done 9:34 PM",
              "                new task? /clear to save 121.3k tokens · ✔ Update installed · /restart to apply", RULE, "❯", RULE]
# The words in the conversation are no notice: a reply on one line, or wrapped so its last line has no mark.
SAID = ["❯ what does the footer say?", "⏺ It says /restart to apply the update.", RULE, "❯", RULE]
SAID_WRAPPED = ["❯ what does the footer say?", "⏺ The footer reads: update installed, and then", "  /restart to apply it.",
                RULE, "❯", RULE]
SAID_AFTER_BLANK = ["❯ what changed?", "⏺ Two things:", "", "/restart to apply the update.", RULE, "❯", RULE]
SAID_LIST = ["❯ what changed?", "⏺ Two things:", "* Update installed", "/restart to apply it.", RULE, "❯", RULE]
SAID_LIKE_SUMMARY = ["❯ what now?", "⏺ Two steps:", "* Wait for 2s before retrying.", "/restart to apply the update.",
                     RULE, "❯", RULE]
# A guard's deny as the last result, then a reply about some other missing file.
DENY_THEN_REPLY = ["❯ build", "  ⎿  PreToolUse:Bash hook error: [gate:x] run in a slot",
                   "⏺ The build failed: No such file or directory.", RULE, "❯", RULE]
# A hook error the session got past: a tool ran after it in the same turn.
RECOVERED = ["❯ go", "  ⎿  PreToolUse:Bash hook error: can't open file '/w/plugin/hooks/gone.py'", "⏺ Bash(make test)",
             "  ⎿  12 passed", "⏺ Done.", RULE, "❯", RULE]
# The missing file wrapped onto the next line.
WRAPPED = ["❯ go", "  ⎿  PreToolUse:Read hook error: python3:", "     can't open file '/w/plugin/hooks/gone.py'", RULE, "❯", RULE]

FAKE_ORCA = r'''#!/usr/bin/env python3
import json, os, sys
path = os.environ["FAKE_ORCA_STATE"]
st = json.load(open(path))
a = sys.argv[1:]
def arg(k): return a[a.index(k) + 1]
if a[:2] == ["terminal", "list"]:
    out = {"terminals": [dict(t, handle=h) for h, t in st["terms"].items()]}
elif a[:2] == ["terminal", "show"]:
    out = {"terminal": dict(st["terms"][arg("--terminal")], handle=arg("--terminal"))}
elif a[:2] == ["worktree", "ps"]:
    out = {"worktrees": st.get("worktrees", [])}
elif a[:2] == ["terminal", "read"]:
    t = st["terms"][arg("--terminal")]
    tail = t["screens"].pop(0) if len(t["screens"]) > 1 else t["screens"][0]
    out = {"terminal": {"tail": tail, "source": "screen"}}
elif a[:2] == ["terminal", "close"]:
    st.setdefault("closed", []).append(arg("--terminal"))
    out = {}
elif a[:2] == ["terminal", "send"]:
    t = st["terms"][arg("--terminal")]
    st["sent"].append([arg("--terminal"), arg("--text")])
    text = arg("--text")
    shown = ["  ⎿  Reloaded skills: 9 skills available"] if text.startswith("/") else ["✶ Pondering… (1s)"]
    base = t["screens"][0]
    top = [i for i, l in enumerate(base) if l.startswith("─")][-2]
    t["screens"] = [base[:top] + ["❯ " + text] + shown + base[top:]]
    out = {}
json.dump(st, open(path, "w"))
print(json.dumps({"ok": True, "result": out}))
'''

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def commit(repo, path, text, msg="c"):
    f = repo / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg)


def setup(tmp):
    """origin (bare), `live` (the loaded checkout, with this sync.py in it) and `other` (another PC)."""
    origin, live, other = tmp / "origin.git", tmp / "live", tmp / "other"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(live)], check=True, capture_output=True)
    for r in (live,):
        git(r, "config", "user.email", "t@example.com"); git(r, "config", "user.name", "t")
    commit(live, "skills/a/SKILL.md", "v1\n")
    git(live, "push", "-q", "origin", "HEAD:main")
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, capture_output=True)
    git(other, "config", "user.email", "t@example.com"); git(other, "config", "user.name", "t")
    (live / "scripts").mkdir(exist_ok=True)
    shutil.copy(HERE / "sync.py", live / "scripts" / "sync.py")
    (live / ".git" / "info").mkdir(exist_ok=True)  # a custom init.templateDir may leave it out
    (live / ".git" / "info" / "exclude").write_text("scripts/\n")
    return live, other


def run_sync(live, env, *args):
    p = subprocess.run([sys.executable, str(live / "scripts" / "sync.py"), *args],
                       capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


def test_sync():
    tmp = pathlib.Path(tempfile.mkdtemp())
    https_env = dict(os.environ, AGENT_SKILLS_STATE=str(tmp / "hstate"), SKILLS_SYNC_NO_NOTIFY="1",
                     GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    state = tmp / "state"
    env = dict(os.environ, AGENT_SKILLS_STATE=str(state), SKILLS_SYNC_NO_NOTIFY="1", SKILLS_SYNC_ORCA="false")
    live, other = setup(tmp)

    code, out = run_sync(live, env, "sync", "--no-broadcast")
    check("up to date", code == 0 and "up to date" in out, out)

    commit(other, "skills/a/SKILL.md", "v2\n"); git(other, "push", "-q", "origin", "main")
    code, out = run_sync(live, env, "sync", "--no-broadcast")
    check("fast-forwards when behind", code == 0 and (live / "skills/a/SKILL.md").read_text() == "v2\n", out)
    pending = json.loads((state / "reload-pending.json").read_text())
    check("a SKILL.md change queues /reload-skills", pending["kind"] == "skills", pending)

    commit(other, "hooks/hooks.json", "{}\n"); git(other, "push", "-q", "origin", "main")
    run_sync(live, env, "sync", "--no-broadcast")
    check("a hooks change raises the queue to /reload-plugins",
          json.loads((state / "reload-pending.json").read_text())["kind"] == "plugins")

    (live / "skills/a/SKILL.md").write_text("local edit\n")
    commit(other, "docs/x.md", "x\n"); git(other, "push", "-q", "origin", "main")
    head = git(live, "rev-parse", "HEAD")
    code, out = run_sync(live, env, "sync", "--no-broadcast")
    check("stops on uncommitted changes", code == 1 and "uncommitted" in out and git(live, "rev-parse", "HEAD") == head, out)
    st = json.loads((state / "sync.json").read_text())
    check("the stop is recorded", st["ok"] is False and "uncommitted" in st["message"], st)
    git(live, "checkout", "-q", "--", ".")

    commit(live, "skills/b/SKILL.md", "local\n")
    code, out = run_sync(live, env, "sync", "--no-broadcast")
    check("stops when diverged, without rebasing", code == 1 and "diverged" in out, out)
    git(live, "reset", "-q", "--hard", "origin/main")

    commit(live, "skills/b/SKILL.md", "unsigned\n")
    code, out = run_sync(live, env, "sync", "--no-broadcast")
    check("refuses to push an unsigned commit", code == 1 and "not signed" in out
          and git(live, "rev-parse", "origin/main") != git(live, "rev-parse", "HEAD"), out)

    key = tmp / "key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    git(live, "reset", "-q", "--hard", "origin/main")
    git(live, "config", "gpg.format", "ssh"); git(live, "config", "user.signingkey", str(key))
    (live / "skills/b").mkdir(parents=True, exist_ok=True); (live / "skills/b/SKILL.md").write_text("signed\n")
    git(live, "add", "-A")
    subprocess.run(["git", "-C", str(live), "commit", "-S", "-qm", "signed"], check=True)
    code, out = run_sync(live, env, "sync", "--no-broadcast")
    check("pushes a signed commit (E: no allowedSignersFile is fine)",
          code == 0 and "pushed 1" in out and git(live, "rev-parse", "origin/main") == git(live, "rev-parse", "HEAD"), out)

    url = git(live, "remote", "get-url", "origin")
    git(live, "remote", "set-url", "origin", "https://example.invalid/r.git")
    code, out = run_sync(live, https_env, "sync", "--no-broadcast")
    check("stops before an HTTPS fetch with no credential helper", code == 1 and "credential helper" in out, out)
    git(live, "remote", "set-url", "origin", url)

    git(live, "checkout", "-q", "-b", "side")
    code, out = run_sync(live, env, "sync", "--no-broadcast")
    check("stops off main", code == 1 and "not on main" in out, out)
    shutil.rmtree(tmp)


def test_reload_kind():
    check("docs need no reload", sync.reload_kind(["docs/a.md", "README.md"]) is None)
    check("scripts and references are read live", sync.reload_kind(
        ["skills/orchestrate/scripts/prog.py", "skills/orchestrate/references/landing.md", "bin/orch"]) is None)
    check("SKILL.md needs /reload-skills", sync.reload_kind(["skills/why/SKILL.md"]) == "skills")
    check("hook wiring needs /reload-plugins", sync.reload_kind(["skills/why/SKILL.md", "hooks/hooks.json"]) == "plugins")
    check("hook scripts are read live", sync.reload_kind(["hooks/guard.py", "hooks/test_guard.py"]) is None)


def test_classify():
    idle = "✳ some task"
    check("idle empty prompt is sendable", sync.classify(IDLE, idle) is None)
    check("fresh placeholder prompt is sendable", sync.classify(FRESH, idle) is None)
    for name, lines in [("turn in progress", BUSY), ("hooks running", HOOKS), ("permission dialog", PERMISSION),
                        ("trust dialog", TRUST), ("AskUserQuestion", ASK), ("typed draft", TYPING), ("shell", SHELL),
                        ("compacting", COMPACTING), ("a custom spinner verb", CUSTOM_VERB),
                        ("claude exited to a shell", EXITED), ("a shell prompt below the box", EXITED_BARE),
                        ("a press-enter screen", PRESS_ENTER)]:
        check(f"{name} is not sendable", sync.classify(lines, idle) is not None)
    check("busy title is not sendable", sync.classify(IDLE, "◑ some task") is not None)
    check("a composer draft is not sendable and the reason shows it",
          "'hi'" in (sync.classify(IDLE, idle, draft="hi") or ""))
    check("the real screen after a reload is sendable", sync.classify(RELOAD_DONE, idle) is None)


def test_confirm():
    before = RELOAD_DONE[:1] + RELOAD_DONE[5:]
    check("a reload shown below its command is confirmed",
          sync.reloaded(before, RELOAD_DONE, "/reload-skills"))
    check("a reload that is not the last message is not confirmed",
          not sync.reloaded(before, RELOAD_DONE, "/reload-plugins"))
    stuck = RELOAD_DONE[:6] + ["❯ /reload-skills"] + RELOAD_DONE[7:]
    check("a command left in the box is not confirmed", not sync.reloaded(RELOAD_DONE, stuck, "/reload-skills"))
    check("an unchanged screen is not confirmed", not sync.reloaded(RELOAD_DONE, RELOAD_DONE, "/reload-skills"))
    text = "run your orchestration check"
    check("text sitting in the box is not a started turn",
          not sync.turn_started(IDLE, [RULE, "❯ " + text, RULE], text))
    check("an echoed message is a started turn",
          sync.turn_started(IDLE, ["❯ " + text, "⏺ ok", RULE, "❯", RULE], text))
    check("a spinner is a started turn", sync.turn_started(IDLE, ["❯ " + text, "✶ Pondering… (1s)", RULE, "❯", RULE], text))


def test_broadcast():
    tmp = pathlib.Path(tempfile.mkdtemp())
    fake = tmp / "orca"
    fake.write_text(FAKE_ORCA); fake.chmod(0o755)
    st = tmp / "orca.json"
    terms = {
        "idle": {"agentIdentity": "claude", "connected": True, "writable": True, "title": "✳ a", "screens": [IDLE]},
        "stale": {"agentIdentity": "claude", "connected": True, "writable": True, "title": "✳ d", "screens": [EXITED]},
        "perm": {"agentIdentity": "claude", "connected": True, "writable": True, "title": "✳ b", "screens": [PERMISSION]},
        "typing": {"agentIdentity": "claude", "connected": True, "writable": True, "title": "✳ c",
                   "screens": [IDLE, IDLE[:2] + ["❯ 아"] + IDLE[3:]]},
        "setup": {"agentIdentity": None, "connected": True, "writable": True, "title": "~/w", "screens": [SHELL]},
        "new": {"agentIdentity": "claude", "connected": True, "writable": True, "title": "✳ e", "screens": [BUSY],
                "worktreeId": "wt-new"},
    }
    for h in ("idle", "stale", "perm", "typing"):
        terms[h]["worktreeId"] = "wt-old"
    # 2026-09-25T23:39:02+0900 is 1790347142000 ms
    worktrees = [{"worktreeId": "wt-old", "createdAt": 1790347142000 - 1}, {"worktreeId": "wt-new", "createdAt": 1790347142000 + 1}]
    st.write_text(json.dumps({"terms": terms, "worktrees": worktrees, "sent": []}))
    state = tmp / "state"; state.mkdir()
    head = subprocess.run(["git", "-C", str(HERE.parent), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (state / "reload-pending.json").write_text(json.dumps({"kind": "plugins", "since": "2026-09-25T23:39:02+0900",
                                                            "done": [], "head": head}))
    env = dict(AGENT_SKILLS_STATE=str(state), SKILLS_SYNC_ORCA=str(fake), FAKE_ORCA_STATE=str(st),
               SKILLS_SYNC_SETTLE_S="0", SKILLS_SYNC_CONFIRM_S="0")
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    import importlib
    importlib.reload(sync)
    try:
        with sync.locked() as got:
            busy = sync.broadcast("skills", dry_run=False)
        check("a broadcast waits while the lock is held", got and busy == 1 and json.loads(st.read_text())["sent"] == [])
        one = sync.broadcast("skills", dry_run=False, only="idle")
        check("--terminal sends to that session only and keeps the rest pending",
              one == 0 and json.loads(st.read_text())["sent"] == [["idle", "/reload-plugins"]]
              and json.loads((state / "reload-pending.json").read_text())["done"] == ["idle"])
        code = sync.broadcast("skills", dry_run=False)
        pending = json.loads((state / "reload-pending.json").read_text())
        st.write_text(json.dumps(dict(json.loads(st.read_text()), terms=dict(
            json.loads(st.read_text())["terms"], idle=dict(terms["idle"], screens=[IDLE])))))
        sync.nudge("idle", "run your orchestration check")
        nudged_perm = sync.nudge("perm", "run your orchestration check")
        cur = json.loads(st.read_text())
        st.write_text(json.dumps(dict(cur, terms=dict(cur["terms"], idle=dict(terms["idle"], screens=[IDLE])))))
        (state / "reload-pending.json").write_text(json.dumps({"kind": "skills", "done": ["idle"], "head": "old"}))
        sync.broadcast(None, dry_run=False, only="idle")
        cur = json.loads(st.read_text())
        resent = cur["sent"].pop()
        st.write_text(json.dumps(cur))
        os.environ["SKILLS_SYNC_ORCA"] = str(tmp / "missing")
        importlib.reload(sync)
        no_orca = (sync.nudge("idle", "x"), sync.broadcast("skills", dry_run=False),
                   sync.broadcast("skills", dry_run=True))
    finally:
        for k, v in old.items():
            os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
        importlib.reload(sync)
    sent = json.loads(st.read_text())["sent"]
    check("only the idle session got the reload, raised to the pending kind",
          sent[:1] == [["idle", "/reload-plugins"]], sent)
    check("a nudge goes to an idle session and not past a dialog",
          sent[1:] == [["idle", "run your orchestration check"]] and nudged_perm == 1, sent)
    check("the others stay pending with a reason", code == 1 and set(pending["failed"]) == {"perm", "typing", "stale"}
          and pending["done"] == ["idle"], pending)
    check("a setup shell is never a target", "setup" not in pending["failed"])
    check("a session opened after the change is neither sent to nor left pending",
          "new" not in pending["failed"] and not any(h == "new" for h, _ in sent), pending)
    check("a session that already reloaded is not sent the reload again",
          [h for h, t in sent if t.startswith("/reload")] == ["idle"], sent)
    check("an unreadable `since` or worktree list leaves every terminal a target",
          sync.opened_after(None, [{"handle": "new", "worktreeId": "wt-new"}]) == set())
    check("no orca means a refusal, not a crash", no_orca == (1, 1, 1), no_orca)
    check("a session done for an older checkout gets the reload again", resent == ["idle", "/reload-skills"], resent)
    shutil.rmtree(tmp)


def test_stale_signal():
    check("a hook error in the last turn is a stale signal",
          (sync.stale_signal(HOOK_BLOCKED) or ("",))[0] == "hook", sync.stale_signal(HOOK_BLOCKED))
    check("the update notice is a stale signal", (sync.stale_signal(RESTART_NOTE) or ("",))[0] == "restart")
    check("a hook error before the last prompt is not", sync.stale_signal(OLD_HOOK_ERROR) is None)
    check("a guard's deny is not", sync.stale_signal(GUARD_DENY) is None)
    check("the notice under the turn's summary is", (sync.stale_signal(STUCK_SHOT) or ("",))[0] == "restart")
    check("the words in a reply are not", all(sync.stale_signal(s) is None for s in (SAID, SAID_WRAPPED, SAID_AFTER_BLANK, SAID_LIST, SAID_LIKE_SUMMARY)))
    check("the summary shapes Claude Code writes are summaries",
          all(sync.TURN_SUMMARY.match(s) for s in (IDLE[0], STUCK_SHOT[4], "✻ Cooked for 1h 2m 3s · done 11:02 PM")))
    check("a missing file in the reply after a guard's deny is not", sync.stale_signal(DENY_THEN_REPLY) is None)
    check("a hook error the session got past is not", sync.stale_signal(RECOVERED) is None)
    check("a missing file wrapped onto the next line is", (sync.stale_signal(WRAPPED) or ("",))[0] == "hook")
    check("a screen without the wording is not judged",
          all(sync.stale_signal(s) is None for s in (IDLE, BUSY, FRESH, RELOAD_DONE, PRESS_ENTER, [], None)))
    check("a restart notice does not make the session busy", sync.classify(RESTART_NOTE, "✳ a") is None)


def test_restart():
    tmp = pathlib.Path(tempfile.mkdtemp())
    fake = tmp / "orca"
    fake.write_text(FAKE_ORCA); fake.chmod(0o755)
    st = tmp / "orca.json"

    def term(title, *screens):
        return {"agentIdentity": "claude", "connected": True, "writable": True, "title": title, "screens": list(screens)}
    busy_note = BUSY[:-2] + RESTART_NOTE[1:]
    terms = {"hooked": term("✳ a", HOOK_BLOCKED), "notice": term("✳ b", RESTART_NOTE),
             "busy": term("◑ c", busy_note), "clean": term("✳ d", IDLE), "old": term("✳ e", OLD_HOOK_ERROR)}
    st.write_text(json.dumps({"terms": terms, "sent": []}))
    state = tmp / "state"; state.mkdir()
    head = subprocess.run(["git", "-C", str(HERE.parent), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (state / "reload-pending.json").write_text(json.dumps({"kind": "skills", "done": ["notice", "busy", "old"], "head": head}))
    env = dict(AGENT_SKILLS_STATE=str(state), SKILLS_SYNC_ORCA=str(fake), FAKE_ORCA_STATE=str(st),
               SKILLS_SYNC_SETTLE_S="0", SKILLS_SYNC_CONFIRM_S="0")
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    import importlib
    importlib.reload(sync)

    def sent():
        return json.loads(st.read_text())["sent"]
    try:
        sync.broadcast(None, dry_run=True)
        dry = (sent(), (state / "restarts.json").exists())
        first = sync.broadcast(None, dry_run=False)
        after_first = sent()
        restarts = json.loads((state / "restarts.json").read_text())
        second = sync.broadcast(None, dry_run=False)
        after_second = sent()
        cur = json.loads(st.read_text())
        cur["terms"]["busy"] = term("✳ c", RESTART_NOTE)
        st.write_text(json.dumps(cur))
        sync.broadcast(None, dry_run=False, only="busy")
        after_idle = sent()
        kept = json.loads((state / "restarts.json").read_text())
        calls = []
        real = (sync.pull_or_push, sync.broadcast_locked)
        sync.pull_or_push, sync.broadcast_locked = (lambda: ("up to date", [])), (lambda k: calls.append(k))
        try:
            (state / "restarts.json").write_text(json.dumps({"x": {"waiting": "turn in progress"}}))
            sync.sync()
            (state / "restarts.json").write_text(json.dumps({"x": {"sent": "t"}}))
            sync.sync()
        finally:
            sync.pull_or_push, sync.broadcast_locked = real
    finally:
        for k, v in old.items():
            os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
        importlib.reload(sync)
    check("a dry run types nothing and writes nothing", dry == ([], False), dry)
    check("an idle session with a hook error gets /reload-plugins and no second reload; the notice gets /restart",
          first == 0 and after_first == [["hooked", "/reload-plugins"], ["notice", "/restart"], ["clean", "/reload-skills"]],
          after_first)
    check("a busy session waits, recorded as waiting",
          "waiting" in restarts.get("busy", {}) and not any(h == "busy" for h, _ in after_first), restarts)
    check("no stale signal, no entry", set(restarts) == {"hooked", "notice", "busy"}, restarts)
    check("a session is sent the command once while its screen still shows the signal",
          second == 0 and after_second == after_first, after_second)
    check("the waiting session gets it once idle", after_idle[len(after_first):] == [["busy", "/restart"]], after_idle)
    check("--terminal keeps the other sessions' entries", "notice" in kept and "sent" in kept["busy"], kept)
    check("a sync runs the broadcast while a session waits, and not for one already sent", calls == [None], calls)
    shutil.rmtree(tmp)


def test_restart_entries():
    """restarts.json across reads that fail: what was typed stays typed, and an unread screen ends nothing."""
    tmp = pathlib.Path(tempfile.mkdtemp())
    keys = ("AGENT_SKILLS_STATE", "SKILLS_SYNC_SETTLE_S", "SKILLS_SYNC_CONFIRM_S")
    old_env = {k: os.environ.get(k) for k in keys}
    os.environ.update(AGENT_SKILLS_STATE=str(tmp), SKILLS_SYNC_SETTLE_S="0", SKILLS_SYNC_CONFIRM_S="0")
    import importlib
    importlib.reload(sync)
    reads, typed = {}, []

    def screen(h):
        r = reads[h].pop(0) if len(reads[h]) > 1 else reads[h][0]
        if isinstance(r, Exception):
            raise r
        return r, sync.classify(r, "✳ a")
    try:
        sync.screen, sync.orca = screen, lambda *a: typed.append(a[3]) if a[:2] == ("terminal", "send") else {}
        reads["a"] = [RESTART_NOTE, RESTART_NOTE, sync.Stop("orca terminal read failed")]
        check("a confirmation read that fails after typing is not 'never sent'",
              sync.send_to("a", "/restart", sync.stale_cleared) == sync.UNCONFIRMED and typed == ["a"], typed)
        reads["a"] = [RESTART_NOTE, RESTART_NOTE, RESTART_NOTE, sync.Stop("orca terminal read failed"), RESTART_NOTE]
        sync.restart_stale(["a"])
        sync.restart_stale(["a"])
        entry = json.loads((tmp / "restarts.json").read_text())["a"]
        check("so it is recorded as sent and never typed again", typed == ["a", "a"] and entry.get("unconfirmed"),
              (typed, entry))
        reads["a"] = [[]]
        sync.restart_stale(["a"])
        check("an unread screen keeps the entry", "a" in json.loads((tmp / "restarts.json").read_text()))
        reads["a"] = [PERMISSION]
        sync.restart_stale(["a"])
        check("so does a dialog with no prompt box", "sent" in json.loads((tmp / "restarts.json").read_text()).get("a", {}))
        sync.restart_stale([])
        check("a terminal Orca does not list now keeps it a while", "a" in json.loads((tmp / "restarts.json").read_text()))
        reads["a"] = [IDLE]
        sync.restart_stale(["a"])
        check("a screen without the signal ends it", json.loads((tmp / "restarts.json").read_text()) == {})
        (tmp / "restarts.json").write_text(json.dumps({"gone": {"sent": "2020-01-01T00:00:00+0000"},
                                                       "waits": {"waiting": "busy", "since": "2020-01-01T00:00:00+0000"}}))
        sync.restart_stale([])
        check("old entries of unlisted terminals go", json.loads((tmp / "restarts.json").read_text()) == {})
    finally:
        for k, v in old_env.items():
            os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
        importlib.reload(sync)
    shutil.rmtree(tmp)


def test_close_setup():
    runner = "/r/.git/worktrees/card/orca/setup-runner.sh"
    done = ["$ bash " + runner, " ERR_PNPM_NO_PKG_MANIFEST  No package.json found", "  ~/w/card on  main", "$"]
    check("the setup script is found on its screen", sync.setup_runner(done) == runner)
    check("a hand-opened shell has no setup script", sync.setup_runner(["$ ls", "$"]) is None)
    tmp = pathlib.Path(tempfile.mkdtemp())
    fake = tmp / "orca"
    fake.write_text(FAKE_ORCA); fake.chmod(0o755)
    st = tmp / "orca.json"
    terms = {"agent": {"agentIdentity": "claude", "screens": [IDLE]}, "setup": {"agentIdentity": None, "screens": [done]},
             "busy": {"agentIdentity": None, "screens": [["$ bash /r/.git/worktrees/other/orca/setup-runner.sh"]]},
             "mine": {"agentIdentity": None, "screens": [["$ ls", "$"]]}}
    st.write_text(json.dumps({"terms": terms, "sent": []}))
    env = dict(SKILLS_SYNC_ORCA=str(fake), FAKE_ORCA_STATE=str(st), SKILLS_SYNC_SETUP_POLL_S="0.05")
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    import importlib
    importlib.reload(sync)
    try:
        sync.running = lambda path: "other" in path
        code = sync.close_setup("/w/card", timeout_s=2, grace_s=0.5)
    finally:
        for k, v in old.items():
            os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
        importlib.reload(sync)
    closed = json.loads(st.read_text()).get("closed", [])
    check("a finished setup terminal is closed; a running one, a hand-opened shell and the agent stay",
          closed == ["setup"], closed)
    check("a setup still running at the timeout is reported", code == 1)
    shutil.rmtree(tmp)


if __name__ == "__main__":
    test_reload_kind()
    test_classify()
    test_confirm()
    test_sync()
    test_broadcast()
    test_stale_signal()
    test_restart()
    test_restart_entries()
    test_close_setup()
    print(f"\n{len(failures)} failure(s)" if failures else "\nall passed")
    sys.exit(1 if failures else 0)
