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
assert req.line(s) == "요청 3건 열림 · 가장 오래된 것 45m · 미연결 30분+ 2건", req.line(s)
assert req.line(req.summary(later, {})) == ""

# The reminder on the next prompt carries ids only: the count, the oldest age, and the unlinked ones.
f = STATE / "requests.jsonl"
hour_ago = req.fleet.iso(req.fleet.utcnow() - dt.timedelta(hours=1))
f.write_text("".join(json.dumps({**r, "ts": hour_ago}) + "\n" for r in req.read()))
text = hook("d9: and reply to the reviewer")
assert text == "Owner request r6 logged · 4 open, oldest 1h00m · unlinked 30m+: 2 (newest: r5 r2)", text
assert len(text.encode()) < 90, len(text.encode())

# Ids never repeat under concurrent prompts.
procs = [subprocess.Popen([sys.executable, HERE / "request_log.py"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                          env={"PATH": "/usr/bin:/bin", "ORCH_FLEET_STATE": str(STATE), "AGENT_SKILLS_STATE": str(ROLES)})
         for _ in range(8)]
for i, p in enumerate(procs):
    p.communicate(json.dumps({"session_id": "s-coord", "prompt": f"parallel {i}"}).encode())
ids = [r["id"] for r in req.read() if r["ev"] == "open"]
assert len(ids) == len(set(ids)) == 14, ids

# Orca's nudge is cut out wherever it was typed; the same text again from the same session, still open, is not a new one.
text = hook("clean up the cards You have 2 orchestration messages. Run `orca orchestration check --run run_0FAKE`. that are idle")
assert text.startswith("Owner request r15 logged"), text
assert opened()[-1] == "clean up the cards that are idle", opened()[-1]
assert hook("clean up the cards that are idle").startswith("Owner request r15 logged (same as before)")
text = hook("clean up the cards that are idle", agent_type="agent-skills:lead", session="s-agent")  # another session's
assert text.startswith("Owner request r16 logged") and "same" not in text, text
assert len(opened()) == 16
text = hook("You have 1 orchestration message review the open cards")  # no period: the human's words stay
assert text.startswith("Owner request r17 logged") and opened()[-1] == "review the open cards", opened()[-1]

# An unlinked request past TTL_MIN expires: out of the counts, still closable. A linked one never expires.
os.environ["ORCH_FLEET_STATE"] = str(TMP / "recon")
T0 = dt.datetime(2026, 1, 5, 9, 0, tzinfo=dt.timezone.utc)
iso = req.fleet.iso


def at(minutes):
    return iso(T0 + dt.timedelta(minutes=minutes))


for n, text in enumerate(["merge it", "make a card", "a remark", "two things", "free-form link", "old one"]):
    req.add(text, "s-c", "term_c" if n != 2 else "term_gone", now=T0 + dt.timedelta(minutes=40 * n))
req.close("r5", "link", to="as-lead #1/#2", now=T0)
req.close("r1", "link", to="acme/app#1", now=T0)
q = req.fold(req.read())
late = T0 + dt.timedelta(minutes=req.TTL_MIN + 300)
assert req.expired(q["r2"], late) and not req.expired(q["r1"], late) and not req.expired(q["r2"], T0)
s = req.summary(late, q)
assert s["open"] == 2 and s["expired"] == 4 and [u["id"] for u in s["unlinked"]] == [], s

# reconcile: a merged PR closes its request; a card the session started in the window is linked, and done once it is
# seen live then gone; an unlinked request whose terminal stays gone is dropped; a free-form link blocks auto-done.
sessions = {"c": {"id": "c", "name": "coord", "parent": None, "created": at(-999)},
            "w": {"id": "w", "name": "card-x", "parent": "c", "created": at(42)}}
prs = {"acme/app#1": {"key": "acme/app#1", "state": "MERGED", "merged_at": at(50), "session": "w"},
       "acme/app#2": {"key": "acme/app#2", "state": "OPEN", "created": at(121), "session": "c"}}
board_rows = {"lead-a": {"id": "lead-a", "status": "moving", "by": "term_c", "created_at": at(122), "parent": None},
              "lead-a/t1": {"id": "lead-a/t1", "status": "moving", "by": "term_c", "created_at": at(123), "parent": "lead-a"},
              "other": {"id": "other", "status": "done", "by": "term_other", "created_at": at(122)}}
by_handle, seen, missing = {"term_c": "c"}, {}, {}
now = T0 + dt.timedelta(minutes=240)
rows = req.reconcile(req.fold(req.read()), now, prs, sessions, board_rows, by_handle, seen, missing, True)
got = {(r["ev"], r["id"], r.get("to")) for r in rows}
assert got == {("done", "r1", None), ("link", "r2", "card:card-x"),
               ("link", "r4", "acme/app#2"), ("link", "r4", "board:lead-a")}, got
assert missing == {"term_gone": {"since": iso(now), "at": iso(now), "reads": 1}}, missing
# A terminal one read lacks is not gone yet: it is dropped once reads GONE_MIN apart keep lacking it, and one seen
# again in between starts over.
later = now + dt.timedelta(minutes=req.GONE_MIN)
again = dict(missing)
assert all(r["ev"] != "drop" for r in req.reconcile(req.fold(req.read()), now, prs, sessions, board_rows, by_handle, {}, again, True))
back = {**by_handle, "term_gone": "c"}
m2 = dict(missing)
req.reconcile(req.fold(req.read()), later, prs, sessions, board_rows, back, {}, m2, True)
assert m2 == {}, m2
drop = [r for r in req.reconcile(req.fold(req.read()), later, prs, sessions, board_rows, by_handle, {}, missing, True)
        if r["ev"] == "drop"]
assert [r["id"] for r in drop] == ["r3"] and missing["term_gone"]["reads"] == 2, (drop, missing)
rows += drop
assert all(r["by"] == "auto" for r in rows) and "acme/app#1 merged" in next(r for r in rows if r["ev"] == "done")["evidence"]
assert seen == {"card-x": {"id": "w", "at": iso(now)}}  # lead-a/t1 rides on lead-a, made just before it by the same terminal
# Two requests in the window before a card: the turn answered both, so the card is linked to neither.
two = {"x1": {"id": "x1", "ts": at(300), "text": "look at the login flow", "handle": "term_c", "status": "open", "links": []},
       "x2": {"id": "x2", "ts": at(305), "text": "and the export too", "handle": "term_c", "status": "open", "links": []}}
both = {**sessions, "y": {"id": "y", "name": "card-y", "parent": "c", "created": at(310)}}
assert req.reconcile(two, now, {}, both, {}, by_handle, {}, {}, True) == []
# An acknowledgement or a short question asks for no card: it is never linked and leaves the other one alone.
for chat in ("고마워", "ok!", "왜 그래?", "Thank you."):
    assert req.chatty(chat), chat
assert not req.chatty("카드 만들어줘") and not req.chatty("acme/app#3 merge 해줘")
two["x1"]["text"], two["x2"]["text"] = "카드 만들어줘", "확인했어"
assert [(r["id"], r["to"]) for r in req.reconcile(two, now, {}, both, {}, by_handle, {}, {}, True)] == [("x1", "card:card-y")]
two["x1"]["text"] = "이건 왜 이래?"
assert req.reconcile(two, now, {}, both, {}, by_handle, {}, {}, True) == []
del two["x1"]
two["x2"]["text"] = "make a card"
assert [r["to"] for r in req.reconcile(two, now, {}, both, {}, by_handle, {}, {}, True)] == ["card:card-y"]
assert req.apply(rows)[0] == rows and req.apply(rows)[0] == []  # the second pass finds them all overtaken
q = req.fold(req.read())
assert q["r1"]["status"] == "done" and q["r3"]["status"] == "drop" and q["r2"]["links"] == ["card:card-x"]

# A failed Orca read closes nothing card- or terminal-based, and a card never seen live is not taken as gone.
gone = {"c": sessions["c"]}
assert req.reconcile(q, now, prs, gone, board_rows, by_handle, seen, {}, False) == []
assert req.reconcile(q, now, prs, {}, board_rows, by_handle, seen, {}, True) == []
assert req.reconcile(q, now, prs, gone, board_rows, by_handle, {}, {}, True) == []
renamed = {**gone, "w": {**sessions["w"], "name": "card-x-renamed"}}  # same worktree, new name: not gone
assert [(r["ev"], r.get("to")) for r in req.reconcile(q, now, prs, renamed, board_rows, by_handle, dict(seen), {}, True)] \
    == [("link", "card:card-x-renamed")]  # linked again under its new name, and not done
rows = req.reconcile(q, now, prs, gone, board_rows, by_handle, seen, {}, True)
assert [(r["ev"], r["id"]) for r in rows] == [("done", "r2")] and "card:card-x removed" in rows[0]["evidence"], rows
req.apply(rows)
# r4 closes only once every link is over: the PR merged and the board row done. A PR closed unmerged is not over.
board_rows["lead-a"]["status"] = "done"
prs["acme/app#2"]["state"] = "CLOSED"
assert req.reconcile(req.fold(req.read()), now, prs, gone, board_rows, by_handle, seen, {}, True) == []
assert not req.finished("https://github.com/acme/app/pull/2", prs, None, {}, {})
prs["acme/app#2"]["state"] = "MERGED"
board_rows["lead-a"]["status"] = "moving"
assert req.reconcile(req.fold(req.read()), now, prs, gone, board_rows, by_handle, seen, {}, True) == []
board_rows["lead-a"]["status"] = "done"
rows = req.reconcile(req.fold(req.read()), now, prs, gone, board_rows, by_handle, seen, {}, True)
assert [(r["ev"], r["id"]) for r in rows] == [("done", "r4")], rows
assert seen == {}  # no open request links a card any more

# orch req link takes several ids and --done.
os.environ["ORCH_FLEET_STATE"] = str(TMP / "cli")
for t in ("a", "b", "c"):
    req.add(t, "s-c", "term_c")
prog = HERE.parent / "skills" / "orchestrate" / "scripts" / "prog.py"
out = subprocess.run([sys.executable, prog, "req", "link", "r1", "r2", "acme/app#3", "--done", "--evidence", "merged"],
                     capture_output=True, text=True, env={**os.environ, "PATH": "/usr/bin:/bin"})
assert out.returncode == 0 and out.stdout.strip() == "r1 r2 → acme/app#3 (done)", out
q = req.fold(req.read())
assert [q[r]["status"] for r in ("r1", "r2", "r3")] == ["done", "done", "open"] and q["r1"]["links"] == ["acme/app#3"]
print("ok")
