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
      model_drift.last_model(tmp / "nope.jsonl") == (None, False)
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
check("patrol without orchestrate.models: no finding, one note",
      not findings and notes == ["orchestrate.models is not set: no model drift judged"], (findings, notes))
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
check("no subprocess anywhere in these rounds", not calls, calls)

print("FAILED" if failures else "all passed")
sys.exit(1 if failures else 0)
