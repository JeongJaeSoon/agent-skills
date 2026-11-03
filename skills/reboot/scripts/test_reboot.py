#!/usr/bin/env python3
import pathlib, sys, tempfile, unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import reboot  # noqa: E402

ROOT = "/work/app"
ME = "term_me"
CLAUDE = 100
SELF = 900  # the check itself, run from a Bash call of the session


def table(*procs):
    """A ps table: the session's claude, the shell running this check, and procs as (pid, ppid, command)."""
    rows = {1: (0, "/sbin/launchd"), CLAUDE: (1, "claude --continue"), 800: (CLAUDE, "/bin/zsh -c python3 reboot.py"),
            SELF: (800, "python3 reboot.py")}
    rows.update({pid: (ppid, cmd) for pid, ppid, cmd in procs})
    return rows


def wait_proc(pid, cmd):
    """A background task as Claude Code starts it: a shell wrapping the command, the python it runs."""
    return [(pid, CLAUDE, f"/bin/zsh -c source snap.sh && eval '{cmd}'"),
            (pid + 1, pid, f"/usr/bin/python3 /x/bin/../skills/orchestrate/scripts/prog.py {cmd[5:]}")]


class Orca:
    """Answers `orca <argv> --json` from a fixture: handles that are live, a Run binding, workers."""

    def __init__(self, live=(ME,), run=None, workers=(), title="orchestrator", here=()):
        self.live, self.run, self.workers, self.title, self.here = set(live), run, list(workers), title, list(here)

    def __call__(self, *argv):
        if argv[:2] == ("terminal", "show"):
            h = argv[argv.index("--terminal") + 1]
            if h not in self.live:
                raise reboot.OrcaError("terminal_handle_stale")
            return {"terminal": {"handle": h, "title": self.title, "worktreePath": ROOT}}
        if argv[:2] == ("terminal", "list"):
            return {"terminals": self.here}
        if argv[:2] == ("orchestration", "run-current"):
            return {"run": {"id": self.run} if self.run else None}
        if argv[:2] == ("orchestration", "worker-list"):
            return {"workers": self.workers}
        raise AssertionError(f"unexpected orca call {argv}")


def run(role=None, slug=None, run_id=None, handle=ME, orca=None, procs=(), queue=None):
    orig = reboot.worktree_root
    reboot.worktree_root = lambda cwd: ROOT
    try:
        return reboot.run_checks(role, slug, run_id, handle, ROOT, call=orca or Orca(), table=table(*procs), pid=SELF,
                                 queue=lambda: queue or {})
    finally:
        reboot.worktree_root = orig


def does(lines):
    return [t for k, t in lines if k == "do"]


class Waits(unittest.TestCase):
    def test_none_running_lead_is_told_to_start_its_wait(self):
        out = does(run("lead", "acme", "run_1", orca=Orca(run="run_1")))
        self.assertEqual(out, ["no inbox wait runs: start `orch wait acme` under run_in_background"])

    def test_none_running_orchestrator_needs_inbox_and_card_watch(self):
        out = does(run("top", run_id="run_1", orca=Orca(run="run_1")))
        self.assertEqual(len(out), 2)
        self.assertIn("orch inbox --wait", out[0])
        self.assertIn("orch watch-cards", out[1])

    def test_two_inbox_waits_name_the_extra_python_pid_once(self):
        procs = wait_proc(200, "orch wait acme") + wait_proc(300, "orch wait acme")
        out = does(run("lead", "acme", "run_1", orca=Orca(run="run_1"), procs=procs))
        self.assertEqual(len(out), 1)
        self.assertIn("2 inbox waits", out[0])
        self.assertIn("pid 301", out[0])
        self.assertNotIn("pid 300", out[0])

    def test_two_card_watches_are_reported(self):
        procs = wait_proc(200, "orch inbox --wait") + wait_proc(300, "orch watch-cards") + wait_proc(400, "orch watch-cards")
        out = does(run("top", run_id="run_1", orca=Orca(run="run_1"), procs=procs))
        self.assertEqual(len(out), 1)
        self.assertIn("2 `orch watch-cards`", out[0])

    def test_one_of_each_is_clean(self):
        procs = wait_proc(200, "orch inbox --wait") + wait_proc(300, "orch watch-cards")
        self.assertEqual(does(run("top", run_id="run_1", orca=Orca(run="run_1"), procs=procs)), [])

    def test_another_sessions_wait_does_not_count(self):
        other = [(500, 1, "claude"), (501, 500, "/bin/zsh -c orch wait acme"), (502, 501, "python3 prog.py wait acme")]
        out = does(run("lead", "acme", "run_1", orca=Orca(run="run_1"), procs=other))
        self.assertEqual(out, ["no inbox wait runs: start `orch wait acme` under run_in_background"])

    def test_a_one_shot_card_watch_is_not_a_watcher(self):
        procs = wait_proc(200, "orch inbox --wait") + wait_proc(300, "orch watch-cards --once")
        out = does(run("top", run_id="run_1", orca=Orca(run="run_1"), procs=procs))
        self.assertEqual(len(out), 1)
        self.assertIn("no `orch watch-cards`", out[0])

    def test_a_worker_is_not_asked_to_wait(self):
        self.assertEqual(does(run()), [])


