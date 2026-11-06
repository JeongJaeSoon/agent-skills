#!/usr/bin/env python3
import json, os, pathlib, sys, tempfile, time, unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import reboot  # noqa: E402
import roles  # noqa: E402  on sys.path through reboot
import fleet  # noqa: E402  the sessions Claude Code registers are read there
os.environ["AGENT_SKILLS_STATE"] = tempfile.mkdtemp()  # the SessionStart record is read from here, never the real one
import rowstate  # noqa: E402  on sys.path through reboot

ROOT = "/work/app"
ME = "term_me"
CLAUDE = 100
SELF = 900  # the check itself, run from a Bash call of the session
PAST = rowstate.QUEUE_ENDED_H * 3600 + 60


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

    def __init__(self, live=(ME,), run=None, workers=(), title="orchestrator", here=(), concise=False, runs=()):
        self.live, self.run, self.workers, self.title, self.here = set(live), run, list(workers), title, list(here)
        self.runs, self.runs_fail, self.list_fails, self.truncated = list(runs), False, False, False
        self.concise = concise

    def __call__(self, *argv):
        if argv[:2] == ("terminal", "show"):
            h = argv[argv.index("--terminal") + 1]
            if h not in self.live:
                raise reboot.OrcaError("terminal_handle_stale")
            return {"terminal": {"handle": h, "title": self.title, "worktreePath": ROOT}}
        if argv[:2] == ("terminal", "list") and self.list_fails:
            raise reboot.OrcaError("no_answer")
        if argv[:2] == ("terminal", "list"):
            return {"terminals": self.here, "truncated": self.truncated}
        if argv[:2] == ("orchestration", "run-current"):
            return {"runId": self.run} if self.concise else {"run": {"id": self.run} if self.run else None}
        if argv[:2] == ("orchestration", "run-list"):
            if self.runs_fail:
                raise reboot.OrcaError("no_answer")
            first = "--cursor" not in argv  # two runs a page, so a third Run is on page two
            chunk = self.runs[:2] if first else self.runs[2:]
            return {"runs": [{"id": r} for r in chunk], "page": {"hasMore": first and len(self.runs) > 2, "nextCursor": "c2"}}
        if argv[:2] == ("orchestration", "worker-list"):
            state = argv[argv.index("--terminal-state") + 1] if "--terminal-state" in argv else None
            run = argv[argv.index("--run") + 1] if "--run" in argv else self.run  # unscoped = this terminal's Run
            return {"workers": [w for w in self.workers if (not state or w.get("terminalState") == state)
                                and (not run or w.get("runId", run) == run)]}
        raise AssertionError(f"unexpected orca call {argv}")


def run(role=None, slug=None, run_id=None, handle=ME, orca=None, procs=(), queue=None, reload_root=None, record=None):
    """record: the session record own_record finds (None: not found), so a test never reads the live session's."""
    orig = reboot.worktree_root
    reboot.worktree_root = lambda cwd: ROOT
    try:
        with mock.patch.object(reboot, "own_record", return_value=record):
            return reboot.run_checks(role, slug, run_id, handle, ROOT, call=orca or Orca(), table=table(*procs), pid=SELF,
                                     queue=lambda: queue or {}, reload_root=reload_root)
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
        self.assertIn("start `orch inbox --stream` with the Monitor tool", out[0])
        self.assertIn("orch watch-cards", out[1])

    def test_a_stream_counts_with_the_card_watch_it_runs_as_its_child(self):
        procs = wait_proc(200, "orch inbox --stream") + [(202, 201, "/usr/bin/python3 /x/prog.py watch-cards --self t")]
        lines = run("top", run_id="run_1", orca=Orca(run="run_1"), procs=procs)
        self.assertEqual(does(lines), [])
        self.assertIn(("ok", "1 inbox wait running"), lines)
        self.assertIn(("ok", "1 orch watch-cards running"), lines)

    def test_a_stream_between_card_rounds_is_not_told_to_start_a_watch(self):
        lines = run("top", run_id="run_1", orca=Orca(run="run_1"), procs=wait_proc(200, "orch inbox --stream"))
        self.assertEqual(does(lines), [])
        self.assertTrue(any(k == "info" and "starts its own card watch" in t for k, t in lines), lines)

    def test_the_old_wait_still_counts_and_is_told_to_switch(self):
        procs = wait_proc(200, "orch inbox --wait") + wait_proc(300, "orch watch-cards")
        lines = run("top", run_id="run_1", orca=Orca(run="run_1"), procs=procs)
        self.assertEqual(does(lines), [])
        self.assertTrue(any(k == "info" and "orch inbox --stream" in t for k, t in lines), lines)

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

    def test_a_lead_waiting_on_another_program_is_told_to_replace_it(self):
        out = does(run("lead", "acme", "run_1", orca=Orca(run="run_1"), procs=wait_proc(200, "orch wait other")))
        self.assertEqual(len(out), 1)
        self.assertIn("stop pid 201", out[0])
        self.assertIn("then start `orch wait acme`", out[0])

    def test_a_lead_with_its_own_wait_and_a_stray_one_stops_only_the_stray(self):
        procs = wait_proc(200, "orch wait acme") + wait_proc(300, "orch inbox --wait")
        out = does(run("lead", "acme", "run_1", orca=Orca(run="run_1"), procs=procs))
        self.assertEqual(len(out), 1)
        self.assertIn("stop pid 301", out[0])
        self.assertNotIn("then start", out[0])

    def test_an_orchestrator_waiting_on_a_program_is_told_to_replace_it(self):
        procs = wait_proc(200, "orch wait acme") + wait_proc(300, "orch watch-cards")
        out = does(run("top", run_id="run_1", orca=Orca(run="run_1"), procs=procs))
        self.assertEqual(len(out), 1)
        self.assertIn("stop pid 201", out[0])
        self.assertIn("then start `orch inbox --stream` with the Monitor tool", out[0])

    def test_a_worker_is_not_asked_to_wait(self):
        self.assertEqual(does(run()), [])


