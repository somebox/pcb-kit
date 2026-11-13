"""Render a region of one copper layer with copper coloured by net (zone fills, pads, tracks, vias), courtyards and
reference labels. A layout-review aid for power stages, where KiCad's own plots show every net in the same colour.

  pcbkit netview BOARD.kicad_pcb OUT.svg X0 Y0 X1 Y1 [--layer B.Cu] [--colours nets.json] [--colour NET=#rrggbb]
                 [--detail] [--fill] [--png OUT.png]

Run on a board whose zones are filled (e.g. after `pcbkit drc --save`), or pass --fill to fill them in memory first.
Nets are coloured by the last path component of their name; the built-in table covers the usual rails, --colours
adds or overrides from a JSON object {name: "#rrggbb"} (a key ending in "*" matches a prefix), and --colour sets one
net. --png converts with rsvg-convert. Harvested from dodeca v1.3's netview.py.

--detail draws what assembly sees on an outer layer: each pad's mask opening dashed green, its paste hatched, the
numbers of the pads on coloured nets, and a red ring round any via hole that lands in paste (solder can run down an
open via). It leaves out courtyards and reference labels, and is drawn at least 640 px across.

Comparing layout options: the same window of several boards side by side, in the same colours, on one sheet.

  pcbkit netview BEFORE.kicad_pcb OUT.svg X0 Y0 X1 Y1 --layer F.Cu --detail --title "Before" \\
      --compare OPTION1.kicad_pcb="Option 1" --compare OPTION2.kicad_pcb="Option 2" \\
      --ref datasheet-fig.png="Datasheet layout" --metrics VOUT,GND --png OUT.png

--ref images (a datasheet's layout figure, an EVM) come first, turned counter-clockwise by --ref-rotate; then the
board, then each --compare board in order; "\\n" in a title starts a new line. With --compare, --ref, --title,
--metrics or --note the output is a sheet: a title over each panel, its numbers under it, one legend and a footnote.
The numbers count the vias whose centres are in the window: with --detail, holes under paste; for each --metrics net,
its through vias, their barrels' copper cross-section (18 um plating outside the finished hole) and their resistance
in parallel through the board. Blind and micro vias are counted apart and left out of the copper and resistance. A
--metrics name matches a net's full name, or else the last part of its name; nets that only share that last part
get a line each. --note adds to the footnote (the DRC result of each option, say).
"""
import argparse
import base64
import json
import math
import os
import subprocess
from xml.sax.saxutils import escape

from .kicad import ensure_pcbnew

COLOURS = {
    "GND": "#8a9bb0", "+3V3": "#74b816", "3V3": "#74b816", "+5V": "#e03131", "5V": "#e03131", "+12V": "#9c36b5",
    "VBUS": "#1098ad", "VIN": "#8d5524", "VBAT": "#2f9e44", "VCC": "#f08c00", "VDD": "#f08c00", "VSYS": "#f08c00",
    "SCL": "#1864ab", "SDA": "#4dabf7", "SW*": "#f5c400", "*_SW": "#f5c400",
}
OTHER = "#c9c9c9"
PLAIN_SCALE = 40.0      # px per mm of a plain view
PANEL_PX = 640.0        # a detail view or a sheet panel is drawn at least this wide
PLATING = 0.018         # mm of copper on a via's barrel wall (JLC's average)
RHO_CU = 1.72e-8        # ohm m
MASK = "#2f9e44"
MASK_LINE = 'stroke-width="1.8" stroke-dasharray="5.5 3.5"'
PASTE_HIT = "#d00000"
FONT = 'font-family="Helvetica"'
HATCH = ('<pattern id="paste" width="7" height="7" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
         '<line x1="0" y1="0" x2="0" y2="7" stroke="#555" stroke-width="1.2"/></pattern>')


def make_colour(table):
    exact = {k: v for k, v in table.items() if "*" not in k}
    prefix = {k[:-1]: v for k, v in table.items() if k.endswith("*")}
    suffix = {k[1:]: v for k, v in table.items() if k.startswith("*")}

    def colour(net):
        if net in exact:                                # a full hierarchical name first, then its last part
            return exact[net]
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


