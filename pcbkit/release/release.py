"""Versioned releases of a project's boards, configured by its pcbkit.toml (pcbkit/config.py).

  pcbkit release [PCB ...] [--minor | --major] [--version X.Y.Z] [--note TEXT] [--dry-run]

For every PCB (or those named, by key, path or folder):
1. Checks, per its profile (ERC; DRC with schematic parity; nothing unconnected). A PCB that fails is a mockup: it is
   reported and left alone.
2. Has it changed since its last release? First its source fingerprint (the board and its schematics with the version
   and date stamps masked, its custom rules, the project file's design settings and net classes, and the fab
   settings); if that differs, its fab fingerprint (every house's files made from a copy with neutral stamps), so a
   KiCad save that only reshuffles a file isn't a change, and a rule change that moves copper or an MPN that changes
   PCBWay's BOM is.
3. A changed board gets the next version: patch by default, --minor or --major when asked, or --version (which must
   come after the last). A board without releases adopts the version its title block carries ("v1.0" -> 1.0.0).
4. The new version is stamped into the board and its root schematic (title block, silkscreen texts carrying the old
   label), refused while KiCad has the folder open; then DRC again: no new errors, no more silkscreen warnings.
5. <release dir>/v<label>/ (committed, never rewritten): GERBER-<key>-v<label>.zip, the JLC BOM and CPL, PCBWay's BOM
   and centroid, schematic.pdf and board.pdf made from this version, MANIFEST.txt; releases.toml beside it, with
   what a board of this version needs by hand (pcbkit production reads it for the orders that pin the version).
   Previews are rebuilt when they are of another version.
Then `pcbkit production` brings the production record up to date. --dry-run says what it would do.
--refresh-parts records again what a board of its last release needs by hand, for boards unchanged since that
release, after pcbkit.toml's part rules changed; nothing else is touched. A change recorded as equivalent (same fab
output) counts as unchanged only when every footprint's fields and flags are still the release's: a Panel field
is on no layer, and a changed one needs another hand part. With
[release] enabled = false, only step 1 runs: the checks are recorded, nothing is stamped or released.
"""
import argparse
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

from .. import production, tomlw
from ..config import Project
from ..kicad import KICAD_CLI
from . import versions as V

PKG_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pcbkit(*args):
    """Run another pcbkit command (the same checkout), quietly; returns its output."""
    env = dict(os.environ)
    env["PYTHONPATH"] = PKG_ROOT + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    r = subprocess.run([sys.executable, "-m", "pcbkit.cli", *map(str, args)], capture_output=True, text=True, env=env)
    if r.returncode:
        raise SystemExit(f"pcbkit {' '.join(map(str, args))} failed:\n{r.stdout[-1500:]}{r.stderr[-1500:]}")
    return r.stdout


def checks(pcb_path, profile):
    """Counts: ERC and DRC errors (zones refilled, the file left as it is); unconnected items and schematic parity
    findings of any severity, as pcbkit drc counts them; and silkscreen findings of any severity (stamping may not add
    any)."""
    from ..checks import drc, erc
    c = dict(erc=0, drc=0, unconnected=0, parity=0, silk=0)
    sch = os.path.splitext(pcb_path)[0] + ".kicad_sch"
    errors = lambda xs: sum(1 for x in xs if x.get("severity") == "error")  # noqa: E731
    with tempfile.TemporaryDirectory() as d:
        if "erc" in profile["checks"] and os.path.exists(sch):
            c["erc"] = errors(erc.run(sch, os.path.join(d, "erc.json")))
        if "drc" in profile["checks"]:
            r = drc.run(pcb_path, refill=True, parity="parity" in profile["checks"] and os.path.exists(sch),
                        out=os.path.join(d, "drc.json"))
            v = r.get("violations", [])
            c.update(drc=errors(v), silk=sum(1 for x in v if "silk" in x.get("type", "")),
                     unconnected=len(r.get("unconnected_items", [])), parity=len(r.get("schematic_parity", [])))
    return c


FAILS = ("erc", "drc", "unconnected", "parity")


def fab_args(project, profile):
    a = ["--houses", ",".join(project.release["fab"]), "--placed", project.release["placed"]]
    if not profile["assembly"]:
        a.append("--no-assembly")
    if profile["extra_layers"]:
        a += ["--extra-layers", ",".join(profile["extra_layers"])]
    if profile["keep_silk_in_mask"]:
        a.append("--keep-silk-in-mask")
    return a


