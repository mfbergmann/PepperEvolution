#!/usr/bin/env python3
"""
Keep the lab's Basecamp (TRiPL project, Pepper section) in step with this repository.

    python scripts/basecamp_sync.py            # documents and to-dos
    python scripts/basecamp_sync.py --dry-run  # say what would change, change nothing
    python scripts/basecamp_sync.py --docs     # only the documents
    python scripts/basecamp_sync.py --tasks    # only the to-dos

What it keeps in Basecamp:
- **Documents.** A "Pepper Evolution" folder (inside the Pepper folder) mirrors the process documentation:
  the docs in ``DOCUMENTS``, the changelog, the wiki's Test-sessions page, the printable recording notice (as a
  PDF upload), and a short "Start here" document with links.
  - Relative links point to GitHub.
  - Each document says which file and commit it comes from.
  - Edits belong in the repository: the next sync overwrites them in Basecamp.
- **To-dos.** A "PepperEvolution" to-do list is the running log of the work, with one section per GitHub milestone
  plus sections for robot sessions and releases.
  - Every GitHub issue becomes a to-do ("#27 ..."), completed when the issue is closed, with the real date in the
    title (Basecamp stamps completion with the day of the sync).
  - Every release in CHANGELOG.md and every robot session on the wiki's Test-sessions page becomes a completed to-do.
  - The "Before the next robot session" plan in docs/HANDOFF.md is an open to-do.

Uses the Basecamp CLI (``basecamp``, logged in as you) and ``gh``. The account, project and folder IDs and what
has been synced live in ``.basecamp/pepper-sync.json``, which is git-ignored: they are lab-internal. Run it
after updating the docs, issues and wiki (CLAUDE.md, "Versions and tracking"). Documents are created without
notifying anyone; new to-dos appear in the project like any other.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
STATE = os.path.join(ROOT, ".basecamp", "pepper-sync.json")
WIKI_CACHE = os.path.join(ROOT, ".basecamp", "wiki")
REPO = "mfbergmann/PepperEvolution"
GITHUB = f"https://github.com/{REPO}"
WIKI_GIT = f"{GITHUB}.wiki.git"

# (repository path, Basecamp title), in the order they are listed in "Start here"
DOCUMENTS: List[Tuple[str, str]] = [
    ("docs/HANDOFF.md", "Handoff notes: state, robot sessions, next steps"),
    ("docs/ROADMAP.md", "Roadmap"),
    ("docs/ARCHITECTURE.md", "Architecture"),
    ("docs/MEMORY.md", "Memory design"),
    ("docs/SAFETY.md", "Safety ledger"),
    ("docs/SETUP_PROFILES.md", "Setup profiles: cloud only or with a local GPU"),
    ("docs/GETTING_STARTED.md", "Getting started"),
    ("docs/BRIDGE_API.md", "Bridge API"),
    ("docs/RESEARCH_2026-09.md", "Research notes (September 2026)"),
    ("CHANGELOG.md", "Changelog"),
    ("README.md", "README"),
    ("CONTRIBUTING.md", "Contributing"),
]
WIKI_PAGES: List[Tuple[str, str]] = [("Test-sessions.md", "Test sessions on the robot (from the wiki)")]
UPLOADS: List[str] = ["docs/signs/recording-notice.pdf"]
START_HERE = "Start here: PepperEvolution"


class BasecampError(RuntimeError):
    pass


# -- the Basecamp CLI ------------------------------------------------------------------------------------------------


def bc(*args: str, stdin: Optional[str] = None) -> Any:
    """Run ``basecamp <args> --json`` and return ``data``; raise BasecampError on failure."""
    env = dict(os.environ, BASECAMP_NONINTERACTIVE="1")
    exe = shutil.which("basecamp") or os.path.expanduser("~/.local/bin/basecamp")
    proc = subprocess.run([exe, *args, "--json"], input=stdin, capture_output=True, text=True, env=env, timeout=120)
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise BasecampError(f"basecamp {' '.join(args[:3])}: {proc.stderr.strip() or proc.stdout.strip()}")
    if not out.get("ok", proc.returncode == 0):
        raise BasecampError(f"basecamp {' '.join(args[:3])}: {out.get('error')} {out.get('hint') or ''}".strip())
    return out.get("data")


def scope(state: Dict[str, Any]) -> List[str]:
    return ["--account", str(state["account"]), "--in", str(state["project"])]


# -- helpers ---------------------------------------------------------------------------------------------------------


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def last_change(path: str) -> str:
    """Short hash and date of the last commit that touched ``path``."""
    out = git("log", "-1", "--format=%h %ad", "--date=short", "--", path)
    return out or "uncommitted"


def for_basecamp(path: str, text: str, source_url: str, changed: str) -> str:
    """Markdown for a Basecamp document: no top heading (Basecamp shows the title), links that work there, a
    line saying where it comes from."""
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines = lines[1:]
    body = "\n".join(lines)
    base = os.path.dirname(path)

    def link(match: "re.Match[str]") -> str:
        target = match.group(1)
        if re.match(r"^(https?:|mailto:|#)", target):
            return match.group(0)
        anchor = ""
        if "#" in target:
            target, anchor = target.split("#", 1)
            anchor = "#" + anchor
        resolved = os.path.normpath(os.path.join(base, target)) if target else path
        kind = "tree" if os.path.isdir(os.path.join(ROOT, resolved)) else "blob"
        return f"]({GITHUB}/{kind}/main/{resolved}{anchor})"

    body = re.sub(r"\]\(([^)\s]+)\)", link, body)
    note = (
        f"*Mirrored from [{path}]({source_url}) (last changed {changed}). The repository is the source: edits "
        "made here are overwritten by the next sync. Diagrams written in Mermaid show as code here; GitHub draws "
        "them.*"
    )
    return note + "\n\n" + body


def wiki_page(name: str) -> Optional[str]:
    """A wiki page's text, from a shallow clone kept in .basecamp/wiki."""
    if os.path.isdir(os.path.join(WIKI_CACHE, ".git")):
        subprocess.run(["git", "-C", WIKI_CACHE, "pull", "-q"], capture_output=True)
    else:
        subprocess.run(["git", "clone", "-q", "--depth", "1", WIKI_GIT, WIKI_CACHE], capture_output=True)
    path = os.path.join(WIKI_CACHE, name)
    return open(path, encoding="utf-8").read() if os.path.exists(path) else None


