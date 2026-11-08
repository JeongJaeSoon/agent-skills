#!/usr/bin/env python3
"""PreToolUse hook of the agent-skills plugin: a `gh pr merge` in a repo the land executor merges goes through `orch land`.

With `orchestrate.land_executor` on, a reviewed PR in a covered repo lands through `orch land` (orchestrate
references/landing.md, "Land executor"); a direct `gh pr merge` skips the executor's checks, and auto mode refuses it
as a merge without review, which used to leave the session handing a merge line to the owner. This hook speaks only
for an orchestrator, a lead or a worker (main conversation or subagent), or a session whose first prompt has a
`PROGRAM: <slug>` line, and only while the executor is enabled:

- a covered repo (skills/orchestrate/scripts/land_scope.py): deny, with the `orch land` line for the program that
  holds the PR (else the brief's PROGRAM, else `<name>-standalone --repo OWNER/NAME`), and the `orch verdict` line to
  run only after an independent review of that head. OWNER/* covers the gh account's own repos only, as the
  executor rules: an org's repo is covered when an entry names it
- a program's PR whose repo GitHub renamed out of the scope (`orch land` refuses it): deny, with the spec that adds
  the new name
- the gh account's own repo that the scope leaves out: pass with a note of the skills-config spec that adds it, or
  `exclude` to keep it out on purpose (an excluded repo passes silently)
- any other repo (an org's, another user's): pass, so its human review and approval flow is unchanged
- a call that changes directory before `gh pr merge` names no repo this hook can trust: deny until `--repo` is given

The level is `orchestrate.land_route` in ~/.claude/agent-skills.json: deny by default, warn, or off. Settings that
cannot be read deny, as `orch land` stops on them; a PR GitHub does not answer for passes with one warning line.
`--disable-auto` and `--help` pass. A merge inside a script or `bash -c`, or through `gh api`, is not seen: a
guardrail, not a sandbox; the executor round re-checks the repo's current name, owner, fork and archive state.
"""
import json, os, pathlib, re, subprocess, sys

import roles
from lib import config, shell

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "skills" / "orchestrate" / "scripts"))
import land_scope  # noqa: E402

KEY = "orchestrate.land_route"
MARK = "[gate:land-route]"
SOURCE = f"(agent-skills hooks/land_route_gate.py, {KEY})"
PROGRAM_LINE = re.compile(r"^[ \t]*PROGRAM:?[ \t]+([\w.-]+)[ \t]*$", re.M)
PR_URL = re.compile(r"https?://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
VALUE_FLAGS = {"-b", "--body", "-F", "--body-file", "-t", "--subject", "-A", "--author-email", "--match-head-commit"}
GH_TIMEOUT = 5
EMIT = pathlib.Path(__file__).resolve().parents[1] / "skills" / "skills-config" / "scripts" / "config_emit.py"


class Unread(Exception):
    """A fact this hook needs could not be read; the call passes with this one line."""


class Refuse(Exception):
    """The call is denied with this text whatever the scope says."""


def repo_flag(w, nxt):
    """(repo, words used) for `-R x`, `--repo x`, `-Rx`, `--repo=x`; (None, 0) otherwise."""
    if w in ("-R", "--repo"):
        return (nxt, 2) if nxt is not None else (None, 1)
    if w.startswith("--repo="):
        return w[7:], 1
    if w.startswith("-R") and len(w) > 2:
        return w[2:], 1
    return None, 0


def parse(words):
    """(repo option, positionals, flags) of a `gh [-R x] pr merge …`, or None when the words are not one."""
    j, repo = 1, None
    while j < len(words) and words[j].startswith("-"):
        got, used = repo_flag(words[j], words[j + 1] if j + 1 < len(words) else None)
        repo, j = got or repo, j + (used or 1)
    if words[j:j + 2] != ["pr", "merge"]:
        return None
    args, pos, flags, k = words[j + 2:], [], set(), 0
    while k < len(args):
        w = args[k]
        if w == "--":
            pos += args[k + 1:]
            break
        got, used = repo_flag(w, args[k + 1] if k + 1 < len(args) else None)
        if used:
            repo, k = got or repo, k + used
            continue
        if w.startswith("-"):
            flags.add(w.split("=")[0])
            k += 2 if w in VALUE_FLAGS else 1
            continue
        pos.append(w)
        k += 1
    return repo, pos, flags


def merges(cmd):
    """[(Command, repo option, positionals)] for each `gh pr merge` in the call that merges (not `--disable-auto`,
    not `--help`), and whether an earlier command in the call changes directory or GH_REPO."""
    out, moved = [], []
    for c in shell.commands(cmd):
        if c.name in ("cd", "pushd", "popd") or (c.name == "export" and any(w.startswith("GH_REPO") for w in c.words)):
            moved.append(c.span[0])
        if c.name != "gh":
            continue
        got = parse(c.words)
        if not got:
            continue
        repo, pos, flags = got
        if flags & {"--disable-auto", "--help", "-h"}:
            continue
        env = next((a.split("=", 1)[1] for a in c.assigns if a.startswith("GH_REPO=")), None)
        out.append((c, repo, env, pos, any(s < c.span[0] for s in moved)))
    return out


def gh(args, cwd):
    try:
        r = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=GH_TIMEOUT, cwd=cwd or None)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Unread(f"gh {' '.join(args[:2])} gave no answer ({type(e).__name__})")
    if r.returncode != 0:
        raise Unread(f"gh {' '.join(args[:2])} failed")
    try:
        return json.loads(r.stdout)
    except ValueError:
        raise Unread(f"gh {' '.join(args[:2])} answered no JSON")


