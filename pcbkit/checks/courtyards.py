"""Footprint courtyards that don't describe the part.

  pcbkit courtyards BOARD.kicad_pcb [--fix] [--margin 0.25] [--ref REF ...] [--easyeda PCB.json]
  pcbkit courtyards LIB.pretty [--fix] [--margin 0.25]

KiCad's placement checks (courtyards_overlap, pth_inside_courtyard) trust each footprint's courtyard. A courtyard
that misses part of the footprint lets another part be placed on it, and DRC passes. KiCad's EasyEDA importer
leaves such courtyards (31 of the 44 parts on espcandle-pcb's base, 2026-11): turned against the pads, a sliver
0.2 mm wide, a connector's 1 mm inside its body, a module's round its pads but not its antenna, a back-side test
pad's on F.CrtYd. This reports, per footprint:
  - no courtyard, or one that isn't a closed outline (footprints with pads that aren't board-only or net ties);
  - a courtyard only on the other side from all its pads (an SMD pad on a side with no courtyard of its own, as
    the thermal land on the back of KiCad's ThermalVias packages, is checked against the other side's);
  - pads (and holes) outside the courtyard by more than 0.02 mm;
  - the body outside the courtyard: the fab outline by more than 0.05 mm or, for a footprint with no fab outline
    (EasyEDA's draw the body only in silk), the silk by more than 0.5 mm, not counting dots (circles under 1 mm
    across: pin-1 and polarity marks). KiCad's own libraries draw silk marks up to 0.8 mm outside the courtyard
    (SOT-23's pin-1 triangle, an electrolytic's +), so silk counts only without fab.
A courtyard larger than the part is not reported: a mounting hole's covers the screw head, a module's its antenna
keep-out. Of KiCad 10's own 15,464 footprints it reports 141 (2026-11), most of them connectors and switches whose
fab drawing shows a part over the board edge, and card-edge connectors; none on boards drawn from them so far.

--fix redraws each reported courtyard as a rectangle in the footprint's own frame around its pads, holes and body
(fab outline, or silk without one) plus --margin (0.25 mm, IPC-7351's nominal), one on each side it has SMD pads
on and on its own side for through-hole parts. A sound courtyard on a side it draws nothing for stays when it
is a keep-out beside the part's own (a standoff, a screw head, the solder side: the part also had one on a side it
draws for); a part's only courtyard, on the other side, goes (a back-side test pad's, a back-mounted header's on
the top). It saves the board or the library footprints. Every outline is checked at points 0.05 mm apart, so an
edge across a notch in the courtyard counts, and arcs at their extremes. `pcbkit check` lists the same findings;
`pcbkit import-easyeda` redraws them on import.

--easyeda PCB.json (a board imported from that EasyEDA Std export) first draws each part's body on its F.Fab or
B.Fab from EasyEDA's component-shape layer, which KiCad's importer drops: the footprints come with silk only, drawn
well off the body (an 0603's 0.3 mm beyond its pads), so a courtyard round the silk is larger than the part. Each
outline is placed by fitting its pads onto the footprint's, so a part moved or turned since the import still gets
its own (mirrored if it changed sides since: pads in a line fit either way); a part that already has a fab drawing, or whose pads no longer match, is left as it is. The check and the
redraw then go by the body. With --fix the outlines are saved too; `pcbkit import-easyeda` draws them on import.
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
STEP = 0.05      # mm between the points checked along an outline


def _mm(nm):
    return nm / 1e6


def _frame(pcbnew, fp):
    """(to_local, to_board): board nm to the footprint's own frame (about its position, unrotated; still mirrored
    if it is on the back) and back, by KiCad's RotatePoint, so the footprint needn't be copied and turned."""
    pos, a = fp.GetPosition(), fp.GetOrientation().AsRadians()
    cs, sn = math.cos(a), math.sin(a)

    def to_local(q):
        x, y = q.x - pos.x, q.y - pos.y
        return x * cs - y * sn, x * sn + y * cs

    def to_board(x, y):
        return pcbnew.VECTOR2I(int(round(pos.x + x * cs + y * sn)), int(round(pos.y - x * sn + y * cs)))
    return to_local, to_board


def _along(pcbnew, a, b, step):
    """Points from a towards b (b left out), at most step nm apart."""
    n = max(1, int(math.ceil(math.hypot(b.x - a.x, b.y - a.y) / step)))
    return [pcbnew.VECTOR2I(int(round(a.x + (b.x - a.x) * k / n)), int(round(a.y + (b.y - a.y) * k / n)))
            for k in range(n)]


def _outline(pcbnew, pts, step, closed=True):
    """A polyline's points with more along each edge, at most step apart: an edge that crosses a notch in the
    courtyard, both its ends inside, still has points outside."""
    if not pts:
        return []
    edges = list(zip(pts, pts[1:] + pts[:1])) if closed else list(zip(pts, pts[1:]))
    out = [q for a, b in edges for q in _along(pcbnew, a, b, step)]
    return out if closed else out + [pts[-1]]


def _arc(pcbnew, c, r, a0, da, step):
    """Points along an arc from angle a0 through da (radians, KiCad's frame), at most step apart, with its
    extremes in x and y."""
    n = max(4, int(math.ceil(abs(da) * r / step)))
    angles = [a0 + da * k / n for k in range(n + 1)]
    lo, hi = min(a0, a0 + da), max(a0, a0 + da)
    k = math.ceil(lo / (math.pi / 2))
    while k * math.pi / 2 <= hi:
        angles.append(k * math.pi / 2)
        k += 1
    return [pcbnew.VECTOR2I(int(round(c.x + r * math.cos(t))), int(round(c.y + r * math.sin(t)))) for t in angles]


def shape_points(pcbnew, s, step):
    """Points along a graphic's centre line, at most step nm apart: arcs and circles with their extremes in x and
    y, a Bezier curve sampled on the curve (its control points are not on it)."""
    t = s.GetShape()
    if t == pcbnew.SHAPE_T_SEGMENT:
        return _outline(pcbnew, [s.GetStart(), s.GetEnd()], step, closed=False)
    if t == pcbnew.SHAPE_T_RECTANGLE:
        return _outline(pcbnew, list(s.GetRectCorners()), step)
    if t == pcbnew.SHAPE_T_CIRCLE:
        return _arc(pcbnew, s.GetCenter(), s.GetRadius(), 0.0, 2 * math.pi, step)
    if t == pcbnew.SHAPE_T_ARC:
        return _arc(pcbnew, s.GetCenter(), s.GetRadius(), s.GetArcAngleStart().AsRadians(),
                    s.GetArcAngle().AsRadians(), step)
    if t == pcbnew.SHAPE_T_POLY:
        ps, out = s.GetPolyShape(), []
        for i in range(ps.OutlineCount()):
            o = ps.Outline(i)
            out += _outline(pcbnew, [o.CPoint(k) for k in range(o.PointCount())], step)
        return out
    if t == pcbnew.SHAPE_T_BEZIER:
        p = [s.GetStart(), s.GetBezierC1(), s.GetBezierC2(), s.GetEnd()]
        # the curve moves at most 3x its longest control-polygon edge per unit t, so this many even steps in t
        # keep the points at most step apart along it
        edge = max(math.hypot(b.x - a.x, b.y - a.y) for a, b in zip(p, p[1:]))
        n = max(8, int(math.ceil(3 * edge / step)))
        w = lambda u: ((1 - u) ** 3, 3 * u * (1 - u) ** 2, 3 * u * u * (1 - u), u ** 3)
        return [pcbnew.VECTOR2I(int(round(sum(c * q.x for c, q in zip(w(k / n), p)))),
                                int(round(sum(c * q.y for c, q in zip(w(k / n), p))))) for k in range(n + 1)]
    return []


def pad_shapes(pcbnew, fp, step):
    """[(pad, number, kind, side, points)]: pad its index, number "" for a peg or hole; kind "smd" or "tht" with
    side "F" or "B" for a pad's copper on that outer layer (a through-hole padstack may differ by layer), and "tht"
    with side None for every hole, as its rotated slot or circle. Points along each outline, at most step nm
    apart."""
    out = []
    for i, p in enumerate(fp.Pads()):
        num = p.GetNumber()
        tht = p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH)
        for layer, side in ((pcbnew.F_Cu, "F"), (pcbnew.B_Cu, "B")):
            if not p.IsOnLayer(layer):
                continue
            poly, pts = p.GetEffectivePolygon(layer, pcbnew.ERROR_INSIDE), []
            for j in range(poly.OutlineCount()):
                o = poly.Outline(j)
                pts += _outline(pcbnew, [o.CPoint(k) for k in range(o.PointCount())], step)
            if pts:
                out.append((i, num, "tht" if tht else "smd", side, pts))
        if p.HasHole():
            h = p.GetEffectiveHoleShape()
            a, b, r = h.GetSeg().A, h.GetSeg().B, h.GetWidth() / 2
            pts = _arc(pcbnew, a, r, 0.0, 2 * math.pi, step) + _arc(pcbnew, b, r, 0.0, 2 * math.pi, step)
            d = math.hypot(b.x - a.x, b.y - a.y)
            if d > 0:  # a slot: its two long sides too
                nx, ny = -(b.y - a.y) / d * r, (b.x - a.x) / d * r
                V = lambda q, k: pcbnew.VECTOR2I(int(round(q.x + k * nx)), int(round(q.y + k * ny)))
                for k in (1, -1):
                    pts += _outline(pcbnew, [V(a, k), V(b, k)], step, closed=False)
            out.append((i, num, "tht", None, pts))
    return out


def body_points(pcbnew, fp, step):
    """('fab' or 'silk', {"F": points, "B": points}): the fab outline when the footprint has one, else its silk
    graphics (not text, and not dots: a circle under 1 mm across is a pin-1 or polarity mark, often drawn off the
    body), by side."""
    shapes = [g for g in fp.GraphicalItems() if isinstance(g, pcbnew.PCB_SHAPE)]
    fab = [g for g in shapes if g.GetLayer() in (pcbnew.F_Fab, pcbnew.B_Fab)]
    silk = [g for g in shapes if g.GetLayer() in (pcbnew.F_SilkS, pcbnew.B_SilkS)
            and not (g.GetShape() == pcbnew.SHAPE_T_CIRCLE and g.GetRadius() < pcbnew.FromMM(DOT))]
    kind, chosen = ("fab", fab) if fab else ("silk", silk)
    out = {"F": [], "B": []}
    for g in chosen:
        out["F" if g.GetLayer() in (pcbnew.F_Fab, pcbnew.F_SilkS) else "B"] += shape_points(pcbnew, g, step)
    return kind, out


def _natural(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def _outside(poly, pts):
    """How far the farthest point lies outside the polygon set, nm (0 if all inside)."""
    return max((math.sqrt(poly.SquaredDistance(p)) for p in pts if not poly.Contains(p)), default=0.0)


def findings(pcbnew, fp):
    """What is wrong with a footprint's courtyard: a list of short sentences, empty if nothing."""
    if fp.IsBoardOnly() or fp.IsNetTie():
        return []
    c = fp
    c.BuildCourtyardCaches()
    step = pcbnew.FromMM(STEP)
    pads = pad_shapes(pcbnew, c, step)
    if not pads:
        return []
    drawn = {s for s, layer in (("F", pcbnew.F_CrtYd), ("B", pcbnew.B_CrtYd))
             if any(isinstance(g, pcbnew.PCB_SHAPE) and g.GetLayer() == layer for g in c.GraphicalItems())}
    cy = {s: c.GetCourtyard(layer) for s, layer in (("F", pcbnew.F_CrtYd), ("B", pcbnew.B_CrtYd))}
    cy = {s: p for s, p in cy.items() if p.OutlineCount()}
    out = [f"{s}.CrtYd is not a closed outline" for s in sorted(drawn - set(cy))]
    if not cy:
        return out or ["no courtyard"]
    smd = {side for _, _, kind, side, _ in pads if kind == "smd"}
    if smd and not any(kind == "tht" for _, _, kind, _, _ in pads) and not smd & set(cy):
        out.append(f"courtyard on {'/'.join(sorted(cy))}.CrtYd, pads on {'/'.join(sorted(smd))}.Cu")
    both = pcbnew.SHAPE_POLY_SET()
    for p in cy.values():
        both.Append(p)
    worst, names, holes = 0.0, set(), set()
    own = "B" if fp.IsFlipped() else "F"
    for i, name, kind, side, pts in pads:
        # copper against its own side's courtyard: an SMD pad's, and a through-hole pad's on the part's own side.
        # An SMD pad on a side with none (the thermal land on the back of KiCad's ThermalVias packages) against the
        # other side's; through-hole copper on the far side and holes against both, since a courtyard there may be
        # only a standoff's ring that needn't cover the pins
        own_side = side in cy and (kind == "smd" or side == own)
        d = _outside(cy[side] if own_side else both, pts)
        if _mm(d) > PAD_TOL:
            worst = max(worst, d)
            (names.add(name) if name else holes.add(i))
    holes = len(holes)
    if names or holes:
        names = sorted(names, key=_natural)
        what = []
        if names:
            what.append(f"pad{'s' if len(names) > 1 else ''} " + ", ".join(names[:6])
                        + (f" (+{len(names) - 6} more)" if len(names) > 6 else ""))
        if holes:
            what.append(f"{holes} unnumbered hole{'s' if holes > 1 else ''}")
        out.append(f"{' and '.join(what)} outside it by up to {_mm(worst):.2f} mm")
    kind, body = body_points(pcbnew, c, step)  # each side's against its own courtyard, as the pads
    d = _mm(max((_outside(cy.get(s, both), body[s]) for s in "FB" if body[s]), default=0.0))
    if d > (FAB_TOL if kind == "fab" else SILK_TOL):
        out.append(f"{kind} outline outside it by up to {d:.2f} mm")
    return out


def redraw(pcbnew, fp, margin=MARGIN):
    """Replace a footprint's courtyards with a rectangle per side it has pads on, round that side's pads, its
    through-hole pads and holes, and the body drawn on that side and on any side that gets no courtyard of its own
    (a back-side test pad's outline drawn on the front), plus margin (mm). A side whose courtyard already holds all
    that keeps it, larger clearance and all: only the faulty side is redrawn. A courtyard on a side it draws no
    rectangle for is kept when it is closed, holds that side's body, and is a keep-out beside the part's own (a
    standoff, a screw head, the solder side): the part also had a courtyard, closed or not, on a side it draws one
    for. A part's only courtyard, on the other side, is the misplaced one and goes: a back-side test pad's, a
    back-mounted header's on the top. Returns a description of the new courtyards, naming any closed one it kept
    or removed."""
    if not margin >= 0:  # a negative one turns a small part's rectangle inside out
        raise ValueError(f"courtyard margin must be 0 mm or more, got {margin}")
    c = fp
    c.BuildCourtyardCaches()
    to_local, to_board = _frame(pcbnew, fp)
    step = pcbnew.FromMM(STEP)
    pads = pad_shapes(pcbnew, c, step)
    kind, body = body_points(pcbnew, c, step)
    own = "B" if fp.IsFlipped() else "F"
    smd = {side for _, _, k, side, _ in pads if k == "smd"}
    tht = any(k == "tht" for _, _, k, _, _ in pads)
    sides = sorted(smd | ({own} if tht or not smd else set()), key="FB".index)
    layer = {"F": pcbnew.F_CrtYd, "B": pcbnew.B_CrtYd}
    tol = pcbnew.FromMM(FAB_TOL if kind == "fab" else SILK_TOL)
    closed = {t for t in "FB" if c.GetCourtyard(layer[t]).OutlineCount()}
    drawn = {t for t in "FB" if any(isinstance(i, pcbnew.PCB_SHAPE) and i.GetLayer() == layer[t]
                                    for i in c.GraphicalItems())}
    # a keep-out beside the part's own courtyard (drawn on a side it needs one, even if open), not instead of it:
    # a part whose only courtyard is on the far side, through-hole or not, had it misplaced (EasyEDA draws a
    # back-mounted header's on the top)
    beside = bool(drawn & set(sides))
    kept = [t for t in "FB" if beside and t not in sides and t in closed
            and _outside(c.GetCourtyard(layer[t]), body[t]) <= tol]
    m, g = pcbnew.FromMM(margin), pcbnew.FromMM(0.01)
    rects = []
    pad_tol = pcbnew.FromMM(PAD_TOL)
    body_of = lambda s: [q for t in "FB" if t == s or (t not in sides and t not in kept) for q in body[t]]
    # a side's courtyard is sound by the rules findings() uses: what is held to that side alone (its SMD pads,
    # and through-hole copper on the part's own side) and its body
    held = lambda s: [q for _, _, k, side, ps in pads if side == s and (k == "smd" or s == own) for q in ps]
    sound = [s for s in sides if s in closed and _outside(c.GetCourtyard(layer[s]), held(s)) <= pad_tol
             and _outside(c.GetCourtyard(layer[s]), body_of(s)) <= tol]
    redrawn = [s for s in sides if s not in sound]
    if not redrawn:  # what may sit in either courtyard (far-side copper, holes) must fit the ones kept
        union = pcbnew.SHAPE_POLY_SET()
        for t in sound + kept:
            union.Append(c.GetCourtyard(layer[t]))
        either = [q for _, _, k, side, ps in pads if not (side in sound and (k == "smd" or side == own)) for q in ps]
        if _outside(union, either) > pad_tol:
            redrawn = [own] if own in sides else sides[:1]
    kept += [s for s in sound if s not in redrawn]
    for s in redrawn:
        # a redrawn side takes all the through-hole copper and holes, its own SMD pads and its body
        pts = [q for _, _, k, side, ps in pads if k == "tht" or side == s for q in ps] + body_of(s)
        xs, ys = zip(*(to_local(q) for q in pts))
        e = 2  # nm: board points are rounded after rotation, so back in the frame they sit a hair off the grid
        x0 = math.floor((min(xs) - m + e) / g) * g
        x1 = math.ceil((max(xs) + m - e) / g) * g
        y0 = math.floor((min(ys) - m + e) / g) * g
        y1 = math.ceil((max(ys) + m - e) / g) * g
        rects.append((s, x0, y0, x1, y1))
    for item in [i for i in fp.GraphicalItems() if isinstance(i, pcbnew.PCB_SHAPE)
                 and i.GetLayer() in [layer[t] for t in "FB" if t not in kept]]:
        fp.Remove(item)
        item.thisown = False  # Remove() hands it to Python, which would free it under the board's caches
    for s, x0, y0, x1, y1 in rects:
        shape = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_POLY)
        corners = pcbnew.VECTOR_VECTOR2I()
        for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            corners.append(to_board(x, y))
        shape.SetPolyPoints(corners)
        shape.SetLayer(layer[s])
        shape.SetWidth(pcbnew.FromMM(WIDTH))
        shape.SetFilled(False)
        fp.Add(shape)
    removed = [t for t in "FB" if t in closed and t not in kept and t not in sides]  # said, not done quietly
    return ", ".join([f"{_mm(x1 - x0):.2f} x {_mm(y1 - y0):.2f} mm on {s}.CrtYd" for s, x0, y0, x1, y1 in rects]
                     + [f"{t}.CrtYd kept" for t in sorted(kept, key="FB".index)]
                     + [f"{t}.CrtYd removed" for t in removed])