def via_ohms(drill, thickness, plating=PLATING):
    """Resistance of one via barrel through a board thickness mm thick: a copper tube of wall pi (drill + t) t."""
    return RHO_CU * thickness * 1e-3 / (wall_area(drill, plating) * 1e-6)


def wall_area(drill, plating=PLATING):
    """Copper cross-section of a via barrel in mm^2. KiCad's drill is the finished hole, so the plating lies outside
    it: the fab drills drill + 2t (JLC's drill tables give the size after plating)."""
    return math.pi * (drill + plating) * plating


def _rings(ps, T):
    """A SHAPE_POLY_SET as [(outline, [hole, ...])], points in mm."""
    out = []
    for i in range(ps.OutlineCount()):
        rings = [ps.Outline(i)] + [ps.Hole(i, h) for h in range(ps.HoleCount(i))]
        pts = [[(T(r.CPoint(k).x), T(r.CPoint(k).y)) for k in range(r.PointCount())] for r in rings]
        if pts[0]:
            out.append((pts[0], [h for h in pts[1:] if h]))
    return out


def _inside(x, y, ring):
    c = False
    for (ax, ay), (bx, by) in zip(ring, ring[1:] + ring[:1]):
        if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
            c = not c
    return c


def _seg_dist(x, y, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(x - ax - t * dx, y - ay - t * dy)


def circle_hits(x, y, r, polys):
    """Whether a circle of radius r at (x, y) overlaps any of the polygons [(outline, holes)] (mm)."""
    for outline, holes in polys:
        if _inside(x, y, outline) and not any(_inside(x, y, h) for h in holes):
            return True
        for ring in [outline] + holes:
            if any(_seg_dist(x, y, a[0], a[1], b[0], b[1]) < r for a, b in zip(ring, ring[1:] + ring[:1])):
                return True
    return False


def _number_key(n):
    return (0, int(n), "") if n.isdigit() else (1, 0, n)


def _load(pcbnew, path, fill):
    b = pcbnew.LoadBoard(path)
    if fill:
        pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    return b


def _check_window(x0, y0, x1, y1):
    if not (x1 > x0 and y1 > y0):
        raise ValueError(f"the window {x0} {y0} {x1} {y1} has no area: X0 Y0 is its top-left corner, X1 Y1 its "
                         "bottom-right, in mm")


def _scale(width, detail_or_sheet):
    return max(PLAIN_SCALE, PANEL_PX / width) if detail_or_sheet else PLAIN_SCALE


def _text_box(x, y, text, size=13):
    """A bold label on a white box, centred on (x, y) px."""
    w, h = 0.62 * size * len(text) + 6, size + 4
    return (f'<rect x="{x - w / 2:.1f}" y="{y - h / 2:.1f}" width="{w:.1f}" height="{h:.1f}" rx="3" fill="white" '
            f'fill-opacity="0.7"/><text x="{x:.1f}" y="{y + size * 0.36:.1f}" font-size="{size}" {FONT} '
            f'font-weight="bold" text-anchor="middle">{escape(text)}</text>')


def draw(pcbnew, b, layer_name, win, colour, scale, detail=False):
    """One board's window on one layer, as SVG elements in px with the window's top-left corner at the origin.

    Returns {"svg": [...], "vias": [(x, y, size, drill, net, in_paste, through)], "pads": {net: {ref: {numbers}}},
    "nets": {nets with copper in the window}, "thickness": mm, "detail": whether mask and paste were drawn (an outer
    layer only)}; positions in mm. A pad counts for "pads" and "nets" when any of its copper is inside the window."""
    T, F = pcbnew.ToMM, pcbnew.FromMM
    x0, y0, x1, y1 = win
    X = lambda v: (v - x0) * scale  # noqa: E731
    Y = lambda v: (v - y0) * scale  # noqa: E731

    def paths(polys):
        for outline, holes in polys:
            yield " ".join("M" + " L".join(f"{X(x):.2f},{Y(y):.2f}" for x, y in ring) + " Z"
                           for ring in [outline] + holes)

    def overlaps(bb, m=0.0):
        return (T(bb.GetRight()) > x0 - m and T(bb.GetLeft()) < x1 + m
                and T(bb.GetBottom()) > y0 - m and T(bb.GetTop()) < y1 + m)

    def shape(item, lyr, grow=0, within=None):
        ps = pcbnew.SHAPE_POLY_SET()
        item.TransformShapeToPolygon(ps, lyr, grow, F(0.002), pcbnew.ERROR_INSIDE)
        if within is not None:
            ps.BooleanIntersection(within)
        return _rings(ps, T)

    layer = b.GetLayerID(layer_name)
    front, back = layer == pcbnew.F_Cu, layer == pcbnew.B_Cu
    mask_layer = pcbnew.F_Mask if front else pcbnew.B_Mask if back else None
    paste_layer = pcbnew.F_Paste if front else pcbnew.B_Paste if back else None
    detail = detail and (front or back)
    clip = pcbnew.SHAPE_POLY_SET()
    clip.NewOutline()
    for x, y in ((x0 - 1, y0 - 1), (x1 + 1, y0 - 1), (x1 + 1, y1 + 1), (x0 - 1, y1 + 1)):
        clip.Append(F(x), F(y))
    window = pcbnew.SHAPE_POLY_SET()
    window.NewOutline()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        window.Append(F(x), F(y))
    zones, tracks, pads, masks, pastes, vias, labels, courts, refs, edges = ([] for _ in range(10))
    nets, pad_nets, via_list, paste_polys, number_at, drills, tech_zones = set(), {}, [], [], [], [], []

    for z in b.Zones():
        if z.GetIsRuleArea() or not z.IsOnLayer(layer) or not z.HasFilledPolysForLayer(layer):
            continue
        ps = pcbnew.SHAPE_POLY_SET(z.GetFilledPolysList(layer))
        ps.BooleanIntersection(clip)             # drawn a little past the window, so its edge isn't cut short
        polys = _rings(ps, T)
        seen = pcbnew.SHAPE_POLY_SET(ps)
        seen.BooleanIntersection(window)
        if seen.OutlineCount():
            nets.add(z.GetNetname())
        for d in paths(polys):
            zones.append(f'<path d="{d}" fill="{colour(z.GetNetname())}" fill-opacity="{0.35 if detail else 0.45}" '
                         f'fill-rule="evenodd"/>')

    for t in b.GetTracks():
        if t.GetClass() == "PCB_VIA":
            p = t.GetPosition()
            x, y, d = T(p.x), T(p.y), T(t.GetWidth(layer))
            if t.IsOnLayer(layer) and math.hypot(max(x0 - x, 0, x - x1), max(y0 - y, 0, y - y1)) < d / 2:
                via_list.append([x, y, d, T(t.GetDrillValue()), t.GetNetname(), False,
                                 t.GetViaType() == pcbnew.VIATYPE_THROUGH])
                nets.add(t.GetNetname())
        elif t.GetLayer() == layer and overlaps(t.GetBoundingBox()):
            if shape(t, layer, within=window):    # a diagonal track's box can reach the window when it doesn't
                nets.add(t.GetNetname())
            if t.GetClass() == "PCB_ARC":
                for d in paths(shape(t, layer)):
                    tracks.append(f'<path d="{d}" fill="{colour(t.GetNetname())}"/>')
                continue
            s, e = t.GetStart(), t.GetEnd()
            dash = ' stroke-dasharray="4 2"' if t.IsLocked() else ""
            tracks.append(f'<line x1="{X(T(s.x)):.2f}" y1="{Y(T(s.y)):.2f}" x2="{X(T(e.x)):.2f}" y2="{Y(T(e.y)):.2f}" '
                          f'stroke="{colour(t.GetNetname())}" stroke-width="{T(t.GetWidth()) * scale:.2f}" '
                          f'stroke-linecap="round"{dash}/>')

    def aperture(polys, kind):
        for d in paths(polys):
            if kind == "mask":
                masks.append(f'<path d="{d}" fill="none" stroke="{MASK}" {MASK_LINE}/>')
            else:
                pastes.append(f'<path d="{d}" fill="url(#paste)" fill-rule="evenodd"/>')
        if kind == "paste":
            paste_polys.extend(polys)

    for fp in b.GetFootprints():
        if not overlaps(fp.GetBoundingBox(False), 0.5):
            continue
        for p in fp.Pads():
            on_cu = p.IsOnLayer(layer)        # an exposed pad's paste is often apertures: pads on the paste layer only
            if not on_cu and not (detail and (p.IsOnLayer(mask_layer) or p.IsOnLayer(paste_layer))):
                continue
            net, polys = p.GetNetname(), shape(p, layer) if on_cu else []
            pc = p.GetPosition()
            px, py = T(pc.x), T(pc.y)
            seen = None                 # a box round the part of the pad's copper inside the window
            inner = shape(p, layer, within=window) if polys else []
            if inner:
                xs = [x for o, _ in inner for x, _ in o]
                ys = [y for o, _ in inner for _, y in o]
                seen = (min(xs), min(ys), max(xs), max(ys))
            if seen:
                nets.add(net)
                pad_nets.setdefault(net, {}).setdefault(fp.GetReference(), set()).add(p.GetNumber())
            look = 'stroke="#333" fill-opacity="0.9"' if detail else 'stroke="black"'
            for d in paths(polys):
                pads.append(f'<path d="{d}" fill="{colour(net)}" {look} stroke-width="0.6"/>')
            if on_cu and p.GetDrillSize().x > 0:
                drills.append((px, py, T(max(p.GetDrillSize().x, p.GetDrillSize().y)) / 2))
                pads.append(f'<circle cx="{X(px):.2f}" cy="{Y(py):.2f}" r="{T(p.GetDrillSize().x) / 2 * scale:.2f}" '
                            f'fill="white" stroke="black"/>')
            if not detail:
                continue
            if p.IsOnLayer(mask_layer):
                aperture(shape(p, layer, p.GetSolderMaskExpansion(mask_layer)), "mask")
            if p.IsOnLayer(paste_layer):
                m = p.GetSolderPasteMargin(paste_layer)
                if m.x == m.y or p.GetShape(layer) == pcbnew.PAD_SHAPE_CUSTOM:
                    aperture(shape(p, layer, m.x), "paste")     # KiCad's plotter grows a custom shape by m.x
                else:                   # a margin ratio on a non-square pad: grow each axis, as KiCad's plotter does
                    q = pcbnew.PAD(p)
                    s = p.GetSize(layer)
                    q.SetSize(layer, pcbnew.VECTOR2I(s.x + 2 * m.x, s.y + 2 * m.y))
                    aperture(shape(q, layer), "paste")
            if seen and colour(net) != OTHER:
                number_at.append((p.GetNumber(), *seen, inner))
        if detail:
            for g in fp.GraphicalItems():
                if g.GetLayer() in (mask_layer, paste_layer):
                    aperture(shape(g, g.GetLayer()), "mask" if g.GetLayer() == mask_layer else "paste")
            tech_zones.extend(fp.Zones())
        elif (layer == pcbnew.B_Cu) == fp.IsFlipped():
            fp.BuildCourtyardCaches()
            for d in paths(_rings(fp.GetCourtyard(pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd), T)):
                courts.append(f'<path d="{d}" fill="none" stroke="#228be6" stroke-width="1" stroke-dasharray="3 2"/>')
            c = fp.GetPosition()
            refs.append(f'<text x="{X(T(c.x)):.1f}" y="{Y(T(c.y)) + 4:.1f}" font-size="13" {FONT} font-weight="bold" '
                        f'text-anchor="middle" fill="black" stroke="white" stroke-width="3" paint-order="stroke">'
                        f'{escape(fp.GetReference())}</text>')
    if detail:
        for g in b.GetDrawings():
            if g.GetLayer() in (mask_layer, paste_layer) and overlaps(g.GetBoundingBox()):
                aperture(shape(g, g.GetLayer()), "mask" if g.GetLayer() == mask_layer else "paste")
        for z in list(b.Zones()) + tech_zones:          # a stencil or mask opening drawn as a zone on that layer
            if z.GetIsRuleArea() or not overlaps(z.GetBoundingBox()):
                continue
            for lyr, kind in ((mask_layer, "mask"), (paste_layer, "paste")):
                if z.IsOnLayer(lyr):
                    ps = pcbnew.SHAPE_POLY_SET(z.GetFilledPolysList(lyr)) if z.HasFilledPolysForLayer(lyr) else None
                    ps = ps if ps is not None and ps.OutlineCount() else pcbnew.SHAPE_POLY_SET(z.Outline())
                    ps.BooleanIntersection(clip)
                    aperture(_rings(ps, T), kind)

    for v in via_list:
        x, y, d, drill, net = v[:5]
        v[5] = bool(detail) and circle_hits(x, y, drill / 2, paste_polys)
        thick = 1.6 if detail else 1
        vias.append(f'<circle cx="{X(x):.2f}" cy="{Y(y):.2f}" r="{d / 2 * scale:.2f}" fill="{colour(net)}" '
                    f'stroke="{"#222" if detail else "black"}" stroke-width="{thick}"/>')
        ring = (f' stroke="{PASTE_HIT}" stroke-width="4"' if v[5]
                else ' stroke="#222" stroke-width="1.6"' if detail else "")
        vias.append(f'<circle cx="{X(x):.2f}" cy="{Y(y):.2f}" r="{drill / 2 * scale:.2f}" fill="white"{ring}/>')

    half = 9.5 / scale                                  # half a label's height, mm
    holes = [(v[0], v[1], v[3] / 2) for v in via_list] + drills
    for number, bx0, by0, bx1, by1, copper in number_at:
        cx, cy, w, h = (bx0 + bx1) / 2, (by0 + by1) / 2, bx1 - bx0, by1 - by0
        spots = [(cx, cy)]
        for k in (1, 2, 3, 4):                          # off the pad's vias, nearest its middle first
            along = [(0, 1)] if h >= 2 * w else [(1, 0)] if w >= 2 * h else [(0, 1), (1, 0), (1, 1), (1, -1)]
            for ax, ay in along:
                spots += [(cx + s * 0.1 * k * ax * w, cy + s * 0.1 * k * ay * h) for s in (1, -1)]
        room = lambda s: min([math.hypot(s[0] - v[0], s[1] - v[1]) - v[2] / 2 for v in via_list] or [9.0])  # noqa: E731
        hw, hh = (0.62 * 13 * len(number) + 6) / 2 / scale, 8.5 / scale     # the label's box, as _text_box draws it
        on_pad = [s for s in spots if any(_inside(s[0], s[1], o) and not any(_inside(s[0], s[1], g) for g in gaps)
                                          for o, gaps in copper)]      # on this pad's copper, not a neighbour's
        clear = [s for s in on_pad if all(math.hypot(max(abs(s[0] - x) - hw, 0), max(abs(s[1] - y) - hh, 0)) > r
                                          for x, y, r in holes)]
        if not clear:                                   # every spot hides a hole (a via's, maybe ringed red, or a
            continue                                    # pad's drill): the legend lists the pad
        spot = next((s for s in clear if room(s) > half), max(clear, key=room))
        labels.append(_text_box(X(spot[0]), Y(spot[1]), number))

    edge = pcbnew.SHAPE_POLY_SET()
    b.GetBoardPolygonOutlines(edge, True)
    for d in paths(_rings(edge, T)):
        edges.append(f'<path d="{d}" fill="none" stroke="black" stroke-width="2"/>')
    for gx in range(int(x0) + 1, int(x1) + 1):          # 1 mm ticks along the edges, labelled every 5 mm
        h = 12 if gx % 5 == 0 else 5
        edges.append(f'<line x1="{X(gx):.1f}" y1="0" x2="{X(gx):.1f}" y2="{h}" stroke="black"/>')
        if gx % 5 == 0:
            edges.append(f'<text x="{X(gx) + 2:.1f}" y="22" font-size="11" {FONT}>{gx}</text>')
    for gy in range(int(y0) + 1, int(y1) + 1):
        h = 12 if gy % 5 == 0 else 5
        edges.append(f'<line x1="0" y1="{Y(gy):.1f}" x2="{h}" y2="{Y(gy):.1f}" stroke="black"/>')
        if gy % 5 == 0:
            edges.append(f'<text x="14" y="{Y(gy) + 4:.1f}" font-size="11" {FONT}>{gy}</text>')

    svg = zones + tracks + pads + masks + pastes + vias + labels + courts + edges + refs
    return {"svg": svg, "vias": [tuple(v) for v in via_list], "pads": pad_nets, "nets": nets,
            "thickness": T(b.GetDesignSettings().GetBoardThickness()), "detail": detail}


def render(board_path, out, x0, y0, x1, y1, layer_name="B.Cu", colours=None, detail=False, fill=False):
    """One board's window as an SVG of its own."""
    _check_window(x0, y0, x1, y1)
    ensure_pcbnew()
    import pcbnew
    colour = make_colour({**COLOURS, **(colours or {})})
    scale = _scale(x1 - x0, detail)
    W, H = (x1 - x0) * scale, (y1 - y0) * scale
    view = draw(pcbnew, _load(pcbnew, board_path, fill), layer_name, (x0, y0, x1, y1), colour, scale, detail)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" height="{H:.0f}" viewBox="0 0 {W:.0f} {H:.0f}">',
           f"<defs>{HATCH}</defs>" if detail else "", '<rect width="100%" height="100%" fill="white"/>']
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(svg + view["svg"] + ["</svg>"]))


