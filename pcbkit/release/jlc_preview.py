"""A checklist for JLC's placement preview, written by pcbkit fab beside the BOM and CPL: PLACEMENT-<name>.md.

JLC places each part from its own (EasyEDA) footprint, turned by the CPL's angle, and a wrong angle can look right at
board scale: a USB-C receptacle half round did (2026-10-26). What caught it was zooming in on each polarised part in
the preview: every leg on its pad, the pink pin-1 dot at the silkscreen's mark, and the 2D view's hover reading
compared with pad 1's position. The checklist lists those parts and the numbers to compare.

- The parts: the CPL-<name>-both.csv placements whose orientation matters, by side then reference: more than two
  pads, two pads that differ (shape, size, drill, layers or angle), or a silkscreen or fab drawing that no swap of
  the two pads (half a turn, or the mirror between them) maps onto itself: a cathode bar, a polarity dot, a "+".
  Two-pad parts drawn symmetrically (resistors, capacitors, inductors, fuses) are left out, and so are one-pad parts.
- A table by part (one line per LCSC number, footprint and rule, with its references), then one row per part:
  LCSC number and footprint; KiCad's angle, the correction and the CPL's angle; the rule the correction came from in
  data/jlc/corrections.csv (an LCSC number, or a pattern and the field it matched) and the date in that rule's
  Checked column, the day it was last seen right in JLC's preview ("never" when it has none); pad 1 and the part's
  centre as the CPL gives it.
- Pad 1 is the pad numbered 1, else A1, else K, else the first in number order (KiCad numbers a diode's or LED's
  cathode 1). Its position is the middle of its copper, which for a custom pad is not its anchor.
- Positions are in mm from the drill/place (aux) origin, Y up: the frame of the Gerbers (kicad-cli plots them from
  that origin with --use-drill-file-origin, Y up as Gerber has it) and of the CPL, which JLC matches to the Gerbers.
  Bottom parts are in it as seen from the top, as in the CPL and the B.Cu Gerber; on the boards it was tried on,
  every pad 1 lands on its pad in the copper Gerber of its side. That the preview's hover reading counts from the
  same origin has not been checked against a live quote: the outline's corners are given in the same frame, so a
  viewer that counts from the outline's lower-left corner reads every position less that corner.
"""
import os
import re

TOL = 30000                            # nm: how far a drawing point may sit from its image under a pad swap


def natural(ref):
    m = re.match(r"(\D*)(\d*)(.*)$", ref)
    return m.group(1), int(m.group(2) or 0), m.group(3)


def _drawing(fp, layer):
    """The footprint's drawing on one layer as points (x, y, r): a circle's centre and radius, the corners, ends and
    mid points of everything else (r 0). Texts carrying a field (${REFERENCE}) are left out; other texts count by
    position."""
    import pcbnew
    pts = []
    for g in fp.GraphicalItems():
        if g.GetLayer() != layer:
            continue
        if isinstance(g, pcbnew.PCB_TEXT):
            if not g.GetText().startswith("${"):
                pts.append((g.GetPosition().x, g.GetPosition().y, 0))
            continue
        if not isinstance(g, pcbnew.PCB_SHAPE):
            continue
        s = g.GetShape()
        if s == pcbnew.SHAPE_T_CIRCLE:
            pts.append((g.GetCenter().x, g.GetCenter().y, g.GetRadius()))
            continue
        if s == pcbnew.SHAPE_T_ARC:
            vs = [g.GetStart(), g.GetArcMid(), g.GetEnd()]
        elif s == pcbnew.SHAPE_T_RECTANGLE:
            vs = list(g.GetRectCorners())
        elif s == pcbnew.SHAPE_T_POLY:
            vs = list(g.GetPolyPoints())
        elif s == pcbnew.SHAPE_T_BEZIER:
            vs = [g.GetStart(), g.GetBezierC1(), g.GetBezierC2(), g.GetEnd()]
        else:
            vs = [g.GetStart(), g.GetEnd()]
        pts += [(v.x, v.y, 0) for v in vs]
    return pts


def _swaps(a, b):
    """The maps that swap two pad centres: half a turn about their midpoint, and the mirror between them."""
    cx, cy = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = (dx * dx + dy * dy) ** 0.5
    maps = [lambda x, y: (2 * cx - x, 2 * cy - y)]
    if n:
        ux, uy = dx / n, dy / n
        maps.append(lambda x, y: (x - 2 * ((x - cx) * ux + (y - cy) * uy) * ux,
                                  y - 2 * ((x - cx) * ux + (y - cy) * uy) * uy))
    return maps