class Handoff(unittest.TestCase):
    def test_lists_the_handoff_and_the_mail_taken_after_it(self):
        mail = {"messages": [{"id": "m1", "type": "status", "created_at": "2026-01-01T00:00:00Z", "subject": "old"},
                             {"id": "m2", "type": "worker_done", "created_at": "2026-01-02T00:00:00Z", "subject": "done",
                              "from_handle": "dispatch:d1"},
                             {"id": "m3", "type": "heartbeat", "created_at": "2026-01-02T00:00:01Z"}]}
        seen = []
        call = lambda *a: (seen.append(a), mail)[1]  # noqa: E731
        lines = reboot.check_handoff("orchestrator", "run_1", call,
                                     last=("2026-01-01T12:00:00Z", {"next": "land acme/app#1", "handle": "term_x"}))
        self.assertEqual(seen[0][:4], ("orchestration", "inbox", "--terminal", "run:run_1"))
        self.assertTrue(lines[0][1].startswith("handoff 2026-01-01T12:00:00Z: next=land acme/app#1"), lines)
        self.assertNotIn("term_x", lines[0][1])
        self.assertEqual([t for _, t in lines[1:]],
                         ["mail since the handoff: m2 worker_done from dispatch:d1: done (handled before the restart? "
                          "check before acting twice)"])

    def test_no_handoff_is_info_only(self):
        self.assertEqual(reboot.check_handoff("orchestrator", None, last=()), [("info", "no handoff recorded for orchestrator")])


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

    def test_spinner_prefix_on_the_tab_title_is_ignored(self):
        procs = wait_proc(200, "orch inbox --stream") + wait_proc(300, "orch watch-cards")
        for title in ("◐ orchestrator", "◑ orchestrator", "✳ orchestrator", "⠐ orchestrator"):
            out = does(run("top", run_id="run_1", orca=Orca(run="run_1", title=title), procs=procs))
            self.assertEqual(out, [], title)
        self.assertEqual(reboot.plain_title("◐ orchestrator-2"), "orchestrator-2")
        self.assertEqual(reboot.plain_title("✳️ orchestrator"), "orchestrator")
        # a symbol that is not a status glyph is part of the title
        for title in ("★ orchestrator", "+ orchestrator"):
            self.assertEqual(reboot.plain_title(title), title)
            out = does(run("top", run_id="run_1", orca=Orca(run="run_1", title=title), procs=procs))
            self.assertEqual(len(out), 1, title)

    def test_session_not_named_orchestrator_gets_a_rename_line(self):
        procs = wait_proc(200, "orch inbox --stream") + wait_proc(300, "orch watch-cards")
        for record, expect in (({"name": "mailbox"}, 1), ({"name": None}, 1), ({"name": "orchestrator"}, 0), ({}, 0), (None, 0)):
            out = does(run("top", run_id="run_1", orca=Orca(run="run_1"), procs=procs, record=record))
            self.assertEqual(len(out), expect, record)
            if expect:
                self.assertIn("`/rename orchestrator`", out[0])
        # a lead is not the orchestrator
        self.assertEqual(does(run("lead", "acme", "run_1", orca=Orca(run="run_1", title="lead"),
                                  procs=wait_proc(200, "orch wait acme"), record={"name": "mailbox"})), [])


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

    def test_concise_answer_is_bound(self):
        lines = run("lead", "acme", "run_1", orca=Orca(run="run_1", concise=True), procs=wait_proc(200, "orch wait acme"))
        self.assertIn(("ok", "bound to Run run_1"), lines)
        self.assertEqual(does(lines), [])

    def test_bound_to_another_run_checks_no_workers_there(self):
        workers = [{"dispatchId": "d_other", "taskId": "t", "runId": "run_1", "terminalState": "active",
                    "agentTerminalHandle": "term_dead"}]
        out = does(run("lead", "acme", "run_9", orca=Orca(run="run_1", workers=workers),
                       procs=wait_proc(200, "orch wait acme")))
        self.assertEqual(len(out), 1)
        self.assertIn("bound to Run run_1, not run_9", out[0])
        self.assertIn("run this check again", out[0])


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
        self.assertIn("--retry-of d1 --task t1 --worktree id:repo::/work/card --agent claude`", out[0])
        self.assertIn("add `--model <id>`", out[0])  # a retry does not inherit the model

    def test_an_active_worker_with_no_terminal_is_shown_not_restarted(self):
        workers = [{"dispatchId": "d1", "taskId": "t1", "terminalState": "active"},
                   {"dispatchId": "d2", "taskId": "t2", "terminalState": "active", "agentTerminalHandle": None}]
        lines = run("lead", "acme", "run_1", orca=Orca(run="run_1", workers=workers), procs=wait_proc(200, "orch wait acme"))
        self.assertEqual(does(lines), [])  # a worker may run without a terminal: no stop or retry on that alone
        infos = [t for k, t in lines if k == "info" and "worker" in t]
        self.assertEqual(len(infos), 2)
        self.assertIn("worker-show --dispatch d1", infos[0])
        self.assertIn("has no terminal on record", infos[1])

    def test_a_card_path_with_a_space_stays_one_argument(self):
        workers = [{"dispatchId": "d1", "taskId": "t1", "terminalState": "active", "agentTerminalHandle": "term_dead",
                    "resource": {"worktreeId": "repo::/work/My Project"}, "projection": {"provider": {"id": "claude"}}}]
        out = does(run("lead", "acme", "run_1", orca=Orca(run="run_1", workers=workers),
                       procs=wait_proc(200, "orch wait acme")))
        self.assertIn("--worktree 'id:repo::/work/My Project'", out[0])

    def test_the_orchestrator_routes_a_leads_worker_to_that_lead(self):
        workers = [{"dispatchId": "d_lead", "taskId": "t_lead", "terminalState": "active", "agentTerminalHandle": "term_lead"},
                   {"dispatchId": "d_kid", "taskId": "t_kid", "terminalState": "active", "agentTerminalHandle": "term_dead",
                    "projection": {"parent": {"taskId": "t_lead"}}}]
        procs = wait_proc(200, "orch inbox --wait") + wait_proc(300, "orch watch-cards")
        out = does(run("top", run_id="run_1", orca=Orca(live=(ME, "term_lead"), run="run_1", workers=workers), procs=procs))
        self.assertEqual(len(out), 1)
        self.assertIn("sits under d_lead", out[0])
        self.assertNotIn("worker-stop", out[0])

    def test_the_recorded_model_is_named_again(self):
        workers = [{"dispatchId": "d1", "taskId": "t1", "terminalState": "active", "agentTerminalHandle": "term_dead",
                    "projection": {"provider": {"id": "claude", "model": "claude-opus-5-5"}}}]
        out = does(run("lead", "acme", "run_1", orca=Orca(run="run_1", workers=workers),
                       procs=wait_proc(200, "orch wait acme")))
        self.assertIn("--agent claude --model claude-opus-5-5`", out[0])

    def test_dispatched_lead_binds_nothing_and_checks_only_its_own_workers(self):
        def row(d, task, handle, parent):
            return {"dispatchId": d, "taskId": task, "runId": "run_p", "terminalState": "active",
                    "agentTerminalHandle": handle, "projection": {"parent": {"taskId": parent}}}
        # retained: a person typed into the lead's terminal. Settled, it is still a dispatched lead: run-use on its
        # coordinator's Run would take that Run's binding away.
        for lead_state, status in (("active", "dispatched"), ("retained", "dispatched"), ("retained", "completed")):
            workers = [dict(row("d_lead", "t_lead", ME, "t_coord"), terminalState=lead_state, dispatchStatus=status),
                       row("d_mine", "t_mine", "term_dead1", "t_lead"),
                       row("d_other", "t_other", "term_dead2", "t_coord")]
            out = does(run("lead", "acme", orca=Orca(run=None, workers=workers), procs=wait_proc(200, "orch wait acme")))
            self.assertEqual(len(out), 1, lead_state)  # no run-use, and the coordinator's other worker is not ours
            self.assertIn("worker d_mine", out[0])
            self.assertIn("worker-stop", out[0])


