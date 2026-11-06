"""Settings from ~/.claude/agent-skills.json ($AGENT_SKILLS_CONFIG) for hooks, and the error log a hook writes when
its own check fails.

A rule's level is one of LEVELS. A key may hold a level, `true` (the rule's default level, or `warn` when that is
`off`) or `false` (`off`); anything else, or no key, is the default.
"""
import datetime, json, os, pathlib

LEVELS = ("deny", "ask", "warn", "off")


def path():
    return pathlib.Path(os.environ.get("AGENT_SKILLS_CONFIG") or "~/.claude/agent-skills.json").expanduser()


def state():
    return pathlib.Path(os.environ.get("AGENT_SKILLS_STATE") or "~/.local/state/agent-skills").expanduser()


def load():
    """The settings as a dict; {} when the file is missing, unreadable or not an object."""
    try:
        cfg = json.loads(path().read_text())
    except (OSError, ValueError):
        return {}
    return cfg if isinstance(cfg, dict) else {}


def get(cfg, dotted, default=None):
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def level(cfg, key, default):
    value = get(cfg, key)
    if value is True:
        return default if default != "off" else "warn"
    if value is False:
        return "off"
    return value if value in LEVELS else default


def append_warning(row):
    """One row in $AGENT_SKILLS_STATE/turn-warnings.jsonl: a turn-end check that fired as a warning, kept so a week of
    them shows each check's false-positive rate before any key goes stronger than warn. Never raises; a write that
    fails goes to guard-errors.jsonl."""
    try:
        state().mkdir(parents=True, exist_ok=True)
        with open(state() / "turn-warnings.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:
        log_error("append_warning", exc)


def log_error(hook, exc):
    """One line in $AGENT_SKILLS_STATE/guard-errors.jsonl: the time, the hook, the exception type and where it was
    raised. No message: it can quote the tool input, and that can hold a secret. Never raises."""
    try:
        tb = exc.__traceback__
        while tb and tb.tb_next:
            tb = tb.tb_next
        where = f"{pathlib.Path(tb.tb_frame.f_code.co_filename).name}:{tb.tb_lineno}" if tb else ""
        line = {"at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "hook": hook,
                "error": type(exc).__name__, "where": where}
        state().mkdir(parents=True, exist_ok=True)
        with open(state() / "guard-errors.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
    except Exception:
        pass