def version() -> str:
    text = open(os.path.join(ROOT, "src", "__init__.py"), encoding="utf-8").read()
    match = re.search(r'__version__ = "([^"]+)"', text)
    return match.group(1) if match else "?"


# -- documents -------------------------------------------------------------------------------------------------------


def ensure_folder(state: Dict[str, Any], dry: bool) -> Optional[int]:
    if state.get("folder"):
        return int(state["folder"])
    listing = bc("files", "list", *scope(state), "--vault", str(state["parent_vault"]))
    for item in listing or []:
        if item.get("type") == "Folder" and item.get("name") == state["folder_name"]:
            state["folder"] = item["id"]
            return int(item["id"])
    print(f"create folder {state['folder_name']!r}")
    if dry:
        return -1  # placeholder: a dry run goes on to list what it would add
    folder = bc("docs", "folders", "create", state["folder_name"], *scope(state), "--vault", str(state["parent_vault"]))
    state["folder"] = folder["id"]
    return int(folder["id"])


def sync_document(state: Dict[str, Any], key: str, title: str, content: str, dry: bool) -> None:
    docs = state.setdefault("docs", {})
    known = docs.get(key)
    digest = sha(title + "\n" + content)
    if known and known.get("hash") == digest:
        return
    if known and known.get("id"):
        print(f"update {title!r}")
        if not dry:
            bc("docs", "update", str(known["id"]), *scope(state), "--title", title, "--content", "-", stdin=content)
            known["hash"] = digest
        return
    print(f"create {title!r}")
    if dry:
        return
    created = bc(
        "docs",
        "documents",
        "create",
        title,
        "-",
        *scope(state),
        "--vault",
        str(state["folder"]),
        "--no-subscribe",
        stdin=content,
    )
    docs[key] = {"id": created["id"], "hash": digest, "url": created.get("app_url"), "title": title}