def source_key(project, pcb):
    return V.source_fingerprint(os.path.join(project.root, pcb["path"]), project.fab_settings(pcb["profile"]))


def fab_fingerprint(project, pcb_path, profile):
    """Every configured house's files, made from a copy of the board with neutral stamps."""
    stem = os.path.splitext(os.path.basename(pcb_path))[0]
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, stem + ".kicad_pcb"), "w", encoding="utf-8").write(
            V.neutral(open(pcb_path, encoding="utf-8").read()))
        for ext in (".kicad_pro", ".kicad_dru"):          # net classes and custom rules: the zone fills use them
            if os.path.exists(os.path.splitext(pcb_path)[0] + ext):
                shutil.copy(os.path.splitext(pcb_path)[0] + ext, d)
        pcbkit("fab", os.path.join(d, stem + ".kicad_pcb"), os.path.join(d, "out"), "--name", "fp",
               *fab_args(project, profile))
        return V.fab_fingerprint(os.path.join(d, "out"))


def previews_current(pdir, pcb_path):
    src = os.path.join(pdir, "SOURCE.txt")
    return os.path.exists(src) and open(src).read() == V.preview_source(pcb_path)


def make_previews(project, pcb, pcb_path):
    pdir = project.previews_dir(pcb)
    if previews_current(pdir, pcb_path):
        return "current"
    # a render left from before would pass for this version's (pcbkit production shows render-top.png first): the
    # renders there afterwards are this run's, from pcbkit previews or previews_after
    for f in glob.glob(os.path.join(pdir, "render-*.png")):
        os.remove(f)
    pcbkit("previews", pcb_path, pdir, *([] if project.release["previews_render"] else ["--no-render"]))
    after = project.release["previews_after"]
    if after:
        r = subprocess.run(after.format(key=pcb["key"], folder=pcb["folder"], sub=pcb["sub"]), shell=True,
                           cwd=project.root, capture_output=True, text=True)
        if r.returncode:
            raise SystemExit(f"{after} failed:\n{r.stdout[-1500:]}{r.stderr[-1500:]}")
    with open(os.path.join(pdir, "SOURCE.txt"), "w") as fh:
        fh.write(V.preview_source(pcb_path))
    return "rebuilt"


def write_release(project, pcb, pcb_path, profile, version, label, entry):
    rdir = project.release_dir(pcb)
    vdir = os.path.join(rdir, label)
    if os.path.exists(vdir):
        raise SystemExit(f"{project.rel(vdir)} exists: release folders are never rewritten")
    key = pcb["key"]
    with tempfile.TemporaryDirectory() as d:
        pcbkit("fab", pcb_path, d, *fab_args(project, profile))
        os.makedirs(vdir)
        files = []

        def take(src, dst):
            shutil.copy2(os.path.join(d, src), os.path.join(vdir, dst))
            files.append(dst)
        take(f"GERBER-{key}.zip", f"GERBER-{key}-{label}.zip")
        if profile["assembly"]:
            both = len(open(os.path.join(d, f"CPL-{key}-both.csv")).read().splitlines())
            front = len(open(os.path.join(d, f"CPL-{key}-front.csv")).read().splitlines())
            side = "both" if both > front else "front"
            if "jlcpcb" in project.release["fab"]:
                take(f"BOM-{key}-{side}.csv", f"BOM-{key}-{label}-jlcpcb.csv")
                take(f"CPL-{key}-{side}.csv", f"CPL-{key}-{label}-jlcpcb.csv")
            if "pcbway" in project.release["fab"]:
                take(f"BOM-{key}-pcbway.csv", f"BOM-{key}-{label}-pcbway.csv")
                take(f"centroid-{key}-pcbway.csv", f"centroid-{key}-{label}-pcbway.csv")
    sch = os.path.splitext(pcb_path)[0] + ".kicad_sch"   # this version's drawings, whatever the previews hold
    if os.path.exists(sch):
        subprocess.run([KICAD_CLI, "sch", "export", "pdf", "-o", os.path.join(vdir, "schematic.pdf"), sch], check=True,
                       capture_output=True)
        files.append("schematic.pdf")
    pcbkit("print", pcb_path, os.path.join(vdir, "board.pdf"))
    files.append("board.pdf")
    with open(os.path.join(vdir, "MANIFEST.txt"), "w") as fh:
        fh.write(f"{key} {label} ({version}), released {entry['date']} by pcbkit release\n"
                 f"board: {pcb['path']}\nsource fingerprint: {entry['source']}\nfab fingerprint: {entry['fab']}\n"
                 f"base commit: {entry['base_commit']}\nfiles:\n" + "".join(f"  {f}\n" for f in files))
    return vdir


