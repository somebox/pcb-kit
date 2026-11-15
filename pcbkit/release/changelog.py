"""Draft a project's CHANGELOG.md from its boards: what changed in each board, in words, since its last release or
since a board named.

  pcbkit changelog [PCB ...] [--since REF|PATH] [--all]

For every PCB (or those named, by key, path or folder), what changed in its netlist and BOM (as pcbkit release tells
a minor version from a patch) and in its layout (parts moved, turned or put on the other side, tracks, vias, zones,
the outline, silkscreen texts):
- since --since: a .kicad_pcb (an as-built board kept in archive/, a release's kicad/ copy), or a git commit, tag or
  branch, which has the board's file at its path (REF:path from the repository's top for a board that has moved);
- else since its last release, as the release's kicad/ copy has it (or the board at the release's base commit);
- else, for a board never released, since the commit where its title block first read the version it reads now: for
  an imported or as-built board, the board that was built.
With --all, each release against the one before it as well, newest first, under "## <board> <label> - <date>" with
its note from releases.toml.

The output is Markdown to edit into the project's CHANGELOG.md (docs/CONVENTIONS.md in pcb-kit): one line per change,
to which the why is added, citing the decision (D12) or review finding (F27) it comes from. It reads files only:
no KiCad, nothing written.
"""
import argparse
import os
import re

from ..config import Project
from . import versions as V


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _copy(project, pcb, entry):
    """A release's board as its kicad/ copy has it, or as git had it at the release's base commit; None if neither."""
    copy = os.path.join(project.release_dir(pcb), entry["label"], "kicad", os.path.basename(pcb["path"]))
    if os.path.exists(copy):
        return _read(copy)
    base = entry.get("base_commit")
    return V.git_text(os.path.join(project.root, pcb["path"]), base) if base else None


def baseline(project, pcb, since=None):
    """The board to compare with: (its text, what it is), or (None, why there is none)."""
    path = os.path.join(project.root, pcb["path"])
    if since:
        if os.path.isfile(since):
            rel = os.path.relpath(os.path.realpath(since), os.path.realpath(project.root))
            return _read(since), since if rel.startswith("..") else rel
        text = V.git_text(path, since)
        return (text, since) if text is not None else \
            (None, f"--since {since}: neither a file nor a git revision that has {os.path.basename(path)}")
    last = V.current(project.release_dir(pcb))
    if last:
        text = _copy(project, pcb, last)
        return (text, f"{last['label']} ({last['date']})") if text is not None else \
            (None, f"{last['label']} has no kicad/ copy and no base commit in git: --since a board or git revision")
    rev = V.title_rev(_read(path))
    origin = V.rev_origin(path, rev)
    text = V.git_text(path, origin[0]) if origin else None
    if text is not None:
        return text, f"{rev} as at {origin[0]} ({origin[1]}), when its title block first read {rev}"
    return None, ("never released, and git has no copy of it reading " + (rev or "a version")
                  + ": --since an as-built board or a git revision")


FIELD = re.compile(r"^(\S+) ((?:footprint|value|LCSC|MPN) .*|DNP|out of the BOM|no longer .*)$")
ADDS = re.compile(r"^adds (\S+) (\(.*\))$")
REMOVES = re.compile(r"^removes (\S+)$")


def grouped(changes):
    """circuit_changes() as a changelog says them: a part's fields on one line ("R4 value 10k -> 20k, LCSC C1 ->
    C2"), and parts that changed alike, were added alike or were removed on one line ("R4, R5 value ...", "adds C6,
    C7 (100nF, ...)", "removes C3, C4"); the nets as they are."""
    parts, order = {}, []
    for c in changes:
        m = FIELD.match(c)
        if m:
            if m.group(1) not in parts:
                parts[m.group(1)] = []
                order.append(("part", m.group(1)))
            parts[m.group(1)].append(m.group(2))
        else:
            order.append(("line", c))
    alike, lines = {}, []
    for kind, x in order:              # parts with the same changes, or added as the same part, together
        m = ADDS.match(x) if kind == "line" else None
        gone = REMOVES.match(x) if kind == "line" else None
        key = ("part", ", ".join(parts[x])) if kind == "part" else ("adds", m.group(2)) if m else \
            ("removes", "") if gone else ("line", x)
        ref = x if kind == "part" else m.group(1) if m else gone.group(1) if gone else None
        if key in alike and ref:
            alike[key].append(ref)
            continue
        alike[key] = [ref] if ref else []
        lines.append(key)
    out = []
    for key in lines:
        refs = ", ".join(sorted(alike[key], key=V._natural))
        out.append(f"{refs} {key[1]}" if key[0] == "part" else f"adds {refs} {key[1]}" if key[0] == "adds" else
                   f"removes {refs}" if key[0] == "removes" else key[1])
    return out


def changes(old, new):
    """(the netlist and BOM changes, the layout changes) from one board's text to another's."""
    return (grouped(V.circuit_changes(V.circuit(None, old), V.circuit(None, new))),
            V.layout_changes(V.layout(None, old), V.layout(None, new)))


def section(title, circuit, layout, note=""):
    out = [title, ""]
    if note:
        out += [note, ""]
    if not circuit and not layout:
        return out + ["- no change to the netlist, BOM or layout", ""]
    if circuit:
        out += ["Circuit (netlist and BOM):"] + [f"- {x}" for x in circuit] + [""]
    if layout:
        out += ["Layout:"] + [f"- {x}" for x in layout] + [""]
    return out


def unreleased(project, pcb, since=None):
    old, what = baseline(project, pcb, since)
    if old is None:
        return [f"### {pcb['key']}", "", f"- nothing to compare with: {what}", ""]
    return section(f"### {pcb['key']}, since {what}", *changes(old, _read(os.path.join(project.root, pcb["path"]))))


def history(project, pcb):
    """Each release against the one before it, newest first."""
    rs = V.releases(project.release_dir(pcb))
    out = []
    for i in range(len(rs) - 1, -1, -1):
        r = rs[i]
        title = f"## {pcb['key']} {r['label']} - {r['date']}"
        note = f"releases.toml: {r['note']}" if r.get("note") else ""
        if i == 0:
            out += [title, ""] + ([note, ""] if note else []) + ["- first release", ""]
            continue
        old, new = _copy(project, pcb, rs[i - 1]), _copy(project, pcb, r)
        if old is None or new is None:
            out += [title, ""] + ([note, ""] if note else []) + \
                [f"- {rs[i - 1]['label'] if old is None else r['label']} has no kicad/ copy to compare with", ""]
            continue
        out += section(title + f" (since {rs[i - 1]['label']})", *changes(old, new), note=note)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pcbs", nargs="*")
    ap.add_argument("--since", help="compare with this .kicad_pcb, or this git revision's copy of each board")
    ap.add_argument("--all", action="store_true", help="each release against the one before it too")
    a = ap.parse_args(argv)
    project = Project()
    pcbs = [project.pcb(n) for n in a.pcbs] if a.pcbs else [p for p in project.pcbs() if not p["excluded"]]
    out = ["## Unreleased", ""]
    for pcb in pcbs:
        out += unreleased(project, pcb, a.since)
    if a.all:
        for pcb in pcbs:
            out += history(project, pcb)
    print("\n".join(out).rstrip())


if __name__ == "__main__":
    main()
