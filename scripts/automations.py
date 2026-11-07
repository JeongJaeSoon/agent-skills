#!/usr/bin/env python3
"""Turn this checkout's periodic automations on or off, and show which are on (docs/automation.md).

Usage:
  skills-sync setup [status] [--json]   every automation: name, default, state, what it does
  skills-sync setup apply               turn on each default-on automation that was never set up
  skills-sync setup enable NAME [--here]
  skills-sync setup disable NAME

Running any of these again changes nothing that is already as asked. `disable` keeps the janitor registered
but off, so `apply` leaves it off. Turning one on writes paths to this checkout into the job, so it refuses a
worktree (it goes away under the job); --here allows it for a LaunchAgent test, never for the shared janitor job.
"""
import json, os, pathlib, plistlib, shlex, shutil, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
REAP = REPO / "skills" / "reap-resources" / "scripts" / "reap.py"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REAP.parent))
import orca_job  # noqa: E402
import preview_watch as pw  # noqa: E402
import sync  # noqa: E402
import pressure  # noqa: E402
import reap  # noqa: E402
import share_server as ss  # noqa: E402

ORCA = os.environ.get("SKILLS_SETUP_ORCA", "orca")
JANITOR_NAME = reap.JANITOR_NAME
CONFIG = pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG", "~/.claude/agent-skills.json")).expanduser()


def sh(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))


def config_model(key):
    """`<key>.model` of agent-skills.json, or None: the value the skill's own `schedule` passes to a new job."""
    try:
        return (json.loads(CONFIG.read_text()).get(key) or {}).get("model") or None
    except (OSError, ValueError, AttributeError):
        return None


def main_checkout():
    return (REPO / ".git").is_dir()


def worktree_refusal(here=False):
    if here or main_checkout():
        return None
    return f"{REPO} 는 worktree 다. 세션이 로드하는 메인 체크아웃에서 다시 실행한다(테스트만 --here)"


# ---------------------------------------------------------------- janitor (Orca automation)

def orca_automations():
    """The automations Orca lists, or a reason they could not be read."""
    if not shutil.which(ORCA):
        return None, "orca CLI 없음: Orca 를 설치하고 다시 실행한다"
    p = sh([ORCA, "automations", "list", "--json"])
    try:
        return json.loads(p.stdout)["result"]["automations"], None
    except (ValueError, KeyError, TypeError):
        return None, f"orca automations list 실패: {(p.stderr or p.stdout).strip()[:200]}"


def janitor_want(workspace, load=False):
    """{field: value} reap's schedule would create, in the shape `orca automations list` reports. The workspace
    is not compared: a janitor moved to a runner worktree stays there."""
    argv = reap.schedule_cmd(REAP, reap.STATE, workspace, "claude", reap.ledger_path(), reap.config().get("inbox_to"),
                             load=load)
    arg = dict(zip(argv, argv[1:]))
    want = {"trigger": arg["--trigger"], "precheck": arg["--precheck"],
            "precheck-timeout": int(arg["--precheck-timeout"]), "prompt": arg["--prompt"]}
    # Only present with reap.model: without the key the stored arguments are left as they are.
    extra = next((x.partition("=")[2] for x in argv if x.startswith("--extra-agent-args=")), None)
    return {**want, **({"extra-agent-args": extra} if extra else {})}


def janitor_drift(a, load=False):
    pre = a.get("precheck") or {}
    have = {"trigger": a.get("rrule"), "precheck": pre.get("command"),
            "precheck-timeout": pre.get("timeoutSeconds"), "prompt": a.get("prompt"),
            "extra-agent-args": a.get("extraAgentArgs")}
    want = janitor_want(f"path:{REPO}", load)
    if a.get("agentId", "claude") != "claude":  # --model is a claude argument; another agent keeps its own
        want.pop("extra-agent-args", None)
    return {k: v for k, v in want.items() if have[k] != v}


def edit_flag(k, v):
    """argv words for one janitor_want field; a value that starts with `--` only parses after `=`."""
    return [f"--{k}={v}"] if k == "extra-agent-args" else [f"--{k}", str(v)]