class Handle(unittest.TestCase):
    def test_stale_handle_names_the_live_terminals_of_this_worktree(self):
        here = [{"handle": "term_new", "title": "lead", "worktreePath": ROOT},
                {"handle": "term_else", "title": "x", "worktreePath": "/work/other"}]
        out = does(run("lead", "acme", "run_1", orca=Orca(live=("term_new",), here=here)))
        self.assertEqual(len(out), 2)  # the stale handle, and the wait; no Run or worker check speaks for a dead terminal
        self.assertIn("term_me is stale", out[0])
        self.assertIn("term_new (lead)", out[0])
        self.assertNotIn("term_else", out[0])

    def test_no_orca_terminal_skips_orca_checks(self):
        lines = run(handle=None, orca=Orca(live=()))
        self.assertEqual([k for k, _ in lines], ["info"])

    def test_orchestrator_tab_title(self):
        procs = wait_proc(200, "orch inbox --wait") + wait_proc(300, "orch watch-cards")
        out = does(run("top", run_id="run_1", orca=Orca(run="run_1", title="claude"), procs=procs))
        self.assertEqual(out, [f"`orca terminal rename --terminal {ME} --title orchestrator`"])


class Run(unittest.TestCase):
    def test_unbound_lead_gets_run_use_with_the_program_run(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "acme"
            p.mkdir()
            (p / "program.json").write_text('{"slug": "acme", "run": "run_9"}')
            orig, reboot.PROGRAMS = reboot.PROGRAMS, pathlib.Path(d)
            try:
                out = does(run("lead", "acme", procs=wait_proc(200, "orch wait acme")))
            finally:
                reboot.PROGRAMS = orig
        self.assertEqual(len(out), 1)
        self.assertIn("run-use --id run_9", out[0])

    def test_bound_to_another_run(self):
        out = does(run("lead", "acme", "run_9", orca=Orca(run="run_1"), procs=wait_proc(200, "orch wait acme")))
        self.assertIn("bound to Run run_1, not run_9", out[0])


class Workers(unittest.TestCase):
    def test_worker_with_a_stale_terminal_gets_the_retry_form(self):
        workers = [{"dispatchId": "d1", "taskId": "t1", "terminalState": "active", "agentTerminalHandle": "term_dead",
                    "resource": {"worktreeId": "repo::/work/card"}, "projection": {"provider": {"id": "claude"}}},
                   {"dispatchId": "d2", "taskId": "t2", "terminalState": "active", "agentTerminalHandle": "term_ok"},
                   {"dispatchId": "d3", "taskId": "t3", "terminalState": "reclaimable", "agentTerminalHandle": "term_gone"}]
        orca = Orca(live=(ME, "term_ok"), run="run_1", workers=workers)
        out = does(run("lead", "acme", "run_1", orca=orca, procs=wait_proc(200, "orch wait acme")))
        self.assertEqual(len(out), 1)
        self.assertIn("worker d1", out[0])
        self.assertIn("--retry-of d1 --task t1 --worktree id:repo::/work/card --agent claude", out[0])


class Queue(unittest.TestCase):
    def test_entries_of_this_worktree_only(self):
        q = {"q-1": {"id": "q-1", "cwd": ROOT + "/sub", "state": "waiting", "what": "orch heavy", "added": 1},
             "q-2": {"id": "q-2", "cwd": "/work/app-other", "state": "waiting", "what": "x", "added": 2},
             "q-3": {"id": "q-3", "cwd": ROOT, "state": "done", "what": "y", "added": 3},
             "q-4": {"id": "q-4", "cwd": ROOT, "state": "gave_up", "what": "docker compose up", "added": 4}}
        out = does(run(queue=q))
        self.assertEqual(len(out), 2)
        self.assertIn("queue wait q-1", out[0])
        self.assertIn("q-4", out[1])
        self.assertIn("never ran", out[1])


class Cli(unittest.TestCase):
    def test_slug_and_top_together_are_refused(self):
        self.assertEqual(reboot.main(["--slug", "acme", "--top"]), 2)


if __name__ == "__main__":
    unittest.main()