def _symmetric(pts, maps):
    for f in maps:
        if all(any(abs(fx - x) <= TOL and abs(fy - y) <= TOL and abs(r - s) <= TOL for x, y, s in pts)
               for fx, fy, r in ((*f(x, y), r) for x, y, r in pts)):
            return True
    return False


def oriented(fp):
    """Whether the part's orientation matters: more than two pads, two pads that differ, or a drawing (silkscreen or
    fab) that marks one of its two pads."""
    import pcbnew
    pads = list(fp.Pads())
    if len(pads) > 2:
        return True
    if len(pads) < 2:
        return False
    cu = pcbnew.B_Cu if fp.IsFlipped() else pcbnew.F_Cu
    turn = fp.GetOrientation().AsDegrees()

    def kind(p):
        s, d = p.GetSize(cu), p.GetDrillSize()
        return (p.GetShape(cu), s.x, s.y, d.x, d.y, p.GetAttribute(), list(p.GetLayerSet().Seq()),
                round((p.GetOrientation().AsDegrees() - turn) % 180, 1))
    if kind(pads[0]) != kind(pads[1]):
        return True
    centres = [(p.GetBoundingBox().GetCenter().x, p.GetBoundingBox().GetCenter().y) for p in pads]
    maps = _swaps(*centres)
    layers = (pcbnew.B_SilkS, pcbnew.B_Fab) if fp.IsFlipped() else (pcbnew.F_SilkS, pcbnew.F_Fab)
    return not all(_symmetric(_drawing(fp, layer), maps) for layer in layers)


def pin1(fp):
    pads = [p for p in fp.Pads() if p.GetNumber()]
    for want in ("1", "A1", "K"):
        for p in pads:
            if p.GetNumber() == want:
                return p
    pads.sort(key=lambda p: natural(p.GetNumber()))
    return pads[0] if pads else next(iter(fp.Pads()), None)


def _outline(board):
    """The outline's lower-left and upper-right corners, along its lines' centres (KiCad's edge bounding box adds
    half the line width; its board polygon flattens arcs)."""
    import pcbnew
    xs, ys = [], []
    for g in list(board.GetDrawings()) + [g for fp in board.GetFootprints() for g in fp.GraphicalItems()]:
        if g.GetLayer() == pcbnew.Edge_Cuts and isinstance(g, pcbnew.PCB_SHAPE):
            box, w = g.GetBoundingBox(), g.GetWidth() // 2
            xs += [box.GetLeft() + w, box.GetRight() - w]
            ys += [box.GetTop() + w, box.GetBottom() - w]
    return (pcbnew.VECTOR2I(min(xs), max(ys)), pcbnew.VECTOR2I(max(xs), min(ys))) if xs else None


def _rule(rule):
    """The Corr., Rule and Checked cells for a corrections.csv row, or None."""
    if not rule:
        return "", "", ""
    pat, corr, off, checked, on = rule
    return (f"{corr:+g}" + (f", offset {off[0]:g}, {off[1]:g}" if off[0] or off[1] else ""),
            f"`{pat}`" + ("" if on == "LCSC" else f" ({on})"), checked or "**never**")


def _row(cells):
    return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"


