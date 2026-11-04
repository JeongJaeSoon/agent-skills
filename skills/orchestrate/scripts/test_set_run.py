"""`orch set <slug> run <run_id>` moves a program to the Run this terminal coordinates. Run: python3 test_set_run.py"""
import contextlib, io, json, os, pathlib, sys, tempfile

tmp = pathlib.Path(tempfile.mkdtemp())
os.environ["PROGRAMS_HOME"] = str(tmp)
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import prog  # noqa: E402

bound = {"id": None}
prog.orca_json = lambda *a: {"runId": bound["id"]} if a[:2] == ("orchestration", "run-current") else {}


def fresh(**extra):
    d = tmp / "acme"
    d.mkdir(exist_ok=True)
    (d / "ledger.jsonl").write_text("")
    (d / "program.json").write_text(json.dumps({"slug": "acme", "run": "run_old", "repo": "o/r", **extra}))
    return d


def cfg(d):
    return json.loads((d / "program.json").read_text())


def set_run(value):
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            prog.cmd_set(["acme", "run", value])
        return 0, out.getvalue()
    except SystemExit as e:
        return e.code, str(e.code)


# A dispatched lead that lost its Dispatch is bound to its own new Run: the program moves there and sheds lead_dispatch.
d = fresh(lead_dispatch="ctx_gone")
bound["id"] = "run_new"
code, said = set_run("run_new")
assert code == 0, said
assert cfg(d)["run"] == "run_new" and "lead_dispatch" not in cfg(d), cfg(d)
ledger = [json.loads(l) for l in (d / "ledger.jsonl").read_text().splitlines()]
assert ledger and "run_old" in ledger[-1]["note"] and "run_new" in ledger[-1]["note"], ledger

# A Run this terminal is not bound to is refused and nothing changes: the program's mail would go to a Run nobody reads.
d = fresh(lead_dispatch="ctx_gone")
code, said = set_run("run_other")
assert code != 0 and "not run_other" in said, (code, said)
assert cfg(d)["run"] == "run_old" and cfg(d)["lead_dispatch"] == "ctx_gone", cfg(d)

# An unbound terminal is refused the same way.
bound["id"] = None
code, said = set_run("run_new")
assert code != 0 and "not bound to any Run" in said, (code, said)

# A terminal that is a worker of the Run it names keeps lead_dispatch: it is still a dispatched lead.
bound["id"] = "run_new"
d = fresh(lead_dispatch="ctx_live")
prog.own_worker = lambda workers, me: {"dispatchId": "ctx_live"}
prog.all_workers = lambda run_id: [{"dispatchId": "ctx_live"}]
os.environ["ORCA_TERMINAL_HANDLE"] = "term_me"
code, said = set_run("run_new")
assert code != 0 and "dispatched" in said, (code, said)
assert cfg(d)["lead_dispatch"] == "ctx_live", cfg(d)

print("orch set run: all pass")
