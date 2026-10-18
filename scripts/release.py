#!/usr/bin/env python3
"""Version and release helper for pcb-kit.

The version is written in four places, which must move together (Claude Code keeps an installed plugin under its
version, so a change released under an old number may not reach an install):

    pyproject.toml              version = "X.Y.Z"
    pcbkit/__init__.py          __version__ = "X.Y.Z"
    package.json                "version": "X.Y.Z"           (pi)
    .claude-plugin/plugin.json  "version": "X.Y.Z"           (Claude Code)

    python3 scripts/release.py check [X.Y.Z]   the four agree (and equal X.Y.Z), CHANGELOG.md has the section
    python3 scripts/release.py bump X.Y.Z      write X.Y.Z everywhere, move [Unreleased] under ## [X.Y.Z] - <today>
    python3 scripts/release.py tag             on an up-to-date main: annotated tag vX.Y.Z, then prints the push
    python3 scripts/release.py notes X.Y.Z     the CHANGELOG section's text (the release workflow's notes)
    python3 scripts/release.py title X.Y.Z     "vX.Y.Z — <summary line>" (the release title)

Pushing the tag runs .github/workflows/release.yml, which checks again and publishes the GitHub release.
Standard library only, and 3.9-compatible like the package.
"""
import datetime
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"
FILES = [
    ("pyproject.toml", r'^(version = ")([^"]+)(")'),
    ("pcbkit/__init__.py", r'^(__version__ = ")([^"]+)(")'),
    ("package.json", r'^(  "version": ")([^"]+)(")'),
    (".claude-plugin/plugin.json", r'^(  "version": ")([^"]+)(")'),
]
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def fail(msg):
    sys.exit("release: " + msg)


def versions():
    out = {}
    for name, pat in FILES:
        found = re.findall(pat, (ROOT / name).read_text(), re.M)
        if len(found) != 1:
            fail(f"{name}: expected one version line, found {len(found)}")
        out[name] = found[0][1]
    return out


def current():
    v = versions()
    if len(set(v.values())) != 1:
        fail("versions disagree: " + ", ".join(f"{k} {x}" for k, x in v.items()))
    return next(iter(v.values()))


def sections():
    """CHANGELOG.md as [(heading line, version or 'Unreleased', body text)] plus the text before the first one."""
    text = CHANGELOG.read_text()
    parts = re.split(r"^(## \[[^\]]+\].*)$", text, flags=re.M)
    out = []
    for i in range(1, len(parts), 2):
        out.append((parts[i], re.match(r"## \[([^\]]+)\]", parts[i]).group(1), parts[i + 1]))
    return parts[0], out


def section(version):
    for _, v, body in sections()[1]:
        if v == version:
            return body.strip()
    return None


def summary(version):
    body = section(version) or ""
    first = body.split("\n\n")[0].strip()
    if not first or first.startswith(("#", "-", "*")):
        return ""
    return " ".join(first.split()).rstrip(".")


def git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if check and r.returncode:
        fail(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def cmd_check(want=None):
    v = versions()
    for name, x in v.items():
        print(f"{x:10} {name}")
    ver = current()
    if want and ver != want:
        fail(f"the files say {ver}, expected {want}")
    if not section(ver):
        fail(f"CHANGELOG.md has no ## [{ver}] section, or it is empty")
    print(f"ok: {ver}, CHANGELOG.md has its section")


def cmd_bump(new):
    if not SEMVER.match(new):
        fail(f"{new} is not X.Y.Z")
    old = current()
    if tuple(map(int, new.split("."))) <= tuple(map(int, old.split("."))):
        fail(f"{new} is not above the current {old}")
    head, secs = sections()
    if not secs or secs[0][1] != "Unreleased":
        fail("CHANGELOG.md must start with an ## [Unreleased] section")
    if not secs[0][2].strip():
        fail("## [Unreleased] is empty: write what changed first")
    if any(v == new for _, v, _ in secs):
        fail(f"CHANGELOG.md already has ## [{new}]")
    today = datetime.date.today().isoformat()
    text = head + "## [Unreleased]\n\n" + f"## [{new}] - {today}" + secs[0][2]
    text += "".join(h + b for h, _, b in secs[1:])
    CHANGELOG.write_text(text)
    for name, pat in FILES:
        p = ROOT / name
        p.write_text(re.sub(pat, lambda m: m.group(1) + new + m.group(3), p.read_text(), count=1, flags=re.M))
    print(f"{old} -> {new} in {len(FILES)} files; CHANGELOG.md [Unreleased] is now [{new}] - {today}")
    print("next: commit, merge to main, then `python3 scripts/release.py tag`")


def cmd_tag():
    ver = current()
    tag = "v" + ver
    if not section(ver):
        fail(f"CHANGELOG.md has no ## [{ver}] section")
    if git("status", "--porcelain", "--untracked-files=no"):
        fail("the working tree has uncommitted changes")
    if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        fail("tag from main")
    git("fetch", "-q", "origin", "main", "--tags")
    if git("rev-parse", "HEAD") != git("rev-parse", "origin/main"):
        fail("main is not the same as origin/main: pull or push first")
    if git("tag", "-l", tag) or git("ls-remote", "--tags", "origin", f"refs/tags/{tag}"):
        fail(f"{tag} exists already")
    git("tag", "-a", tag, "-m", cmd_title(ver, echo=False))
    print(f"tagged {tag} at {git('rev-parse', '--short', 'HEAD')}; publish it with:\n  git push origin {tag}")


def cmd_title(version, echo=True):
    s = summary(version)
    t = f"v{version} — {s}" if s else f"v{version}"
    if echo:
        print(t)
    return t


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "check" and len(rest) <= 1:
        cmd_check(rest[0].lstrip("v") if rest else None)
    elif cmd == "bump" and len(rest) == 1:
        cmd_bump(rest[0].lstrip("v"))
    elif cmd == "tag" and not rest:
        cmd_tag()
    elif cmd == "notes" and len(rest) == 1:
        body = section(rest[0].lstrip("v"))
        if not body:
            fail(f"no ## [{rest[0]}] section in CHANGELOG.md")
        print(body)
    elif cmd == "title" and len(rest) == 1:
        cmd_title(rest[0].lstrip("v"))
    else:
        fail("usage: release.py check [X.Y.Z] | bump X.Y.Z | tag | notes X.Y.Z | title X.Y.Z")


if __name__ == "__main__":
    main(sys.argv[1:])