class Restarted(unittest.TestCase):
    """Orca marks the Dispatches whose terminals a restart took abandoned; they are what the check must bring back."""

    def row(self, d, status, state="retained", handle="term_dead", **extra):
        return {"dispatchId": d, "taskId": "t_" + d, "terminalState": state, "dispatchStatus": status,
                "agentTerminalHandle": handle, "resource": {"worktreeId": "repo::/work/My Card"},
                "projection": {"provider": {"id": "claude", "model": "opus"}}, **extra}

    def lines(self, *workers):
        return run("lead", "acme", "run_1", orca=Orca(run="run_1", workers=workers), procs=wait_proc(200, "orch wait acme"))

    def test_an_abandoned_worker_with_a_dead_terminal_is_a_recovery_target(self):
        for state in ("active", "retained", "reclaimable"):
            out = does(self.lines(self.row("d1", "abandoned", state)))
            self.assertEqual(len(out), 1, state)
            self.assertIn("worker d1", out[0])
            self.assertIn("abandoned", out[0])
            self.assertIn("orca terminal create --worktree 'id:repo::/work/My Card'", out[0])
            self.assertIn("claude --resume <session-id> --model opus", out[0])
            self.assertIn("worker-start --spec-file <resume instruction> --worktree 'id:repo::/work/My Card' --terminal", out[0])
            self.assertNotIn("worker-stop", out[0])  # it is already settled: no stop, and no retry-of

    def test_the_worktree_id_is_kept_whole_so_a_shared_path_picks_the_right_workspace(self):
        w = self.row("d1", "abandoned")
        w["resource"]["worktreeId"] = "repo::/work/card::workspace:1"
        out = does(self.lines(w))[0]
        self.assertEqual(out.count("--worktree id:repo::/work/card::workspace:1 "), 2)

    def test_a_worktree_with_a_live_agent_terminal_is_not_resumed_again(self):
        # Resumed from a lead on another Run: this Run keeps only the abandoned row, the worktree has the live agent.
        live = [{"handle": "term_new", "agentIdentity": "claude", "worktreePath": "/work/My Card"}]
        other_run = self.row("d_new", "dispatched", "active", handle="term_new", runId="run_other")
        other_run["resource"] = {"worktreeId": "repo::/work/My Card::workspace:2"}
        orca = Orca(run="run_1", workers=[self.row("d1", "abandoned"), other_run], here=live, runs=("run_1", "run_other"))
        lines = run("lead", "acme", "run_1", orca=orca, procs=wait_proc(200, "orch wait acme"))
        self.assertEqual(does(lines), [])
        self.assertTrue([t for k, t in lines if k == "info" and "d1" in t and "live agent terminal" in t])

    def test_the_dispatch_on_a_later_page_of_runs_is_found(self):
        live = [{"handle": "term_new", "agentIdentity": "claude"}]
        other_run = self.row("d_new", "dispatched", "active", handle="term_new", runId="run_c")
        other_run["resource"] = {"worktreeId": "repo::/elsewhere"}
        orca = Orca(run="run_1", workers=[self.row("d1", "abandoned"), other_run], here=live, runs=("run_1", "run_b", "run_c"))
        self.assertEqual(does(run("lead", "acme", "run_1", orca=orca, procs=wait_proc(200, "orch wait acme"))), [])

    def test_an_earlier_finished_dispatch_on_the_terminal_is_no_proof_of_a_resume(self):
        live = [{"handle": "term_new", "agentIdentity": "claude"}]
        done = self.row("d_done", "completed", "retained", handle="term_new")
        done["resource"] = {"worktreeId": "repo::/elsewhere"}
        orca = Orca(run="run_1", workers=[self.row("d1", "abandoned"), done], here=live, runs=("run_1",))
        out = does(run("lead", "acme", "run_1", orca=orca, procs=wait_proc(200, "orch wait acme")))
        self.assertEqual(len(out), 1)
        self.assertIn("--terminal term_new", out[0])

    def test_a_dispatch_that_reported_after_the_abandoned_one_proves_a_resume(self):
        live = [{"handle": "term_new", "agentIdentity": "claude"}]
        old = self.row("d1", "abandoned", projection={"evidence": {"lastObservedAt": 100}})
        for stamp, expect in ((200, 0), (50, 1)):  # resumed and finished on another Run / an older worker of the terminal
            done = self.row("d_done", "completed", "retained", handle="term_new", projection={"evidence": {"lastObservedAt": stamp}})
            done["resource"] = {"worktreeId": "repo::/elsewhere"}
            orca = Orca(run="run_1", workers=[old, done], here=live, runs=("run_1",))
            out = does(run("lead", "acme", "run_1", orca=orca, procs=wait_proc(200, "orch wait acme")))
            self.assertEqual(len(out), expect, stamp)

    def test_a_diagnosis_terminal_beside_the_resumed_worker_does_not_hide_it(self):
        for order in (("term_diag", "term_new"), ("term_new", "term_diag")):
            live = [{"handle": h, "agentIdentity": "claude"} for h in order]
            other = self.row("d_new", "dispatched", "active", handle="term_new", runId="run_other")
            other["resource"] = {"worktreeId": "repo::/elsewhere"}
            orca = Orca(run="run_1", workers=[self.row("d1", "abandoned"), other], here=live, runs=("run_1", "run_other"))
            self.assertEqual(does(run("lead", "acme", "run_1", orca=orca, procs=wait_proc(200, "orch wait acme"))), [], order)

    def test_a_worker_resumed_elsewhere_and_finished_with_its_terminal_closed_is_not_woken(self):
        old = self.row("d1", "abandoned", projection={"evidence": {"lastObservedAt": 100}})
        done = self.row("d_done", "completed", "released", handle=None, runId="run_other",
                        projection={"evidence": {"lastObservedAt": 200}})
        orca = Orca(run="run_1", workers=[old, done], here=[], runs=("run_1", "run_other"))
        lines = run("lead", "acme", "run_1", orca=orca, procs=wait_proc(200, "orch wait acme"))
        self.assertEqual(does(lines), [])

    def test_two_agent_terminals_without_a_dispatch_give_one_command_with_a_placeholder(self):
        live = [{"handle": h, "agentIdentity": "claude"} for h in ("term_diag", "term_resume")]
        orca = Orca(run="run_1", workers=[self.row("d1", "abandoned")], here=live, runs=("run_1",))
        out = does(run("lead", "acme", "run_1", orca=orca, procs=wait_proc(200, "orch wait acme")))
        self.assertEqual(len(out), 1)
        self.assertIn("--terminal <the handle of the worker's own terminal: term_diag, term_resume>", out[0])

    def test_a_cut_short_terminal_list_issues_no_recovery_command(self):
        orca = Orca(run="run_1", workers=[self.row("d1", "abandoned")], here=[])
        orca.truncated = True
        with self.assertRaises(reboot.OrcaError):
            run("lead", "acme", "run_1", orca=orca, procs=wait_proc(200, "orch wait acme"))

    def test_an_unreadable_terminal_or_run_list_ends_the_check_with_orca_not_answering(self):
        live = [{"handle": "term_new", "agentIdentity": "claude"}]
        for attr in ("list_fails", "runs_fail"):
            orca = Orca(run="run_1", workers=[self.row("d1", "abandoned")], here=live)
            setattr(orca, attr, True)
            with self.assertRaises(reboot.OrcaError, msg=attr):  # main() turns it into exit 2
                run("lead", "acme", "run_1", orca=orca, procs=wait_proc(200, "orch wait acme"))

    def test_a_released_abandoned_worker_is_listed_not_hidden(self):
        lines = self.lines(self.row("d1", "abandoned", "released"))
        self.assertEqual(does(lines), [])
        self.assertTrue([t for k, t in lines if k == "info" and "d1" in t and "released" in t])

    def test_a_resumed_session_with_no_dispatch_yet_is_told_to_get_one(self):
        # The run stopped between the terminal and `worker-start --terminal`: the session runs but has no mailbox.
        live = [{"handle": "term_new", "agentIdentity": "claude", "worktreePath": "/work/My Card"}]
        orca = Orca(run="run_1", workers=[self.row("d1", "abandoned")], here=live)
        out = does(run("lead", "acme", "run_1", orca=orca, procs=wait_proc(200, "orch wait acme")))
        self.assertEqual(len(out), 1)
        self.assertIn("worker-start --spec-file <resume instruction> --worktree 'id:repo::/work/My Card' --terminal term_new", out[0])
        self.assertNotIn("terminal create", out[0])

    def test_the_resume_command_follows_the_workers_agent(self):
        def line(agent):
            w = self.row("d1", "abandoned")
            w["projection"]["provider"] = {"id": agent, "model": "m1"}
            return does(self.lines(w))[0]
        self.assertIn('"codex resume <session-id> -m m1"', line("codex"))
        self.assertNotIn("claude --resume", line("codex"))
        other = line("gemini")
        self.assertNotIn("claude --resume", other)
        self.assertIn("gemini", other)

    def test_an_abandoned_worker_with_no_terminal_on_record_is_one_too(self):
        out = does(self.lines(self.row("d1", "abandoned", handle=None)))
        self.assertEqual(len(out), 1)
        self.assertIn("worker d1", out[0])

    def test_an_abandoned_worker_whose_terminal_is_live_is_left_alone(self):
        orca = Orca(live=(ME, "term_ok"), run="run_1", workers=[self.row("d1", "abandoned", handle="term_ok")])
        out = run("lead", "acme", "run_1", orca=orca, procs=wait_proc(200, "orch wait acme"))
        self.assertEqual(does(out), [])

    def test_settled_workers_are_not_woken_and_are_told_apart(self):
        lines = self.lines(self.row("d1", "completed", "active"), self.row("d2", "failed", "retained"),
                           self.row("d3", "completed", "released"))
        self.assertEqual(does(lines), [])
        infos = [t for k, t in lines if k == "info" and "finished" in t]
        self.assertEqual(len(infos), 1)
        self.assertIn("2 workers", infos[0])  # the released one is gone for good and is not counted

    def test_a_resumed_worker_is_not_woken_again(self):  # worker-list is newest first
        old = self.row("d_old", "abandoned")
        for status in ("dispatched", "completed"):
            new = self.row("d_new", status, "active", handle="term_ok")
            orca = Orca(live=(ME, "term_ok"), run="run_1", workers=[new, old])
            out = run("lead", "acme", "run_1", orca=orca, procs=wait_proc(200, "orch wait acme"))
            self.assertEqual(does(out), [], status)

    def test_an_abandoned_worker_under_another_lead_goes_to_that_lead(self):
        lead = {"dispatchId": "d_lead", "taskId": "t_lead", "terminalState": "active", "agentTerminalHandle": "term_lead"}
        kid = self.row("d_kid", "abandoned", projection={"parent": {"taskId": "t_lead"}})
        procs = wait_proc(200, "orch inbox --wait") + wait_proc(300, "orch watch-cards")
        out = does(run("top", run_id="run_1", orca=Orca(live=(ME, "term_lead"), run="run_1", workers=[kid, lead]), procs=procs))
        self.assertEqual(len(out), 1)
        self.assertIn("sits under d_lead", out[0])
        self.assertNotIn("terminal create", out[0])

    def test_an_abandoned_worker_is_not_an_ok_line(self):
        lines = self.lines(self.row("d1", "abandoned"))
        self.assertFalse([t for k, t in lines if k == "ok" and "has a live terminal" in t])


