"""Start a hardware project the way the others are laid out.

  pcbkit new DIR --board NAME [--title "..."] [--description "..."] [--company "..."] [--no-git] [--no-bomi]

Creates DIR with README.md, DESIGN.md, LAYOUT.md, PRODUCTION.md, .gitignore and pcbkit.toml from the kit's templates (placeholders
filled in), boards/NAME/ with an empty KiCad 10 project (root schematic, blank board, lib tables pointing at lib/),
lib/<project>.kicad_sym and lib/<project>.pretty (empty), docs/previews/, docs/datasheets/, scripts/ with the
previews wrapper and the builder glue for pcbkit.schematic, a bomi project in the board folder, and a first git
commit. See docs/CONVENTIONS.md in the kit for what goes where.
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from .kicad import data_path

KIT = Path(__file__).resolve().parents[1]
TEMPLATES = KIT / "templates" / "project"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def kicad_project(bdir, board, project, title, company):
    root = str(uuid.uuid4())
    today = datetime.date.today().isoformat()
    write(bdir / f"{board}.kicad_pro", json.dumps({
        "board": {"design_settings": {"rule_severities": {}, "rules": {}}},
        "meta": {"filename": f"{board}.kicad_pro", "version": 1},
        "net_settings": {"classes": [{"name": "Default", "clearance": 0.15, "track_width": 0.25, "via_diameter": 0.6,
                                      "via_drill": 0.3, "microvia_diameter": 0.3, "microvia_drill": 0.1,
                                      "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
                                      "wire_width": 6, "bus_width": 12, "line_style": 0,
                                      "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)",
                                      "priority": 2147483647}], "meta": {"version": 3}},
        "sheets": [[root, "Root"]],
    }, indent=2) + "\n")
    write(bdir / f"{board}.kicad_sch",
          f'(kicad_sch (version 20250114) (generator "pcbkit") (generator_version "10.0") (uuid "{root}")\n'
          f'\t(paper "A3")\n\t(title_block (title "{title}") (date "{today}") (rev "0.1-draft") (company "{company}"))\n'
          f'\t(lib_symbols)\n\t(sheet_instances (path "/" (page "1")))\n\t(embedded_fonts no)\n)\n')
    shutil.copy(data_path("templates", "blank.kicad_pcb"), bdir / f"{board}.kicad_pcb")
    write(bdir / "sym-lib-table",
          f'(sym_lib_table\n  (version 7)\n  (lib (name "{project}")(type "KiCad")(uri "${{KIPRJMOD}}/../../lib/{project}.kicad_sym")'
          '(options "")(descr "Project symbols"))\n)\n')
    write(bdir / "fp-lib-table",
          f'(fp_lib_table\n  (version 7)\n  (lib (name "{project}")(type "KiCad")(uri "${{KIPRJMOD}}/../../lib/{project}.pretty")'
          '(options "")(descr "Project footprints"))\n)\n')


def new(dir_, board, title=None, description="", company="", git=True, bomi=True):
    d = Path(dir_).resolve()
    project = d.name
    title = title or project
    if d.exists() and any(d.iterdir()):
        sys.exit(f"{d} exists and is not empty")
    today = datetime.date.today().isoformat()
    subst = {"<Project>": title, "<project>": project, "<date>": today, "<name>": board, "<b>": board,
             "<one line: what is done, what is next>": "scaffolded with pcbkit new; requirements next",
             "<where the design stands>": "requirements being written", "<placed / routed / DRC state>": "no board yet",
             "<What it is, in two sentences. Which version is current, how versions are named.>": description}
    for f in ("README.md", "DESIGN.md", "LAYOUT.md", "PRODUCTION.md", ".gitignore", "pcbkit.toml"):
        text = (TEMPLATES / f).read_text()
        for k, v in subst.items():
            text = text.replace(k, v)
        write(d / f, text)
    for sub in ("docs/previews", "docs/datasheets", "archive", f"lib/{project}.pretty"):
        (d / sub).mkdir(parents=True)
        write(d / sub / ".gitkeep", "")
    lib = d / "lib"
    write(lib / f"{project}.kicad_sym", '(kicad_symbol_lib (version 20241209) (generator "pcbkit") (generator_version "10.0")\n)\n')
    kicad_project(d / "boards" / board, board, project, title, company)
    write(d / "scripts" / "make_previews.sh", (TEMPLATES / "scripts" / "make_previews.sh").read_text().replace("<b>", board))
    write(d / "scripts" / "make_fab.sh", (TEMPLATES / "scripts" / "make_fab.sh").read_text())
    write(d / "scripts" / "builder.py", (TEMPLATES / "scripts" / "builder.py").read_text().replace("<b>", board))
    for f in ("make_previews.sh", "make_fab.sh"):
        os.chmod(d / "scripts" / f, 0o755)
    if bomi and shutil.which("bomi"):
        subprocess.run(["bomi", "init", "--name", project, "--description", description or title], cwd=d / "boards" / board,
                       capture_output=True)
    if git:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=d, check=True)
        subprocess.run(["git", "add", "-A"], cwd=d, check=True)
        subprocess.run(["git", "commit", "-q", "-m", f"{project}: scaffold with pcbkit new"], cwd=d, check=True)
    print(f"{d}: boards/{board}/{board}.kicad_pro, lib/{project}.*, docs/, scripts/, README/DESIGN/LAYOUT/PRODUCTION"
          + (", bomi" if bomi and shutil.which("bomi") else "") + (", git" if git else ""))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", help="project directory to create (its name is the project and library name)")
    ap.add_argument("--board", required=True, help="first board name (boards/NAME/NAME.kicad_pro)")
    ap.add_argument("--title", help="human title (default: the directory name)")
    ap.add_argument("--description", default="")
    ap.add_argument("--company", default="")
    ap.add_argument("--no-git", action="store_true")
    ap.add_argument("--no-bomi", action="store_true")
    a = ap.parse_args(argv)
    new(a.dir, a.board, a.title, a.description, a.company, not a.no_git, not a.no_bomi)


if __name__ == "__main__":
    main()
