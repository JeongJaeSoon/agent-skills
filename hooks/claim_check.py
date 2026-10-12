#!/usr/bin/env python3
"""Stop and SubagentStop hook of the agent-skills plugin: blocks a completion claim made without a run after the last edit.

A turn that edited code and then said "고쳤다" or "all tests pass" with no command run after the last edit reports
work nobody has seen run. The hook blocks that stop once, with the edited files and the matched claim, so the agent
runs the check or says the work is unverified. A turn is everything after the last prompt a person typed.

Passes without a decision:
  - `stop_hook_active` (the agent already got one block; blocking again could loop),
  - AGENT_SKILLS_CLAIM_CHECK=0,
  - no edit to a code file this turn (an investigation turn, or one that touched only .md, .txt or .rst files),
  - a Bash call after the last code edit,
  - no past-tense completion claim in the final reply or in a Telegram reply sent this turn, counting neither
    fenced or inline code nor quoted lines, and none at all when the reply itself says the work is not verified.
Any error exits 0 silently.
"""
import json, os, re, sys

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
PROSE_EXT = (".md", ".markdown", ".mdx", ".txt", ".rst")
TAIL_BYTES = 8 << 20  # a turn longer than this is cut to its last 8 MB, enough for any final run and claim

# Past tense only: "완료 조건" and "fix the bug" are not claims.
CLAIM = re.compile(
    r"(?:완료(?:했|됐|되었|하였)|고쳤|수정(?:했|하였|됐|되었)|해결(?:했|하였|됐|되었)|구현(?:했|하였)|마쳤|끝냈"
    r"|통과(?:했|합니다|한다)|(?:잘\s*)?(?:동작|작동)(?:합니다|한다|해요)"
    r"|\b(?:I'?ve|I have|we'?ve|has been|have been|is now|are now)\s+(?:fixed|implemented|resolved|completed)\b"
    r"|^\s*(?:[-*]\s+)?(?:fixed|implemented|resolved|done)\b"
    r"|\ball (?:the )?(?:tests|checks) pass(?:ed)?\b|\bworks now\b|\bnow works\b)",
    re.I | re.M)
UNVERIFIED = re.compile(
    r"(?:검증하지\s*않|확인하지\s*않|(?:돌리|실행하)지\s*(?:않|못)|검증\s*(?:전|안\s*함)|미검증"
    r"|\bnot (?:yet )?(?:verified|tested|run)\b|\bunverified\b|\buntested\b|NOT VERIFIED)",
    re.I)
FENCE = re.compile(r"```.*?(?:```|\Z)|~~~.*?(?:~~~|\Z)", re.S)
INLINE = re.compile(r"`[^`\n]*`")


def blocks(entry):
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


def is_prompt(entry):
    """A prompt a person typed: a user entry that carries text and is neither a tool result nor injected context."""
    if entry.get("type") != "user" or entry.get("isMeta"):
        return False
    bs = blocks(entry)
    return bool(bs) and not any(b.get("type") == "tool_result" for b in bs)


def turn(path):
    """The entries after the last typed prompt, oldest first."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        f.seek(max(0, f.tell() - TAIL_BYTES))
        lines = f.read().splitlines()
    out = []
    for raw in reversed(lines):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if is_prompt(entry):
            break
        out.append(entry)
    return out[::-1]


def strip(text):
    text = INLINE.sub(" ", FENCE.sub(" ", text))
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith(">"))


def judge(entries, last_message=None):
    """The block reason for this turn, or None."""
    edited, last_edit, ran_after, said, final = [], -1, False, [], []
    for i, entry in enumerate(entries):
        if entry.get("type") == "user":
            final = []  # the final reply is the text after the last tool result
        if entry.get("type") != "assistant":
            continue
        for b in blocks(entry):
            if b.get("type") == "text":
                final.append(b.get("text") or "")
            if b.get("type") != "tool_use":
                continue
            name, args = b.get("name") or "", b.get("input") or {}
            target = args.get("file_path") or args.get("notebook_path") or ""
            if name in EDIT_TOOLS and target and not target.lower().endswith(PROSE_EXT):
                edited.append(target)
                last_edit, ran_after = i, False
            elif name == "Bash" and last_edit >= 0:
                ran_after = True
            elif "telegram" in name.lower() and name.endswith("reply") and isinstance(args.get("text"), str):
                said.append(args["text"])
    if not edited or ran_after:
        return None
    text = strip("\n".join(said + [last_message if isinstance(last_message, str) else "\n".join(final)]))
    m = CLAIM.search(text)
    if not m or UNVERIFIED.search(text):
        return None
    files = ", ".join(sorted({os.path.basename(p) for p in edited})[:5])
    return (f"claim_check: this turn edited {files} and the reply claims \"{m.group(0).strip()}\", but no command ran "
            f"after the last edit. Run the check that proves the claim now and report its output, or say plainly "
            f"that the change is not verified. A run from before the last edit is not evidence. "
            f"(AGENT_SKILLS_CLAIM_CHECK=0 turns this check off.)")


def main():
    if os.environ.get("AGENT_SKILLS_CLAIM_CHECK", "1") == "0":
        return
    event = json.load(sys.stdin)
    if event.get("stop_hook_active"):
        return
    path = event.get("agent_transcript_path") or event.get("transcript_path")
    if not path or not os.path.isfile(path):
        return
    reason = judge(turn(path), event.get("last_assistant_message"))
    if reason:
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
