"""Printable 2-D PDF of a board or panel, at 1:1 on its page so it can be measured or laid against real parts.

  pcbkit print BOARD.kicad_pcb OUT.pdf [--hide-refs REGEX]

Page 1 is the front, seen from the front. Page 2 is the back, mirrored so it reads as seen from the back. Each page
has the board edge (including a panel's V-cut lines), part outlines with sketched pads (Fab), silkscreen, solder-mask
openings, and the title block with a note saying which side the page shows. Part values are left off. --hide-refs
hides references matching a regex (e.g. "D\\d+(_\\d+)?" for a board whose 0402 LEDs would carry unreadable numbers).
Plots from a temporary copy; the board file is not changed. Needs pdfunite (brew install poppler).
Harvested from the projects' plot_print.py.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

from ..kicad import KICAD_CLI, ensure_pcbnew


def plot(src, out, hide_refs=None):
    ensure_pcbnew()
    import pcbnew
    if not shutil.which("pdfunite"):
        raise SystemExit("pcbkit print needs pdfunite (brew install poppler)")
    b = pcbnew.LoadBoard(src)
    if hide_refs:
        for fp in b.GetFootprints():
            if re.fullmatch(hide_refs, fp.GetReference()):
                fp.Reference().SetVisible(False)
                for g in fp.GraphicalItems():
                    if g.GetClass() == "PCB_TEXT" and "${REFERENCE}" in g.GetText():
                        g.SetVisible(False)
    tb = b.GetTitleBlock()
    tmp = tempfile.mkdtemp()
    try:
        pages = []
        for side, note, extra in (("F", "FRONT, seen from the front. Scale 1:1", []),
                                  ("B", "BACK, mirrored: seen from the back. Scale 1:1", ["--mirror"])):
            tb.SetComment(1, note)
            os.makedirs(os.path.join(tmp, side))
            copy = os.path.join(tmp, side, os.path.basename(src))       # keeps the real name in the title block
            pcbnew.PCB_IO_MGR.Save(pcbnew.PCB_IO_MGR.KICAD_SEXP, copy, b)
            pdf = os.path.join(tmp, f"{side}.pdf")
            subprocess.run([KICAD_CLI, "pcb", "export", "pdf", "--mode-single", "--layers",
                            f"Edge.Cuts,{side}.Fab,{side}.SilkS,{side}.Mask", "--sketch-pads-on-fab-layers",
                            "--exclude-value", "--include-border-title", "--scale", "1", *extra, "-o", pdf, copy],
                           check=True, capture_output=True)
            pages.append(pdf)
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        subprocess.run(["pdfunite", *pages, out], check=True)
    finally:
        shutil.rmtree(tmp)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("out")
    ap.add_argument("--hide-refs", help="regex of references to leave off")
    a = ap.parse_args(argv)
    print(plot(a.board, a.out, a.hide_refs))


if __name__ == "__main__":
    main()
