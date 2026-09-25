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
elif a[:2] == ["terminal", "read"]:
    t = st["terms"][arg("--terminal")]
    tail = t["screens"].pop(0) if len(t["screens"]) > 1 else t["screens"][0]
    out = {"terminal": {"tail": tail, "source": "screen"}}
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
    check("a composer draft is not sendable", sync.classify(IDLE, idle, draft="hi") is not None)
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
    }
    st.write_text(json.dumps({"terms": terms, "sent": []}))
    state = tmp / "state"; state.mkdir()
    (state / "reload-pending.json").write_text(json.dumps({"kind": "plugins", "done": []}))
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
    check("no orca means a refusal, not a crash", no_orca == (1, 1, 1), no_orca)
    shutil.rmtree(tmp)


if __name__ == "__main__":
    test_reload_kind()
    test_classify()
    test_confirm()
    test_sync()
    test_broadcast()
    print(f"\n{len(failures)} failure(s)" if failures else "\nall passed")
    sys.exit(1 if failures else 0)
