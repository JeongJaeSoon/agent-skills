"""model_drift.py and patrol's M finding: a session off its role's model is shown, never typed into.
Run: python3 test_model_drift.py"""
import json, os, pathlib, subprocess, sys, tempfile

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["AGENT_SKILLS_STATE"] = str(tmp / "state")
os.environ["ORCH_FLEET_STATE"] = str(tmp / "dashboard")
os.environ["AGENT_SKILLS_PATROL"] = "off"
os.environ["CLAUDE_CONFIG_DIR"] = str(tmp / "claude")
(tmp / "dashboard").mkdir()
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "reap-resources" / "scripts"))
import model_drift, patrol, roles  # noqa: E402

SONNET, OPUS = "claude-sonnet-5-5", "claude-opus-5-5"
MODELS = {"lead": "opus", "worker": "sonnet"}
WORKER_PROMPT = "You are working inside Orca, a multi-agent IDE.\nYou are a dispatched worker.\nTASK"
LEAD_PROMPT = "ROLE: lead demo\nbrief"
failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "FAIL ") + name + ("" if cond else f"  {detail}"))
    if not cond:
        failures.append(name)


def session(handle, sid, prompt, model, name="card", agent=None, after=""):
    """A live terminal: its roles.py record, its row in `claude agents`, its transcript. `after` is a tool result's
    text written after the last assistant row."""
    folder = tmp / "claude" / "projects" / "p"
    folder.mkdir(parents=True, exist_ok=True)
    rows = [{"type": "agent-setting", "agentSetting": agent}] if agent else []
    rows.append({"type": "user", "message": {"role": "user", "content": prompt}})
    if model:
        rows.append({"type": "assistant", "timestamp": "2026-11-04T10:00:00Z",
                     "message": {"role": "assistant", "model": model, "content": []}})
        rows.append({"type": "assistant", "timestamp": "2026-11-04T10:00:01Z",
                     "message": {"role": "assistant", "model": "<synthetic>", "content": []}})
    if after:
        rows.append({"type": "user", "message": {"role": "user", "content": after}})
    (folder / f"{sid}.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    roles.record_terminal(handle, sid, "/w", 2000)
    return {"pid": 1, "cwd": "/w", "kind": "interactive", "startedAt": 1_000_000, "sessionId": sid, "name": name,
            "status": "idle"}


def terms(*handles):
    return [{"handle": h} for h in handles]


calls = []
real_run, real_popen = subprocess.run, subprocess.Popen
subprocess.run = lambda *a, **k: calls.append(a) or real_run(*a, **k)
subprocess.Popen = lambda *a, **k: calls.append(a) or real_popen(*a, **k)

rows = [session("t_worker", "s-worker", WORKER_PROMPT, OPUS),
        session("t_lead", "s-lead", LEAD_PROMPT, SONNET),
        session("t_orch", "s-orch", "hello", SONNET, name="orchestrator"),
        session("t_human", "s-human", "hello", OPUS),
        session("t_fine", "s-fine", WORKER_PROMPT, SONNET),
        session("t_new", "s-new", WORKER_PROMPT, None),
        session("t_self", "s-self", WORKER_PROMPT, OPUS)]
all_terms = terms("t_worker", "t_lead", "t_orch", "t_human", "t_fine", "t_new", "t_self")

drifts, notes, blind = model_drift.run(all_terms, rows, None, MODELS, me="t_self")
check("nothing unreadable, so nothing blind", not blind and not notes, (blind, notes))
by ={d["handle"]: d for d in drifts}
check("a worker on opus is shown against worker=sonnet", set(by) == {"t_worker", "t_lead", "t_orch"}, sorted(by))
check("the finding names the session-only fix", "claude --resume s-worker --model sonnet" in by["t_worker"]["line"],
      by["t_worker"]["line"])
check("a lead on sonnet is shown against lead=opus", by["t_lead"]["role"] == "lead" and by["t_lead"]["want"] == "opus")
check("an orchestrator reads the lead key", by["t_orch"]["role"] == "orchestrator" and by["t_orch"]["want"] == "opus")
check("no human session, no session on its model, no transcript without a model, never the patrol's own",
      not {"t_human", "t_fine", "t_new", "t_self"} & set(by))
check("the synthetic row's model is not the actual one", model_drift.last_model(
    tmp / "claude" / "projects" / "p" / "s-worker.jsonl") == (OPUS, True))

again, _, _ = model_drift.run(all_terms, rows, None, MODELS, me="t_self")
check("a second round shows the same, and nothing was run or typed", again == drifts and not calls, calls)

drifts, notes, _ = model_drift.run(all_terms, rows, None, {"worker": "sonnet"}, me="t_self")
check("no lead key: lead and orchestrator are not judged, one note says so",
      {d["handle"] for d in drifts} == {"t_worker"} and notes == ["orchestrate.models.lead is not set: no model drift judged for that role"],
      (sorted(d["handle"] for d in drifts), notes))
check("an alias with a context suffix matches", model_drift.matches(OPUS, "opus[1m]") and not model_drift.matches(SONNET, "opus"))
check("a failed listing is no judgement and the round is blind", model_drift.run(all_terms, [], "exit 1", MODELS)[::2] == ([], True))
check("an empty listing is an answer, not a failure", model_drift.run(all_terms, [], None, MODELS) == ([], [], False))
late = tmp / "claude" / "projects" / "p" / "late.jsonl"
late.write_text("\n".join([json.dumps({"type": "agent-setting", "agentSetting": "agent-skills:orchestrator"})]
                          + [json.dumps({"type": "user", "message": {"content": "x"}})] * 200
                          + [json.dumps({"type": "agent-setting", "agentSetting": "agent-skills:lead"})]))
check("the last agent-setting row wins, however far into the file", model_drift.agent_type(late) == "agent-skills:lead")

# Codex review: what an alias means, which agent started the session, a quoted fix, a huge tool result.
check("opusplan and default are not compared", model_drift.matches(OPUS, "opusplan") is None
      and model_drift.matches(SONNET, "default") is None)
check("a full id must be the same model", model_drift.matches("claude-opus-5-5", "claude-opus-5-5") is True
      and model_drift.matches("claude-opus-5-5-20261001", "claude-opus-5-5") is True
      and model_drift.matches("claude-opus-5-50", "claude-opus-5-5") is False
      and model_drift.matches("claude-opus-5-5-1", "claude-opus-5-5") is False)
check("an unreadable transcript is not 'no turn yet'",
      model_drift.last_model(tmp / "nope.jsonl") == (None, None)
      and model_drift.last_model(tmp / "claude" / "projects" / "p" / "s-new.jsonl") == (None, True))
check("a transcript whose last 16 MiB hold no assistant row is not complete", (lambda p: (
    p.write_bytes(b'{"type":"user"}\n' * (17 * 2**20 // 16)), model_drift.last_model(p))[1])(tmp / "huge.jsonl") == (None, False))
d, _, blind = model_drift.run(terms("t_worker"), rows, None, MODELS, transcript=lambda s: tmp / "nope.jsonl")
check("an unreadable transcript makes the round blind", blind and not d, (d, blind))
d, notes, _ = model_drift.run(terms("t_worker"), rows, None, {"lead": "opus", "worker": "opusplan"})
check("an unresolvable value gives a note, not a finding", not d and len(notes) == 1 and "opusplan" in notes[0], (d, notes))
d, _, _ = model_drift.run(terms("t_worker"), rows, None, {"lead": "opus", "worker": "sonnet[1m]"})
check("the fix is shell-quoted", "--model 'sonnet[1m]'" in d[0]["line"] if d else False, d)
rows += [session("t_agent", "s-agent", WORKER_PROMPT, SONNET, agent="agent-skills:lead"),
         session("t_big", "s-big", WORKER_PROMPT, OPUS, after="x" * 600_000)]
d, _, _ = model_drift.run(terms("t_agent", "t_big"), rows, None, MODELS)
check("--agent lead reads the lead key (sonnet is off opus), not the worker's", [(x["handle"], x["role"]) for x in d][:1] == [("t_agent", "lead")], d)
check("an assistant row under a 600 KB tool result is still found", any(x["handle"] == "t_big" and x["have"] == OPUS for x in d), d)
check("a bound session with no readable model makes the round blind", model_drift.run(
    terms("t_worker"), rows, None, MODELS, transcript=lambda s: None)[2] is True)

# patrol: the finding and the brief's reason; no key at all is one note.
facts = {"terminals": [{"handle": "t_worker", "worktreePath": "/w/card", "agentIdentity": "claude", "connected": True}],
         "sessions": [], "agent_rows": (rows, None)}
os.environ["AGENT_SKILLS_CONFIG"] = str(tmp / "cfg.json")
patrol.CONFIG = tmp / "cfg.json"  # patrol read the path at import
(tmp / "cfg.json").write_text(json.dumps({"orchestrate": {"models": MODELS}}))
findings, verdicts, notes = [], {"check": {}}, []
patrol.model_round(facts, findings, verdicts, notes)
check("patrol adds an M finding and a check reason", [f["code"] for f in findings] == ["M"]
      and findings[0]["title"].startswith("❔ ") and verdicts["check"] == {"/w/card": "모델 불일치: " + OPUS + " (설정 sonnet)"},
      (findings, verdicts))
(tmp / "cfg.json").write_text("{}")
findings, verdicts, notes = [], {"check": {}}, []
patrol.model_round(facts, findings, verdicts, notes)
check("patrol without orchestrate.models: no finding, no note (the feature is off)",
      not findings and notes == [], (findings, notes))
(tmp / "cfg.json").write_text(json.dumps({"orchestrate": {"models": MODELS}}))
blind_facts = dict(facts, agent_rows=([], "exit 1"), failed=set())
patrol.model_round(blind_facts, [], {"check": {}}, [])
standing = {"M1": {"id": "M1", "code": "M"}}
patrol.log_rows, patrol.standing = lambda: [], lambda rows=None: standing
check("a round that could not look marks models failed", blind_facts["failed"] == {"models"}, blind_facts["failed"])
_, gone = patrol.write_round([], {}, True, NOW := 1_800_000_000.0, blind_facts["failed"])
check("and then keeps the M finding already standing", gone == [], gone)
_, gone = patrol.write_round([], {}, True, NOW, set())
check("a round that looked and found nothing clears it", [g["id"] for g in gone] == ["M1"], gone)
for what, setup in (("a cut terminal list", lambda f: f.update(terminals_cut=True)),
                    ("a failed terminal list", lambda f: f["failed"].add("terminals")),
                    ("an unreadable config", lambda f: (tmp / "cfg.json").write_text("{not json")),
                    ("a missing model_drift module", lambda f: setattr(patrol, "model_drift", None))):
    (tmp / "cfg.json").write_text(json.dumps({"orchestrate": {"models": MODELS}}))
    saved, f2 = patrol.model_drift, dict(facts, failed=set())
    setup(f2)
    patrol.model_round(f2, [], {"check": {}}, [])
    patrol.model_drift = saved
    check(f"{what} marks models failed, so a standing M stays", "models" in f2["failed"], f2["failed"])
# The collector's one-cell mark (fleet.model_tags): family letter, `!` when off the role's configured model.
check("model_tag: family letter, and ! only when the role's model says another",
      [model_drift.model_tag(m, w) for m, w in ((OPUS, "sonnet"), (SONNET, "sonnet"), ("claude-haiku-5-5", None),
                                                 ("some-model", "sonnet"), (None, "sonnet"), (OPUS, "opusplan"))]
      == ["O!", "S", "H", "?!", "", "O"])
os.environ["CLAUDE_PROJECTS_DIR"] = str(tmp / "claude" / "projects")
(tmp / "cfg.json").write_text(json.dumps({"orchestrate": {"models": MODELS}}))
for name, model in (("-w-worker", OPUS), ("-w-lead", OPUS), ("-w-human", OPUS), ("-w-empty", None)):
    folder = tmp / "claude" / "projects" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "a.jsonl").write_text(json.dumps({"type": "assistant", "message": {"model": model}}) if model else "")
sess = {"w": {"path": "/w/worker", "kind": "task"}, "l": {"path": "/w/lead", "kind": "orchestration"},
        "h": {"path": "/w/human", "kind": "standalone"}, "e": {"path": "/w/empty", "kind": "task"}, "n": {"kind": "task"}}
patrol.fleet.model_tags(sess)
check("model_tags: a worker on opus is O!, a lead on opus is O, a human's session has no want, none is empty",
      {k: v["model_tag"] for k, v in sess.items()} == {"w": "O!", "l": "O", "h": "O", "e": "", "n": ""}, sess)
(tmp / "claude" / "projects" / "-w-worker" / "b.jsonl").write_text(
    json.dumps({"type": "assistant", "message": {"model": SONNET}}))
patrol.fleet.model_tags(sess)
check("model_tags reads the newest transcript", sess["w"]["model_tag"] == "S", sess["w"])
# Another config home (an Orca profile) holds the card's newer transcript; an unfinished read is not remembered.
profile = tmp / "profile" / "projects" / "-w-worker"
profile.mkdir(parents=True)
(profile / "c.jsonl").write_text(json.dumps({"type": "assistant", "message": {"model": OPUS}}))
os.environ["ORCH_CLAUDE_HOMES"] = os.pathsep.join([str(tmp / "profile"), str(tmp / "claude")])
patrol.fleet.model_tags(sess)
check("model_tags looks in every config home, newest first", sess["w"]["model_tag"] == "O!", sess["w"])
del os.environ["ORCH_CLAUDE_HOMES"]
seen = tmp / "claude" / "projects" / "-w-seen"
seen.mkdir(parents=True)
(seen / "s.jsonl").write_text(json.dumps({"type": "assistant", "message": {"model": SONNET}}))
real_last = model_drift.last_model
model_drift.last_model = lambda p: (None, None)
check("an unreadable file gives no model", model_drift.dir_model([seen]) is None)
model_drift.last_model = real_last
check("and is tried again on the unchanged file", model_drift.dir_model([seen]) == SONNET)
huge = tmp / "claude" / "projects" / "-w-huge"
huge.mkdir(parents=True)
(huge / "h.jsonl").write_bytes(b'{"type":"user"}\n' * (17 * 2**20 // 16))
reads = []
model_drift.last_model = lambda p: reads.append(p) or real_last(p)
model_drift.dir_model([huge]), model_drift.dir_model([huge])
model_drift.last_model = real_last
check("a file with no model in its last 16 MiB is scanned once while it stays the same", len(reads) == 1, reads)
check("no subprocess anywhere in these rounds", not calls, calls)

# orchestrate.model_autoswitch: a stub stands in for scripts/sync.py, so no terminal is ever touched.
import contextlib  # noqa: E402
real_sync = model_drift.fleet.load_sync()
RULE = "─" * 30
SHELL = ["Resume this session with:", "claude --resume s-worker", "me@host card %"]
BACK = [RULE, "❯", RULE, "  ⏵⏵ auto mode on"]
IDLE = ["⏺ done", "✻ Worked for 1s · done 10:00 PM", RULE, "❯", RULE, "  ⏵⏵ auto mode on (shift+tab to cycle)"]
BG = ["⏺ done", "✻ Crunched for 22s · done 7:47 PM · 1 shell still running", RULE, "❯", RULE,
      "  ⏵⏵ auto mode on (shift+tab to cycle)"]
LOOKALIKE = ["claude --resume s-worker", RULE, "❯", RULE, "  custom context: 42%"]


class FakeSync:
    UNCONFIRMED, Stop = real_sync.UNCONFIRMED, real_sync.Stop
    norm, prompt_box = staticmethod(real_sync.norm), staticmethod(real_sync.prompt_box)
    background_running, classify = staticmethod(real_sync.background_running), staticmethod(real_sync.classify)

    def __init__(self, screens, live=None, lock=True, boom=None):
        self.typed, self.screens, self.lock, self.boom = [], list(screens), lock, boom
        self.live = IDLE if live is None else live  # what the session shows before the switch

    @contextlib.contextmanager
    def locked(self, wait_s=0):
        yield self.lock

    def screen(self, handle):
        return self.live, real_sync.classify(self.live, "✳ card")

    def send_to(self, handle, text, confirmed, still=None):
        reason = still and still(self.live)
        if self.boom:
            raise self.boom
        if not reason:
            self.typed.append(text)
        return reason

    def orca(self, *args):
        if args[1] == "send":
            self.typed.append(args[args.index("--text") + 1])
            return {}
        return {"terminal": {"tail": self.screens.pop(0) if len(self.screens) > 1 else self.screens[0]}}


def drift_of(handle):
    return next(d for d in model_drift.run(terms(handle), rows, None, MODELS)[0])


claims = tmp / "claims"
nowait = lambda s: None
worker = drift_of("t_worker")
fresh = lambda listing: (lambda: (listing, None, ""))  # the listing read again under the lock
busy = [dict(r, status="busy") if r["sessionId"] == "s-worker" else r for r in rows]


tick = [0.0]  # a clock that only the sleeps move, so a wait that never succeeds ends at its deadline at once


def go(d, name, fake, listing=None, rows_=None, read=None):
    return model_drift.autoswitch(d, rows_ or rows, tmp / name, fake, lambda s: tick.__setitem__(0, tick[0] + s),
                                  read or fresh(listing or rows), lambda: tick[0])


fake = FakeSync([SHELL, BACK])
check("an idle worker off its model is exited, then resumed on the role's model in the same terminal",
      go(worker, "claims", fake) == ("switched", "") and fake.typed == ["/exit", "claude --resume s-worker --model sonnet"],
      fake.typed)
check("the claim records the result", json.loads((claims / "s-worker.json").read_text())["result"] == "switched")
again = FakeSync([SHELL, BACK])
check("a session is tried once: the second round types nothing",
      go(worker, "claims", again) == ("skip", "already tried once") and not again.typed, again.typed)
for who in ("t_lead", "t_orch"):
    f = FakeSync([SHELL, BACK])
    check(f"{who} is never switched, only shown", go(drift_of(who), "claims", f)[0] == "skip" and not f.typed, f.typed)
# Codex review 1: the listing is minutes old, so the terminal is judged again under the lock.
f = FakeSync([SHELL, BACK])
check("a worker that is no longer idle in the fresh listing is skipped, claim unspent",
      go(worker, "claims-busy", f, busy)[0] == "skip" and not f.typed and not (tmp / "claims-busy").exists(), f.typed)
swapped = [dict(r, sessionId="s-human") if r["sessionId"] == "s-worker" else r for r in rows]
f = FakeSync([SHELL, BACK])
check("a terminal that now holds another session is not typed into",
      go(worker, "claims-swap", f, swapped)[0] == "skip" and not f.typed, f.typed)
f = FakeSync([SHELL, BACK])
check("a fresh listing that failed is no yes",
      go(worker, "claims-nolist", f, read=lambda: ([], "exit 1", ""))[0] == "skip" and not f.typed, f.typed)
# Codex review 2: the last check before the key reads the listing again, after the screens were judged stable.
answers = iter([rows, swapped])
f = FakeSync([SHELL, BACK])
check("a session that replaced the worker after the first check is not typed into, and the claim is released",
      go(worker, "claims-late", f, read=lambda: (next(answers), None, ""))[0] == "skip" and not f.typed
      and not (tmp / "claims-late" / "s-worker.json").exists(), f.typed)
fixed = [dict(r) for r in rows]
f = FakeSync([SHELL, BACK])
fixed_model = tmp / "claude" / "projects" / "p" / "s-worker.jsonl"
saved_text = fixed_model.read_text()
fixed_model.write_text(saved_text.replace(OPUS, SONNET))
check("a session already on the role's model by now is not restarted",
      go(worker, "claims-fixed", f)[0] == "skip" and not f.typed, f.typed)
fixed_model.write_text(saved_text)
# Codex review 2: the production background predicate, not a stub.
f = FakeSync([SHELL, BACK], live=BG)
check("a shell still running on the screen is skipped, nothing typed, no claim",
      go(worker, "claims-bg", f)[0] == "skip" and not f.typed and not (tmp / "claims-bg").exists(), f.typed)
f = FakeSync([SHELL, BACK], live=[l for l in IDLE if "⏵⏵" not in l])
check("a screen with no mode line to show background work on is skipped",
      go(worker, "claims-nomode", f)[0] == "skip" and not f.typed, f.typed)
f = FakeSync([SHELL, BACK], live=["Do you want to proceed?", "❯ 1. Yes", "  2. No"])
check("an open dialog is skipped", go(worker, "claims-dialog", f)[0] == "skip" and not f.typed, f.typed)
# Codex review 3: only this session's shell prompt lets the resume line out.
check("a live Claude screen with a percent status and a resume hint is not a shell prompt",
      not model_drift._shell_prompt("s-worker", LOOKALIKE) and model_drift._shell_prompt("s-worker", SHELL)
      and not model_drift._shell_prompt("s-other", SHELL))
fake = FakeSync([LOOKALIKE])
got = go(worker, "claims-stuck", fake)
check("no shell prompt after /exit: the resume line is never typed, the failure is kept and not retried",
      got[0] == "failed" and fake.typed == ["/exit"] and (tmp / "claims-stuck" / "s-worker.json").exists(), (got, fake.typed))
fake = FakeSync([SHELL, SHELL])
got = go(worker, "claims-noback", fake)
check("a resume that shows no Claude prompt is a failure", got[0] == "failed" and len(fake.typed) == 2, (got, fake.typed))
f = FakeSync([SHELL, BACK], lock=False)
check("a running sync or broadcast: skipped, nothing typed", go(worker, "claims-lock", f)[0] == "skip" and not f.typed, f.typed)
# Codex review 4: a send whose outcome is unknown keeps the claim.
f = FakeSync([SHELL, BACK], boom=real_sync.Stop("orca terminal send failed: timed out"))
got = go(worker, "claims-unknown", f)
check("an unknown send outcome is a failure with the claim kept, so no second try",
      got[0] == "failed" and json.loads((tmp / "claims-unknown" / "s-worker.json").read_text())["result"] == "failed"
      and go(worker, "claims-unknown", FakeSync([SHELL, BACK]))[1] == "already tried once", got)
# Codex review 5: patrol's budget alarm is a BaseException; the attempt is recorded and reaches the human once.
class Alarm(BaseException):
    pass


f = FakeSync([SHELL, BACK], boom=Alarm())
try:
    go(worker, "claims-alarm", f)
    raised = False
except Alarm:
    raised = True
check("an interrupted attempt is recorded as failed and the interruption is not swallowed",
      raised and json.loads((tmp / "claims-alarm" / "s-worker.json").read_text())["result"] == "failed")
lost = tmp / "claims-lost"
lost.mkdir()
(lost / "s-gone.json").write_text(json.dumps({"session": "s-gone", "handle": "t_gone", "at": 900.0}))
(lost / "s-young.json").write_text(json.dumps({"session": "s-young", "handle": "t_young", "at": 1590.0}))
(lost / "s-ok.json").write_text(json.dumps({"session": "s-ok", "handle": "t_ok", "at": 1000.0, "result": "switched"}))
got = model_drift.unreported(lost, now=1600.0)
check("unreported: a claim with no result after 10 minutes is lost, a young or switched one is not",
      [c["session"] for c in got] == ["s-gone"] and "interrupted" in got[0]["why"], got)
check("unreported marks nothing itself: a notice that failed is offered again", model_drift.unreported(lost, now=1600.0) == got)
model_drift.mark_reported(lost, got[0])
check("and once marked, each is reported once", model_drift.unreported(lost, now=1600.0) == [])
alarmed = model_drift.unreported(tmp / "claims-alarm")
check("a failed claim is reported once too", [c["session"] for c in alarmed] == ["s-worker"]
      and not model_drift.mark_reported(tmp / "claims-alarm", alarmed[0])
      and model_drift.unreported(tmp / "claims-alarm") == [])
# Codex review 3: a claim cut short by a crash is not silent, and a claim is written whole or not at all.
(lost / "s-cut.json").write_text('{"session": "s-cu')
os.utime(lost / "s-cut.json", (1000.0, 1000.0))
check("a malformed old claim is reported as unreadable", [(c["session"], c["why"]) for c in model_drift.unreported(
    lost, now=1000.0 + 700)] == [("s-cut", "the claim file is unreadable")])
check("a malformed young claim is left alone", model_drift.unreported(lost, now=1000.0 + 100) == [])
model_drift.mark_reported(lost, {"session": "s-cut", "handle": "", "why": "the claim file is unreadable"})
check("and marking it makes it readable and reported", model_drift.unreported(lost, now=1000.0 + 700) == [])
check("a claim is created exclusively and whole", (lambda p: (model_drift._put(p, {"a": 1}, exclusive=True),
      json.loads(p.read_text()), not p.with_suffix(".tmp").exists()))(tmp / "whole.json")[1:] == ({"a": 1}, True))
try:
    model_drift._put(tmp / "whole.json", {"a": 2}, exclusive=True)
    twice = True
except FileExistsError:
    twice = False
check("and never twice", not twice and json.loads((tmp / "whole.json").read_text()) == {"a": 1})
# Codex review 4: the wait is a deadline on the clock, not a count of polls.
slow = [0.0]


class SlowSync(FakeSync):
    def orca(self, *args):
        slow[0] += 5.0  # a terminal read that takes 5 s
        return {"terminal": {"tail": ["❯"]}}


check("a wait ends at its deadline however long each read takes", model_drift._wait(
    SlowSync([]), "t", lambda lines: False, 15.0, lambda s: None, lambda: slow[0]) is False and slow[0] == 15.0, slow)

cfg = tmp / "cfg.json"
for value, on in ((True, True), ("true", False), (1, False), (None, False)):
    cfg.write_text(json.dumps({"orchestrate": {"model_autoswitch": value}}))
    check(f"model_autoswitch {value!r} is {'on' if on else 'off'}", model_drift.autoswitch_on() is on)
cfg.write_text("{}")
check("no key is off", model_drift.autoswitch_on() is False)

asked = []


def stub_switch(d, rows_, claims_):
    asked.append((d["handle"], d["role"]))
    return stub_switch.answer


def switch_facts(handle):
    return {"terminals": [{"handle": handle, "worktreePath": "/w/card", "agentIdentity": "claude", "connected": True}],
            "sessions": [], "agent_rows": (rows, None)}


def switch_round(handle, config, dry=False, answer=("switched", "")):
    asked.clear()
    stub_switch.answer = answer
    cfg.write_text(json.dumps({"orchestrate": {"models": MODELS, **config}}))
    notes = []
    patrol.model_round(switch_facts(handle), [], {"check": {}}, notes, dry=dry, switch=stub_switch)
    return notes


switch_round("t_worker", {})
check("no model_autoswitch key: patrol never calls the switch", not asked, asked)
switch_round("t_worker", {"model_autoswitch": True}, dry=True)
check("a dry round never switches", not asked, asked)
notes = switch_round("t_worker", {"model_autoswitch": True})
check("with the key, an off-model worker is handed to the switch and the note says so",
      asked == [("t_worker", "worker")] and any("restarted on sonnet" in n for n in notes), (asked, notes))
switch_round("t_lead", {"model_autoswitch": True})
check("a lead off its model is shown but never handed to the switch", not asked, asked)
notes = switch_round("t_worker", {"model_autoswitch": True}, answer=("failed", "no shell prompt after /exit"))
inbox = (tmp / "dashboard" / "inbox-external.jsonl").read_text()
check("a failed switch is a note and a human inbox item keyed by the session",
      any("auto-switch failed" in n for n in notes) and "model-switch:s-worker" in inbox, (notes, inbox))
gone = patrol.STATE / "model-switch"
gone.mkdir(parents=True, exist_ok=True)
(gone / "s-lost.json").write_text(json.dumps({"session": "s-lost", "handle": "t_worker", "at": 1.0}))
real_inbox = patrol.fleet.inbox_add


def broken_inbox(*a, **k):
    raise OSError("disk full")


patrol.fleet.inbox_add = broken_inbox
notes = switch_round("t_lead", {"model_autoswitch": True})
patrol.fleet.inbox_add = real_inbox
check("an inbox that fails leaves the abandoned attempt unreported, so the next round tells again",
      [c["session"] for c in model_drift.unreported(gone)] == ["s-lost"] and any("could not be reported" in n for n in notes), notes)
notes = switch_round("t_lead", {"model_autoswitch": True})
check("patrol tells the human of an abandoned attempt even when no worker is off its model now",
      any("auto-switch failed" in n and "interrupted" in n for n in notes)
      and "model-switch:s-lost" in (tmp / "dashboard" / "inbox-external.jsonl").read_text(), notes)
notes = switch_round("t_worker", {"model_autoswitch": True}, answer=("skip", "not idle"))
check("a skip says nothing", not any("auto-switch" in n for n in notes), notes)

print("FAILED" if failures else "all passed")
sys.exit(1 if failures else 0)