class LostDispatch(unittest.TestCase):
    def program(self, **cfg):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = pathlib.Path(d.name) / "acme"
        p.mkdir()
        (p / "program.json").write_text(json.dumps({"slug": "acme", "run": "run_parent", **cfg}))
        orig, reboot.PROGRAMS = reboot.PROGRAMS, pathlib.Path(d.name)
        self.addCleanup(setattr, reboot, "PROGRAMS", orig)

    def test_a_dispatched_lead_that_lost_its_dispatch_is_not_sent_to_its_parents_run(self):
        self.program(lead_dispatch="ctx_gone")
        out = does(run("lead", "acme", procs=wait_proc(200, "orch wait acme")))
        self.assertEqual(len(out), 1)
        self.assertNotIn("run-use", out[0])
        self.assertIn("orca orchestration run-create --objective", out[0])
        self.assertIn("orch set acme run <the new Run>", out[0])
        self.assertIn("ctx_gone", out[0])

    def test_a_lead_already_bound_to_its_own_run_is_told_to_set_the_program_there(self):
        self.program(lead_dispatch="ctx_gone")  # run-create done, the session ended before `orch set`
        out = does(run("lead", "acme", orca=Orca(run="run_own"), procs=wait_proc(200, "orch wait acme")))
        self.assertEqual(len(out), 1)
        self.assertNotIn("run-use", out[0])
        self.assertIn("orch set acme run run_own", out[0])

    def test_a_lead_resumed_under_a_new_dispatch_is_not_ok_while_the_program_names_the_old_one(self):
        self.program(lead_dispatch="ctx_old")
        new = {"dispatchId": "ctx_new", "taskId": "t", "runId": "run_parent", "terminalState": "active",
               "dispatchStatus": "dispatched", "agentTerminalHandle": ME}
        out = does(run("lead", "acme", orca=Orca(run=None, workers=[new]), procs=wait_proc(200, "orch wait acme")))
        self.assertEqual(len(out), 1)
        self.assertIn("run-create", out[0])
        self.assertIn("ctx_old", out[0])
        # With the Dispatch the program names, the lead is fine.
        self.program(lead_dispatch="ctx_new")
        self.assertEqual(does(run("lead", "acme", orca=Orca(run=None, workers=[new]), procs=wait_proc(200, "orch wait acme"))), [])

    def test_the_abandoned_row_of_this_terminal_is_no_dispatch(self):
        gone = {"dispatchId": "ctx_gone", "taskId": "t", "runId": "run_parent", "terminalState": "retained",
                "dispatchStatus": "abandoned", "agentTerminalHandle": ME}
        self.program(lead_dispatch="ctx_gone")
        for bound, expect in ((None, "run-create"), ("run_own", "orch set acme run run_own")):
            out = does(run("lead", "acme", orca=Orca(run=bound, workers=[gone]), procs=wait_proc(200, "orch wait acme")))
            self.assertEqual(len(out), 1, bound)
            self.assertIn(expect, out[0])
            self.assertNotIn("run-use", out[0])

    def test_a_lead_that_was_never_dispatched_still_gets_run_use(self):
        self.program()
        out = does(run("lead", "acme", procs=wait_proc(200, "orch wait acme")))
        self.assertIn("run-use --id run_parent", out[0])

    def test_after_the_program_moved_the_lead_is_bound_and_clean(self):
        self.program(run="run_own")
        lines = run("lead", "acme", orca=Orca(run="run_own"), procs=wait_proc(200, "orch wait acme"))
        self.assertEqual(does(lines), [])