def git(project, *args):
    r = subprocess.run(["git", "-C", project.root, *args], capture_output=True, text=True)
    return r.stdout.strip()


def restamp(pcb, profile, before, old_label, new_label, date):
    """Stamp new_label into the board; None, or why not. DRC again: no new errors, no more silkscreen findings."""
    pcb_path = pcb["abspath"]
    lock = V.locked(pcb_path)
    if lock:
        return "open in KiCad (" + " ".join(os.path.basename(x) for x in lock) + ")"
    V.stamp(pcb_path, old_label, new_label, date)
    after = checks(pcb_path, profile)
    if any(after[k] for k in FAILS) or after["silk"] > before["silk"]:
        raise SystemExit(f"{pcb['path']}: stamping {new_label} made the checks worse ({before} -> {after}); "
                         "revert the board with git")
    return None


def release(project, pcb, part="patch", version=None, note="", dry=False):
    pcb_path = pcb["abspath"] = os.path.join(project.root, pcb["path"])
    profile, key = pcb["profile"], pcb["key"]
    if pcb["excluded"]:
        return dict(state="excluded")
    if not profile["release"]:
        return dict(state="not released (profile)")
    c = checks(pcb_path, profile)
    if any(c[k] for k in FAILS):
        return dict(state="mockup", checks=c)
    if not project.release["enabled"]:
        return dict(state="checked", checks=c)
    rdir = project.release_dir(pcb)
    last = V.current(rdir)
    src = source_key(project, pcb)
    on_board = V.title_rev(open(pcb_path, encoding="utf-8").read())
    old_label = on_board if re.fullmatch(r"v\d+(?:\.\d+){1,2}", on_board) else last["label"] if last else ""
    same = last and (src == last["source"] or src in last.get("equivalent", []))
    ffp = None if same else fab_fingerprint(project, pcb_path, profile)
    if last and (same or ffp == last["fab"]):
        r = dict(state="current", version=last["version"], label=last["label"], checks=c)
        if not same:
            r["note"] = "changed files, same fab output: recorded as equivalent"
            if not dry:
                V.update_last(rdir, key, equivalent=last.get("equivalent", []) + [src])
        if on_board != last["label"]:
            if dry:
                r["note"] = f"the board says {on_board or 'nothing'}: would stamp {last['label']}"
            else:
                why = restamp(pcb, profile, c, old_label, last["label"], str(last["date"]))
                r["note"] = f"the board said {on_board or 'nothing'}: " + (f"not stamped, {why}" if why else
                                                                            f"stamped {last['label']}")
        if project.release["previews"] and not dry and not previews_current(project.previews_dir(pcb), pcb_path):
            r["previews"] = make_previews(project, pcb, pcb_path)
            V.update_last(rdir, key, previews=hashlib.sha1(V.preview_source(pcb_path).encode()).hexdigest())
            r["note"] = "; ".join(x for x in (r.get("note"), "previews rebuilt") if x)
        return r
    if last:
        if version and V.parse(version) <= V.parse(last["version"]):
            return dict(state=f"not released: --version {version} doesn't come after {last['version']}", checks=c)
        new = version or V.bump(last["version"], part)
        why = note or (f"{part} after {last['version']}" if not version else f"after {last['version']}")
    else:
        new = version or V.adopt(on_board) or "1.0.0"
        why = note or ("first release" if version or not on_board else
                       f"first release, its title block's {on_board}")
    lab = V.label(new, project.release["label"])
    if os.path.exists(os.path.join(rdir, lab)):
        return dict(state=f"not released: {project.rel(os.path.join(rdir, lab))} exists", checks=c)
    if dry:
        return dict(state="would release", version=new, label=lab, checks=c, note=why)
    if lab != on_board:
        stuck = restamp(pcb, profile, c, old_label, lab, str(V.today()))
        if stuck:
            return dict(state="not released: " + stuck, checks=c)
    entry = dict(version=new, label=lab, date=V.today(), source=source_key(project, pcb), fab=ffp,
                 equivalent=[], base_commit=git(project, "rev-parse", "--short", "HEAD"), note=why,
                 parts=production.board_parts(project, pcb), parts_inputs=production.hand_inputs(project, pcb))
    pv = make_previews(project, pcb, pcb_path) if project.release["previews"] else "off"
    entry["previews"] = hashlib.sha1(V.preview_source(pcb_path).encode()).hexdigest() if pv != "off" else ""
    vdir = write_release(project, pcb, pcb_path, profile, new, lab, entry)
    V.record(rdir, key, entry)
    return dict(state="released", version=new, label=lab, checks=c, note=why, previews=pv, dir=project.rel(vdir))


