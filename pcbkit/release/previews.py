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

`layer_png`, `render_png` and `turntable` are the pictures on their own, at any size: `pcbkit overview` makes its viewer
from them.
"""
import argparse
import os
import re
import shutil
import subprocess
import tempfile

from ..kicad import KICAD_PYTHON, kicad_cli, quiet


def layer_png(pcb, layers, png, width=3600, mirror=False, tmp=None, background="white"):
    """Some of a board's layers as one picture, WIDTH pixels wide: kicad-cli's SVG through rsvg-convert, on white or,
    with BACKGROUND None, on nothing, so pictures of single layers stack. Every picture of a board has the same
    frame, the board's own area. Returns the board's size in mm, (w, h)."""
    own = None if tmp else tempfile.mkdtemp()
    try:
        s = os.path.join(tmp or own, os.path.splitext(os.path.basename(png))[0] + ".svg")
        subprocess.run([kicad_cli(), "pcb", "export", "svg", "--layers", layers, "--mode-single", "--fit-page-to-board",
                        "--exclude-drawing-sheet", *(["--mirror"] if mirror else []), "-o", s, pcb], check=True, capture_output=True)
        subprocess.run(["rsvg-convert", "-w", str(width), *(["-b", background] if background else []), s, "-o", png],
                       check=True, stderr=subprocess.DEVNULL if not background else None)
        with open(s, encoding="utf-8") as fh:
            m = re.search(r'viewBox="[-\d.]+ [-\d.]+ ([\d.]+) ([\d.]+)"', fh.read(4000))
        return (float(m.group(1)), float(m.group(2))) if m else None
    finally:
        if own:
            shutil.rmtree(own)


def render_png(pcb, out, side="top", size=(1600, 1600), zoom=1.0, rotate=None, perspective=False, background=None):
    """A 3-D render of one side (kicad-cli pcb render), PNG or JPEG by OUT's name. ROTATE is (x, y, z) in degrees:
    z turns the board on its own axis, x tilts it (-50: seen from above at an angle). True when it was written."""
    cmd = [kicad_cli(), "pcb", "render", "--side", side, "--width", str(size[0]), "--height", str(size[1]), "--zoom", str(zoom)]
    if rotate:
        cmd += ["--rotate", ",".join("%g" % v for v in rotate)]
    if perspective:
        cmd.append("--perspective")
    if background:
        cmd += ["--background", background]
    return subprocess.run(cmd + ["-o", out, pcb], capture_output=True).returncode == 0 and os.path.exists(out)


def webp(png):
    """PNG to WebP beside it (cwebp: brew install webp), the PNG removed. The new name, or None without cwebp."""
    if not shutil.which("cwebp"):
        return None
    out = os.path.splitext(png)[0] + ".webp"
    if subprocess.run(["cwebp", "-quiet", "-q", "80", "-alpha_q", "90", png, "-o", out], capture_output=True).returncode:
        return None
    os.remove(png)
    return out


def turntable(pcb, out, frames=16, size=(800, 600), tilt=-50, sides=("top", "bottom"), jobs=4):
    """The board turning on its own axis, seen from above at an angle, FRAMES pictures a side into OUT:
    turn-<side>-NN.webp on a clear background, or .jpg on KiCad's own where there is no cwebp (then at most 12 a
    side, since a JPEG frame is three times a WebP one). Returns {side: [file names]}; a side that failed is left out."""
    from concurrent.futures import ThreadPoolExecutor
    clear = bool(shutil.which("cwebp"))
    n = frames if clear else min(frames, 12)

    def frame(job):
        side, i = job
        stem = os.path.join(out, "turn-%s-%02d" % (side, i))
        ok = render_png(pcb, stem + (".png" if clear else ".jpg"), side, size, rotate=(tilt, 0, 360.0 * i / n),
                        perspective=True, background="transparent" if clear else None)
        name = (webp(stem + ".png") if clear else stem + ".jpg") if ok else None
        return side, os.path.basename(name) if name else None

    os.makedirs(out, exist_ok=True)
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        done = list(pool.map(frame, [(side, i) for side in sides for i in range(n)]))
    made = {side: [name for s, name in done if s == side] for side in sides}
    return {side: names for side, names in made.items() if names and all(names)}


def previews(pcb, out, zoom=1.0, render_size=(1600, 1600), render=True, hide_refs=None):
    pcb = os.path.abspath(pcb)
    stem = os.path.splitext(pcb)[0]
    sch = stem + ".kicad_sch"
    os.makedirs(out, exist_ok=True)
    made = []
    if os.path.exists(sch):
        subprocess.run([kicad_cli(), "sch", "export", "pdf", "-o", os.path.join(out, "schematic.pdf"), sch], check=True, capture_output=True)
        made.append("schematic.pdf")
    with open(pcb) as fh:
        text = fh.read()
    if "(footprint " not in text:
        print(f"{out}: {' '.join(made)} (no footprints on the board yet)")
        return
    tmp = tempfile.mkdtemp()
    try:
        def svg(layers, name, *extra):
            png = os.path.join(out, name + ".png")
            layer_png(pcb, layers, png, 3600, mirror="--mirror" in extra, tmp=tmp)
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
            for side in ("top", "bottom"):
                if render_png(pcb, os.path.join(out, f"render-{side}.png"), side, render_size, zoom):
                    made.append(f"render-{side}.png")
                else:
                    print(f"render {side} failed")
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
