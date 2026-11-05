"""Offline tests for the priority groups (`orch board set --priority`) and their summary in `orch brief render`, on
invented data.

Run: python3 test_board_priority.py
"""
import json, os, pathlib, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="test-board-priority-"))
env = {**os.environ, "ORCH_FLEET_STATE": str(tmp / "dashboard"), "ORCH_BRIEF_LAST": str(tmp / "brief-last"),
       "ORCH_FLEET_CONFIG": str(tmp / "none.json"), "AGENT_SKILLS_STATE": str(tmp / "state"), "TZ": "UTC",
       "AGENT_SKILLS_CONFIG": str(tmp / "none.json"), "PROGRAMS_HOME": str(tmp / "programs")}
os.environ.update(env)
sys.path.insert(0, str(HERE))
import store as db_store  # noqa: E402

db_store.import_apply(checks=False)
NOW = "2026-10-13T14:30:00Z"


def orch(*args):
    r = subprocess.run([sys.executable, str(HERE / "prog.py"), *args], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout.rstrip("\n"), r.stderr.strip()


def ok(*args):
    code, out, err = orch(*args)
    assert code == 0, (args, out, err)
    return out


def render(*extra):
    return ok("brief", "render", "--periodic", "--now", NOW, "--no-refresh", *extra)


# Three leads: alpha (a phase, a done sub-task and an open one), beta (one row), gamma (no group).
ok("board", "lead", "add", "alpha", "--handle", "term_0FAKE1", "--repo", "acme/web")
ok("board", "lead", "add", "beta", "--handle", "term_0FAKE2", "--repo", "acme/api")
ok("board", "lead", "add", "gamma", "--handle", "term_0FAKE3", "--repo", "acme/ops")
(tmp / "dashboard").mkdir(parents=True, exist_ok=True)
(tmp / "dashboard" / "state.json").write_text(json.dumps({"generated_at": NOW, "sessions": [
    {"id": s, "phase": "working", "terminals": [{"handle": h, "agent": "claude", "connected": True, "writable": True}]}
    for s, h in (("s1", "term_0FAKE1"), ("s2", "term_0FAKE2"), ("s3", "term_0FAKE3"))]}))
ok("board", "set", "alpha/t1", "--title", "첫 작업", "--status", "done")
ok("board", "set", "alpha/t2", "--title", "둘째 작업", "--next", "CI 확인")
ok("board", "set", "alpha/t3", "--title", "셋째 작업", "--status", "held", "--next", "t2 머지 뒤")
ok("board", "set", "beta", "--title", "베타 작업", "--status", "owner", "--next", "승인")

# No group recorded: no section, and the report is the one this code printed before the field existed.
plain = {"text": render(), "tg": render("--telegram"), "full": json.loads(render("--json"))["full"]}
assert "우선순위" not in plain["text"] + plain["tg"] + plain["full"], plain

# Refusals say the form.
for bad in ("high", "x:name", "1:", ":name", "1000:name"):
    code, _, err = orch("board", "set", "alpha", "--priority", bad)
    assert code == 1 and err.startswith("--priority is RANK:NAME"), (bad, err)

# A group on the lead covers its sub-tasks; a row's own group wins over its parent's.
ok("board", "set", "alpha", "--priority", "2:Group B")
ok("board", "set", "beta", "--priority", "1:Group A")
text = render()
lines = text.splitlines()
at = lines.index("## 🧭 우선순위")
summary = lines[at:at + 5]
assert summary[1] == "" and summary[2].startswith("- **Group A** — 진행 0 · 대기 1 · 완료 0 · 다음: ") and "승인" in summary[2], summary
assert summary[3].startswith("- **Group B** — 진행 1 · 대기 1 · 완료 1 · 다음: ") and "CI 확인" in summary[3], summary
assert summary[4] == "" or summary[4].startswith("## "), summary  # gamma is in no group, so no third line
# It is above the owner's list, under the headline and the guide lines; the headline keeps its place and form.
assert lines[0].startswith("**") and at < lines.index("## 🙋 지금 오너가 할 일"), text
assert all(l.startswith(("## ", "**", ">", "- ")) or not l for l in lines if "우선순위" in l or "Group" in l), text
ok("board", "set", "alpha/t3", "--priority", "1:Group A")
assert "- **Group A** — 진행 0 · 대기 2 · 완료 0" in render() and "- **Group B** — 진행 1 · 대기 0 · 완료 1" in render()

# Telegram: plain text, the same order. The dashboard's full copy carries the section too.
tg = render("--telegram")
assert "\n🧭 우선순위\n\nGroup A — 진행 0 · 대기 2 · 완료 0 · 다음: " in tg and "\nGroup B — 진행 1 · 대기 0 · 완료 1" in tg, tg
assert "**" not in tg.split("🧭 우선순위")[1].split("🙋")[0], tg
assert "## 🧭 우선순위" in json.loads(render("--json"))["full"]

# Everything else in the report is what it was without groups.
def without(t, tg=False):
    out, skip = [], False
    for l in t.splitlines():
        if l == ("🧭 우선순위" if tg else "## 🧭 우선순위"):
            skip = True
            continue
        if skip and (l.startswith("## ") or l.startswith("🙋") or l.startswith("📋")):
            skip = False
        if not skip:
            out.append(l)
    return "\n".join(out)
assert without(render()) == plain["text"] and without(render("--telegram"), True) == plain["tg"]

# Rank orders the groups, not the name; a group with only done rows says so; `-` clears.
ok("board", "set", "alpha", "--priority", "0:Zed")
ok("board", "set", "alpha/t3", "--priority", "-")
ok("board", "set", "beta", "--status", "done")
text = render()
assert text.index("**Zed**") < text.index("**Group A**") and "**Group A** — 진행 0 · 대기 0 · 완료 1 · 다음: 없음" in text, text
for rid in ("alpha", "beta"):
    ok("board", "set", rid, "--priority", "-")
assert "우선순위" not in render()

print("ok")