class OtherHost(Exception):
    """The repo is on a GitHub host other than github.com, which the executor's scope does not name."""


def plain(repo):
    """OWNER/NAME of gh's `[HOST/]OWNER/NAME` (a URL too); OtherHost for a host that is not github.com."""
    if not repo:
        return None
    r = re.sub(r"^https?://", "", repo).rstrip("/").removesuffix(".git")
    if r.count("/") == 2:
        host, _, r = r.partition("/")
        if host.casefold() not in ("github.com", "www.github.com"):
            raise OtherHost(host)
    return r


def target(repo_opt, env, pos, moved, cwd):
    """(repo, PR number or None, head sha or None) that this `gh pr merge` acts on."""
    repo_opt, env = plain(repo_opt), plain(env)
    urls = [PR_URL.search(p) for p in pos]
    named = {r.casefold(): r for r in [repo_opt, env, *(u[1] for u in urls if u)] if r}
    if len(named) > 1:
        raise Refuse(f"this `gh pr merge` names more than one repo ({', '.join(sorted(named.values()))}): name one")
    arg = pos[0] if pos else None
    number = next((int(u[2]) for u in urls if u), None) or (int(arg.lstrip("#")) if arg and arg.lstrip("#").isdigit()
                                                            else None)
    if named:
        repo = next(iter(named.values()))
        try:
            v = gh(["pr", "view", *([arg] if arg else []), "--repo", repo, "--json", "number,headRefOid,url"], cwd)
        except Unread:
            return repo, number, None
        m = PR_URL.search(v.get("url") or "")  # the repo's current name, when an old one was given
        return (m[1] if m else repo), v.get("number") or number, v.get("headRefOid")
    if moved:
        raise Refuse("this call changes directory (or GH_REPO) before `gh pr merge`, so its repo is unknown here: "
                     "name it with `--repo OWNER/NAME`")
    v = gh(["pr", "view", *([arg] if arg else []), "--json", "number,headRefOid,url"], cwd)
    m = PR_URL.search(v.get("url") or "")
    if not m:
        raise Unread("gh pr view gave no PR url")
    return m[1], v.get("number") or int(m[2]), v.get("headRefOid")


def executor():
    """The enabled land_executor settings with their parsed scope, or None. Unreadable settings refuse."""
    path = config.path()
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except OSError as e:
        raise Refuse(f"{path} is unreadable ({type(e).__name__}), and `orch land` stops on it too: fix the file")
    try:
        cfg = (json.loads(text).get("orchestrate") or {}).get("land_executor")
    except (ValueError, AttributeError):
        if "land_executor" in text:
            raise Refuse(f"{path} cannot be parsed, and `orch land` stops on it too: fix the file (skills-config)")
        return None
    if not isinstance(cfg, dict) or cfg.get("enabled") is not True:
        return None
    try:
        return cfg, land_scope.parse(cfg)
    except land_scope.ScopeError as e:
        raise Refuse(f"{e}, and `orch land` stops on it too: fix it with skills-config")


def programs_home():
    return pathlib.Path(os.environ.get("PROGRAMS_HOME") or "~/.claude/programs").expanduser()


