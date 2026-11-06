"""Every skill under skills/ is in the three skill catalogs and the three README tables.
Run: python3 scripts/test_catalog_coverage.py"""
import pathlib, re, sys, tempfile

REPO = pathlib.Path(__file__).resolve().parents[1]
CATALOGS = ("docs/skills.md", "docs/skills.en.md", "docs/skills.ja.md")
READMES = ("README.md", "README.ja.md", "README.ko.md")


def skill_names(root):
    return sorted(p.parent.name for p in (root / "skills").glob("*/SKILL.md"))


def uncovered(root):
    """`file: skill` for each skill a catalog has no heading for, or a README table row does not name."""
    names, out = skill_names(root), []
    for rel in CATALOGS:
        headings = set(re.findall(r"^###+ ([\w-]+)(?=\s|$)", (root / rel).read_text(), re.M))
        out += [f"{rel}: {n}" for n in names if n not in headings]
    for rel in READMES:
        rows = [l for l in (root / rel).read_text().splitlines() if l.startswith("|")]
        named = set(re.findall(r"`([\w-]+)`", "\n".join(rows)))
        out += [f"{rel}: {n}" for n in names if n not in named]
    return out


# The rule itself, on a fake tree: a skill missing from one catalog and one README is named once each.
fake = pathlib.Path(tempfile.mkdtemp(prefix="test-catalog-"))
for n in ("alpha", "beta"):
    (fake / "skills" / n).mkdir(parents=True)
    (fake / "skills" / n / "SKILL.md").write_text("---\nname: x\n---\n")
(fake / "docs").mkdir()
for rel in CATALOGS:
    # beta.v2 is another name: a heading must end after the skill name.
    (fake / rel).write_text("### alpha (old name `x`)\n\n### beta\n" if rel != "docs/skills.en.md" else "### alpha\n### beta.v2\n")
for rel in READMES:
    (fake / rel).write_text("| g | `alpha`, `beta` | d |\n" if rel != "README.ko.md" else "| g | `alpha` | d |\nbeta in prose, `beta`\n")
assert uncovered(fake) == ["docs/skills.en.md: beta", "README.ko.md: beta"], uncovered(fake)

# This checkout: nothing is missing.
missing = uncovered(REPO)
assert not missing, "skills missing from a catalog or README table:\n  " + "\n  ".join(missing)
assert len(skill_names(REPO)) > 20, "found too few skills; the glob is wrong"
print("test_catalog_coverage: ok")
sys.exit(0)