def refresh_parts(project, pcb, dry=False):
    """Record the last release's parts again with the current rules, when the board is unchanged since it."""
    if pcb["excluded"]:
        return dict(state="excluded")
    rdir = project.release_dir(pcb)
    last = V.current(rdir)
    if not last:
        return dict(state="not released")
    src = source_key(project, pcb)
    inputs = production.hand_inputs(project, pcb)
    # an equivalent source has the release's fab output, not necessarily its hand parts (a changed Panel field): it
    # counts only when what the parts are read from is the release's too
    if src != last["source"] and not (src in last.get("equivalent", []) and inputs == last.get("parts_inputs")):
        return dict(state="changed since its last release: parts left as they were", version=last["version"],
                    label=last["label"])
    parts = production.board_parts(project, pcb)
    same = parts == {k: last.get("parts", {}).get(k, {}) for k in parts}
    if not dry and (not same or inputs != last.get("parts_inputs")):     # an older release gets its inputs recorded
        V.update_last(rdir, pcb["key"], parts=parts, parts_inputs=inputs)
    return dict(state="parts unchanged" if same else ("would record parts" if dry else "parts recorded"),
                version=last["version"], label=last["label"])


def save_state(project, results):
    """production/boards.toml: each PCB's last check and release state (script-owned; read by pcbkit production)."""
    pdir = os.path.join(project.root, project.production["dir"])
    path = os.path.join(pdir, "boards.toml")
    data = tomlw.load(path).get("pcb", {}) if os.path.exists(path) else {}
    for pcb, r in results:
        d = dict(path=pcb["path"], folder=pcb["folder"], state="released" if r["state"] == "current" else r["state"])
        if "checks" in r:
            d.update({k: v for k, v in r["checks"].items()})
        data[pcb["key"]] = d
    os.makedirs(pdir, exist_ok=True)
    tomlw.dump({"pcb": data}, path, "Every PCB's last checks and state, written by pcbkit release. Don't edit by hand.")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pcbs", nargs="*")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--minor", action="store_true")
    g.add_argument("--major", action="store_true")
    ap.add_argument("--version", help="the version for a first release (X.Y.Z)")
    ap.add_argument("--note", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh-parts", action="store_true",
                    help="record the last release's hand parts again (after the part rules changed); nothing else")
    a = ap.parse_args(argv)
    project = Project()
    if not project.release["enabled"]:
        if a.refresh_parts:                          # nothing is released, so no release's record is rewritten either
            print(f"releases are off in {project.path} ([release] enabled = false): no release's parts refreshed")
            return
        print(f"releases are off in {project.path} ([release] enabled = false): checks only")
    if a.version:
        V.parse(a.version)
    part = "major" if a.major else "minor" if a.minor else "patch"
    pcbs = [project.pcb(n) for n in a.pcbs] if a.pcbs else project.pcbs()
    if a.refresh_parts:
        for pcb in pcbs:
            r = refresh_parts(project, pcb, a.dry_run)
            ver = f" {r['label']} ({r['version']})" if r.get("version") else ""
            print(f"{pcb['path']:60s} {r['state']}{ver}")
        if not a.dry_run and project.production["enabled"]:
            production.build(project)
        return
    results = []
    for pcb in pcbs:
        r = release(project, pcb, part, a.version, a.note, a.dry_run)
        results.append((pcb, r))
        ver = f" {r['label']} ({r['version']})" if r.get("version") else ""
        extra = "; ".join(x for x in (r.get("note", ""), r.get("dir", "")) if x)
        chk = r.get("checks")
        why = ""
        if r["state"] == "mockup":
            why = " (" + ", ".join(f"{k} {v}" for k, v in chk.items() if v and k != "silk") + ")"
        print(f"{pcb['path']:60s} {r['state']}{ver}{why}" + (f": {extra}" if extra else ""))
    if not a.dry_run:
        save_state(project, results)
        if project.production["enabled"]:
            production.build(project)


if __name__ == "__main__":
    main()
