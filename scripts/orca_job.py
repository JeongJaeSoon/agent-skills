#!/usr/bin/env python3
"""Run a periodic shell job from an Orca automation's precheck, and keep a record of how it ended.

Usage:
  orca_job.py run NAME [--interval S] [--timeout S] -- COMMAND...   run COMMAND, record the result, exit 1
  orca_job.py status [--json]                                       the record of every job

Orca automations always carry an agent prompt, but a precheck runs first and a non-zero exit skips the agent
(`skipped_precheck`). A job that needs no agent therefore does its work inside the precheck and always exits 1, so
the agent never starts. Since that hides a failing job behind the same `skipped_precheck`, `run` writes
<state>/jobs/NAME.json (when, rc, one message line) and `status` / setup-pc read it back (docs/automation.md).
"""
import contextlib, json, os, pathlib, shutil, signal, subprocess, sys, time

STATE = pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser()
JOBS = STATE / "jobs"
SKIPPED = 1  # the exit code that tells Orca not to start the agent; every `run` ends with it


def record_path(name):
    return JOBS / f"{name}.json"


def read(name):
    try:
        return json.loads(record_path(name).read_text())
    except (OSError, ValueError):
        return None


def write(name, rec):
    JOBS.mkdir(parents=True, exist_ok=True)
    tmp = record_path(name).with_name(f".{name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False))
    os.replace(tmp, record_path(name))


def job_path():
    """Orca starts the precheck with whatever PATH the app has; the jobs need git, gh, orca, docker and claude."""
    first = [os.path.dirname(p) for p in (sys.executable, *map(shutil.which, ("git", "orca"))) if p]
    home = pathlib.Path.home()
    rest = ["/opt/homebrew/bin", "/usr/local/bin", str(home / ".local" / "bin"), "/usr/sbin", "/usr/bin", "/bin"]
    return ":".join(dict.fromkeys([*first, *os.environ.get("PATH", "").split(":"), *rest]))


def ended(rc):
    if rc < 0:
        return f"killed by signal {-rc}"
    return "ok" if rc == 0 else f"exit {rc}"


def last_line(text):
    lines = [x.strip() for x in (text or "").splitlines() if x.strip()]
    return lines[-1][:200] if lines else ""


def run(name, cmd, interval=None, timeout=None, ok_codes=(0,)):
    """Run cmd to its end (or timeout), write the record, and return SKIPPED whatever happened. ok_codes: exits that
    are a result, not a failure (pressure.py round exits 1 and 2 for warn and danger)."""
    started = time.time()
    env = {**os.environ, "PATH": job_path()}
    if cmd and cmd[0] == "python3":  # the interpreter running this wrapper, not whatever the app's PATH finds first
        cmd = [sys.executable, *cmd[1:]]
    try:
        # Its own process group, so a timeout takes the job's children (git, docker, gh) down with it.
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
                                stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise
        rc = proc.returncode
        out = stdout if rc in ok_codes else (last_line(stderr) and stderr) or stdout  # a failure: stderr, else stdout
        message = f"{ended(rc)}: {last_line(out)}" if last_line(out) else ended(rc)
    except subprocess.TimeoutExpired:
        rc, message = -signal.SIGKILL, f"timed out after {timeout:g}s"
    except OSError as e:
        rc, message = 127, f"could not start: {e}"
    write(name, {"name": name, "at": time.time(), "took": round(time.time() - started, 1), "rc": rc,
                 "ok": rc in ok_codes, "interval": interval, "message": message})
    if rc not in ok_codes:
        print(f"{name}: {message}", file=sys.stderr)
    return SKIPPED


def stale(rec, now=None):
    """Seconds late past three intervals, or None. The app being closed, or the automation being off, shows up here."""
    if not rec or not rec.get("interval"):
        return None
    late = (now or time.time()) - rec["at"] - 3 * rec["interval"]
    return late if late > 0 else None


def records(jobs_dir=None):
    out = []
    for p in sorted((jobs_dir or JOBS).glob("*.json")):
        try:
            rec = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and {"name", "at", "ok", "message"} <= rec.keys():  # a hand-edited file is skipped
            out.append(rec)
    return out


def forget(name):
    """Drop a job's record: a job turned off must not read as 'late' for ever."""
    record_path(name).unlink(missing_ok=True)


def command(name, argv, interval, timeout, ok=(0,)):
    """The precheck string an automation stores for a job."""
    import shlex
    return shlex.join(["python3", "-I", str(pathlib.Path(__file__).resolve()), "run", name,
                       "--interval", str(interval), "--timeout", str(timeout), "--ok", ",".join(map(str, ok)),
                       "--", *argv])


def main(argv):
    if argv[:1] == ["run"] and "--" in argv:
        i = argv.index("--")
        head, cmd = argv[1:i], argv[i + 1:]
        opts = dict(zip(head[1::2], head[2::2]))
        if not head or not cmd or head[0].startswith("-"):
            print(__doc__, file=sys.stderr)
            return SKIPPED
        num = lambda k: float(opts[k]) if k in opts else None  # noqa: E731
        ok = tuple(int(x) for x in opts.get("--ok", "0").split(","))
        return run(head[0], cmd, num("--interval"), num("--timeout"), ok)
    if argv[:1] == ["status"]:
        recs = records()
        if "--json" in argv:
            print(json.dumps(recs, ensure_ascii=False, indent=2))
        for r in ([] if "--json" in argv else recs):
            late = stale(r)
            print(f"{r['name']}: {'ok' if r['ok'] else 'FAILED'} · {r['message']} · "
                  f"{int((time.time() - r['at']) // 60)}분 전" + (" · 주기를 넘김" if late else ""))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