def same_repo(recorded, repo, asked, cwd):
    """A program's recorded repo is this one: by either name given, else by where GitHub sends the recorded name now
    (a program made before a rename keeps the old one). A lookup that fails refuses: guessing "another repo" would
    hand out a fresh standalone program over a program's own gate."""
    if recorded.casefold() in {repo.casefold(), (asked or repo).casefold()}:
        return True
    try:
        return str(gh(["api", f"repos/{recorded}"], cwd).get("full_name", "")).casefold() == repo.casefold()
    except Unread:
        raise Refuse(f"could not tell whether {recorded}, which a program records, is {repo} now: name the program,"
                     " `orch land <slug> --pr N`")


def holders(repo, pr, asked, cwd):
    """[(slug, merge_policy)] of the programs on `repo` (under any of its names) whose ledger records `pr`."""
    out = []
    for f in sorted(programs_home().glob("*/program.json")):
        try:
            cfg = json.loads(f.read_text())
            with open(f.parent / "ledger.jsonl", encoding="utf-8") as led:
                if not any(json.loads(l).get("pr") == pr for l in led if l.strip()):
                    continue
        except (OSError, ValueError, AttributeError):
            continue
        if same_repo(str(cfg.get("repo", "")), repo, asked, cwd):
            out.append((f.parent.name, cfg.get("merge_policy"), str(cfg.get("repo", ""))))
    return out


def brief_program(first, repo, asked, cwd):
    """The brief's PROGRAM slug when that program is on `repo`, else None."""
    m = PROGRAM_LINE.search(first or "")
    if not m:
        return None
    try:
        cfg = json.loads((programs_home() / m[1] / "program.json").read_text())
    except (OSError, ValueError):
        return None
    recorded = str(cfg.get("repo", ""))
    return (m[1], recorded) if same_repo(recorded, repo, asked, cwd) else None


def route(repo, pr, first, asked=None, cwd=None):
    """(slug, needs --repo, merge_policy) of the program `orch land` should use for this PR."""
    held = holders(repo, pr, asked, cwd) if pr else []
    if len(held) > 1:
        raise Refuse(f"{repo}#{pr} is in more than one program ({', '.join(h[0] for h in held)}): land it from the one"
                     " that owns it with `orch land <slug> --pr N`")
    if held:
        return held[0][0], False, held[0][1], held[0][2]
    if found := brief_program(first, repo, asked, cwd):
        return found[0], False, None, found[1]
    return f"{repo.split('/')[-1]}-standalone", True, None, None


def land_lines(repo, pr, head, slug, with_repo, policy, sources):
    at = f" --repo {repo}" if with_repo else ""
    n = pr or "<N>"
    text = (f"Land it with `orch land {slug}{at} --pr {n} --wait-minutes 50`. That lands only on a pass verdict at the "
            f"head ({(head or '<head sha>')[:12]}) from {' or '.join(sources)}: after that review, and only then, record "
            f"it with `orch verdict {slug}{at} --pr {n} --sha {head or '<head sha>'} --source <who reviewed> "
            "--evidence <link to that review>`. With no "
            "review of this head, get it first (deliver-ticket §3).")
    if policy == "human-gate":
        text += f" {slug} is human-gate: `orch land` asks the human, so report READY with READY-WAIT: owner-merge."
    return text


def scope_spec(cfg, repo):
    repos = [r for r in cfg.get("repos") or [] if isinstance(r, str)]
    one = {"agent_skills": {"set": {"orchestrate.land_executor.repos": repos + [repo]}}}
    owner = {"agent_skills": {"set": {"orchestrate.land_executor.repos": repos + [f"{repo.split('/')[0]}/*"]}}}
    return json.dumps(one), json.dumps(owner)


def covers(scope, repo, login):
    """land_scope.how, with OWNER/* of anyone but the gh account's login covering nothing (land_scope.owner_refused)."""
    how = land_scope.how(scope, repo)
    return None if how == "owner" and land_scope.owner_refused(scope, repo, login()) else how


