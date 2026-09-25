"""Render docs/skills.md and its translations (docs/skills.{en,ja}.md) into one catalog page.

Usage: python3 scripts/catalog/build.py [OUT.html]   (default: ./skill-catalog.html)
"""
import html, pathlib, re, subprocess, sys

HERE = pathlib.Path(__file__).parent
REPO = HERE.parents[1]
OUT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "skill-catalog.html")
SRC_PATH = "docs/skills.md"


def git(*args):
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True).stdout.strip()


src_ko = (REPO / SRC_PATH).read_text()
sha = git("log", "-1", "--format=%h", "--", SRC_PATH)
dirty = bool(git("status", "--porcelain", "--", SRC_PATH))


def load_translation(lang):
    text = (REPO / "docs" / f"skills.{lang}.md").read_text()
    m = re.match(r"<!-- translated-from: (\w+) -->\n", text)
    if not m:
        sys.exit(f"skills.{lang}.md must start with <!-- translated-from: <sha> -->")
    base = m.group(1)
    newer = git("log", "--format=%h %s", f"{base}..HEAD", "--", SRC_PATH)
    stale = bool(newer) or dirty
    if stale:
        print(f"WARNING: {SRC_PATH} changed after {base}; skills.{lang}.md is stale:", file=sys.stderr)
        print(newer or "  (uncommitted changes)", file=sys.stderr)
    return text[m.end():], base, stale


TAGS = {"사용자 호출 전용": "user-only", "user-invoked only": "user-only", "ユーザー呼び出し専用": "user-only"}
ALIAS_H2 = {"별칭", "Aliases", "エイリアス"}


def slug(text):
    t = re.sub(r"[`*]", "", text).strip().lower()
    t = re.sub(r"\(.*?\)", "", t).strip()
    return re.sub(r"[^\w가-힣]+", "-", t).strip("-")