def sync_upload(state: Dict[str, Any], path: str, dry: bool) -> None:
    uploads = state.setdefault("uploads", {})
    full = os.path.join(ROOT, path)
    digest = hashlib.sha256(open(full, "rb").read()).hexdigest()[:16]
    known = uploads.get(path)
    if known and known.get("hash") == digest:
        return
    if known and known.get("id"):
        print(f"new version of {os.path.basename(path)}")
        if not dry:
            bc("docs", "replace", str(known["id"]), full, *scope(state))
            known["hash"] = digest
        return
    print(f"upload {os.path.basename(path)}")
    if dry:
        return
    description = f"The printable notice for people near Pepper (source: [{path}]({GITHUB}/blob/main/{path}))."
    created = bc(
        "uploads", "create", full, *scope(state), "--vault", str(state["folder"]), "--description", description
    )
    uploads[path] = {"id": created["id"], "hash": digest, "url": created.get("app_url")}


def start_here(state: Dict[str, Any]) -> str:
    docs = state.get("docs", {})
    links = []
    for path, title in DOCUMENTS:
        url = docs.get(path, {}).get("url")
        links.append(f"- [{title}]({url})" if url else f"- {title}")
    for name, title in WIKI_PAGES:
        url = docs.get(f"wiki:{name}", {}).get("url")
        links.append(f"- [{title}]({url})" if url else f"- {title}")
    todo = state.get("todolist_url")
    return "\n".join(
        [
            "An off-board mind for TRiPL's SoftBank Pepper: a small bridge on the robot exposes its hardware, and a "
            "host application listens, sees, remembers and decides with Claude, then speaks and moves through Pepper. "
            "The goal is presence.",
            "",
            f"**Current version:** {version()} (see the Changelog). **Where things stand and what is next:** the "
            "Handoff notes and the Roadmap.",
            "",
            "**In this folder** (mirrored from the repository by `scripts/basecamp_sync.py`; edit them there):",
            *links,
            "- The printable recording notice (PDF)",
            "",
            "**Elsewhere:**",
            f"- Code and issues: [{REPO}]({GITHUB}) ([milestones]({GITHUB}/milestones))",
            f"- Wiki: [{REPO} wiki]({GITHUB}/wiki)",
            (
                (f"- Milestones, issues, robot sessions and releases as to-dos: [PepperEvolution to-do list]({todo})")
                if todo
                else "- Milestones, issues, robot sessions and releases: the PepperEvolution to-do list"
            ),
            "",
            "Session recordings (voices, photos, transcripts) stay on the lab's host machine and are not copied here.",
        ]
    )


def sync_documents(state: Dict[str, Any], dry: bool) -> None:
    if ensure_folder(state, dry) is None:
        print("(the folder does not exist yet: run without --dry-run to create it and its documents)")
        return
    for path, title in DOCUMENTS:
        text = open(os.path.join(ROOT, path), encoding="utf-8").read()
        content = for_basecamp(path, text, f"{GITHUB}/blob/main/{path}", last_change(path))
        sync_document(state, path, title, content, dry)
    for name, title in WIKI_PAGES:
        text = wiki_page(name)
        if text is None:
            print(f"(wiki page {name} not found)")
            continue
        page = name[:-3] if name.endswith(".md") else name
        content = for_basecamp(name, text, f"{GITHUB}/wiki/{page}", "see the wiki history")
        sync_document(state, f"wiki:{name}", title, content, dry)
    for path in UPLOADS:
        sync_upload(state, path, dry)
    sync_document(state, "start-here", START_HERE, start_here(state), dry)


# -- to-dos ----------------------------------------------------------------------------------------------------------


