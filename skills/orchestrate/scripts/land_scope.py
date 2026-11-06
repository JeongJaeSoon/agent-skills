"""Which repos `orchestrate.land_executor` covers. prog.py, watch-prs' prs.py and hooks/land_route_gate.py all read the
scope through here, so a merge path and the gate that points at it never disagree.

  repos    OWNER/NAME for one repo, OWNER/* for every repo of that owner, where OWNER is the gh account's own login
           (`owner_refused`: an org's repos go in by name only). GitHub names are case-insensitive.
  exclude  OWNER/NAME entries that stay out even when OWNER/* covers them.

Any other wildcard (`*`, `*/*`, `*/NAME`, `OWNER/app-*`) is an error, never a wider match. A repo covered only through
OWNER/* that is a fork or archived is out (`wildcard_only`): those take a network call, so the callers that already
make one (the executor round, the standalone program's creation) check it. Stdlib only, and it never exits.
"""
import re

PART = re.compile(r"[A-Za-z0-9_.-]+")


class ScopeError(ValueError):
    pass


def _split(entry, where, wildcard):
    if not isinstance(entry, str) or entry.count("/") != 1:
        raise ScopeError(f"{where}: {entry!r} is not OWNER/NAME")
    owner, name = entry.split("/")
    if not PART.fullmatch(owner) or not (PART.fullmatch(name) or (wildcard and name == "*")):
        raise ScopeError(f"{where}: {entry!r} is not OWNER/NAME{' or OWNER/*' if wildcard else ''}")
    return owner.casefold(), name.casefold()


def parse(cfg):
    """The scope of a land_executor dict: {"names": {(owner, name)}, "owners": {owner}, "exclude": {(owner, name)}}.
    A repos entry that is not a string is skipped, as before this module; a malformed string is a ScopeError."""
    repos, exclude = cfg.get("repos"), cfg.get("exclude")
    if repos is not None and not isinstance(repos, list):
        raise ScopeError("orchestrate.land_executor.repos must be a list of OWNER/NAME or OWNER/* strings")
    if exclude is not None and not isinstance(exclude, list):
        raise ScopeError("orchestrate.land_executor.exclude must be a list of OWNER/NAME strings")
    names, owners = set(), set()
    for r in repos or []:
        if not isinstance(r, str):
            continue
        owner, name = _split(r, "orchestrate.land_executor.repos", True)
        (owners.add(owner) if name == "*" else names.add((owner, name)))
    out = {_split(r, "orchestrate.land_executor.exclude", False) for r in exclude or []}
    return {"names": names, "owners": owners, "exclude": out}


def key(repo):
    owner, _, name = str(repo).partition("/")
    return owner.casefold(), name.casefold()


def how(scope, repo):
    """"name" when an entry names the repo, "owner" when only OWNER/* covers it, None when it is out."""
    k = key(repo)
    if k in scope["exclude"]:
        return None
    if k in scope["names"]:
        return "name"
    return "owner" if k[0] in scope["owners"] else None


def excluded(scope, repo):
    return key(repo) in scope["exclude"]


def owners(scope):
    """Every owner an entry names, OWNER/NAME entries included."""
    return scope["owners"] | {o for o, _ in scope["names"]}


def owner_refused(scope, repo, login):
    """Why OWNER/* cannot cover `repo`: the wildcard is for the gh account's own login, never an org or another user.
    An OWNER/NAME entry is not judged here: naming an org repo is how it gets in. None when the repo stays in."""
    if how(scope, repo) == "owner" and key(repo)[0] != str(login).casefold():
        return (f"{repo} is covered only by {repo.split('/')[0]}/*, and OWNER/* covers the gh account's own repos"
                f" ({login}) only: list an org's repo by name")
    return None


def wildcard_only(scope, repo, meta):
    """Why a repo OWNER/* alone covers is out after all, from prog.repo_meta's isFork and isArchived; None when it is in."""
    if how(scope, repo) != "owner":
        return None
    for field, what in (("isFork", "a fork"), ("isArchived", "archived")):
        if meta.get(field):
            return f"{repo} is {what}, and OWNER/* leaves those out: list it by name in orchestrate.land_executor.repos"
    return None
