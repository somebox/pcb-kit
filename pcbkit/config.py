"""A project's pcbkit.toml: which boards it has, how they are released, where its production record lives.

Every key is optional; the defaults are the layout in docs/CONVENTIONS.md (boards/<name>/, releases/<board>/,
docs/previews/<board>/, production/). The file sits at the project's root; pcbkit finds it from the current folder
upwards, and without one uses the defaults with the git root (or the current folder) as the project.

Paths may use {key} (the PCB's file stem), {folder} (its board folder, relative to the root) and {sub} ("" for a
folder's only top-level PCB, "/<stem>" for any other: a second PCB in the folder, or one in a subfolder such as
panel/), e.g. "{folder}/fab{sub}" or "releases/{key}".
"""
import copy
import fnmatch
import glob
import json
import os
import subprocess

from . import tomlw

DEFAULTS = {
    "boards": {
        "folders": ["boards/*"],       # globs of board folders, relative to the root
        "subfolders": [],              # subfolders of a board folder that hold more PCBs (e.g. "panel")
        "exclude": [],                 # globs of PCB paths (or folders) to leave out
    },
    "release": {
        "enabled": True,               # versions, stamping, release folders; false: pcbkit release only checks
        "dir": "releases/{key}",       # where each version's files go (committed)
        "label": "short",              # "short": v1.0 when the patch is 0, v1.0.1 otherwise; "full": v1.0.0
        "fab": ["jlcpcb", "pcbway"],   # whose assembly files to write
        "placed": "not-excluded",      # parts the assembler places: "not-excluded" (in BOM and position files),
                                       # or "field:NAME=VALUE" (e.g. "field:Assembly=jlc")
        "previews": True,              # pcbkit previews of each release
        "previews_render": True,       # its 3-D renders too (false when a project renders them itself)
        "previews_dir": "docs/previews/{key}",
        "previews_after": "",          # a project command run after the previews, e.g. "scripts/render.sh {key}"
        "profile": [],                 # per PCB, first match on its path wins; then DEFAULT_PROFILE
    },
    "production": {
        "enabled": True,
        "page": "PRODUCTION.md",       # gets a generated section between pcbkit's markers
        "dir": "production",           # orders/<name>/order.toml, stock.toml
        "part": [],                    # rules: what a board needs besides its hand-soldered parts (pcb, field,
                                       # match, part; buy, group, replace: production.py)
    },
}
DEFAULT_PROFILE = {"match": "*", "release": True, "checks": ["erc", "drc", "parity"], "assembly": True,
                   "extra_layers": []}


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def find_root(start=None):
    d = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.exists(os.path.join(d, "pcbkit.toml")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, cwd=start or None)
    return r.stdout.strip() if r.returncode == 0 else os.path.abspath(start or os.getcwd())


class Project:
    def __init__(self, root=None):
        self.root = find_root(root)
        self.path = os.path.join(self.root, "pcbkit.toml")
        self.cfg = copy.deepcopy(DEFAULTS)
        if os.path.exists(self.path):
            _merge(self.cfg, tomlw.load(self.path))
        self.release = self.cfg["release"]
        self.production = self.cfg["production"]

    def rel(self, path):
        return os.path.relpath(os.path.abspath(path), self.root)

    def pcbs(self):
        """Every PCB in the project, in folder order: dicts with key, path (relative), folder, sub, profile, excluded."""
        out, b = [], self.cfg["boards"]
        for pattern in b["folders"]:
            for folder in sorted(glob.glob(os.path.join(self.root, pattern))):
                if not os.path.isdir(folder):
                    continue
                top = sorted(p for p in glob.glob(os.path.join(folder, "*.kicad_pcb"))
                             if not os.path.basename(p).startswith((".", "~")))
                found = [(p, "" if len(top) == 1 else "/" + os.path.splitext(os.path.basename(p))[0]) for p in top]
                for sub in b["subfolders"]:
                    found += [(p, "/" + os.path.splitext(os.path.basename(p))[0])
                              for p in sorted(glob.glob(os.path.join(folder, sub, "*.kicad_pcb")))
                              if not os.path.basename(p).startswith((".", "~"))]
                for path, sub in found:
                    rel = self.rel(path)
                    out.append(dict(key=os.path.splitext(os.path.basename(path))[0], path=rel,
                                    folder=self.rel(folder), sub=sub, profile=self.profile(rel),
                                    excluded=any(fnmatch.fnmatch(rel, x) or fnmatch.fnmatch(self.rel(folder), x)
                                                 for x in b["exclude"])))
        paths = {}
        for p in out:
            paths.setdefault(p["key"], []).append(p["path"])
        dup = {k: v for k, v in paths.items() if len(v) > 1}
        if dup:
            raise SystemExit("PCBs are known by their file names, and these share one: "
                             + "; ".join(f"{k}: {', '.join(v)}" for k, v in dup.items()) + ". Rename one.")
        return out

    def pcb(self, name):
        """A PCB by key, or by its path or folder: relative to the project's root, or to the current folder."""
        names = {os.path.normpath(name), self.rel(name)}
        for p in self.pcbs():
            if name == p["key"] or p["path"] in names or p["folder"] in names:
                return p
        raise SystemExit(f"{name}: no such PCB in {self.root} (pcbkit.toml [boards] folders)")

    def profile(self, rel):
        for prof in self.release.get("profile", []):
            if fnmatch.fnmatch(rel, prof.get("match", "*")):
                return dict(DEFAULT_PROFILE, **prof)
        return dict(DEFAULT_PROFILE)

    def fab_settings(self, profile):
        """The settings a PCB's fab files are made with, as text: part of its source fingerprint, so changing them is
        a change to compare. For an assembled board that includes the placement file's rotation table
        (data/jlc/corrections.csv): a corrected rotation makes another CPL from the same board."""
        settings = dict(fab=self.release["fab"], placed=self.release["placed"], assembly=profile["assembly"],
                        extra_layers=profile["extra_layers"])
        if profile["assembly"]:
            import hashlib
            from .kicad import data_path
            with open(data_path("jlc", "corrections.csv"), "rb") as fh:
                settings["rotations"] = hashlib.sha1(fh.read()).hexdigest()
        return json.dumps(settings, sort_keys=True)

    def fill(self, template, pcb):
        return os.path.join(self.root, template.format(key=pcb["key"], folder=pcb["folder"], sub=pcb["sub"]))

    def release_dir(self, pcb):
        return self.fill(self.release["dir"], pcb)

    def previews_dir(self, pcb):
        return self.fill(self.release["previews_dir"], pcb)