def issues() -> List[Dict[str, Any]]:
    out = subprocess.run(
        [
            "gh",
            "issue",
            "list",
            "--repo",
            REPO,
            "--state",
            "all",
            "--limit",
            "500",
            "--json",
            "number,title,state,milestone,closedAt,url",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(json.loads(out), key=lambda i: i["number"])


def milestones() -> List[Dict[str, Any]]:
    out = subprocess.run(
        ["gh", "api", f"repos/{REPO}/milestones?state=all&per_page=100"], capture_output=True, text=True, check=True
    ).stdout
    return sorted(json.loads(out), key=lambda m: m["title"])


def releases() -> List[Tuple[str, str, str]]:
    """(version, date, name) for every release heading in CHANGELOG.md, oldest first."""
    text = open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8").read()
    found = re.findall(r"^## (\d+\.\d+\.\d+) \((\d{4}-\d{2}-\d{2})\): (.+)$", text, flags=re.M)
    return sorted(found, key=lambda r: [int(x) for x in r[0].split(".")])


def sessions() -> List[Dict[str, str]]:
    """Rows of the wiki's Test-sessions table (date, where, what, findings), oldest first."""
    text = wiki_page("Test-sessions.md") or ""
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 4 and re.match(r"^\d{4}-\d{2}-\d{2}$", cells[0]):
            rows.append({"date": cells[0], "where": cells[1], "what": cells[2], "findings": cells[3]})
    return rows


def next_session() -> Optional[Tuple[str, str]]:
    """The "Before the next robot session (...)" plan in HANDOFF.md: (key, title), or None."""
    text = open(os.path.join(ROOT, "docs", "HANDOFF.md"), encoding="utf-8").read()
    match = re.search(r"^## Before the next robot session(?: \((.+?)\))?\s*$", text, flags=re.M)
    if not match:
        return None
    label = match.group(1) or "next"
    # one key for the planned session whatever its label, so a renamed plan updates the to-do instead of closing it
    return "next", f"Next robot session: {label} (the checks are in the Handoff notes)"


def ensure_list(state: Dict[str, Any], dry: bool) -> Optional[int]:
    if state.get("todolist"):
        return int(state["todolist"])
    for todolist in bc("todolists", "list", *scope(state), "--todoset", str(state["todoset"]), "--all") or []:
        if todolist.get("name") == state["list_name"]:
            state["todolist"], state["todolist_url"] = todolist["id"], todolist.get("app_url")
            return int(todolist["id"])
    print(f"create to-do list {state['list_name']!r}")
    if dry:
        return -1  # placeholder: a dry run goes on to list what it would add
    description = (
        f'<div>The running log of the <a href="{GITHUB}">PepperEvolution</a> work: one section per milestone '
        "with its GitHub issues, plus robot sessions and releases. Kept in step with GitHub by "
        "<code>scripts/basecamp_sync.py</code>; completed items carry their real date in the title.</div>"
    )
    created = bc(
        "todolists",
        "create",
        state["list_name"],
        *scope(state),
        "--todoset",
        str(state["todoset"]),
        "--description",
        description,
    )
    state["todolist"], state["todolist_url"] = created["id"], created.get("app_url")
    return int(created["id"])


def ensure_group(state: Dict[str, Any], name: str, dry: bool) -> Optional[int]:
    groups = state.setdefault("groups", {})
    if name in groups:
        return int(groups[name])
    print(f"create section {name!r}")
    if dry:
        return None
    created = bc("todolistgroups", "create", name, *scope(state), "--list", str(state["todolist"]))
    groups[name] = created["id"]
    return int(created["id"])


def sync_todo(state: Dict[str, Any], key: str, group: str, title: str, description: str, done: bool, dry: bool) -> None:
    todos = state.setdefault("todos", {})
    known = todos.get(key)
    if known is None:
        group_id = ensure_group(state, group, dry)
        print(f"add to-do {title!r}{' (done)' if done else ''}")
        if dry:
            return
        created = bc("todos", "create", title, *scope(state), "--list", str(group_id), "--description", description)
        known = todos[key] = {"id": created["id"], "title": title, "done": False}
    if known.get("title") != title:
        print(f"rename to-do {known.get('title')!r} -> {title!r}")
        if not dry:
            bc("todos", "update", str(known["id"]), *scope(state), "--title", title)
            known["title"] = title
    if done and not known.get("done"):
        print(f"complete {title!r}")
        if not dry:
            bc("todos", "complete", str(known["id"]), *scope(state))
            known["done"] = True
    elif not done and known.get("done"):
        print(f"reopen {title!r}")
        if not dry:
            bc("todos", "uncomplete", str(known["id"]), *scope(state))
            known["done"] = False


def sync_tasks(state: Dict[str, Any], dry: bool) -> None:
    if ensure_list(state, dry) is None:
        print("(the to-do list does not exist yet: run without --dry-run to create it)")
        return
    for milestone in milestones():  # sections in milestone order, created before any to-do
        ensure_group(state, milestone["title"], dry)
    ensure_group(state, "Other issues", dry)
    ensure_group(state, "Robot sessions", dry)
    ensure_group(state, "Releases", dry)
    for issue in issues():
        group = (issue.get("milestone") or {}).get("title") or "Other issues"
        done = issue["state"] == "CLOSED"
        title = f"#{issue['number']} {issue['title']}"
        if done and issue.get("closedAt"):
            title += f" (done {issue['closedAt'][:10]})"
        description = f"[GitHub issue #{issue['number']}]({issue['url']}) in {group}."
        sync_todo(state, f"issue:{issue['number']}", group, title, description, done, dry)
    for row in sessions():
        key = f"session:{row['date']}:{sha(row['where'])}"
        title = f"Robot session {row['date']}, {row['where']}: {row['what']}"[:240]
        more = f"More in the Handoff notes and the wiki's [Test sessions]({GITHUB}/wiki/Test-sessions)."
        description = f"{row['findings']}\n\n{more}"
        sync_todo(state, key, "Robot sessions", title, description, True, dry)
    upcoming = next_session()
    todos = state.setdefault("todos", {})
    for key in [k for k in todos if k.startswith("next:")]:  # the old per-label keys: keep the to-do, new key
        todos.setdefault("next", todos.pop(key))
    if upcoming is None and "next" in todos:  # the plan is gone from HANDOFF: the session was held
        known = todos.pop("next")
        todos[f"held:{known['id']}"] = known
        sync_todo(state, f"held:{known['id']}", "Robot sessions", known["title"], "", True, dry)
    if upcoming is not None:
        description = f"[Before the next robot session]({GITHUB}/blob/main/docs/HANDOFF.md) in the Handoff notes."
        sync_todo(state, upcoming[0], "Robot sessions", upcoming[1], description, False, dry)
    for number, date, name in releases():
        title = f"{number}: {name} (released {date})"
        description = f"[CHANGELOG]({GITHUB}/blob/main/CHANGELOG.md) · [code at v{number}]({GITHUB}/tree/v{number})"
        sync_todo(state, f"release:{number}", "Releases", title, description, True, dry)


# -- main ------------------------------------------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
    parser.add_argument("--docs", action="store_true", help="only the documents")
    parser.add_argument("--tasks", action="store_true", help="only the to-dos")
    args = parser.parse_args()
    if not os.path.exists(STATE):
        sys.exit(f"No {STATE}: it holds the Basecamp account, project and folder IDs (see docs/HANDOFF.md)")
    state = json.load(open(STATE, encoding="utf-8"))
    both = not args.docs and not args.tasks
    try:
        if args.tasks or both:  # first, so "Start here" can link to the to-do list
            sync_tasks(state, args.dry_run)
        if args.docs or both:
            sync_documents(state, args.dry_run)
    finally:
        if not args.dry_run:
            with open(STATE, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=1)
    print("done" + (" (dry run: nothing changed)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