def check(event, cmd):
    """(text, deny) for this call, or None when it passes: deny False is a note the call passes with."""
    found = merges(cmd)
    if not found:
        return None
    first = roles.first_prompt(event.get("transcript_path"))
    who = roles.session(event, first)
    if not roles.applies("land_route", who) and not PROGRAM_LINE.search(first or ""):
        return None
    ex = executor()
    if not ex:
        return None
    cfg, scope = ex
    sources = cfg.get("verdict_sources") or ["review:codex"]
    sources = sources if isinstance(sources, list) else [str(sources)]
    cwd = event.get("cwd")
    known, notes = [], []  # a note waits for the call's other merges: any one of them may deny

    def login():
        if not known:
            known.append(str(gh(["api", "user"], cwd).get("login", "")))
        return known[0]

    for _c, repo_opt, env, pos, moved in found:
        try:
            repo, pr, head = target(repo_opt, env, pos, moved, cwd)
            asked = plain(repo_opt) or plain(env)
        except OtherHost:
            continue
        if asked and land_scope.excluded(scope, asked):  # an exclusion under the old name still holds
            continue
        if covers(scope, repo, login):
            slug, with_repo, policy, _ = route(repo, pr, first, asked, cwd)
            return (f"{MARK} {SOURCE} {repo} is in orchestrate.land_executor's scope: its PRs merge through the land "
                    f"executor, not `gh pr merge`. {land_lines(repo, pr, head, slug, with_repo, policy, sources)} "
                    "Do not hand a `gh pr merge` line to the owner. A fork or an archived repo that only OWNER/* "
                    "covers is refused by the executor: list it by name."), True
        if land_scope.excluded(scope, repo) or repo.split("/")[0].casefold() not in land_scope.owners(scope):
            continue
        personal = repo.split("/")[0].casefold() == login().casefold()
        try:
            slug, with_repo, policy, recorded = route(repo, pr, first, asked, cwd)
        except Refuse as e:  # out of scope, a program lookup that fails denies nothing
            notes += [f"{MARK} {SOURCE} {repo} is outside orchestrate.land_executor's scope; {e}"] if personal else []
            continue
        moved = bool(recorded) and recorded.casefold() != repo.casefold() and bool(covers(scope, recorded, login))
        if not personal and not moved:
            continue  # an org's or another user's repo out of scope keeps its own review and approval flow
        if not with_repo and not moved:  # a program's PR: `orch land` merges it directly while the repo is out
            notes.append(f"{MARK} {SOURCE} {repo} is outside orchestrate.land_executor's scope, and "
                         f"{repo}#{pr or '<N>'} belongs to program {slug}: `orch land` merges it directly. "
                         f"{land_lines(repo, pr, head, slug, False, policy, sources)}")
            continue
        one, owner = scope_spec(cfg, repo)
        if moved:  # prog.executor_for refuses it: covered under the recorded name only
            return (f"{MARK} {SOURCE} program {slug} records {recorded}, which GitHub calls {repo} now, and the scope "
                    f"covers only the old name, so `orch land` refuses it until the new name is in scope. Put it in "
                    f"with skills-config: write the spec {one} to a file, run `python3 {EMIT} emit --spec <that file> "
                    "--out <script file>`, and give the owner the run line it prints (or the owner adds "
                    f"{repo} to orchestrate.land_executor.exclude to merge it directly). Then: "
                    f"{land_lines(repo, pr, head, slug, False, policy, sources)} Never hand a `gh pr merge` line to "
                    "the owner."), True
        notes.append(f"{MARK} {SOURCE} {repo} is your own repo outside orchestrate.land_executor's scope, so this call "
                     "passes and merges as it would without the executor. To land its PRs through the executor, put it "
                     f"in scope with skills-config: write the spec {one} to a file (or {owner} for every repo of "
                     f"{repo.split('/')[0]}, including ones made later), run `python3 {EMIT} emit --spec <that file> "
                     "--out <script file>`, and give the owner the run line it prints. Once it is in scope: "
                     f"{land_lines(repo, pr, head, slug, True, policy, sources)} To keep {repo} out on purpose, the "
                     "owner adds it to orchestrate.land_executor.exclude, which silences this note.")
    return (" ".join(notes), False) if notes else None


def emit(decision, text):
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse"}}
    if decision:
        out["hookSpecificOutput"].update(permissionDecision=decision, permissionDecisionReason=text)
    else:
        out["systemMessage"] = text
        out["hookSpecificOutput"]["additionalContext"] = text
    return out


def run(event):
    cmd = (event.get("tool_input") or {}).get("command") if isinstance(event, dict) else None
    if not isinstance(cmd, str) or "merge" not in cmd or "gh" not in cmd:
        return None
    level = config.level(config.load(), KEY, "deny")
    if level == "off":
        return None
    try:
        try:
            text, deny = check(event, cmd) or (None, False)
        except Refuse as e:
            text, deny = f"{MARK} {SOURCE} {e}", True
        if text:
            return emit(level if deny and level != "warn" else None, text)
    except Unread as e:
        return emit(None, f"agent-skills hooks/land_route_gate.py passed this call unjudged: {e}.")
    except Exception as e:
        config.log_error("land_route_gate", e)
        return emit(None, f"agent-skills hooks/land_route_gate.py could not finish ({type(e).__name__}) and let this call through.")
    return None


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if out := run(event):
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