def _margin(text):
    """--margin: a number of mm, 0 or more."""
    try:
        v = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number: {text}")
    if not v >= 0:
        raise argparse.ArgumentTypeError(f"must be 0 mm or more, got {text}")
    return v


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
    ap.add_argument("--margin", type=_margin, default=MARGIN, help="mm round pads and body when redrawing (0.25)")
    ap.add_argument("--ref", action="append", default=[], metavar="REF",
                    help="with --fix on a board: only these footprints (repeatable)")
    ap.add_argument("--easyeda", metavar="PCB.json",
                    help="the EasyEDA Std PCB export the board was imported from: draw each part's body on F.Fab "
                         "or B.Fab from it first (saved with --fix; with --fix --ref, only on those parts)")
    a = ap.parse_args(argv)
    ensure_pcbnew()
    import pcbnew
    path = Path(a.path)
    if a.easyeda and path.suffix == ".pretty":
        ap.error("--easyeda takes a board, whose parts it finds by reference")
    bodies = []
    if path.suffix == ".pretty":
        names = sorted(pcbnew.FootprintEnumerate(str(path)))
        fps = [(n, pcbnew.FootprintLoad(str(path), n)) for n in names]
        rows = [(n, n, findings(pcbnew, fp), fp) for n, fp in fps]
    else:
        board = pcbnew.LoadBoard(str(path))
        if a.easyeda:
            from ..import_easyeda import draw_bodies, easyeda_bodies
            # a targeted repair saves only the footprints asked for
            only = [fp for fp in board.GetFootprints() if not (a.fix and a.ref) or fp.GetReference() in a.ref]
            bodies = sorted(draw_bodies(pcbnew, only, easyeda_bodies(a.easyeda)), key=_natural)
            print(f"bodies from {Path(a.easyeda).name} drawn on fab: {len(bodies)}"
                  + (f" ({', '.join(bodies)})" if bodies else ""))
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
    if not fixed and not (a.fix and bodies):
        return
    if path.suffix == ".pretty":  # all redrawn first: after a footprint save the bindings return untyped objects
        io = pcbnew.PCB_IO_MGR.FindPlugin(pcbnew.PCB_IO_MGR.KICAD_SEXP)
        for fp in fixed:
            io.FootprintSave(str(path), fp)
    else:
        save_board(board, path)
    print(f"redrew {len(fixed)} courtyard(s)" + (f", drew {len(bodies)} bodies" if bodies else "") + f" in {path}")


if __name__ == "__main__":
    main()