def _wrap(text, chars):
    chars, lines, line = max(1, chars), [], ""
    for word in text.split():
        while len(word) > chars:                        # a word longer than a line (a long net name) is cut
            if line:
                lines.append(line)
                line = ""
            lines.append(word[:chars])
            word = word[chars:]
        if line and len(line) + 1 + len(word) > chars:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + ([line] if line else [])


def _image_href(path):
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp", "gif": "gif", "svg": "svg+xml"}.get(ext, "png")
    with open(path, "rb") as fh:
        return f"data:image/{mime};base64," + base64.b64encode(fh.read()).decode()


def _pad_list(refs, limit=8):
    parts = []
    for ref in sorted(refs):
        nums = sorted(refs[ref], key=_number_key)
        parts.append(f"{ref} " + ", ".join(nums[:limit]) + (", ..." if len(nums) > limit else ""))
    return "; ".join(parts)


def sheet(boards, out, x0, y0, x1, y1, layer_name="B.Cu", colours=None, detail=False, fill=False, refs=(),
          ref_rotate=0, metrics=(), cols=None, note=""):
    """Several boards' windows side by side, after any reference images: boards and refs are [(path, title)]."""
    _check_window(x0, y0, x1, y1)
    ensure_pcbnew()
    import pcbnew
    colour = make_colour({**COLOURS, **(colours or {})})
    scale = _scale(x1 - x0, True)
    PW, PH = (x1 - x0) * scale, (y1 - y0) * scale
    M, GX, GY = 24, 36, 30
    views = []
    for path, _title in boards:
        views.append(draw(pcbnew, _load(pcbnew, path, fill), layer_name, (x0, y0, x1, y1), colour, scale, detail))
    paste = any(v["detail"] for v in views)            # an outer layer, whatever the board calls it

    def in_window(view):
        return [v for v in view["vias"] if x0 < v[0] < x1 and y0 < v[1] < y1]

    names = {}                  # what each --metrics name means, the same in every panel
    for key in metrics:
        found = {v[4] for view in views for v in in_window(view) if v[4] == key or v[4].split("/")[-1] == key}
        names[key] = [key] if key in found or not found else sorted(found)  # the full name asked for, if any

    def numbers(view):
        inside = in_window(view)
        lines = [f"holes under paste: {sum(1 for v in inside if v[5])}"] if paste else []
        for key in metrics:
            for name in names[key]:                   # nets sharing a last name part are not in parallel
                label = key if len(names[key]) == 1 else name
                on_net = [v for v in inside if v[4] == name]
                vs = [v for v in on_net if v[6]]      # blind and micro vias span part of the board: not counted
                other = f", {len(on_net) - len(vs)} blind/micro not counted" if len(vs) < len(on_net) else ""
                if not on_net:
                    lines.append(f"{label}: no vias")
                    continue
                if not vs:
                    lines.append(f"{label}: no through vias{other}")
                    continue
                ohms = 1 / sum(1 / via_ohms(v[3], view["thickness"]) for v in vs)
                lines.append(f"{label}: {len(vs)} vias, wall {sum(wall_area(v[3]) for v in vs):.3f} mm², "
                             f"{ohms * 1000:.2f} mΩ{other}")
        return lines

    panels = [("ref", path, title) for path, title in refs] + [("board", v, t) for v, (_p, t) in zip(views, boards)]
    titles = [[w for line in (t or "").replace("\\n", "\n").split("\n") for w in (_wrap(line, int(PW / 8.0)) or [""])]
              for _k, _x, t in panels]                # 15 px text, about 8 px a character: wrapped to the panel
    under = [[w for line in (numbers(x) if k == "board" else []) for w in _wrap(line, int(PW / 7.4))]
             for k, x, _t in panels]
    cols = cols or (len(panels) if len(panels) <= 2 else 2 if len(panels) <= 4 else 3)
    rows = -(-len(panels) // cols)
    title_h = 19 * max(len(t) for t in titles) + 8
    under_h = (18 * max(len(u) for u in under) + 16) if any(under) else 8
    cell_h = title_h + PH + under_h
    W = 2 * M + cols * PW + (cols - 1) * GX
    svg = [f"<defs>{HATCH}</defs>", '<rect width="100%" height="100%" fill="white"/>']
    for i, (kind, item, _t) in enumerate(panels):
        cx, cy = M + (i % cols) * (PW + GX), M + (i // cols) * (cell_h + GY)
        for j, line in enumerate(titles[i]):
            svg.append(f'<text x="{cx:.1f}" y="{cy + 15 + 19 * j:.1f}" font-size="15" {FONT}>{escape(line)}</text>')
        py = cy + title_h
        svg.append(f'<svg x="{cx:.1f}" y="{py:.1f}" width="{PW:.1f}" height="{PH:.1f}" '
                   f'viewBox="0 0 {PW:.1f} {PH:.1f}">')
        if kind == "ref":
            turned = ref_rotate % 180 == 90
            w, h = (PH, PW) if turned else (PW, PH)
            svg.append(f'<g transform="rotate({-ref_rotate} {PW / 2:.1f} {PH / 2:.1f})"><image x="{(PW - w) / 2:.1f}" '
                       f'y="{(PH - h) / 2:.1f}" width="{w:.1f}" height="{h:.1f}" preserveAspectRatio="xMidYMid meet" '
                       f'xlink:href="{_image_href(item)}"/></g>')
        else:
            svg.append('<rect width="100%" height="100%" fill="white"/>')
            svg += item["svg"]
        svg.append("</svg>")
        svg.append(f'<rect x="{cx:.1f}" y="{py:.1f}" width="{PW:.1f}" height="{PH:.1f}" fill="none" stroke="#333" '
                   f'stroke-width="1.2"/>')
        for j, line in enumerate(under[i]):
            svg.append(f'<text x="{cx:.1f}" y="{py + PH + 22 + 18 * j:.1f}" font-size="14" {FONT}>'
                       f'{escape(line)}</text>')

    shown = sorted({n for v in views for n in v["nets"] if colour(n) != OTHER}, key=lambda n: (n.split("/")[-1], n))
    short = [n.split("/")[-1] for n in shown]
    items = []
    for n, s in zip(shown, short):
        on = {}
        for v in views:
            for ref, nums in v["pads"].get(n, {}).items():
                on.setdefault(ref, set()).update(nums)
        name = s if short.count(s) == 1 else n          # nets sharing a last part go by their full names
        label = name + (f" ({_pad_list(on)})" if on else "")
        items.append((f'<rect width="24" height="14" fill="{colour(n)}"/>', label))
    if paste:
        items += [('<rect width="24" height="14" fill="url(#paste)" stroke="#555"/>', "solder paste"),
                  (f'<line x1="0" y1="7" x2="24" y2="7" stroke="{MASK}" {MASK_LINE}/>', "mask opening"),
                  (f'<circle cx="12" cy="7" r="6" fill="white" stroke="{PASTE_HIT}" stroke-width="3"/>',
                   "via hole in paste")]
    lcols = 3
    while lcols > 1 and max([34 + 7.4 * len(t) for _s, t in items] or [0]) > (W - 2 * M) / lcols:
        lcols -= 1
    top = M + rows * cell_h + (rows - 1) * GY + 10
    width = (W - 2 * M) / lcols
    chars = max(12, int((width - 34) / 7.4))           # an entry longer than its column wraps under itself
    for r in range(0, len(items), lcols):
        row = [(swatch, _wrap(text, chars)) for swatch, text in items[r:r + lcols]]
        for j, (swatch, lines) in enumerate(row):
            lx = M + j * width
            svg.append(f'<g transform="translate({lx:.1f} {top:.1f})">{swatch}</g>')
            for k, line in enumerate(lines):
                svg.append(f'<text x="{lx + 32:.1f}" y="{top + 12 + 17 * k:.1f}" font-size="14" {FONT}>'
                           f'{escape(line)}</text>')
        top += 22 + 17 * (max(len(lines) for _s, lines in row) - 1)
    top += 12
    foot = []
    if metrics and views:
        thicks = [v["thickness"] for v in views]
        board = (f"the {thicks[0]:g} mm board" if len(set(thicks)) == 1 else
                 "each board, board by board " + ", ".join(f"{t:g}" for t in thicks) + " mm thick")
        foot.append(f"Vias counted with their centres in the window. Wall = copper cross-section of their barrels "
                    f"at {PLATING * 1000:.0f} µm plating outside the finished hole; mΩ = a net's through vias in "
                    f"parallel through {board}.")
    if note:
        foot.append(note)
    lines = _wrap(" ".join(foot), int((W - 2 * M) / 6.0))
    for j, line in enumerate(lines):
        svg.append(f'<text x="{M}" y="{top + 12 + 16 * j:.1f}" font-size="12" fill="#333" {FONT}>'
                   f'{escape(line)}</text>')
    H = top + 16 * len(lines) + M
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{W:.0f}" '
            f'height="{H:.0f}" viewBox="0 0 {W:.0f} {H:.0f}">')
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join([head] + svg + ["</svg>"]))


