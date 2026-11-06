"""Footprint courtyards that don't describe the part.

  pcbkit courtyards BOARD.kicad_pcb [--fix] [--margin 0.25] [--ref REF ...]
  pcbkit courtyards LIB.pretty [--fix] [--margin 0.25]

KiCad's placement checks (courtyards_overlap, pth_inside_courtyard) trust each footprint's courtyard. A courtyard
that misses part of the footprint lets another part be placed on it, and DRC passes. KiCad's EasyEDA importer
leaves such courtyards (31 of the 44 parts on espcandle-pcb's base, 2026-11): turned against the pads, a sliver
0.2 mm wide, a connector's 1 mm inside its body, a module's round its pads but not its antenna, a back-side test
pad's on F.CrtYd. This reports, per footprint:
  - no courtyard, or one that isn't a closed outline (footprints with pads that aren't board-only or net ties);
  - a courtyard only on the other side from all its pads;
  - pads (and holes) outside the courtyard by more than 0.02 mm;
  - the body outside the courtyard: the fab outline by more than 0.05 mm or, for a footprint with no fab outline
    (EasyEDA's draw the body only in silk), the silk by more than 0.5 mm, not counting dots (circles under 1 mm
    across: pin-1 and polarity marks). KiCad's own libraries draw silk marks up to 0.8 mm outside the courtyard
    (SOT-23's pin-1 triangle, an electrolytic's +), so silk counts only without fab.
A courtyard larger than the part is not reported: a mounting hole's covers the screw head, a module's its antenna
keep-out. Of KiCad 10's own 15,464 footprints it reports 142 (2026-11), most of them connectors and switches whose
fab drawing shows a part over the board edge, and card-edge connectors; none on boards drawn from them so far.

--fix redraws each reported courtyard as a rectangle in the footprint's own frame around its pads, holes and body
(fab outline, or silk without one) plus --margin (0.25 mm, IPC-7351's nominal), on the side of its pads (the
footprint's own side for through-hole parts), and saves the board or the library footprints. `pcbkit check` lists
the same findings; `pcbkit import-easyeda` redraws them on import.
"""
import argparse
import math
import re
from pathlib import Path

from ..kicad import ensure_pcbnew, save_board

PAD_TOL = 0.02   # mm: a pad this far outside is rounding
FAB_TOL = 0.05
SILK_TOL = 0.5   # silk without fab: lines stand a little off the body, marks further
DOT = 0.5        # mm radius: a silk circle smaller than this is a mark (pin 1, polarity), not the body
MARGIN = 0.25
WIDTH = 0.05     # courtyard line width, as KiCad's libraries


def _mm(nm):
    return nm / 1e6


def local_copy(pcbnew, fp):
    """A copy of the footprint at the origin, unrotated (still mirrored if it is on the back), its courtyard
    polygons built."""
    c = pcbnew.FOOTPRINT(fp)
    c.SetOrientationDegrees(0)
    c.SetPosition(pcbnew.VECTOR2I(0, 0))
    c.BuildCourtyardCaches()
    return c


def shape_points(pcbnew, s):
    """Points along a graphic's centre line: corners and ends, and circles and arcs sampled."""
    t, V = s.GetShape(), pcbnew.VECTOR2I
    if t == pcbnew.SHAPE_T_SEGMENT:
        return [s.GetStart(), s.GetEnd()]
    if t == pcbnew.SHAPE_T_RECTANGLE:
        return list(s.GetRectCorners())
    if t == pcbnew.SHAPE_T_CIRCLE:
        c, r = s.GetCenter(), s.GetRadius()
        return [V(int(c.x + r * math.cos(k * math.pi / 8)), int(c.y + r * math.sin(k * math.pi / 8))) for k in range(16)]
    if t == pcbnew.SHAPE_T_ARC:
        c, r = s.GetCenter(), s.GetRadius()
        a0, da = s.GetArcAngleStart().AsRadians(), s.GetArcAngle().AsRadians()
        return [V(int(c.x + r * math.cos(a0 + da * k / 8)), int(c.y + r * math.sin(a0 + da * k / 8))) for k in range(9)]
    if t == pcbnew.SHAPE_T_POLY:
        ps, out = s.GetPolyShape(), []
        for i in range(ps.OutlineCount()):
            o = ps.Outline(i)
            out += [o.CPoint(k) for k in range(o.PointCount())]
        return out
    if t == pcbnew.SHAPE_T_BEZIER:
        return [s.GetStart(), s.GetBezierC1(), s.GetBezierC2(), s.GetEnd()]
    return []