def render(src, prefix):
    # English ids get a prefix so both languages can share one page without duplicate ids.
    def inline(t):
        parts = re.split(r"(`[^`]+`)", t)
        out = []
        for p in parts:
            if p.startswith("`") and p.endswith("`") and len(p) > 1:
                out.append(f"<code>{html.escape(p[1:-1])}</code>")
                continue
            p = html.escape(p, quote=False)
            p = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", p)

            def link(m):
                href = m.group(2)
                if href.startswith("#"):
                    href = "#" + prefix + slug(href[1:].replace("-", " "))
                ext = ' target="_blank" rel="noopener"' if href.startswith("http") else ""
                return f'<a href="{html.escape(href)}"{ext}>{m.group(1)}</a>'
            p = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, p)
            out.append(p)
        return "".join(out)

    lines = src.splitlines()
    body, toc, heads, i = [], [], [], 0
    group, skills, aliases, past_skills = None, 0, 0, False
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("```"):
            j = i + 1
            while not lines[j].startswith("```"):
                j += 1
            body.append('<div class="scroll"><pre><code>' + html.escape("\n".join(lines[i + 1:j])) + "</code></pre></div>")
            i = j + 1
            continue
        m = re.match(r"^(#{1,3}) (.*)", ln)
        if m:
            level, text = len(m.group(1)), m.group(2)
            if level == 1:
                i += 1
                continue
            hid = prefix + slug(text)
            heads.append(hid)
            note = re.search(r"\((.*?)\)", text)
            name = re.sub(r"\s*\(.*?\)", "", text) if level == 3 else text
            badge = ""
            if level == 3 and note:
                cls = TAGS.get(note.group(1), "alias")
                badge = f' <span class="badge {cls}">{inline(note.group(1))}</span>'
            k = f' data-k="{len(heads)}"'
            if level == 2:
                past_skills = past_skills or text in ALIAS_H2
                group = {"id": hid, "text": text, "items": [], "alias": text in ALIAS_H2}
                toc.append(group)
                body.append(f'<h2 id="{hid}"{k}>{inline(text)}</h2>')
            else:
                skills += not past_skills
                if group:
                    group["items"].append((hid, name))
                body.append(f'<h3 id="{hid}"{k}>{inline(name)}{badge}</h3>')
            i += 1
            continue
        if ln.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, data = rows[0], rows[2:]
            if group and group["alias"]:
                aliases += len(data)
            t = "<thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in head) + "</tr></thead><tbody>"
            t += "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in data)
            body.append(f'<div class="scroll"><table>{t}</tbody></table></div>')
            continue
        if re.match(r"^\s*(- |\d+\. )", ln):
            items = []
            while i < len(lines) and re.match(r"^\s*(- |\d+\. )", lines[i]):
                mm = re.match(r"^(\s*)(- |\d+\. )(.*)", lines[i])
                items.append((len(mm.group(1)) // 2, "ol" if mm.group(2)[0].isdigit() else "ul", mm.group(3)))
                i += 1
            out, stack = [], []
            for depth, kind, text in items:
                while len(stack) > depth + 1:
                    out.append(f"</li></{stack.pop()}>")
                if len(stack) == depth + 1:
                    out.append("</li>")
                while len(stack) < depth + 1:
                    out.append(f"<{kind}>")
                    stack.append(kind)
                out.append(f"<li>{inline(text)}")
            while stack:
                out.append(f"</li></{stack.pop()}>")
            body.append("".join(out))
            continue
        if ln.strip():
            para = [ln]
            i += 1
            while i < len(lines) and lines[i].strip() and not re.match(r"^(#|\||```|\s*- |\s*\d+\. )", lines[i]):
                para.append(lines[i])
                i += 1
            body.append(f"<p>{inline(' '.join(para))}</p>")
            continue
        i += 1

    dead = {h[1:] for h in re.findall(r'href="(#[^"]*)"', "".join(body))} - set(heads)
    if dead:
        sys.exit(f"dead in-page links ({prefix or 'ko'}): {sorted(dead)}")
    intro = body.pop(0)
    nav = []
    for g in toc:
        sub = "".join(f'<li><a href="#{h}">{html.escape(n)}</a></li>' for h, n in g["items"])
        nav.append(f'<li><a class="grp" href="#{g["id"]}">{html.escape(g["text"])}</a>'
                   + (f"<ul>{sub}</ul>" if sub else "") + "</li>")
    return {"intro": intro, "nav": "".join(nav), "body": "\n".join(body),
            "heads": len(heads), "skills": skills, "aliases": aliases}


# Per-language chrome around the rendered markdown. {sha}/{base} are filled in below.
UI = {
    "ko": {"h1": "agent-skills 스킬 카탈로그", "nav": "목차", "eyebrow": "",
           "facts": ("스킬", "별칭", "명령", "hook"),
           "footer": "정본은 저장소의 docs/skills.md다. 이 페이지는 {sha} 시점의 보기용 사본이다.", "stale": ""},
    "en": {"h1": "agent-skills skill catalog", "nav": "Contents", "eyebrow": " · translated from {base}",
           "facts": ("skills", "aliases", "commands", "hooks"),
           "footer": "The source of truth is docs/skills.md in the repo, written in Korean. This page is a "
                     "read-only copy as of {sha}; the English text was translated from {base}.",
           "stale": " docs/skills.md has changed since then, so the English text may lag behind the Korean."},
    "ja": {"h1": "agent-skills スキルカタログ", "nav": "目次", "eyebrow": " · {base} から翻訳",
           "facts": ("スキル", "エイリアス", "コマンド", "フック"),
           "footer": "正本はリポジトリの docs/skills.md（韓国語）です。このページは {sha} 時点の閲覧用コピーで、"
                     "日本語は {base} から翻訳しました。",
           "stale": " その後 docs/skills.md が更新されているため、日本語が韓国語より古い可能性があります。"},
}

docs = {"ko": (render(src_ko, ""), sha, False)}
for lang in ("en", "ja"):
    text, base, stale = load_translation(lang)
    docs[lang] = (render(text, lang + "-"), base, stale)
ko = docs["ko"][0]
for lang, (doc, _, _) in docs.items():
    for key in ("heads", "skills", "aliases"):
        if doc[key] != ko[key]:
            sys.exit(f"skills.{lang}.md structure differs from {SRC_PATH}: {key} {doc[key]} != {ko[key]}")

parts = {k: [] for k in ("EYEBROW", "H1", "INTRO", "FACTS", "NAV", "BODY", "FOOTER")}
for lang, (doc, base, stale) in docs.items():
    ui, a = UI[lang], f'data-l="{lang}" lang="{lang}"'
    f = ui["facts"]
    parts["EYEBROW"].append(f'<span {a}>{ui["eyebrow"].format(base=base)}</span>' if ui["eyebrow"] else "")
    parts["H1"].append(f'<span {a}>{ui["h1"]}</span>')
    parts["INTRO"].append(f'<div {a}>{doc["intro"]}</div>')
    parts["FACTS"].append(
        f'<ul class="facts" {a}><li>{f[0]} <b>{doc["skills"]}</b></li><li>{f[1]} <b>{doc["aliases"]}</b></li>'
        f'<li>{f[2]} <b>orch · orch-dash</b></li><li>{f[3]} <b>2</b></li>'
        f'<li>pstack pin <b>b42effe</b> → upstream <b>12d587d</b></li></ul>')
    parts["NAV"].append(f'<nav class="toc" {a} aria-label="{ui["nav"]}"><ul>{doc["nav"]}</ul></nav>')
    parts["BODY"].append(f'<div {a}>\n{doc["body"]}\n</div>')
    parts["FOOTER"].append(f'<span {a}>{ui["footer"].format(sha=sha, base=base)}{ui["stale"] if stale else ""}</span>')

page = (HERE / "template.html").read_text()
for k, v in parts.items():
    page = page.replace("{{" + k + "}}", "\n".join(v))
page = page.replace("{{SHA}}", sha)
if "{{" in page:
    sys.exit("unfilled placeholder in template: " + re.search(r"{{\w+}}", page).group(0))
OUT.write_text(page)
print(OUT, len(page), f"source {sha};", ", ".join(
    f"{lang} from {base}" + (" (STALE)" if stale else "") for lang, (_, base, stale) in docs.items() if lang != "ko"))