class OrcaJob:
    """An Orca automation found by its name; disabling keeps it registered so `apply` leaves it off."""
    default = True
    job_name = ""

    def model(self):
        """The model its config key names, or None (no key: whatever the job has stays)."""
        return None

    def refresh_model(self):
        """Set a registered job's `--model` to the key's value, touching nothing else (not even on/off). None when
        there is nothing to change; otherwise (code, message). A key that was removed does not clear the job's
        arguments, and a job on another agent keeps its own."""
        if not self.model():
            return None
        want = f"--model {self.model()}"
        a, err = self.find()
        if a is None:
            return 1, err
        if not a or a.get("agentId", "claude") != "claude" or a.get("extraAgentArgs") == want:
            return None
        p = sh([ORCA, "automations", "edit", "--id", a["id"], f"--extra-agent-args={want}", "--json"])
        if p.returncode:
            return 1, f"orca automations edit 실패: {(p.stderr or p.stdout).strip()[:200]}"
        return 0, f"모델 인자를 맞춤: {want}"

    def find(self):
        autos, err = orca_automations()
        if autos is None:
            return None, err
        return next((a for a in autos if a.get("name") == self.job_name), {}), None

    def disable(self):
        a, err = self.find()
        if a is None:
            return 1, err
        if not a or not a.get("enabled"):
            return 0, "이미 꺼져 있음"
        p = sh([ORCA, "automations", "edit", "--id", a["id"], "--disabled", "--json"])
        if p.returncode:
            return 1, f"orca automations edit 실패: {(p.stderr or p.stdout).strip()[:200]}"
        return 0, "끔 (등록은 남겨 두어 apply 가 다시 켜지 않는다)"


class ReapJob(OrcaJob):
    def status(self):
        a, err = self.find()
        if a is None:
            return "unknown", err
        if not a:
            return "absent", "등록 안 됨"
        # From a worktree every path differs from the job's; that is not drift.
        drift = janitor_drift(a, self.load) if main_checkout() else {}
        detail = f"{a.get('rrule')} · {(a.get('workspaceId') or '').partition('::')[2]}"
        if drift:
            detail += f" · reap schedule 과 다름: {', '.join(drift)} (enable 로 맞춤)"
        return ("on" if a.get("enabled") else "off"), detail

    def model(self):
        return reap.config().get("model")

    def create_args(self):
        return [], None

    def enable_job(self, here):
        a, err = self.find()
        if a is None:
            return 1, err
        # --here never applies: the janitor is one shared Orca job, and a worktree's paths die with it.
        if not a:
            refusal = worktree_refusal()
            if refusal:
                return 1, refusal
            args, skip = self.create_args()
            if skip:
                return 0, skip
            p = sh([sys.executable, str(REAP), "schedule", *args, "--write"])
            return p.returncode, (p.stdout + p.stderr).strip()
        drift = janitor_drift(a, self.load)
        if not main_checkout():  # a worktree's paths differ from the job's; the model does not depend on the checkout
            drift = {k: v for k, v in drift.items() if k == "extra-agent-args"}
        if not drift and a.get("enabled"):
            return 0, "이미 켜져 있음"
        # orca rejects --precheck-timeout without --precheck.
        fields = {"precheck": janitor_want(f"path:{REPO}", self.load)["precheck"], **drift} \
            if "precheck-timeout" in drift else drift
        edit = [x for k, v in fields.items() for x in edit_flag(k, v)]
        p = sh([ORCA, "automations", "edit", "--id", a["id"], *edit, "--enabled", "--json"])
        if p.returncode:
            return 1, f"orca automations edit 실패: {(p.stderr or p.stdout).strip()[:200]}"
        return 0, "켬" + (f" (reap schedule 에 맞춘 필드: {', '.join(drift)})" if drift else "")


class JanitorLoad(ReapJob):
    name = "janitor-load"
    job_name = reap.LOAD_NAME
    load = True
    what = ("부하 정리 라운드(Orca automation, 30분마다 load 만 확인). 5분 load 가 reap.load_limit(기본 CPU 수) 이상이고 "
            "1시간 안에 정리 라운드가 없었을 때만 janitor 와 같은 라운드를 한 번 돈다. janitor 가 꺼져 있거나 없으면 돌지 않는다")

    def create_args(self):
        """Follow the janitor: none while the user has it off or removed (apply would turn reaping back on), and in
        the workspace it was moved to."""
        j, _ = Janitor().find()
        if not j or not j.get("enabled"):
            return [], "janitor 가 꺼져 있거나 없어 만들지 않음"
        ws = ((j or {}).get("workspaceId") or "").partition("::")[2]
        return ["--load", *(["--workspace", f"path:{ws}"] if ws else [])], None

    def enable(self, here):
        return self.enable_job(here)

    def disable(self):
        code, msg = OrcaJob.disable(self)
        if not code:
            orca_job.forget(self.name)
        return code, msg


