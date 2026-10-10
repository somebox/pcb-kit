"""Render a region of one copper layer with copper coloured by net (zone fills, pads, tracks, vias), courtyards and
reference labels. A layout-review aid for power stages, where KiCad's own plots show every net in the same colour.

  pcbkit netview BOARD.kicad_pcb OUT.svg X0 Y0 X1 Y1 [--layer B.Cu] [--colours nets.json] [--png OUT.png]

Run on a board whose zones are filled (e.g. after `pcbkit drc --save`). Nets are coloured by the last path
component of their name; the built-in table covers the usual rails, and --colours adds or overrides from a JSON
object {name: "#rrggbb"} (a key ending in "*" matches a prefix). --png converts with rsvg-convert. Harvested from
dodeca v1.3's netview.py.
"""
import argparse
import json
import subprocess

from .kicad import ensure_pcbnew

COLOURS = {
    "GND": "#8a9bb0", "+3V3": "#74b816", "3V3": "#74b816", "+5V": "#e03131", "5V": "#e03131", "+12V": "#9c36b5",
    "VBUS": "#1098ad", "VIN": "#8d5524", "VBAT": "#2f9e44", "VCC": "#f08c00", "VDD": "#f08c00", "VSYS": "#f08c00",
    "SCL": "#1864ab", "SDA": "#4dabf7", "SW*": "#f5c400", "*_SW": "#f5c400",
}
OTHER = "#c9c9c9"


def make_colour(table):
    exact = {k: v for k, v in table.items() if "*" not in k}
    prefix = {k[:-1]: v for k, v in table.items() if k.endswith("*")}
    suffix = {k[1:]: v for k, v in table.items() if k.startswith("*")}

    def colour(net):
        name = net.split("/")[-1]
        if name in exact:
            return exact[name]
        for p, c in prefix.items():
            if name.startswith(p):
                return c
        for s, c in suffix.items():
            if name.endswith(s):
                return c
        return OTHER
    return colour


def poly_paths(ps, sx, sy):
    out = []
    for i in range(ps.OutlineCount()):
        rings = [ps.Outline(i)] + [ps.Hole(i, h) for h in range(ps.HoleCount(i))]
        d = ""
        for r in rings:
            pts = [r.CPoint(k) for k in range(r.PointCount())]
            if pts:
                d += "M" + " L".join(f"{sx(p.x):.2f},{sy(p.y):.2f}" for p in pts) + " Z "
        out.append(d)
    return out


