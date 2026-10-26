#!/usr/bin/env python3
"""Check this PC against what agent-skills needs, and against a baseline exported from another PC. Read-only.

Usage:
  setup_check.py check [--baseline FILE] [--json]   one table: status, item, have, next step
  setup_check.py export                             print a baseline of this PC to stdout (no env values)
  setup_check.py automode-spec --baseline FILE      print a tune-automode spec adding the baseline's missing rules

It never writes a file. Every "next" is a line for the human to run or a skill to hand the change to.
Paths follow CLAUDE_CONFIG_DIR (default ~/.claude), CLAUDE_SETTINGS, AGENT_SKILLS_STATE and
AGENT_SKILLS_CONFIG_DIR (default ~/.config/agent-skills).
"""
import datetime, json, os, pathlib, re, shutil, subprocess, sys

REPO = pathlib.Path(__file__).resolve().parents[3]
UPSTREAM = "https://github.com/JeongJaeSoon/agent-skills"
MARKET = "jeongjaesoon"
MARKET_ID = f"agent-skills@{MARKET}"
LABEL = "io.github.jeongjaesoon.agent-skills-sync"  # bootstrap.py's launchd job
ENV_KEY = "CLAUDE_CODE_PLUGIN_DIRS"
TOOLS = ("git", "python3", "claude", "gh", "orca")
# The agent-skills.json keys whose absence turns a feature off (docs/configuration-migration.md).
CONFIG_KEYS = ("tracker.adapter", "notes.adapter", "reflect.ledger", "reflect.skill_repository", "reap.user_folders")
# A rule that carries a credential itself, not one that talks about tokens: known prefixes or a long opaque string.
SECRETISH = re.compile(r"\b(ghp_|gho_|github_pat_|sk-|xox[abpr]-|AKIA)|(?=[\w+=-]*\d)[\w+=-]{40,}")
UPDATE_LINES = [f"! claude plugin marketplace update {MARKET}", f"! claude plugin update {MARKET_ID}"]


def paths():
    cfg = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()
    return {
        "cfg": cfg,
        "settings": pathlib.Path(os.environ.get("CLAUDE_SETTINGS") or cfg / "settings.json").expanduser(),
        "skills_json": cfg / "agent-skills.json",
        "installed": cfg / "plugins" / "installed_plugins.json",
        "state": pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser(),
        "local": pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG_DIR") or "~/.config/agent-skills").expanduser(),
    }