# ---------------------------------------------------------------- preview-watch (LaunchAgent)

def launchd_loaded():
    return sh(["launchctl", "print", pw.agent_target()]).returncode == 0


def plist_spec():
    try:
        return plistlib.loads(pw.PLIST.read_bytes())
    except (OSError, ValueError, plistlib.InvalidFileException):
        return None


def tailscale_ready():
    cli = pw.tailscale_cli(pw.config())
    return bool(cli) and os.access(cli, os.X_OK)


class PreviewWatch:
    name = "preview-watch"
    default = False
    what = ("loopback 에서 뜬 웹페이지를 tailnet HTTPS 로 자동 공개하고 내려가면 닫는 LaunchAgent(모델 없음). "
            "tailscale 을 쓸 때만 켠다")

    def status(self):
        if sys.platform != "darwin":
            return "unsupported", "macOS LaunchAgent 전용"
        spec, loaded = plist_spec(), launchd_loaded()
        if spec is None and not loaded:
            return "absent", "등록 안 됨"
        if not loaded:
            return "off", f"{pw.PLIST} 는 있으나 로드 안 됨 (enable 로 다시 로드)"
        detail = f"{pw.config()['interval']}초마다"
        if main_checkout() and spec != pw.spec(pw.config()["interval"]):
            script = next((a for a in (spec or {}).get("ProgramArguments", []) if a.endswith(".py")), "?")
            detail += f" · 등록된 내용이 이 체크아웃과 다름: {script} (enable 로 다시 씀)"
        return "on", detail

    def enable(self, here):
        if sys.platform != "darwin":
            return 1, "macOS LaunchAgent 전용이다. 다른 OS 는 cron 에서 `bin/preview-watch run` 을 주기 실행한다"
        if not tailscale_ready():
            return 1, ("tailscale CLI 를 찾지 못해 켜지 않는다. tailscale 을 설치하고 로그인하거나, "
                       "~/.claude/agent-skills.json 의 \"preview-watch\".\"tailscale\" 에 CLI 경로를 적는다")
        if launchd_loaded() and plist_spec() == pw.spec(pw.config()["interval"]):
            return 0, "이미 켜져 있음"
        refusal = worktree_refusal(here)
        if refusal:
            return 1, refusal
        code = pw.cmd_install(here_ok=here)
        return code, "실패 (위 출력)" if code else "켬"

    def disable(self):
        if sys.platform != "darwin":
            return 0, "macOS LaunchAgent 전용(이 OS 에는 등록된 것이 없다)"
        # Entries left open by a failed uninstall keep this going until they are closed.
        if plist_spec() is None and not launchd_loaded() and not pw.load_state()["owned"]:
            return 0, "이미 꺼져 있음"
        code = pw.cmd_uninstall()
        return code, ("job 은 내렸으나 serve 항목을 다 닫지 못함. disable 을 다시 돌린다" if code
                      else "끔 (watcher 가 연 serve 항목도 닫았다)")


# ---------------------------------------------------------------- sync, pressure-watch, reap-hard, orch-dash, land-executor (Orca automation, no agent)

SHELL_PROMPT = ("이 자동화는 precheck 가 주기 작업을 직접 실행하고 항상 건너뛰도록 만든 것이라 에이전트가 시작되지 않는다. "
                "이 프롬프트가 실행됐다면 precheck 가 건너뛰지 않은 것이다: 아무것도 하지 않고 '정기 작업 precheck 이상' 이라고만 "
                "보고한다.")
SHELL_TIMEOUT_S = 300


