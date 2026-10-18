"""JLC assembly panels with KiKit: stacked copies, 5 mm rails, tooling holes, fiducials, order number on the rail.

  pcbkit panel BOARD.kicad_pcb OUT_DIR [--style vcut|mousebite|both] [--rows 3] [--previews DIR] [--hide-refs REGEX]
               [--preset X.json ...]
  pcbkit setup-kikit                  # once: installs the pinned KiKit into ~/.local/share/pcbkit

Styles (presets in data/panel/):
  vcut       V-cut between modules. JLC Standard assembly only. Clean edges.
  mousebite  2 mm gaps, tabs with stamp holes placed where the long edges are copper-free (edge_tabs.py).
             Economic or Standard; leaves small bumps to file off.
Output, per style: OUT_DIR/<board>-panel-<style>.kicad_pcb (the fab panel) and drc-<style>.rpt. V-cut panels are
checked on a twin (-check) with the V-cuts on Cmts.User: JLC wants them on Edge.Cuts, which KiCad reports as an open
outline. With --previews, top/bottom renders and a printable 1:1 PDF go there too; --hide-refs leaves matching
references off that PDF, as in `pcbkit previews` (a LED field's designators bury it). Extra --preset files layer on
top (a project's own rows/tabs/text tweaks). Harvested from the sign's make_panel.sh and setup_tools.sh.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

from ..kicad import KICAD_CLI, KICAD_PYTHON, data_path, quiet, tools_dir

# KiKit master at ff94e84 (2026-09-25). Release v1.8.1 predates the KiCad 10.0.6 SWIG iterator fix (KiKit #938);
# move the pin to the next release once it ships.
KIKIT_COMMIT = "ff94e842a9cbcf334085cfa4107e0ddc1111257b"


def kikit_dir():
    return os.path.join(tools_dir(), "kikit")


def setup_kikit():
    d = kikit_dir()
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.dirname(d), exist_ok=True)
    subprocess.run([KICAD_PYTHON, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--target", d,
                    f"git+https://github.com/yaqwsx/KiKit@{KIKIT_COMMIT}"], check=True)
    print(f"KiKit {KIKIT_COMMIT[:7]} installed in {d}")


def kpy(*args):
    r = subprocess.run([KICAD_PYTHON, *args], capture_output=True, text=True, env=dict(os.environ, PYTHONPATH=kikit_dir()))
    return r.returncode, quiet(r.stdout + r.stderr)


def drc_counts(rpt):
    t = open(rpt).read()
    g = lambda rx: (re.search(rx, t, re.M) or [None, "?"])[1]
    return g(r"^\*\* Found (\d+) DRC violations"), g(r"^\*\* Found (\d+) unconnected")


def panel(board, out, styles=("vcut", "mousebite"), rows=3, previews=None, extra_presets=(), hide_refs=None):
    if not os.path.isdir(os.path.join(kikit_dir(), "kikit")):
        sys.exit("KiKit is not installed: run `pcbkit setup-kikit`")
    board = os.path.abspath(board)
    name = os.path.splitext(os.path.basename(board))[0]
    os.makedirs(out, exist_ok=True)
    # footprint library for DRC on the panel: resolve the board's ${KIPRJMOD} to its real directory
    src_tbl = os.path.join(os.path.dirname(board), "fp-lib-table")
    if os.path.exists(src_tbl):
        with open(src_tbl) as fh, open(os.path.join(out, "fp-lib-table"), "w") as oh:
            oh.write(fh.read().replace("${KIPRJMOD}", os.path.dirname(board)))
    for st in styles:
        style = data_path("panel", f"{st}.json")
        if not os.path.exists(style):
            sys.exit(f"no panel style {st}")
        fab = os.path.join(out, f"{name}-panel-{st}.kicad_pcb")
        check = fab
        for f in os.listdir(out):
            if f.startswith(f"{name}-panel-{st}") and f.endswith((".kicad_pcb", ".kicad_prl", ".kicad_pro")):
                os.remove(os.path.join(out, f))
        # the package's post script and tabs plugin, by absolute path, plus the row count, layered last
        override = {"layout": {"rows": rows}, "post": {"script": data_path("panel", "panel_post.py")}}
        if "edge_tabs" in open(style).read():
            override["tabs"] = {"code": data_path("panel", "edge_tabs.py") + ".EdgeTabs"}
        ov = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(override, ov); ov.close()
        presets = ["-p", data_path("panel", "jlc_base.json"), "-p", style]
        for p in extra_presets:
            presets += ["-p", p]
        presets += ["-p", ov.name]

        def panelize(output, *opts):
            return kpy("-m", "kikit.ui", "panelize", *presets, *opts, board, output)
        rc, log = panelize(fab)
        print(log.strip())
        if st == "vcut":
            check = os.path.join(out, f"{name}-panel-{st}-check.kicad_pcb")
            panelize(check, "--cuts", "vcuts; layer: Cmts.User")
        os.unlink(ov.name)
        if rc or not (os.path.exists(fab) and os.path.exists(check)):
            sys.exit(f"KiKit failed for {name} ({st})")
        rpt = os.path.join(out, f"drc-{st}.rpt")
        subprocess.run([KICAD_CLI, "pcb", "drc", "--severity-error", "--severity-warning", "-o", rpt, check], capture_output=True)
        viol, unc = drc_counts(rpt)
        _, size = kpy("-c", f"import pcbnew; b=pcbnew.LoadBoard({check!r}); r=b.GetBoardEdgesBoundingBox(); "
                            f"print(f'{{pcbnew.ToMM(r.GetWidth()):.2f}} x {{pcbnew.ToMM(r.GetHeight()):.2f}} mm')")
        if previews:
            os.makedirs(previews, exist_ok=True)
            for side in ("top", "bottom"):
                r = subprocess.run([KICAD_CLI, "pcb", "render", "--side", side, "--width", "2400", "--height", "1800", "--zoom", "1.05",
                                    "-o", os.path.join(previews, f"panel-{st}-{side}.png"), check], capture_output=True)
                if r.returncode:
                    print(f"render {side} failed")
            pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            cmd = [KICAD_PYTHON, "-m", "pcbkit.release.plot_print", fab, os.path.join(previews, f"panel-{st}.pdf")]
            if hide_refs:
                cmd += ["--hide-refs", hide_refs]
            subprocess.run(cmd, capture_output=True, env=dict(os.environ, PYTHONPATH=pkg_root))
        print(f"{out} ({st}): panel {size.strip().splitlines()[-1] if size.strip() else '?'}; DRC {viol} violations, {unc} unconnected")
        if viol != "0":
            sys.exit(f"panel DRC violations: see {rpt}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("out")
    ap.add_argument("--style", default="both", choices=["vcut", "mousebite", "both"])
    ap.add_argument("--rows", type=int, default=3)
    ap.add_argument("--previews", help="directory for renders and the printable PDF")
    ap.add_argument("--preset", action="append", default=[], help="extra KiKit preset JSON, layered on top")
    ap.add_argument("--hide-refs", help="regex of references to leave off the printable PDF (with --previews)")
    a = ap.parse_args(argv)
    styles = ("vcut", "mousebite") if a.style == "both" else (a.style,)
    panel(a.board, a.out, styles, a.rows, a.previews, a.preset, a.hide_refs)


if __name__ == "__main__":
    main()