class Queue(unittest.TestCase):
    def test_entries_of_this_worktree_only(self):
        q = {"q-1": {"id": "q-1", "cwd": ROOT + "/sub", "state": "waiting", "what": "orch heavy", "added": 1,
                     "command": "orch heavy acme -- make test"},
             "q-2": {"id": "q-2", "cwd": "/work/app-other", "state": "waiting", "what": "x", "added": 2},
             "q-3": {"id": "q-3", "cwd": ROOT, "state": "done", "what": "y", "added": 3},
             "q-4": {"id": "q-4", "cwd": ROOT, "state": "gave_up", "what": "docker compose up", "added": 4},
             "q-5": {"id": "q-5", "cwd": ROOT, "state": "retrying", "what": "orch heavy", "added": 5},
             "q-6": {"id": "q-6", "cwd": ROOT, "state": "failed", "what": "orch heavy", "added": 6}}
        out = does(run(queue=q))
        self.assertEqual(len(out), 4)
        self.assertIn(f"python3 {reboot.PRESSURE} queue wait q-1", out[0])
        self.assertIn(f"run the held command again from {ROOT}/sub: `orch heavy acme -- make test`", out[0])
        self.assertIn("deadline passed", out[1])
        for line in out[2:]:  # the retry was started: no wait, the result first
            self.assertNotIn("queue wait", line)
            self.assertIn("result", line)

    def entry(self, qid, state, **times):
        return {qid: {"id": qid, "cwd": ROOT, "state": state, "what": "orch heavy", "added": 1, "command": "c", **times}}

    def listed(self, *entries):
        out = does(run(queue={k: e for q in entries for k, e in q.items()}))
        return [line for line in out if "pressure queue" in line]

    def test_an_entry_that_ended_long_ago_is_not_listed(self):
        for state in ("gave_up", "failed"):
            self.assertEqual(self.listed(self.entry("q-1", state, ended=time.time() - PAST)), [], state)

    def test_a_late_sweep_does_not_keep_it_listed(self):  # the earlier of ended and deadline, as on the row
        q = self.entry("q-1", "gave_up", ended=time.time(), deadline=time.time() - PAST)
        self.assertEqual(self.listed(q), [])

    def test_an_entry_that_just_ended_is_listed(self):
        for state in ("gave_up", "failed"):
            out = self.listed(self.entry("q-1", state, ended=time.time() - 60))
            self.assertEqual(len(out), 1, state)

    def test_a_waiting_or_retrying_entry_is_listed_whatever_its_times(self):
        # `queue wait` writes down the end of a late entry, so reboot still has to send the session there.
        for state in ("waiting", "retrying"):
            q = self.entry("q-1", state, ended=time.time() - PAST, deadline=time.time() - PAST)
            self.assertEqual(len(self.listed(q)), 1, state)

    def test_the_row_and_reboot_share_one_judgment(self):
        q = self.entry("q-1", "gave_up", ended=time.time() - 60)
        self.assertEqual(len(self.listed(q)), 1)
        with mock.patch.object(rowstate, "queue_over", return_value=True) as over:
            self.assertEqual(self.listed(q), [])
        self.assertTrue(over.called)
        self.assertIn("queue_over", rowstate.judge.__code__.co_names)