def write(board, placed, placement, out, name):
    """placed: the footprints in CPL-<name>-both.csv; placement(fp) -> (x, y, rotation, side, rule), as the CPL has
    them, rule a corrections.csv row (pattern, rotation, offset, checked, field) or None. Writes PLACEMENT-<name>.md;
    returns its path, the rows by side, and the rows under a rule never checked."""
    import pcbnew
    mm = pcbnew.ToMM
    o = board.GetDesignSettings().GetAuxOrigin()
    at = lambda v: (mm(v.x - o.x), -mm(v.y - o.y))      # noqa: E731  board units -> the CPL's frame
    rows = {"top": [], "bottom": []}
    left_out = 0
    for fp in placed:
        if not oriented(fp):
            left_out += 1
            continue
        x, y, rot, side, rule = placement(fp)
        kicad = round(fp.GetOrientation().AsDegrees(), 6) % 360     # -1e-13 is 0, not 360
        p = pin1(fp)
        rows[side].append(dict(ref=fp.GetReference(), lcsc=fp.GetFieldText("LCSC").strip(),
                               fp=str(fp.GetFPID().GetLibItemName()), kicad=kicad, cpl=rot, rule=rule,
                               pad=p.GetNumber() if p else "", pad_at=at(p.GetBoundingBox().GetCenter()) if p else None,
                               centre=(x, y)))
    for side in rows:
        rows[side].sort(key=lambda r: natural(r["ref"]))
    never = [r for side in rows for r in rows[side] if r["rule"] and not r["rule"][3]]

    corners = _outline(board)
    n = sum(len(v) for v in rows.values())
    f2 = lambda v: ", ".join("0.000" if f"{c:.3f}" == "-0.000" else f"{c:.3f}" for c in v)  # noqa: E731
    lines = [f"# Placement check: {name}", "",
             f"Parts to check in JLC's placement preview before paying: {n} of the {n + left_out} parts JLC places on "
             f"both sides ({len(rows['top'])} top, {len(rows['bottom'])} bottom). Two-pad parts drawn symmetrically are "
             f"left out ({left_out}). For a front-only order, only the top ones.", "",
             "For each one, zoom in until single pads show:",
             "- every leg sits on its pad, and the body lies the way KiCad's footprint has it;",
             "- the pink pin-1 dot sits on pad 1, by the silkscreen's pin-1 or cathode mark;",
             "- in the 2D view, the hover reading over pad 1 matches *Pad 1 at* below.", "",
             "Positions are in mm from the board's drill/place origin with Y up: the frame of the Gerbers and the CPL, "
             "the one JLC places parts in. Bottom parts are in it as seen from the top, as in the CPL."
             + (" The board outline runs from ({}) to ({}); if the preview reads (0, 0) at the outline's lower-left "
                "corner, subtract that corner from every position here.".format(*(f2(at(v)) for v in corners))
                if corners else "")
             + " KiCad's status bar shows the same numbers with Preferences, PCB Editor, Origins & Axes set to the "
             "drill/place file origin and the Y axis increasing up.", "",
             "*CPL* is KiCad's angle (on the bottom, 180 minus KiCad's) plus *Corr.*, the correction from pcbkit's "
             "rotation table (data/jlc/corrections.csv). *Rule* is the table's row: an LCSC number, or a pattern "
             "and the field it matched. *Checked* is the last time that row was seen right in JLC's preview; it was "
             "right for the parts checked then, and JLC's footprint for another part in the same package can still "
             "differ (SOT-23-5 comes both ways). **never**: no check on record, look at these first.", ""]
    if never:
        rules = {}
        for r in never:
            rules.setdefault(r["rule"][0], []).append(r["ref"])
        lines += ["Under a rule never checked in the preview: "
                  + "; ".join(f"`{k}` ({', '.join(v)})" for k, v in rules.items()) + ".", ""]
    groups = {}
    for side in ("top", "bottom"):
        for r in rows[side]:
            groups.setdefault((side, r["lcsc"], r["fp"], r["rule"]), []).append(r["ref"])
    lines += ["## By part", "", "One line for each part and rule: at least one of each, and the **never** lines first.",
              "", "| Side | LCSC | Footprint | Parts | Corr. | Rule | Checked |",
              "|---|---|---|---|--:|---|---|"]
    for (side, lcsc, fpn, rule), refs in groups.items():
        parts = ", ".join(refs) if len(refs) <= 4 else f"{refs[0]}, {refs[1]} … {refs[-1]} ({len(refs)})"
        lines.append(_row((side, lcsc, fpn, parts) + _rule(rule)))
    lines.append("")
    for side in ("top", "bottom"):
        if not rows[side]:
            continue
        lines += [f"## {side.capitalize()} ({len(rows[side])})", "",
                  "| Ref | LCSC | Footprint | KiCad | Corr. | CPL | Rule | Checked | Pad 1 at | Centre |",
                  "|---|---|---|--:|--:|--:|---|---|---|---|"]
        for r in rows[side]:
            corr, src, checked = _rule(r["rule"])
            pad = f"{r['pad']}: {f2(r['pad_at'])}" if r["pad_at"] else ""
            lines.append(_row((r["ref"], r["lcsc"], r["fp"], f"{r['kicad']:g}", corr, f"{r['cpl']:g}", src, checked,
                               pad, f2(r["centre"]))))
        lines.append("")
    path = os.path.join(out, f"PLACEMENT-{name}.md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return path, rows, never
