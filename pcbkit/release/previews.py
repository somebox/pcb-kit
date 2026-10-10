"""Review previews for one board into a folder (the projects use docs/previews/<board>/):

  pcbkit previews BOARD.kicad_pcb OUT_DIR [--zoom 1.0] [--render-size 1600x1600] [--no-render] [--hide-refs REGEX]

  schematic.pdf          every sheet (from the .kicad_sch next to the board)
  pcb-front.png          F.Cu + front silk
  pcb-back.png           B.Cu + back silk, mirrored (as seen when hand-soldering)
  assembly-back.png      back pads + silk + Fab (every reference and value), mirrored: the hand-assembly map
  board.pdf              printable 1:1 drawing: front page, then the back mirrored (pcbkit print)
  planes-routing.png     4-layer boards: In1 with the F.Cu tracks over it, above In2 with the B.Cu tracks (plane
                         coverage, and what the routing cut out of it); needs ImageMagick, else the two halves stay
  render-top.png, render-bottom.png   3-D renders (kicad-cli pcb render)

Needs rsvg-convert (brew install librsvg) and pdfunite (poppler). Harvested from the projects' make_previews.sh.
"""
import argparse
import os
import shutil
import subprocess
import tempfile

from ..kicad import KICAD_CLI, KICAD_PYTHON, quiet


def previews(pcb, out, zoom=1.0, render_size=(1600, 1600), render=True, hide_refs=None):
    pcb = os.path.abspath(pcb)
    stem = os.path.splitext(pcb)[0]
    sch = stem + ".kicad_sch"
    os.makedirs(out, exist_ok=True)
    made = []
    if os.path.exists(sch):
        subprocess.run([KICAD_CLI, "sch", "export", "pdf", "-o", os.path.join(out, "schematic.pdf"), sch], check=True, capture_output=True)
        made.append("schematic.pdf")
    with open(pcb) as fh:
        text = fh.read()
    if "(footprint " not in text:
        print(f"{out}: {' '.join(made)} (no footprints on the board yet)")
        return
    tmp = tempfile.mkdtemp()
    try:
        def svg(layers, name, *extra):
            s = os.path.join(tmp, name + ".svg")
            subprocess.run([KICAD_CLI, "pcb", "export", "svg", "--layers", layers, "--mode-single", "--fit-page-to-board",
                            "--exclude-drawing-sheet", *extra, "-o", s, pcb], check=True, capture_output=True)
            png = os.path.join(out, name + ".png")
            subprocess.run(["rsvg-convert", "-w", "3600", "-b", "white", s, "-o", png], check=True)
            made.append(name + ".png")
            return png
        svg("F.Cu,F.SilkS,Edge.Cuts", "pcb-front")
        svg("B.Cu,B.SilkS,Edge.Cuts", "pcb-back", "--mirror")
        svg("B.Paste,B.SilkS,B.Fab,Edge.Cuts", "assembly-back", "--mirror")
        if '"In1.Cu"' in text:
            a = svg("In1.Cu,F.Cu,Edge.Cuts", "planes-in1")
            b = svg("In2.Cu,B.Cu,Edge.Cuts", "planes-in2")
            if shutil.which("magick"):
                font = "/System/Library/Fonts/Supplemental/Arial.ttf"
                subprocess.run(["magick", a, b, "-resize", "2400x", "-gravity", "north", "-background", "white", "-font", font,
                                "(", "-clone", "0", "-splice", "0x90", "-pointsize", "44", "-annotate", "+0+20",
                                "In1.Cu plane with the F.Cu tracks over it", ")",
                                "(", "-clone", "1", "-splice", "0x90", "-pointsize", "44", "-annotate", "+0+20",
                                "In2.Cu plane with the B.Cu tracks over it", ")",
                                "-delete", "0,1", "-append", os.path.join(out, "planes-routing.png")], check=True)
                os.remove(a); os.remove(b)
                made = [m for m in made if not m.startswith("planes-in")] + ["planes-routing.png"]
        from . import plot_print  # runs under KiCad's Python: spawn it there
        cmd = [KICAD_PYTHON, "-m", "pcbkit.release.plot_print", pcb, os.path.join(out, "board.pdf")]
        if hide_refs:
            cmd += ["--hide-refs", hide_refs]
        env = dict(os.environ, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        r = subprocess.run(cmd, capture_output=True, text=True, env=env)
        if r.returncode:
            print("board.pdf failed:", quiet(r.stderr)[-400:])
        else:
            made.append("board.pdf")
        if render:
            w, h = render_size
            for side in ("top", "bottom"):
                r = subprocess.run([KICAD_CLI, "pcb", "render", "--side", side, "--width", str(w), "--height", str(h),
                                    "--zoom", str(zoom), "-o", os.path.join(out, f"render-{side}.png"), pcb], capture_output=True)
                if r.returncode:
                    print(f"render {side} failed")
                else:
                    made.append(f"render-{side}.png")
    finally:
        shutil.rmtree(tmp)
    print(f"{out}: {' '.join(made)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("out")
    ap.add_argument("--zoom", type=float, default=1.0)
    ap.add_argument("--render-size", default="1600x1600")
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--hide-refs", help="regex of references to leave off the printable drawing")
    a = ap.parse_args(argv)
    w, h = (int(v) for v in a.render_size.lower().split("x"))
    previews(a.board, a.out, a.zoom, (w, h), not a.no_render, a.hide_refs)


if __name__ == "__main__":
    main()