def _titled(spec):
    path, _, title = spec.partition("=")
    return path, title or os.path.relpath(path)


def _count(text):
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError(f"{text}: must be 1 or more")
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("out", help="SVG path")
    ap.add_argument("x0", type=float); ap.add_argument("y0", type=float)
    ap.add_argument("x1", type=float); ap.add_argument("y1", type=float)
    ap.add_argument("--layer", default="B.Cu")
    ap.add_argument("--colours", help="JSON file {net: colour}")
    ap.add_argument("--colour", action="append", default=[], metavar="NET=#RRGGBB", help="one net's colour; repeatable")
    ap.add_argument("--detail", action="store_true", help="mask openings, paste, pad numbers, via holes in paste")
    ap.add_argument("--fill", action="store_true", help="fill the zones in memory before drawing")
    ap.add_argument("--compare", action="append", default=[], metavar="BOARD[=TITLE]", help="another board; repeatable")
    ap.add_argument("--ref", action="append", default=[], metavar="IMAGE[=TITLE]", help="a reference image; repeatable")
    ap.add_argument("--ref-rotate", type=int, default=0, choices=(0, 90, 180, 270), help="turn --ref images, CCW")
    ap.add_argument("--title", help="the first board's title on a sheet")
    ap.add_argument("--metrics", default="", help="nets whose vias to count, comma-separated (full name or last "
                    "part; a last part shared by several nets gives a line per net)")
    ap.add_argument("--cols", type=_count, help="panels per row on a sheet")
    ap.add_argument("--note", default="", help="added to a sheet's footnote")
    ap.add_argument("--png", help="also convert to this PNG with rsvg-convert")
    a = ap.parse_args(argv)
    try:
        _check_window(a.x0, a.y0, a.x1, a.y1)
    except ValueError as e:
        ap.error(str(e))
    colours = {}
    if a.colours:
        with open(a.colours) as fh:
            colours = json.load(fh)
    for spec in a.colour:
        net, _, value = spec.partition("=")
        colours[net] = value
    metrics = [m.strip() for m in a.metrics.split(",") if m.strip()]
    if a.compare or a.ref or a.title or metrics or a.note:
        boards = [(a.board, a.title or os.path.relpath(a.board))] + [_titled(s) for s in a.compare]
        sheet(boards, a.out, a.x0, a.y0, a.x1, a.y1, a.layer, colours, a.detail, a.fill,
              [_titled(s) for s in a.ref], a.ref_rotate, metrics, a.cols, a.note)
    else:
        render(a.board, a.out, a.x0, a.y0, a.x1, a.y1, a.layer, colours, a.detail, a.fill)
    if a.png:
        subprocess.run(["rsvg-convert", "-b", "white", a.out, "-o", a.png], check=True)
    print(a.png or a.out)


if __name__ == "__main__":
    main()
