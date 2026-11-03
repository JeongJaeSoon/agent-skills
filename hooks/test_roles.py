"""roles.py: the four roles, their precedence and the rule matrix.
Run: python3 hooks/test_roles.py"""
import json, os, pathlib, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
TMP = pathlib.Path(tempfile.mkdtemp())
os.environ["AGENT_SKILLS_STATE"] = str(TMP / "state")
sys.path.insert(0, str(HERE))
import roles  # noqa: E402

PREAMBLE = ("You are working inside Orca, a multi-agent IDE. You are a dispatched worker.\n"
            "Your coordinator's terminal handle is: term_x\n=== TASK ===\nfix X\n")
LEAD_BRIEF = "app: lead\nMODE: orchestrate\nROLE: lead app\nDELEGATE  nothing yourself\nPREDICATE  ABC-1 Done\n"


def transcript(name, *prompts):
    path = TMP / f"{name}.jsonl"
    lines = [{"type": "summary"}, {"type": "user", "isMeta": True, "message": {"content": "meta"}}]
    lines += [{"type": "user", "message": {"content": [{"type": "text", "text": p}]}} for p in prompts]
    path.write_text("\n".join(json.dumps(x) for x in lines))
    return str(path)


def record(session, role, slug=None):
    roles.roles_dir().mkdir(parents=True, exist_ok=True)
    entry = {"role": role, **({"slug": slug} if slug else {})}
    (roles.roles_dir() / f"{session}.json").write_text(json.dumps(entry))


def who(session="s", agent=None, prompt=None, agent_id=None, path=None, **extra):
    event = {"session_id": session, "transcript_path": path, **extra}
    if agent:
        event["agent_type"] = agent
    if agent_id:
        event["agent_id"] = agent_id
    return roles.session(event, prompt)


# 1. A declared role comes first: --agent, a ROLE record, a lead's brief as the first prompt.
assert who(agent="agent-skills:orchestrator").role == "orchestrator"
assert who(agent="agent-skills:lead", session_title="lead:app").slug == "app"
record("r-orch", "orchestrator")
assert who("r-orch").role == "orchestrator" and who("r-orch").declared
record("r-lead", "lead", "app")
assert (who("r-lead").role, who("r-lead").slug) == ("lead", "app")
assert who(prompt=LEAD_BRIEF).role == "lead"
# A lead Orca dispatched is still a lead, and supervised.
lead = who(prompt=PREAMBLE + LEAD_BRIEF)
assert (lead.role, lead.supervised, lead.slug) == ("lead", True, "app"), lead
lead = who(agent="agent-skills:lead", prompt=PREAMBLE)
assert (lead.role, lead.supervised) == ("lead", True), lead
record("r-lead2", "lead", "app")
assert who("r-lead2", prompt=PREAMBLE + "CLOSE: done\n").role == "lead", "a ROLE record beats the preamble and CLOSE"

# 2. Undeclared: the Dispatch preamble or a CLOSE/KEEP row makes a worker; the attributes say which kind.
w = who(prompt=PREAMBLE)
assert (w.role, w.supervised, w.close, w.keep, w.declared) == ("worker", True, False, False, False), w
w = who(prompt="/goal fix X\nCLOSE: end-session §8\n")
assert (w.role, w.supervised, w.close) == ("worker", False, True), w
w = who(prompt="/goal fix X\nKEEP    reused by the parent\n")
assert (w.role, w.keep) == ("worker", True), w
w = who(path=transcript("t-worker", PREAMBLE, "a later prompt"))
assert w.role == "worker", "the first prompt, read from the transcript"

# 3. Anything else is general: a session a human opened never becomes a worker, whatever it says later.
assert who(prompt="please fix the CLOSE button").role == "general"
assert who(prompt="KEEP the old API").role == "general"
assert who(path=transcript("t-human", "look at this", PREAMBLE)).role == "general", "a later paste is not the first prompt"
assert who(path=str(TMP / "missing.jsonl")).role == "general"
assert who().role == "general"
assert who(prompt="take the lead on this (lead: me)").role == "general"

# 4. ROLE: none turns every role off: --agent, a lead brief and the preamble alike.
record("r-none", "none")
for kw in ({"agent": "agent-skills:orchestrator"}, {"agent": "agent-skills:lead"}, {"prompt": LEAD_BRIEF},
           {"prompt": PREAMBLE}, {"prompt": "CLOSE: x"}):
    assert who("r-none", **kw).role == "general", kw
assert roles.resolve({"agent_type": "agent-skills:orchestrator"}, {"role": "none"}) is None

# The session title counts at SessionStart only, as reorient.py always read it.
assert roles.resolve({"hook_event_name": "SessionStart", "session_title": "orchestrator"}, None) == {"role": "orchestrator"}
assert roles.resolve({"hook_event_name": "SessionStart", "session_title": "lead:app"}, None) == {"role": "lead", "slug": "app"}
assert roles.resolve({"hook_event_name": "UserPromptSubmit", "session_title": "orchestrator"}, None) is None
assert roles.declared("../etc") is None

# declare() records the last ROLE line and keeps `none`.
assert roles.declare("d1", "x\nROLE: lead app\nROLE: orchestrator\n") == {"role": "orchestrator"}
assert roles.declare("d1", "no role line") == {"role": "orchestrator"}
assert roles.declare("d1", "ROLE: none") == {"role": "none"}

# Actor: a subagent carries its parent's role; the matrix decides which rules reach it.
sub = who("r-orch", agent="agent-skills:lead", agent_id="a1")
assert (sub.role, sub.actor) == ("orchestrator", "subagent"), "the subagent's own agent_type is not its parent's role"
assert who(agent="agent-skills:orchestrator", agent_id="a1").role == "general"
assert roles.applies("danger", sub) and not roles.applies("ask_user_question", sub)
for role in ("orchestrator", "lead", "worker"):
    assert roles.applies("ask_user_question", roles.Session(role)), role
assert not roles.applies("ask_user_question", roles.Session("general"))
assert roles.applies("wait_foreground", roles.Session("lead")) and not roles.applies("wait_foreground", roles.Session("worker"))
assert roles.applies("danger", roles.Session("general"))
assert not roles.applies("no-such-rule", roles.Session("orchestrator"))
print("roles: all pass")