def run(cmd, timeout=20):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def read_json(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def leaf_keys(d, prefix=""):
    out = []
    for k, v in (d or {}).items():
        key = f"{prefix}{k}"
        out += leaf_keys(v, key + ".") if isinstance(v, dict) and v else [key]
    return out


def dig(d, dotted):
    for part in dotted.split("."):
        if not isinstance(d, dict) or part not in d:
            return None
        d = d[part]
    return d


def automode_rules(settings):
    am = settings.get("autoMode") or {}
    return {s: [r for r in am.get(s) or [] if r != "$defaults"] for s in ("allow", "soft_deny", "environment")}


def personal(cfg):
    names = lambda d: sorted(p.name for p in (cfg / d).iterdir() if not p.name.startswith(".")) if (cfg / d).is_dir() else []
    return {"CLAUDE.md": (cfg / "CLAUDE.md").is_file(), "skills": names("skills"), "agents": names("agents")}


def export():
    p = paths()
    s = read_json(p["settings"], {})
    allow = (s.get("permissions") or {}).get("allow") or []
    rules = automode_rules(s)
    return {
        "exported_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "language": s.get("language"),
        "outputStyle": s.get("outputStyle"),
        "defaultMode": (s.get("permissions") or {}).get("defaultMode"),
        "autoMode": {k: [r for r in v if not SECRETISH.search(r)] for k, v in rules.items()},
        "permissions_allow": [r for r in allow if not SECRETISH.search(r)],
        "skipped_secretish": sum(1 for r in allow + sum(rules.values(), []) if SECRETISH.search(r)),
        "agent_skills_keys": leaf_keys(read_json(p["skills_json"], {})),
        "personal": personal(p["cfg"]),
    }


# ---------------------------------------------------------------- checks; each returns rows

def row(status, item, have, nxt=""):
    return {"status": status, "item": item, "have": have, "next": nxt}


def check_tools(run=run):
    rows = []
    for t in TOOLS:
        w = shutil.which(t)
        rows.append(row("ok", t, w) if w else row("missing", t, "PATH 에 없음", f"{t} 설치"))
    if shutil.which("gh"):
        rc, _ = run(["gh", "auth", "status"])
        rows.append(row("ok", "gh auth", "로그인됨") if rc == 0 else row("missing", "gh auth", "로그인 안 됨", "! gh auth login"))
    _, sign = run(["git", "config", "--global", "--get", "commit.gpgsign"])
    _, key = run(["git", "config", "--global", "--get", "user.signingkey"])
    if sign == "true" and key:
        rows.append(row("ok", "git 서명", "commit.gpgsign=true, signingkey 있음"))
    else:
        rows.append(row("missing", "git 서명", f"commit.gpgsign={sign or '-'}, signingkey {'있음' if key else '없음'}",
                        "서명 키를 만들고 git config --global commit.gpgsign true · user.signingkey 설정"))
    return rows


def install_mode(settings, installed):
    """('inline', checkout) / ('marketplace', sha) / ('both', checkout) / ('none', None)."""
    dirs = [pathlib.Path(d).expanduser() for d in ((settings.get("env") or {}).get(ENV_KEY) or "").split(os.pathsep) if d]
    checkout = next((d for d in dirs if (d / "skills").is_dir() and (d / ".claude-plugin").is_dir()), None)
    market_on = bool((settings.get("enabledPlugins") or {}).get(MARKET_ID))
    entry = ((installed.get("plugins") or {}).get(MARKET_ID) or [{}])[0]
    if checkout and market_on:
        return "both", checkout
    if checkout:
        return "inline", checkout
    if market_on and entry:
        return "marketplace", entry.get("gitCommitSha")
    return "none", None


def check_install(settings, installed, p, run=run, darwin=sys.platform == "darwin", now=None):
    mode, where = install_mode(settings, installed)
    _, remote = run(["git", "ls-remote", UPSTREAM, "refs/heads/main"])
    remote = remote.split()[0] if remote else ""
    if mode == "none":
        return [row("missing", "agent-skills 설치", "로드 안 됨",
                    f"고치며 쓰면 clone 뒤 python3 <체크아웃>/scripts/bootstrap.py --write, 쓰기만 하면 "
                    f"! claude plugin marketplace add JeongJaeSoon/agent-skills · ! claude plugin install {MARKET_ID}")]
    if mode == "both":
        return [row("warn", "agent-skills 설치", f"체크아웃 {where} 와 {MARKET_ID} 둘 다 켜짐 (hook 두 번)",
                    f"python3 {where}/scripts/bootstrap.py --write 가 {MARKET_ID} 를 끈다")]
    if mode == "marketplace":
        rows = [row("ok", "agent-skills 설치", f"마켓플레이스 {MARKET_ID} @ {(where or '?')[:7]}")]
        if not remote:
            rows.append(row("warn", "최신 여부", "origin main 을 읽지 못함 (네트워크)", "다시 실행"))
        elif where == remote:
            rows.append(row("ok", "최신 여부", f"origin main {remote[:7]} 과 같음"))
        else:
            rows.append(row("differs", "최신 여부", f"설치 {(where or '?')[:7]} · origin main {remote[:7]}",
                            " ; ".join(UPDATE_LINES) + " ; 그 뒤 떠 있는 세션에서 /reload-plugins"))
        return rows
    rows = [row("ok", "agent-skills 설치", f"체크아웃 {where} (CLAUDE_CODE_PLUGIN_DIRS)")]
    rc, branch = run(["git", "-C", str(where), "rev-parse", "--abbrev-ref", "HEAD"])
    _, head = run(["git", "-C", str(where), "rev-parse", "HEAD"])
    if (where / ".git").is_file() or branch != "main":
        rows.append(row("warn", "체크아웃", f"{branch or '?'} 브랜치{' (worktree)' if (where / '.git').is_file() else ''}",
                        "세션이 읽는 체크아웃은 main 의 메인 체크아웃이어야 한다"))
    if remote and head and head != remote:
        rows.append(row("differs", "최신 여부", f"체크아웃 {head[:7]} · origin main {remote[:7]}",
                        "! skills-sync sync 뒤 skills-sync broadcast"))
    elif remote:
        rows.append(row("ok", "최신 여부", f"origin main {remote[:7]} 과 같음"))
    sync = read_json(p["state"] / "sync.json", None)
    if not sync:
        rows.append(row("missing", "자동 동기화", "sync.json 없음 (한 번도 안 돎)", f"python3 {where}/scripts/bootstrap.py --write"))
    else:
        at = datetime.datetime.strptime(sync["at"], "%Y-%m-%dT%H:%M:%S%z")
        age = ((now or datetime.datetime.now(at.tzinfo)) - at).total_seconds()
        stale = age > 2 * sync.get("interval", 900)
        if not sync.get("ok") or stale:
            rows.append(row("warn", "자동 동기화", f"{sync.get('message')} · {int(age // 60)}분 전",
                            "! skills-sync status 로 이유를 보고 고친 뒤 ! skills-sync sync"))
        else:
            rows.append(row("ok", "자동 동기화", f"{sync.get('message')} · {int(age // 60)}분 전"))
    if darwin:
        rc, _ = run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"])
        if rc:
            rows.append(row("missing", "launchd 동기화 작업", "로드 안 됨", f"python3 {where}/scripts/bootstrap.py --write"))
    return rows


def check_automations(run=run):
    rc, out = run([sys.executable, str(REPO / "scripts" / "automations.py"), "status", "--json"], timeout=60)
    try:
        items = json.loads(out)
    except ValueError:
        return [row("warn", "정기 자동화", f"상태를 읽지 못함 (rc {rc})", "! skills-sync setup")]
    off = [a["name"] for a in items if a.get("default") == "on" and a.get("state") != "on"]
    if off:
        return [row("missing", "정기 자동화", f"기본 on 인데 꺼짐: {', '.join(off)}", "메인 체크아웃에서 ! skills-sync setup apply")]
    return [row("ok", "정기 자동화", "기본 on 전부 켜짐")]


def check_settings(settings, base):
    rows = []
    perms = settings.get("permissions") or {}
    for key, have, how in (("language", settings.get("language"), "/config"),
                           ("outputStyle", settings.get("outputStyle"), "/config"),
                           ("permissions.defaultMode", perms.get("defaultMode"), "update-config")):
        want = (base or {}).get(key.split(".")[-1])
        if base and want != have:
            rows.append(row("differs", key, f"{have!r} (기준 {want!r})", f"{how} 로 {want!r}"))
        else:
            rows.append(row("ok" if have else "info", key, repr(have) if have else "미설정 (모델 기본값)"))
    rules = automode_rules(settings)
    count = ", ".join(f"{k} {len(v)}" for k, v in rules.items())
    if base:
        missing = sum(len(set((base.get("autoMode") or {}).get(k) or []) - set(v)) for k, v in rules.items())
        rows.append(row("differs", "autoMode", f"{count} · 기준보다 {missing}개 없음",
                        "automode-spec --baseline 으로 spec 을 만들어 tune-automode emit") if missing
                    else row("ok", "autoMode", count))
        lack = sorted(set(base.get("permissions_allow") or []) - set(perms.get("allow") or []))
        rows.append(row("differs", "permissions.allow", f"기준보다 {len(lack)}개 없음: {', '.join(lack[:3])}{' …' if len(lack) > 3 else ''}",
                        "update-config 로 추가") if lack else row("ok", "permissions.allow", f"{len(perms.get('allow') or [])}개"))
    else:
        rows.append(row("ok" if any(rules.values()) else "info", "autoMode", count if any(rules.values()) else "사용자 규칙 없음 (기본 분류기만)",
                        "" if any(rules.values()) else "막히는 일이 생기면 tune-automode, 처음부터는 /auto-mode-setup"))
        rows.append(row("info", "permissions.allow", f"{len(perms.get('allow') or [])}개"))
    return rows


def check_config(p, base):
    cfg = read_json(p["skills_json"], None)
    rows = []
    if cfg is None:
        rows.append(row("info", "agent-skills.json", "없음 (트래커·노트·회고 쓰기 꺼짐)",
                        "원래 PC 에서 복사하거나 각 스킬의 설정 절대로 만든다"))
        cfg = {}
    else:
        unset = [k for k in CONFIG_KEYS if dig(cfg, k) is None]
        rows.append(row("info" if unset else "ok", "agent-skills.json", f"미설정: {', '.join(unset)}" if unset else "주요 키 있음"))
    if base:
        lack = sorted(set(base.get("agent_skills_keys") or []) - set(leaf_keys(cfg)))
        if lack:
            rows.append(row("differs", "agent-skills.json 키", f"기준보다 없음: {', '.join(lack)}", "원래 PC 의 값을 옮긴다"))
    names = p["local"] / "internal-names.txt"
    rows.append(row("ok", "internal-names.txt", str(names)) if names.is_file()
                else row("info", "internal-names.txt", "없음", "공개 저장소에 쓰는 PC 면 write-skill §4 의 목록을 옮긴다"))
    mine = personal(p["cfg"])
    if base:
        b = base.get("personal") or {}
        lack = ([] if mine["CLAUDE.md"] or not b.get("CLAUDE.md") else ["CLAUDE.md"]) + \
            [f"skills/{n}" for n in sorted(set(b.get("skills") or []) - set(mine["skills"]))] + \
            [f"agents/{n}" for n in sorted(set(b.get("agents") or []) - set(mine["agents"]))]
        if lack:
            rows.append(row("differs", "개인 지침·스킬", f"없음: {', '.join(lack)}", "원래 PC 의 ~/.claude 에서 복사"))
        else:
            rows.append(row("ok", "개인 지침·스킬", "기준과 같음"))
    else:
        rows.append(row("info", "개인 지침·스킬",
                        f"CLAUDE.md {'있음' if mine['CLAUDE.md'] else '없음'}, skills {len(mine['skills'])}, agents {len(mine['agents'])}"))
    return rows


def check(base=None):
    p = paths()
    settings = read_json(p["settings"], {})
    rows = check_tools(run) + check_install(settings, read_json(p["installed"], {}), p, run)
    if install_mode(settings, {})[0] in ("inline", "both"):
        rows += check_automations(run)
    return rows + check_settings(settings, base) + check_config(p, base)


def automode_spec(base):
    have = automode_rules(read_json(paths()["settings"], {}))
    add = {k: [r for r in v if r not in have[k]] for k, v in (base.get("autoMode") or {}).items()}
    return {"add": {k: v for k, v in add.items() if v}}


def table(rows):
    cell = lambda s: str(s).replace("|", "\\|")
    lines = ["| 상태 | 항목 | 지금 | 다음 |", "|---|---|---|---|"]
    lines += [f"| {r['status']} | {cell(r['item'])} | {cell(r['have'])} | {cell(r['next'])} |" for r in rows]
    left = sum(r["status"] in ("missing", "differs", "warn") for r in rows)
    return "\n".join(lines) + f"\n\n남은 것 {left}개 / 전체 {len(rows)}개"


def main(argv):
    if not argv or argv[0] not in ("check", "export", "automode-spec"):
        print(__doc__)
        return 2
    base = None
    if "--baseline" in argv:
        base = read_json(pathlib.Path(argv[argv.index("--baseline") + 1]).expanduser(), None)
        if base is None:
            print("baseline 을 읽지 못했다", file=sys.stderr)
            return 1
    if argv[0] == "export":
        print(json.dumps(export(), indent=2, ensure_ascii=False))
    elif argv[0] == "automode-spec":
        if base is None:
            print("--baseline FILE 이 필요하다", file=sys.stderr)
            return 2
        print(json.dumps(automode_spec(base), indent=2, ensure_ascii=False))
    else:
        rows = check(base)
        print(json.dumps(rows, indent=2, ensure_ascii=False) if "--json" in argv else table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