def pad_shapes(pcbnew, fp):
    """[(number, side, points)] for each pad: number "" for a peg or hole; side "F" or "B" for an SMD pad, None for
    a through-hole pad or hole; points round its copper on its outer layer, and round its hole."""
    out = []
    for p in fp.Pads():
        ls = p.GetLayerSet()
        tht = p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH)
        layer = pcbnew.F_Cu if ls.Contains(pcbnew.F_Cu) else pcbnew.B_Cu if ls.Contains(pcbnew.B_Cu) else None
        if layer is None and not p.HasHole():
            continue  # paste or mask only
        pts = []
        if layer is not None:
            poly = p.GetEffectivePolygon(layer, pcbnew.ERROR_INSIDE)
            for i in range(poly.OutlineCount()):
                o = poly.Outline(i)
                pts += [o.CPoint(k) for k in range(o.PointCount())]
        if p.HasHole():
            c, d = p.GetPosition(), p.GetDrillSize()
            pts += [pcbnew.VECTOR2I(int(c.x + d.x / 2 * math.cos(k * math.pi / 4)), int(c.y + d.y / 2 * math.sin(k * math.pi / 4)))
                    for k in range(8)]
        side = None if tht or layer is None else "F" if layer == pcbnew.F_Cu else "B"
        out.append((p.GetNumber(), side, pts))
    return out


def body_points(pcbnew, fp):
    """('fab' or 'silk', points): the fab outline when the footprint has one, else its silk graphics (not text, and
    not dots: a circle under 1 mm across is a pin-1 or polarity mark, often drawn off the body)."""
    fab, silk = [], []
    for g in fp.GraphicalItems():
        if not isinstance(g, pcbnew.PCB_SHAPE):
            continue
        if g.GetLayer() in (pcbnew.F_Fab, pcbnew.B_Fab):
            fab += shape_points(pcbnew, g)
        elif g.GetLayer() in (pcbnew.F_SilkS, pcbnew.B_SilkS):
            if g.GetShape() == pcbnew.SHAPE_T_CIRCLE and g.GetRadius() < pcbnew.FromMM(DOT):
                continue
            silk += shape_points(pcbnew, g)
    return ("fab", fab) if fab else ("silk", silk)


def _natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def _outside(poly, pts):
    """How far the farthest point lies outside the polygon set, nm (0 if all inside)."""
    return max((math.sqrt(poly.SquaredDistance(p)) for p in pts if not poly.Contains(p)), default=0.0)


def findings(pcbnew, fp):
    """What is wrong with a footprint's courtyard: a list of short sentences, empty if nothing."""
    if fp.IsBoardOnly() or fp.IsNetTie():
        return []
    c = local_copy(pcbnew, fp)
    pads = pad_shapes(pcbnew, c)
    if not pads:
        return []
    drawn = {s for s, layer in (("F", pcbnew.F_CrtYd), ("B", pcbnew.B_CrtYd))
             if any(isinstance(g, pcbnew.PCB_SHAPE) and g.GetLayer() == layer for g in c.GraphicalItems())}
    cy = {s: c.GetCourtyard(layer) for s, layer in (("F", pcbnew.F_CrtYd), ("B", pcbnew.B_CrtYd))}
    cy = {s: p for s, p in cy.items() if p.OutlineCount()}
    out = [f"{s}.CrtYd is not a closed outline" for s in sorted(drawn - set(cy))]
    if not cy:
        return out or ["no courtyard"]
    smd_sides = {side for _, side, _ in pads}
    if None not in smd_sides and not smd_sides & set(cy):
        out.append(f"courtyard on {'/'.join(sorted(cy))}.CrtYd, pads on {'/'.join(sorted(smd_sides))}.Cu")
    both = pcbnew.SHAPE_POLY_SET()
    for p in cy.values():
        both.Append(p)
    worst, names, holes = 0.0, [], 0
    for name, side, pts in pads:
        d = _outside(cy.get(side, both) if side else both, pts)
        if _mm(d) > PAD_TOL:
            worst = max(worst, d)
            if name:
                names.append(name)
            else:
                holes += 1
    if names or holes:
        names.sort(key=_natural)
        what = []
        if names:
            what.append(f"pad{'s' if len(names) > 1 else ''} " + ", ".join(names[:6])
                        + (f" (+{len(names) - 6} more)" if len(names) > 6 else ""))
        if holes:
            what.append(f"{holes} unnumbered hole{'s' if holes > 1 else ''}")
        out.append(f"{' and '.join(what)} outside it by up to {_mm(worst):.2f} mm")
    kind, pts = body_points(pcbnew, c)
    d = _mm(_outside(both, pts)) if pts else 0.0
    if d > (FAB_TOL if kind == "fab" else SILK_TOL):
        out.append(f"{kind} outline outside it by up to {d:.2f} mm")
    return out