def cron_period(seconds):
    """The period cron can run evenly at, in seconds: a divisor of 60 minutes, or of 24 hours from an hour up (45 min
    becomes 30, 90 min becomes 1 h). Cron steps that do not divide the field restart at its top and run unevenly."""
    minutes = max(1, round(float(seconds) / 60))
    if minutes < 60:
        return max(d for d in (1, 2, 3, 4, 5, 6, 10, 12, 15, 20, 30) if d <= minutes) * 60
    return max(d for d in (1, 2, 3, 4, 6, 8, 12, 24) if d <= min(24, minutes // 60)) * 3600


def cron_every(seconds):
    """5-field cron for cron_period(seconds)."""
    p = cron_period(seconds)
    return f"*/{p // 60} * * * *" if p < 3600 else f"0 */{p // 3600} * * *"


class ShellJob(OrcaJob):
    """A periodic shell job with no agent: an Orca automation whose precheck (scripts/orca_job.py) runs the command,
    records how it ended and exits 1, so Orca skips the agent every time. A job that needs no model must not start one."""

    def __init__(self, name, what, argv, interval, default, ok=(0,), turned_off=None, clear_off=None, gate=None,
                 prepare=None, timeout=SHELL_TIMEOUT_S):
        self.name, self.job_name, self.what, self.default = name, f"agent-skills {name}", what, default
        self.argv, self.interval, self.ok, self.turned_off, self.clear_off = argv, interval, ok, turned_off, clear_off
        self.gate = gate  # () -> why this PC must not get the job yet, or None; checked only while it is unregistered
        self.prepare = prepare  # () -> (code, message), run before the job is created or turned on
        self.timeout = timeout

    def stale(self):
        """On, but registered with other paths, trigger or precheck than this checkout's: apply writes them again."""
        a, _ = self.find()
        return bool(a) and a.get("enabled") and main_checkout() and bool(self.drift(a))

    def disable(self):
        code, msg = super().disable()
        if not code:
            orca_job.forget(self.name)  # a stopped job's last run must not turn into a 'late' warning
        return code, msg

    def enable(self, here):
        if self.prepare:
            code, msg = self.prepare()
            if code:
                return code, msg
        code, msg = self.enable_job(here)
        if not code and self.clear_off:
            self.clear_off()
        return code, msg

    def want(self):
        secs = cron_period(self.interval() if callable(self.interval) else self.interval)  # what cron really runs at
        return {"trigger": cron_every(secs), "prompt": SHELL_PROMPT, "precheck-timeout": self.timeout,
                "precheck": orca_job.command(self.name, self.argv, secs, self.timeout - 20, self.ok)}

    def drift(self, a):
        pre = a.get("precheck") or {}
        have = {"trigger": a.get("rrule"), "prompt": a.get("prompt"), "precheck": pre.get("command"),
                "precheck-timeout": pre.get("timeoutSeconds")}
        return {k: v for k, v in self.want().items() if have[k] != v}

    def status(self):
        a, err = self.find()
        if a is None:
            return "unknown", err
        if not a:
            if self.turned_off and self.turned_off():
                return "off", "LaunchAgent 시절 `pressure.py uninstall` 로 끈 표지가 있음 (enable 로 다시 켬)"
            if self.gate and (why := self.gate()):
                return "unconfigured", why
            return "absent", "등록 안 됨"
        detail = f"{a.get('rrule')} · 에이전트 없이 precheck 로 실행"
        drift = self.drift(a) if main_checkout() else {}  # from a worktree every path differs; that is not drift
        if drift:
            detail += f" · 등록된 내용이 이 체크아웃과 다름: {', '.join(drift)} (enable 로 맞춤)"
        rec = orca_job.read(self.name)
        if rec and not rec["ok"]:
            detail += f" · 마지막 실행 실패: {rec['message']}"
        return ("on" if a.get("enabled") else "off"), detail

    def model(self):
        return None  # no agent, so no model argument

    def enable_job(self, here):
        a, err = self.find()
        if a is None:
            return 1, err
        if not a:
            refusal = worktree_refusal()  # --here never applies: the job points at this checkout's files
            if refusal:
                return 1, refusal
            w = self.want()
            argv = [ORCA, "automations", "create", "--name", self.job_name, "--trigger", w["trigger"],
                    "--provider", "claude", "--workspace-mode", "existing", "--workspace", f"path:{REPO}",
                    "--precheck", w["precheck"], "--precheck-timeout", str(w["precheck-timeout"]),
                    "--prompt", w["prompt"], "--json"]
            p = sh(argv)
            return (1, f"orca automations create 실패: {(p.stderr or p.stdout).strip()[:200]}") if p.returncode \
                else (0, "켬 (Orca 가 꺼져 있는 동안은 돌지 않는다)")
        drift = self.drift(a) if main_checkout() else {}
        if not drift and a.get("enabled"):
            return 0, "이미 켜져 있음"
        fields = {"precheck": self.want()["precheck"], **drift} if "precheck-timeout" in drift else drift
        p = sh([ORCA, "automations", "edit", "--id", a["id"], *[x for k, v in fields.items() for x in edit_flag(k, v)],
                "--enabled", "--json"])
        if p.returncode:
            return 1, f"orca automations edit 실패: {(p.stderr or p.stdout).strip()[:200]}"
        return 0, "켬" + (f" (맞춘 필드: {', '.join(drift)})" if drift else "")


class Janitor(ShellJob):
    def __init__(self):
        super().__init__("janitor", "30분마다 모델 없는 정리 라운드(Orca automation precheck, reap-resources). 에이전트가 만든 끝난 리소스만 "
                         "닫거나 휴지통으로 옮긴다. compose 는 down 까지이고 -v·prune·볼륨/이미지 삭제는 하지 않는다",
                         shlex.split(reap.round_cmd(REAP, reap.STATE, reap.ledger_path(), "precheck", "--act")),
                         reap.INTERVAL_S, True, timeout=reap.PRECHECK_TIMEOUT)
        self.job_name = JANITOR_NAME

    def disable(self):
        code, msg = super().disable()
        load_code, load_msg = JanitorLoad().disable()
        return code | load_code, f"{msg} · janitor-load: {load_msg}"

    def enable(self, here):
        j, _ = self.find()
        code, msg = self.enable_job(here)
        load = JanitorLoad()
        a, _ = load.find()
        # Only undoes `disable janitor`: a load job turned off alone under a running janitor stays off.
        if code or not j or j.get("enabled") or not a or a.get("enabled"):
            return code, msg
        load_code, load_msg = load.enable_job(here)
        return load_code, f"{msg} · janitor-load: {load_msg}"


def planned(key):
    """The section of agent-skills.json that the run about to write has, else the file as it is now."""
    if PLANNED_CONFIG is not None:
        return PLANNED_CONFIG.get(key) or {}
    try:
        return json.loads(CONFIG.read_text()).get(key) or {}
    except (OSError, ValueError, AttributeError):
        return {}


# Set by plan() to the agent-skills.json that --apply is about to write, so the dry run judges what apply will see.
PLANNED_CONFIG = None


LAND_EXEC = REPO / "skills" / "orchestrate" / "scripts" / "land_exec.py"


def land_setup():
    """The land queue's table, created before the job is turned on so its first round finds it."""
    p = subprocess.run([sys.executable, "-I", str(LAND_EXEC), "setup"], capture_output=True, text=True)
    return p.returncode, ("" if not p.returncode else
                          f"land_exec.py setup 실패, 켜지 않음: {(p.stderr or p.stdout).strip()[:200]}")


def reap_hard_interval():
    import reap_hard  # not at import: it pulls in the whole guard stack
    return planned("reap-hard").get("interval") or reap_hard.config()["interval"]


def reap_hard_gate():
    """No job until the protect list exists: reap-hard closes cards without asking, and without the file it refuses to run."""
    import reap_hard
    path = pathlib.Path(planned("reap-hard").get("protect_file") or reap_hard.config()["protect_file"]).expanduser()
    return None if path.is_file() else f"보호 목록 파일 {path} 가 없음 (patrol job 이 꺼져 있으면 순찰 `orch patrol` 도 이 job 이 띄운다)"


def pressure_interval():
    return (planned("reap").get("pressure") or {}).get("interval") or pressure.config()["interval"]


SWEEP_TIMEOUT_S = 2700  # sweep.py tick's own step budgets add up to 2500 s (patrol 400, shadow 600, reap-hard 900, round 600)


def sweep_interval():
    import sweep  # not at import: it pulls in the whole guard stack
    return sweep.interval()


def sweep_protect_problem():
    """Why a tick would stop at once, or None: without the protect list every pass of the old cleaners refuses too."""
    import guard
    try:
        guard.read_protect(guard.protect_file())
    except guard.NoProtect as e:
        return str(e)
    return None


def sweep_prepare():
    why = sweep_protect_problem()
    return (1, f"sweep 을 켜지 않는다: {why}") if why else (0, "")


SHELL_JOBS = (
    ShellJob("pstack-upstream", "하루 한 번 cursor/plugins 의 pstack 이 vendor/pstack 사본에서 얼마나 바뀌었는지 모델 없이 비교해 "
             "알리는 Orca automation(pstack-sync.py --notify). skills/ 와 사본은 건드리지 않는다. 이 repo 를 "
             "고치는 PC 에서만 켠다",
             ["python3", str(HERE / "pstack-sync.py"), "--notify"], 86400, False),
    ShellJob("sync", "15분마다 체크아웃을 origin 에 맞추는 Orca automation(모델 없음, skills-sync sync). 세션이 읽는 스킬을 최신으로 둔다",
             ["python3", str(HERE / "sync.py"), "sync"], sync.INTERVAL_S, True),
    ShellJob("pressure-watch", "PC 사용량(load·메모리 압박·swap·디스크·컨테이너)을 모델 없이 재고, 높으면 reap 이 올린 안전한 대상만 "
             "정리해 알리는 Orca automation(docs/pressure.md). 아무것도 종료하지 않고, 정리할 것이 없으면 상위 소비자만 알린다",
             ["python3", str(REAP.parent / "pressure.py"), "round"], pressure_interval, True, ok=(0, 1, 2),
             turned_off=pressure.disabled, clear_off=lambda: pressure.DISABLED.unlink(missing_ok=True)),
    ShellJob("reap-hard", "30분마다 끝난 세션이 남긴 프로세스·창을 강하게 정리하는 Orca automation(모델 없음, reap_hard.py run). "
             "patrol job 이 꺼져 있으면 세션 순찰(`orch patrol`)도 이 회차가 띄운다. 묻지 않고 카드를 지우므로 보호 목록 파일이 있는 PC에서만 켠다",
             ["python3", str(HERE / "reap_hard.py"), "run"], reap_hard_interval, True, gate=reap_hard_gate),
    ShellJob("orch-dash", "10분마다 오케스트레이터 대시보드 서버를 확인해 내려갔으면 올리는 Orca automation(모델 없음, orch-dash ensure). "
             "읽기 위주이고 아무것도 지우지 않는다",
             ["python3", str(REPO / "skills" / "orchestrate" / "scripts" / "dash.py"), "ensure"], 600, True),
    ShellJob("patrol", "30분마다 모든 에이전트 카드를 모델 없이 순찰하는 Orca automation(orch patrol --if-due). 발견은 카드를 띄운 세션의 "
             "inbox 대기에 나타나고, 정리 후보는 기록만 한다. 꺼져 있으면 reap-hard 가 30분 회차마다 대신 돈다",
             ["python3", str(REPO / "skills" / "orchestrate" / "scripts" / "prog.py"), "patrol", "--if-due"], 1800, False,
             timeout=420),
    ShellJob("land-executor", "orchestrate.land_executor 가 켜진 repo 에서 `orch land` 가 남긴 머지 요청을 1분마다 모델 없이 다시 "
             "검사해 머지하는 Orca automation(skills/orchestrate/references/landing.md). 설정 키를 켜는 것은 skills-config 로 따로 한다",
             ["python3", str(LAND_EXEC), "round"], 60, False, prepare=land_setup),
    ShellJob("sweep", "정리기 v2 한 회차를 주기마다 모델 없이 돌리는 Orca automation(sweep.py tick: 순찰 → 그림자 → reap-hard → 소유 규칙 정리). "
             "`reap-hard` 자동화 자리를 이어받고, 한 번에 하나만 돈다(sweep-tick.lock). 규칙을 넘기는 것은 `sweep.py own` 으로 따로 한다. "
             "보호 목록 파일이 있는 PC에서만 켠다",
             ["python3", str(REAP.parent / "sweep.py"), "tick"], sweep_interval, False, gate=lambda: sweep_protect_problem(),
             prepare=sweep_prepare, timeout=SWEEP_TIMEOUT_S),
)
SWEEP = next(j for j in SHELL_JOBS if j.name == "sweep")


# ---------------------------------------------------------------- share-server (LaunchAgent)

class ShareServer:
    name = "share-server"
    default = False
    what = ("공유 폴더를 127.0.0.1 에서 Caddy 로 읽기 전용 서빙하는 LaunchAgent(docs/share-server.md). md 렌더·썸네일. "
            "tailnet 공개는 하지 않는다. 공유 폴더를 tailscale serve 로 열어 쓸 때만 켠다")

    def status(self):
        if sys.platform != "darwin":
            return "unsupported", "macOS LaunchAgent 전용"
        spec, loaded = ss.plist_spec(), ss.loaded()
        if spec is None and not loaded:
            return "absent", "등록 안 됨"
        if not loaded:
            return "off", f"{ss.plist_path()} 는 있으나 로드 안 됨 (enable 로 다시 로드)"
        cfg = ss.config()
        detail = f"{cfg['root']} → http://{ss.HOST}:{cfg['port']}/"
        if main_checkout() and spec != ss.spec():
            script = next((a for a in (spec or {}).get("ProgramArguments", []) if a.endswith(".py")), "?")
            detail += f" · 등록된 내용이 이 체크아웃과 다름: {script} (enable 로 다시 씀)"
        return "on", detail

    def enable(self, here):
        if sys.platform != "darwin":
            return 1, "macOS LaunchAgent 전용이다. 다른 OS 는 서비스 관리자로 `share_server.py serve` 를 띄운다"
        if ss.loaded() and ss.plist_spec() == ss.spec():
            return 0, "이미 켜져 있음"
        refusal = worktree_refusal(here)
        if refusal:
            return 1, refusal
        code = ss.cmd_install(here)
        return code, "실패 (위 출력)" if code else "켬"

    def disable(self):
        if sys.platform != "darwin":
            return 0, "macOS LaunchAgent 전용(이 OS 에는 등록된 것이 없다)"
        if ss.plist_spec() is None and not ss.loaded():
            return 0, "이미 꺼져 있음"
        return ss.cmd_uninstall(), "끔"


# ---------------------------------------------------------------- watch-prs, watch-mentions (Orca automation)

class Watch(OrcaJob):
    """A watch skill's Orca automation (`<skill>/scripts/<key>.py schedule --write`). It needs the skill's key in
    agent-skills.json, so a PC without the personal config reports `unconfigured` and `apply` passes it by."""

    def __init__(self, name, what):
        self.name, self.job_name, self.key = name, f"agent-skills {name}", name.removeprefix("watch-")
        self.script = REPO / "skills" / name / "scripts" / f"{self.key}.py"
        self.what = f"{what}. agent-skills.json 의 \"{self.key}\" 키가 있을 때만 켠다"

    def configured(self):
        try:
            section = planned(self.key)
            return bool(section) and set(section) != {"model"}  # the round's model alone does not turn the watch on
        except (AttributeError, TypeError):
            return False

    def model(self):
        return config_model(self.key)

    def status(self):
        a, err = self.find()
        if a is None:
            return "unknown", err
        if a:
            return ("on" if a.get("enabled") else "off"), f"{a.get('rrule')}"
        if not self.configured():
            return "unconfigured", f"agent-skills.json 에 \"{self.key}\" 키가 없음"
        return "absent", "등록 안 됨"

    def enable(self, here):
        a, err = self.find()
        if a is None:
            return 1, err
        if a:
            if a.get("enabled"):
                return self.refresh_model() or (0, "이미 켜져 있음")
            model = f"--model {self.model()}" if self.model() and a.get("agentId", "claude") == "claude" else None
            extra = [f"--extra-agent-args={model}"] if model and a.get("extraAgentArgs") != model else []
            p = sh([ORCA, "automations", "edit", "--id", a["id"], *extra, "--enabled", "--json"])
            return (1, f"orca automations edit 실패: {(p.stderr or p.stdout).strip()[:200]}") if p.returncode else (0, "켬")
        if not self.configured():
            return 1, f"agent-skills.json 에 \"{self.key}\" 키가 없어 켜지 않는다({self.name} 스킬의 설정 절)"
        refusal = worktree_refusal()
        if refusal:
            return 1, refusal
        p = sh([sys.executable, str(self.script), "schedule", "--write"])
        return p.returncode, (p.stdout + p.stderr).strip()[-300:]


class ReflectStanding(OrcaJob):
    """The reflect standing round (`skill_usage.py schedule --write`). Not an automation `setup` turns on or off: it
    is here only so `apply` brings its model in line with reflect.model."""
    name = "reflect-standing"
    job_name = "agent-skills reflect standing"

    def model(self):
        return config_model("reflect")


REFLECT = ReflectStanding()

AUTOMATIONS = (Janitor(), JanitorLoad(), PreviewWatch(), *SHELL_JOBS, ShareServer(),
               Watch("watch-prs", "나를 지명한 리뷰 요청과 내 PR 의 리뷰 대기를 오케스트레이터나 리드에게 모아 보내는 Orca automation"),
               Watch("watch-mentions", "공개 채널의 멘션·참여 스레드 답글을 오케스트레이터나 리드에게 모아 보내는 Orca automation"))
LABEL = {"on": "켜짐", "off": "꺼짐", "absent": "꺼짐(미설정)", "unknown": "확인 불가", "unsupported": "미지원",
         "unconfigured": "꺼짐(설정 키 없음)"}


def rows():
    out = []
    for a in AUTOMATIONS:
        state, detail = a.status()
        out.append({"name": a.name, "default": "on" if a.default else "off", "state": state, "detail": detail,
                    "what": a.what})
    return out


def cmd_status(as_json):
    rs = rows()
    if as_json:
        print(json.dumps(rs, ensure_ascii=False, indent=2))
        return 0
    for r in rs:
        print(f"{r['name']}: {LABEL[r['state']]} (기본 {r['default']}) · {r['detail']}\n  {r['what']}")
    for line in land_drift():
        print(line)
    print("\n켜기: skills-sync setup enable <이름>   끄기: skills-sync setup disable <이름>")
    return 0


def land_drift():
    """land_exec.py drift's warning lines (nothing with the executor off); one line when it cannot finish."""
    try:
        p = subprocess.run([sys.executable, "-I", str(LAND_EXEC), "drift"], capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        return ["경고: land executor 범위 점검이 90초 안에 끝나지 않음 (orch land-exec drift)"]
    if p.returncode:
        return [f"경고: land executor 범위 점검 실패: {(p.stderr or p.stdout).strip()[:200]}"]
    return p.stdout.splitlines()


def report(a, code, msg):
    print(f"{a.name}: {msg}", file=sys.stderr if code else sys.stdout)
    return code


def decide(a):
    """(verb, state, detail, message) for one automation: what `apply` does, which the dry run lists before it does it.
    verb is install (never set up), refresh (on, but the registered job differs from this checkout's), skip or fail."""
    state, detail = a.status()
    if not a.default:
        return "skip", state, detail, f"기본 off, 그대로 둠 ({LABEL[state]})"
    if state == "absent":
        return "install", state, detail, detail
    if state == "unknown":
        return "fail", state, detail, detail
    if state == "on" and hasattr(a, "stale") and a.stale():
        return "refresh", state, detail, detail
    return "skip", state, detail, f"{LABEL[state]}, 그대로 둠" + (" (사용자가 끔)" if state == "off" else "")


def plan(config=None):
    """The lines setup.py's dry run shows: one per automation `apply` would install or refresh, and one per default-on
    automation it cannot (unknown, or unconfigured with the reason). Nothing is written. `config` is the
    agent-skills.json the run is about to write, which the later steps read."""
    global PLANNED_CONFIG
    PLANNED_CONFIG, out = config, []
    try:
        for a in AUTOMATIONS:
            verb, state, detail, _ = decide(a)
            if verb in ("install", "refresh"):
                out.append(f"automation {a.name}: {'install' if verb == 'install' else 'refresh the registered job'}")
            elif verb == "fail" or (a.default and state == "unconfigured"):
                out.append(f"automation {a.name}: 건너뜀, {detail}")
    finally:
        PLANNED_CONFIG = None
    return out


def cmd_apply(here):
    code = 0
    for a in AUTOMATIONS:
        verb, state, detail, msg = decide(a)
        if verb in ("install", "refresh"):
            code |= report(a, *a.enable(here))
        elif verb == "fail":
            code |= report(a, 1, detail)
        else:
            report(a, 0, msg)
            # Only the model argument: on or off stays as the user left it, and the rest of the job is `enable`'s.
            if isinstance(a, OrcaJob) and state in ("on", "off") and (r := a.refresh_model()):
                code |= report(a, *r)
    if r := REFLECT.refresh_model():
        code |= report(REFLECT, *r)
    return code


def main(argv):
    cmd = argv[0] if argv and not argv[0].startswith("-") else "status"
    here = "--here" in argv
    if cmd == "status":
        return cmd_status("--json" in argv)
    if cmd == "apply":
        return cmd_apply(here)
    if cmd in ("enable", "disable") and len(argv) > 1:
        a = next((a for a in AUTOMATIONS if a.name == argv[1]), None)
        if a is None:
            print(f"모르는 자동화: {argv[1]}. 있는 것: {', '.join(x.name for x in AUTOMATIONS)}", file=sys.stderr)
            return 2
        return report(a, *(a.enable(here) if cmd == "enable" else a.disable()))
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
