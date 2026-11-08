"""Offline transcript-adapter checks; no real accounts or session data are read."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile

import measure

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    old_config, old_argv, old_gh = os.environ.get("CLAUDE_CONFIG_DIR"), sys.argv, measure.gh
    os.environ["CLAUDE_CONFIG_DIR"] = str(root)
    try:
        a, b = measure.ts("2026-01-01T00:00:00Z"), measure.ts("2026-01-02T00:00:00Z")
        assert not measure.usage("app", a, b)
        project = root / "projects" / "-example-app"
        project.mkdir(parents=True)
        row = {"type": "assistant", "timestamp": "2026-01-01T12:00:00Z", "requestId": "r",
               "message": {"id": "m", "usage": {"output_tokens": 7, "cache_read_input_tokens": 11}}}
        (project / "session.jsonl").write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
        usage = measure.usage("app", a, b)
        assert usage["claude_calls"] == 1 and usage["claude_output"] == 7
        assert usage["claude_cache_read"] == 11
        assert not measure.usage("unrelated", a, b)
        # L-463: a card whose worktree name lacks the repo name is found by the tickets the ledger lists.
        card = root / "projects" / "-example-worktrees-abc-12-fix"
        card.mkdir(parents=True)
        (card / "s.jsonl").write_text(json.dumps({**row, "requestId": "r2", "message": {"id": "m2", "usage": {"output_tokens": 5}}}) + "\n")
        assert measure.usage(["app"], a, b)["claude_output"] == 7
        assert measure.usage(["app", "abc-12"], a, b)["claude_output"] == 12
        assert measure.usage(["app", "app"], a, b)["claude_output"] == 7  # one directory is counted once
        programs = root / "programs"
        (programs / "demo").mkdir(parents=True)
        (programs / "demo" / "ledger.jsonl").write_text("\n".join(json.dumps(x) for x in [
            {"ts": "2026-01-01T03:00:00+00:00", "ev": "spawned", "ticket": "ABC-12", "note": "ctx_1"},
            {"ts": "2026-01-01T03:00:00+00:00", "ev": "spawned", "ticket": "ABC-12", "note": "ctx_2"},
            {"ts": "2026-01-01T04:00:00+00:00", "ev": "main_flake", "run": "77", "attempt": 2},
            {"ts": "2026-01-05T04:00:00+00:00", "ev": "main_flake", "run": "78", "attempt": 2}]) + "\n")
        os.environ["PROGRAMS_HOME"] = str(programs)
        info = measure.program_info("demo", a, b)
        assert info["tickets"] == ["abc-12"] and info["flakes"] == [{"run": "77", "attempt": 2}], info
        # L-463: a main run that failed on attempt 1 and was rerun green shows only as attempt > 1 in the run list.
        asked = []
        def fake_gh(*args):
            asked.append(args)
            return {"conclusion": "failure"} if args[-1].endswith("/attempts/1") and "/runs/2/" in args[-1] else {"conclusion": "success"}
        measure.gh = fake_gh
        runs = [{"databaseId": 1, "event": "push", "headBranch": "main", "conclusion": "success", "attempt": 1},
                {"databaseId": 2, "event": "push", "headBranch": "main", "conclusion": "success", "attempt": 3},
                {"databaseId": 3, "event": "push", "headBranch": "main", "conclusion": "success", "attempt": 2},
                {"databaseId": 4, "event": "push", "headBranch": "main", "conclusion": "failure", "attempt": 2},
                {"databaseId": 5, "event": "pull_request", "headBranch": "x", "conclusion": "success", "attempt": 2}]
        flaked = measure.rerun_greens("acme/app", runs)
        assert [r["databaseId"] for r in flaked] == [2], flaked  # runs 1, 4 and 5 need no attempt lookup
        assert len(asked) == 2, asked
        measure.gh = lambda *args: []
        assert not measure.usage("app", b, measure.ts("2026-01-03T00:00:00Z"))
        issues = root / "issues.json"
        issues.write_text(json.dumps([{"id": "ABC-1", "state_type": "started",
                                      "created_at": "2026-01-01T01:00:00Z"}]))
        measure.gh = lambda *args: []
        output = root / "report.json"
        sys.argv = ["measure.py", "--repo", "acme/app", "--issues", str(issues),
                    "--since", a.isoformat(), "--until", b.isoformat(), "--json", str(output)]
        text = io.StringIO()
        with contextlib.redirect_stdout(text):
            measure.main()
        report = json.loads(output.read_text())
        assert report["usage_measured"] and report["usage"]["claude_output"] == 7
        assert str(root / "projects") in text.getvalue()
        sys.argv += ["--usage-match", "unrelated"]
        text = io.StringIO()
        with contextlib.redirect_stdout(text):
            measure.main()
        assert not json.loads(output.read_text())["usage_measured"]
        assert "미측정" in text.getvalue()
        sys.argv = sys.argv[:-2] + ["--program", "demo"]
        text = io.StringIO()
        with contextlib.redirect_stdout(text):
            measure.main()
        report = json.loads(output.read_text())
        assert report["usage"]["claude_output"] == 12 and report["main_flake"] == 1, report
        assert "main_flake" in text.getvalue() and "abc-12" in text.getvalue(), text.getvalue()
    finally:
        sys.argv, measure.gh = old_argv, old_gh
        os.environ.pop("PROGRAMS_HOME", None)
        if old_config is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = old_config

print("measure.py: all pass")