def redraw(pcbnew, fp, margin=MARGIN):
    """Replace a footprint's courtyard with a rectangle round its pads, holes and body plus margin (mm), on its
    pads' side. Returns a description of the new courtyard."""
    c = local_copy(pcbnew, fp)
    pads = pad_shapes(pcbnew, c)
    pts = [p for _, _, ps in pads for p in ps] + body_points(pcbnew, c)[1]
    m, g = pcbnew.FromMM(margin), pcbnew.FromMM(0.01)
    x0 = math.floor((min(p.x for p in pts) - m) / g) * g
    x1 = math.ceil((max(p.x for p in pts) + m) / g) * g
    y0 = math.floor((min(p.y for p in pts) - m) / g) * g
    y1 = math.ceil((max(p.y for p in pts) + m) / g) * g
    sides = {side for _, side, _ in pads}
    if len(sides) == 1 and None not in sides:
        side = sides.pop()
    else:
        side = "B" if fp.IsFlipped() else "F"
    for item in [i for i in fp.GraphicalItems()
                 if isinstance(i, pcbnew.PCB_SHAPE) and i.GetLayer() in (pcbnew.F_CrtYd, pcbnew.B_CrtYd)]:
        fp.Remove(item)
    pos = fp.GetPosition()
    s = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_POLY)
    corners = pcbnew.VECTOR_VECTOR2I()
    for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        corners.append(pcbnew.VECTOR2I(pos.x + x, pos.y + y))
    s.SetPolyPoints(corners)
    s.SetLayer(pcbnew.F_CrtYd if side == "F" else pcbnew.B_CrtYd)
    s.SetWidth(pcbnew.FromMM(WIDTH))
    s.SetFilled(False)
    s.Rotate(pos, fp.GetOrientation())
    fp.Add(s)
    return f"{_mm(x1 - x0):.2f} x {_mm(y1 - y0):.2f} mm on {side}.CrtYd"


def board_findings(pcbnew, board):
    """[(reference, footprint name, findings)] for the footprints of a loaded board that have any."""
    rows = []
    for fp in board.GetFootprints():
        f = findings(pcbnew, fp)
        if f:
            rows.append((fp.GetReference(), str(fp.GetFPID().GetLibItemName()), f))
    return sorted(rows, key=lambda r: r[0])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="a .kicad_pcb, or a .pretty footprint library")
    ap.add_argument("--fix", action="store_true", help="redraw the reported courtyards and save")
    ap.add_argument("--margin", type=float, default=MARGIN, help="mm round pads and body when redrawing (0.25)")
    ap.add_argument("--ref", action="append", default=[], metavar="REF",
                    help="with --fix on a board: only these footprints (repeatable)")
    a = ap.parse_args(argv)
    ensure_pcbnew()
    import pcbnew
    path = Path(a.path)
    if path.suffix == ".pretty":
        names = sorted(pcbnew.FootprintEnumerate(str(path)))
        fps = [(n, pcbnew.FootprintLoad(str(path), n)) for n in names]
        rows = [(n, n, findings(pcbnew, fp), fp) for n, fp in fps]
    else:
        board = pcbnew.LoadBoard(str(path))
        rows = [(fp.GetReference(), str(fp.GetFPID().GetLibItemName()), findings(pcbnew, fp), fp)
                for fp in board.GetFootprints()]
    rows = sorted((r for r in rows if r[2]), key=lambda r: _natural(r[0]))
    print(f"== courtyards: {len(rows)}")
    fixed = []
    for ref, name, f, fp in rows:
        line = f"  {ref} ({name}): {'; '.join(f)}" if ref != name else f"  {name}: {'; '.join(f)}"
        if a.fix and (not a.ref or ref in a.ref):
            line += f" -> redrawn, {redraw(pcbnew, fp, a.margin)}"
            fixed.append(fp)
        print(line)
    if not fixed:
        return
    if path.suffix == ".pretty":  # all redrawn first: after a footprint save the bindings return untyped objects
        io = pcbnew.PCB_IO_MGR.FindPlugin(pcbnew.PCB_IO_MGR.KICAD_SEXP)
        for fp in fixed:
            io.FootprintSave(str(path), fp)
    else:
        save_board(board, path)
    print(f"redrew {len(fixed)} courtyard(s) in {path}")


if __name__ == "__main__":
    main()