def render(board_path, out, x0, y0, x1, y1, layer_name="B.Cu", colours=None):
    ensure_pcbnew()
    import pcbnew
    T = pcbnew.ToMM
    colour = make_colour({**COLOURS, **(colours or {})})
    b = pcbnew.LoadBoard(board_path)
    layer = b.GetLayerID(layer_name)
    S = 40.0  # px per mm
    sx = lambda v: (T(v) - x0) * S
    sy = lambda v: (T(v) - y0) * S
    W, H = (x1 - x0) * S, (y1 - y0) * S
    inside = lambda p: x0 - 3 < T(p.x) < x1 + 3 and y0 - 3 < T(p.y) < y1 + 3
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" height="{H:.0f}" viewBox="0 0 {W:.0f} {H:.0f}">',
           '<rect width="100%" height="100%" fill="white"/>']
    for z in b.Zones():
        if z.GetIsRuleArea() or not z.IsOnLayer(layer):
            continue
        fill = z.GetFilledPolysList(layer)
        for d in poly_paths(fill, sx, sy):
            svg.append(f'<path d="{d}" fill="{colour(z.GetNetname())}" fill-opacity="0.45" fill-rule="evenodd"/>')
    for t in b.GetTracks():
        if t.GetClass() == "PCB_VIA":
            if inside(t.GetPosition()):
                p = t.GetPosition()
                svg.append(f'<circle cx="{sx(p.x):.2f}" cy="{sy(p.y):.2f}" r="{T(t.GetWidth(layer)) / 2 * S:.2f}" '
                           f'fill="{colour(t.GetNetname())}" stroke="black" stroke-width="1"/>')
                svg.append(f'<circle cx="{sx(p.x):.2f}" cy="{sy(p.y):.2f}" r="{T(t.GetDrillValue()) / 2 * S:.2f}" fill="white"/>')
        elif t.GetLayer() == layer and (inside(t.GetStart()) or inside(t.GetEnd())):
            s, e = t.GetStart(), t.GetEnd()
            dash = ' stroke-dasharray="4 2"' if t.IsLocked() else ""
            svg.append(f'<line x1="{sx(s.x):.2f}" y1="{sy(s.y):.2f}" x2="{sx(e.x):.2f}" y2="{sy(e.y):.2f}" '
                       f'stroke="{colour(t.GetNetname())}" stroke-width="{T(t.GetWidth()) * S:.2f}" '
                       f'stroke-linecap="round"{dash}/>')
    labels = []
    for fp in b.GetFootprints():
        if not inside(fp.GetPosition()):
            continue
        for p in fp.Pads():
            if not p.IsOnLayer(layer):
                continue
            ps = pcbnew.SHAPE_POLY_SET()
            p.TransformShapeToPolygon(ps, layer, 0, pcbnew.FromMM(0.01), pcbnew.ERROR_INSIDE)
            for d in poly_paths(ps, sx, sy):
                svg.append(f'<path d="{d}" fill="{colour(p.GetNetname())}" stroke="black" stroke-width="0.6"/>')
            if p.GetDrillSize().x > 0:
                q = p.GetPosition()
                svg.append(f'<circle cx="{sx(q.x):.2f}" cy="{sy(q.y):.2f}" r="{T(p.GetDrillSize().x) / 2 * S:.2f}" fill="white" stroke="black"/>')
        side = pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd
        if (layer == pcbnew.B_Cu) == fp.IsFlipped():
            fp.BuildCourtyardCaches()
            cy = fp.GetCourtyard(side)
            for d in poly_paths(cy, sx, sy):
                svg.append(f'<path d="{d}" fill="none" stroke="#228be6" stroke-width="1" stroke-dasharray="3 2"/>')
            c = fp.GetPosition()
            labels.append(f'<text x="{sx(c.x):.1f}" y="{sy(c.y) + 4:.1f}" font-size="13" font-family="Helvetica" '
                          f'font-weight="bold" text-anchor="middle" fill="black" stroke="white" stroke-width="3" '
                          f'paint-order="stroke">{fp.GetReference()}</text>')
    edge = pcbnew.SHAPE_POLY_SET()
    b.GetBoardPolygonOutlines(edge, True)
    for d in poly_paths(edge, sx, sy):
        svg.append(f'<path d="{d}" fill="none" stroke="black" stroke-width="2"/>')
    for gx in range(int(x0) + 1, int(x1) + 1):  # 1 mm grid ticks along the edges, labelled every 5 mm
        h = 12 if gx % 5 == 0 else 5
        svg.append(f'<line x1="{(gx - x0) * S:.1f}" y1="0" x2="{(gx - x0) * S:.1f}" y2="{h}" stroke="black"/>')
        if gx % 5 == 0:
            svg.append(f'<text x="{(gx - x0) * S + 2:.1f}" y="22" font-size="11" font-family="Helvetica">{gx}</text>')
    for gy in range(int(y0) + 1, int(y1) + 1):
        h = 12 if gy % 5 == 0 else 5
        svg.append(f'<line x1="0" y1="{(gy - y0) * S:.1f}" x2="{h}" y2="{(gy - y0) * S:.1f}" stroke="black"/>')
        if gy % 5 == 0:
            svg.append(f'<text x="14" y="{(gy - y0) * S + 4:.1f}" font-size="11" font-family="Helvetica">{gy}</text>')
    svg += labels + ["</svg>"]
    open(out, "w").write("\n".join(svg))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("out", help="SVG path")
    ap.add_argument("x0", type=float); ap.add_argument("y0", type=float)
    ap.add_argument("x1", type=float); ap.add_argument("y1", type=float)
    ap.add_argument("--layer", default="B.Cu")
    ap.add_argument("--colours", help="JSON file {net: colour}")
    ap.add_argument("--png", help="also convert to this PNG with rsvg-convert")
    a = ap.parse_args(argv)
    colours = json.load(open(a.colours)) if a.colours else None
    render(a.board, a.out, a.x0, a.y0, a.x1, a.y1, a.layer, colours)
    if a.png:
        subprocess.run(["rsvg-convert", "-b", "white", a.out, "-o", a.png], check=True)
    print(a.png or a.out)


if __name__ == "__main__":
    main()
