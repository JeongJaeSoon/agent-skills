#!/usr/bin/env python3
"""UserPromptSubmit hook of the agent-skills plugin: closes a dashboard decision the human answered in the terminal.

A decision registered with `orch decide add` left the dashboard only when it was answered there or the coordinator ran
`orch decide done`. Answered in the coordinator's own chat, it stayed open whenever the coordinator forgot to close it.

Only the terminal that registered a decision (its handle in decisions.json, from $ORCA_TERMINAL_HANDLE) acts on it, so
a worker's prompt never closes the coordinator's decision. A prompt closes one only when it is unambiguous:
  - it starts with the id: `decision d3: yes`, `d3: yes`, `d3 yes`; the rest of the first line is the answer, or
  - its first line starts with an option label (case-insensitive, not followed by a letter or digit, so "Yes" does
    not match "Yesterday") that exactly one open decision of this terminal has.
Anything else only adds a reminder listing the open decisions to the prompt's context. Any error exits 0 silently.
"""
import json, os, pathlib, re, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "skills" / "orchestrate" / "scripts"))

ID_RE = re.compile(r"\s*(?:decision\s+)?(d\d+)(?:\s*[:：]\s*|\s+)(\S.*)", re.I)
REMIND_MAX = 5


def label_match(line, label):
    """True when the line starts with the label as a word of its own (a label shorter than two characters is too loose)."""
    n = len(label)
    return n >= 2 and line[:n].casefold() == label.casefold() and not (len(line) > n and line[n].isascii() and line[n].isalnum())


def pick(prompt, mine):
    """(decision, answer) the prompt settles unambiguously, else (None, None)."""
    line = next((l.strip() for l in prompt.splitlines() if l.strip()), "")
    m = ID_RE.fullmatch(line)
    if m:
        d = mine.get(m.group(1).lower())
        return (d, m.group(2).strip()) if d else (None, None)
    hits = []
    for d in mine.values():
        labels = [o["label"] for o in d.get("options") or [] if label_match(line, o.get("label") or "")]
        if labels:
            hits.append((d, max(labels, key=len)))  # "Yes, but later" over "Yes"
    return hits[0] if len(hits) == 1 else (None, None)


def main():
    handle = os.environ.get("ORCA_TERMINAL_HANDLE", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", handle):
        return
    event = json.load(sys.stdin)
    prompt = event.get("prompt") if isinstance(event.get("prompt"), str) else ""
    import fleet
    mine = {d["id"]: d for d in fleet.open_decisions() if d.get("handle") == handle}
    if not mine or not prompt.strip():
        return
    d, answer = pick(prompt, mine)
    if d:
        fleet.decision_close(d["id"], "done", answer[:fleet.DECISION_ANSWER_MAX])
        text = (f"This message answers the dashboard decision {d['id']} ({d['title']}): \"{answer[:300]}\". It is now "
                f"recorded as done (`orch decide done` is not needed); act on the answer.")
    else:
        listed = "; ".join(f"{x['id']} {x['title']}" + (f" (options: {', '.join(o['label'] for o in x['options'])})" if x.get("options") else "")
                           for x in list(mine.values())[:REMIND_MAX])
        text = (f"Open dashboard decisions you registered: {listed}. If this message answers or settles one, run "
                f"`orch decide done <id> --answer \"<answer>\"` now.")
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
