"""request_log.py and owner_requests.py: a coordinator's human prompts become open requests, nothing else does.
Run: python3 hooks/test_request_log.py"""
import datetime as dt, json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp())
STATE, ROLES = TMP / "dashboard", TMP / "agent-state"
os.environ["ORCH_FLEET_STATE"] = str(STATE)
sys.path.insert(0, str(HERE.parent / "skills" / "orchestrate" / "scripts"))
import owner_requests as req  # noqa: E402


def hook(prompt, session="s-coord", **event):
    env = {"PATH": "/usr/bin:/bin", "ORCH_FLEET_STATE": str(STATE), "AGENT_SKILLS_STATE": str(ROLES),
           "ORCA_TERMINAL_HANDLE": "term_coord"}
    out = subprocess.run([sys.executable, HERE / "request_log.py"],
                         input=json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": session, "prompt": prompt, **event}),
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0 and out.stderr == "", out  # never blocks a prompt, never a loud failure
    return json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"] if out.stdout else None


def opened():
    return [q["text"] for q in req.fold(req.read()).values()]


# An undeclared session logs nothing; a session declared in this very prompt does (reorient may not have written yet).
assert hook("merge acme/app#1 please", session="s-plain") is None and opened() == []
text = hook("ROLE: orchestrator\ncheck whether acme/app#1 is merged")
assert text == "Owner request r1 logged", text
assert opened() == ["check whether acme/app#1 is merged"]
(ROLES / "roles").mkdir(parents=True, exist_ok=True)
(ROLES / "roles" / "s-coord.json").write_text(json.dumps({"role": "orchestrator"}))
(ROLES / "roles" / "s-lead.json").write_text(json.dumps({"role": "lead", "slug": "acme"}))
(ROLES / "roles" / "s-quiet.json").write_text(json.dumps({"role": "none"}))
assert hook("look at the flaky test", session="s-quiet") is None
assert hook("look at the flaky test", agent_type="agent-skills:lead", session="s-agent") is not None  # --agent, no record

# Not human requests: harness rows, card relays, the compaction preamble, Orca's nudge, a bare ROLE line, blanks.
# A slash command arrives as typed; an answer to a dashboard decision is recorded by decision.py, open or just closed.
(STATE).mkdir(parents=True, exist_ok=True)
(STATE / "decisions.json").write_text(json.dumps({"decisions": {"d3": {"id": "d3", "status": "done"}}}))
for p in ["/brief", "  /reload-plugins", "/goal finish the ledger", "d3: yes", "D3 later",
          "<task-notification><task-id>b1</task-id></task-notification>", "<command-name>/brief</command-name>",
          "[card-a] PR ready: acme/app#2", "[lead:acme] DONE", "This session is being continued from a previous conversation...",
          "You have 1 orchestration message waiting", "ROLE: orchestrator", "   \n "]:
    assert hook(p) is None, p
# Human ones: pasted text, an image, a long line (masked and capped). (`d9: ...` names no decision, so it is one too,
# checked further down.)
assert hook('<pasted_content id="x">log</pasted_content> why does this fail?')
assert hook("[Image #1] this banner is wrong")
assert hook("deploy with API_TOKEN=abc123secret then tell me " + "x" * 400, session="s-lead")
texts = opened()
assert len(texts) == 5 and texts[3] == "[Image #1] this banner is wrong", texts
assert "abc123secret" not in texts[4] and "[masked]" in texts[4] and len(texts[4]) == req.TEXT_MAX, texts[4]
rows = req.read()
assert rows[-1]["session"] == "s-lead" and rows[-1]["handle"] == "term_coord"
assert oct((STATE / "requests.jsonl").stat().st_mode & 0o777) == "0o600"

# link, done, drop; a closed one refuses another close; an unknown id refuses.
req.close("r1", "link", to="acme/app#1")
req.close("r1", "done", note="merged at abc1234")
req.close("r3", "drop", note="only a remark")
for rid, ev in (("r1", "done"), ("r9", "drop")):
    try:
        req.close(rid, ev, note="x")
        raise AssertionError(f"{rid} {ev} should refuse")
    except ValueError:
        pass
q = req.fold(req.read())
assert q["r1"]["status"] == "done" and q["r1"]["links"] == ["acme/app#1"] and q["r1"]["note"] == "merged at abc1234"
assert q["r3"]["status"] == "drop" and q["r2"]["status"] == "open"

# summary: open count, oldest age, and open ones without a link past UNLINKED_MIN.
later = req.fleet.parse(q["r2"]["ts"]) + dt.timedelta(minutes=45)
req.close("r4", "link", to="card-b")
s = req.summary(later)
assert s["open"] == 3 and s["oldest_min"] == 45, s
assert [u["id"] for u in s["unlinked"]] == ["r2", "r5"], s
assert req.line(s).startswith("요청 3건 열림 · 가장 오래된 것 45m · 미연결 30분+: r2 r5"), req.line(s)
assert req.line(req.summary(later, {})) == ""

# The reminder on the next prompt carries ids only: the count, the oldest age, and the unlinked ones.
f = STATE / "requests.jsonl"
hour_ago = req.fleet.iso(req.fleet.utcnow() - dt.timedelta(hours=1))
f.write_text("".join(json.dumps({**r, "ts": hour_ago}) + "\n" for r in req.read()))
text = hook("d9: and reply to the reviewer")
assert text == "Owner request r6 logged · 4 open, oldest 1h00m · unlinked 30m+: r2 r5", text
assert len(text.encode()) < 80, len(text.encode())

# Ids never repeat under concurrent prompts.
procs = [subprocess.Popen([sys.executable, HERE / "request_log.py"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                          env={"PATH": "/usr/bin:/bin", "ORCH_FLEET_STATE": str(STATE), "AGENT_SKILLS_STATE": str(ROLES)})
         for _ in range(8)]
for i, p in enumerate(procs):
    p.communicate(json.dumps({"session_id": "s-coord", "prompt": f"parallel {i}"}).encode())
ids = [r["id"] for r in req.read() if r["ev"] == "open"]
assert len(ids) == len(set(ids)) == 14, ids
print("ok")