class Reload(unittest.TestCase):
    """A session that loaded another hooks/hooks.json than the checkout has now is told to reload; no record is not it."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        (self.tmp / "plugin/hooks").mkdir(parents=True)
        (self.tmp / "plugin/hooks/hooks.json").write_text('{"hooks": {}}')
        self.sha = roles.hooks_sha(self.tmp / "plugin")
        patcher = mock.patch.dict("os.environ", {"AGENT_SKILLS_STATE": str(self.tmp / "state")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def record(self, sha):
        roles.record_terminal(ME, "s1", ROOT, time.time(), {"plugin_version": None, "hooks_sha": sha})

    def check(self, session="s1"):
        return reboot.check_reload(ME, self.tmp / "plugin", session)

    def test_record_of_another_session_is_not_this_ones(self):
        self.record("0" * 64)  # s1 started last in the terminal and has since ended; the caller is s2
        (kind, text), = self.check("s2")
        self.assertEqual(kind, "info")
        self.assertNotIn("reload needed", text)

    def test_unknown_own_session_says_so(self):
        self.record("0" * 64)
        (kind, text), = self.check(None)
        self.assertEqual(kind, "info")
        self.assertIn("could not be told", text)

    def test_own_session_comes_from_the_process_tree(self):
        # fleet's own process-table shape (ppid, start, command) and a registration under a config home, unmocked:
        # reboot's (ppid, command) table does not fit live_sessions.
        home = self.tmp / "home"
        (home / "sessions").mkdir(parents=True)
        for pid, sid in ((CLAUDE, "s1"), (4242, "s9")):
            (home / "sessions" / f"{pid}.json").write_text(json.dumps(
                {"pid": pid, "sessionId": sid, "cwd": ROOT, "procStart": "Thu Nov 5 08:00:00 2026"}))
        procs = {CLAUDE: (1, "Thu Nov 5 08:00:00 2026", "claude --continue"), 4242: (1, "Thu Nov 5 08:00:00 2026", "claude")}
        with mock.patch.dict("os.environ", {"ORCH_CLAUDE_HOMES": str(home)}), mock.patch.object(fleet, "ps_table", return_value=procs):
            self.assertEqual(reboot.own_session(table(), SELF), "s1")
        with mock.patch.object(fleet, "ps_table", side_effect=OSError):
            self.assertIsNone(reboot.own_session(table(), SELF))

    def test_own_record_names_the_caller_not_the_first_registration_of_its_session(self):
        # One session id resumed in two terminals: live_sessions keeps the first registration's name for both.
        home = self.tmp / "home2"
        (home / "sessions").mkdir(parents=True)
        for pid, name in ((4242, "mailbox"), (CLAUDE, "orchestrator")):
            (home / "sessions" / f"{pid}.json").write_text(json.dumps(
                {"pid": pid, "sessionId": "s1", "cwd": ROOT, "procStart": "Thu Nov 5 08:00:00 2026", "name": name}))
        stamp = "Thu Nov 5 08:00:00 2026"
        procs = {CLAUDE: (1, stamp, "claude"), 4242: (1, stamp, "claude")}
        with mock.patch.dict("os.environ", {"ORCH_CLAUDE_HOMES": str(home)}), mock.patch.object(fleet, "ps_table", return_value=procs):
            self.assertEqual(reboot.own_record(table(), SELF)["name"], "orchestrator")

    def test_same_sha_is_ok(self):
        self.record(self.sha)
        self.assertEqual([k for k, _ in self.check()], ["ok"])

    def test_other_sha_is_info_not_do(self):
        self.record("0" * 64)
        (kind, text), = self.check()
        self.assertEqual(kind, "info")
        self.assertIn("reload needed", text)

    def test_no_record_is_not_called_stale(self):
        (kind, text), = self.check()
        self.assertEqual(kind, "info")
        self.assertNotIn("reload needed", text)
        self.assertIn("unknown", text)

    def test_unreadable_record_is_not_called_missing_or_stale(self):
        roles.terminal_file(ME).parent.mkdir(parents=True, exist_ok=True)
        roles.terminal_file(ME).write_text("{not json")
        (kind, text), = self.check()
        self.assertEqual(kind, "info")
        self.assertIn("unreadable", text)

    def test_unreadable_reference_warns_and_does_not_block(self):
        self.record(self.sha)
        (kind, text), = reboot.check_reload(ME, self.tmp / "missing", "s1")
        self.assertEqual(kind, "info")
        self.assertIn("unreadable", text)

    def test_exit_stays_zero_on_stale(self):
        self.record("0" * 64)
        with mock.patch.object(reboot, "own_session", return_value="s1"):
            lines = run(reload_root=self.tmp / "plugin")
        self.assertFalse(does(lines))
        self.assertTrue(any("reload needed" in t for _, t in lines))


class Cli(unittest.TestCase):
    def test_slug_and_top_together_are_refused(self):
        self.assertEqual(reboot.main(["--slug", "acme", "--top"]), 2)


if __name__ == "__main__":
    unittest.main()
