"""Enclosure template: a board outline with mounting holes and keep-outs from a small JSON description.

  pcbkit outline BOARD.kicad_pcb enclosure.json

The JSON (kept in the project, measured from the box) looks like:

  {"name": "...", "notes": ["..."],
   "origin": "centre of the mounting pattern, x along the long axis, mm",
   "board": {"width": 90, "height": 58, "corner_radius": 3,
             "notches": [{"corner": "+x+y", "width": 10, "height": 8}],      # optional rectangular corner cuts
             "cutouts": [{"x": 22.65, "y": 39.15, "boss": 7.5, "clearance": 1.0, "fillet": 1.0}]},  # or boss notches
   "mounts":   [{"x": -31.2, "y": -14.85, "hole": 2.2, "boss": 5.0, "name": "H1"}, ...],   # NPTH holes
   "keepouts": [{"x": -39.0, "y": -22.3, "d": 7.5, "what": "lid screw boss"}, ...],       # no parts/copper, both sides
   "drawings": [{"rect": [92, 61], "label": "plateau"}, {"rect": [123.6, 67.5], "label": "plate"},
                {"circle": [x, y, d], "label": "..."}, {"line": [x0, y0, x1, y1], "label": "..."}]}   # reference geometry on Dwgs.User

Writes into BOARD: the outline on Edge.Cuts (lines and arcs, centred at 100,100 mm), one board-only footprint per
mount with an NPTH pad, a rule area per keep-out (no tracks, vias, pads, footprints or fills on F.Cu and B.Cu,
a circle grown by 0.5 mm), the reference geometry and labels on Dwgs.User and the notes on Cmts.User. Existing
Edge.Cuts items and earlier template footprints (references starting with H) are replaced; everything else on
the board is kept, so it can be rerun after the layout starts. Needs pcbnew.
"""
import argparse
import json
import math

from .kicad import ensure_pcbnew, save_board

CX, CY = 100.0, 100.0  # board-coordinate centre of the pattern, mm


def _mirror_items(items, sx, sy):
    """Mirror outline items of the +x+y corner into another corner, keeping the counter-clockwise walk."""
    m = lambda p: (sx * p[0], sy * p[1])
    out = [tuple([it[0]] + [m(p) for p in it[1:]]) for it in items]
    if sx * sy < 0:  # one mirror flips the orientation: walk the pieces backwards
        out = [(it[0], it[2], it[1]) if it[0] == "line" else ("arc", it[3], it[2], it[1]) for it in reversed(out)]
    return out


def boss_notch(hw, hh, xc, yc, R, f):
    """Corner cut around a boss at (xc, yc) (first quadrant): straight in from the long edge along y = yc - R,
    an arc of radius R around the boss, straight out to the short edge along x = xc - R, with convex fillets of
    radius f where the cut meets the edges. Counter-clockwise walk, arriving up the long edge x = hw."""
    pt = lambda cx, cy, r, a: (cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
    items = []
    # fillet at (hw, yc - R): centre (hw - f, yc - R - f), from 0 deg to 90 deg
    c1 = (hw - f, yc - R - f)
    items.append(("arc", pt(*c1, f, 0), pt(*c1, f, 45), pt(*c1, f, 90)))
    items.append(("line", pt(*c1, f, 90), (xc, yc - R)))                          # in along y = yc - R
    items.append(("arc", (xc, yc - R), pt(xc, yc, R, 225), (xc - R, yc)))         # around the boss
    c2 = (xc - R - f, hh - f)
    items.append(("line", (xc - R, yc), pt(*c2, f, 0)))                           # out along x = xc - R
    items.append(("arc", pt(*c2, f, 0), pt(*c2, f, 45), pt(*c2, f, 90)))          # fillet at (xc - R, hh)
    return items


def outline_points(w, h, r, notches=None, cutouts=None):
    """Rounded rectangle with optional corner notches (rectangular) or boss cut-outs, walked counter-clockwise
    (y up). A cut-out {"x", "y", "boss", "clearance", "fillet"} (or "r" for the cut radius) is a notch that hugs
    the boss: straight in from the long edge, an arc around the boss, straight out to the short edge, with convex
    fillets routable by a standard cutter. Returns ('line', a, b) and ('arc', start, mid, end) in enclosure mm."""
    hw, hh = w / 2, h / 2
    cut = {n["corner"]: (n["width"], n["height"]) for n in notches or []}
    boss = {}
    for c in cutouts or []:
        sx, sy = (1 if c["x"] > 0 else -1), (1 if c["y"] > 0 else -1)
        R = c.get("r") or c["boss"] / 2 + c.get("clearance", 1.0)
        boss[f"{'+' if sx > 0 else '-'}x{'+' if sy > 0 else '-'}y"] = (abs(c["x"]), abs(c["y"]), R, c.get("fillet", 1.0), sx, sy)
    corners = [
        ("+x+y", (hw - r, hh - r), 0, lambda nw, nh: [(hw, hh - nh), (hw - nw, hh - nh), (hw - nw, hh)]),
        ("-x+y", (-hw + r, hh - r), 90, lambda nw, nh: [(-hw + nw, hh), (-hw + nw, hh - nh), (-hw, hh - nh)]),
        ("-x-y", (-hw + r, -hh + r), 180, lambda nw, nh: [(-hw, -hh + nh), (-hw + nw, -hh + nh), (-hw + nw, -hh)]),
        ("+x-y", (hw - r, -hh + r), 270, lambda nw, nh: [(hw - nw, -hh), (hw - nw, -hh + nh), (hw, -hh + nh)]),
    ]
    pieces = []  # per corner: (entry point, [items], exit point)
    for name, (cx, cy), a0, notch in corners:
        if name in boss:
            xc, yc, R, f, sx, sy = boss[name]
            if xc - R - f <= 0 or yc - R - f <= 0 or xc - R >= hw or yc - R >= hh:
                raise SystemExit(f"boss cut-out at {name} does not fit the board: boss {xc},{yc}, cut radius {R}, fillet {f}")
            items = _mirror_items(boss_notch(hw, hh, xc, yc, R, f), sx, sy)
            entry = items[0][1]
            exit_ = items[-1][2] if items[-1][0] == "line" else items[-1][3]
            pieces.append((entry, items, exit_))
        elif name in cut:
            pts = notch(*cut[name])
            pieces.append((pts[0], [("line", pts[0], pts[1]), ("line", pts[1], pts[2])], pts[2]))
        else:
            pt = lambda a: (cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
            pieces.append((pt(a0), [("arc", pt(a0), pt(a0 + 45), pt(a0 + 90))], pt(a0 + 90)))
    out = []
    for i, (entry, items, exit_) in enumerate(pieces):
        out += items
        nxt_entry = pieces[(i + 1) % 4][0]
        if any(abs(a - b) > 1e-6 for a, b in zip(nxt_entry, exit_)):
            out.append(("line", exit_, nxt_entry))
    return out


def top_level_blocks(text):
    """(start, end, head) of every depth-1 s-expression inside the file's root form."""
    out, depth, i, start, head = [], 0, 0, None, ""
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            j = text.index('"', i + 1)
            while text[j - 1] == "\\":
                j = text.index('"', j + 1)
            i = j + 1
            continue
        if c == "(":
            depth += 1
            if depth == 2:
                start = i
                k = i + 1
                while k < n and text[k] not in " \n\t)":
                    k += 1
                head = text[i + 1:k]
        elif c == ")":
            if depth == 2 and start is not None:
                out.append((start, i + 1, head))
                start = None
            depth -= 1
        i += 1
    return out


def strip_template(board_path):
    """Remove what an earlier `pcbkit outline` run wrote: Edge.Cuts / Dwgs.User / Cmts.User graphics and text,
    keep-out zones named keepout:*, and the H* mounting-hole footprints. Done on the file text."""
    from pathlib import Path
    path = Path(board_path)
    text = path.read_text()
    drop = []
    for s, e, head in top_level_blocks(text):
        blk = text[s:e]
        if head in ("gr_line", "gr_arc", "gr_circle", "gr_rect", "gr_poly", "gr_text", "dimension") and \
                any(f'(layer "{l}")' in blk for l in ("Edge.Cuts", "Dwgs.User", "Cmts.User", "User.1")):
            drop.append((s, e))
        elif head == "zone" and '(name "keepout:' in blk:
            drop.append((s, e))
        elif head == "footprint" and blk.startswith('(footprint "MountingHole_'):
            drop.append((s, e))
    if not drop:
        return 0
    out, i = [], 0
    for s, e in drop:
        j = s
        while j > i and text[j - 1] in " \t\n":
            j -= 1
        out.append(text[i:j])
        i = e
    out.append(text[i:])
    path.write_text("".join(out))
    return len(drop)


def circle_poly(x, y, r, n=48):
    return [(x + r * math.cos(2 * math.pi * k / n), y + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def apply(board_path, spec):
    ensure_pcbnew()
    import pcbnew
    MM = pcbnew.FromMM
    P = lambda x, y: pcbnew.VECTOR2I(MM(CX + x), MM(CY - y))  # enclosure y up -> KiCad y down
    strip_template(board_path)  # earlier template items go on the text: BOARD.Remove() corrupts the bindings
    b = pcbnew.LoadBoard(str(board_path))

    bd = spec["board"]
    for item in outline_points(bd["width"], bd["height"], bd.get("corner_radius", 2), bd.get("notches"), bd.get("cutouts")):
        s = pcbnew.PCB_SHAPE(b)
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(MM(0.1))
        if item[0] == "line":
            s.SetShape(pcbnew.SHAPE_T_SEGMENT)
            s.SetStart(P(*item[1])); s.SetEnd(P(*item[2]))
        else:
            s.SetShape(pcbnew.SHAPE_T_ARC)
            s.SetArcGeometry(P(*item[1]), P(*item[2]), P(*item[3]))
        b.Add(s)

    print_layer = getattr(pcbnew, "User_1", None)
    if print_layer is not None:  # a thick copy of the outline for 1:1 prints (Edge.Cuts stays thin for the fab)
        for item in outline_points(bd["width"], bd["height"], bd.get("corner_radius", 2), bd.get("notches"), bd.get("cutouts")):
            s = pcbnew.PCB_SHAPE(b)
            s.SetLayer(print_layer)
            s.SetWidth(MM(0.5))
            if item[0] == "line":
                s.SetShape(pcbnew.SHAPE_T_SEGMENT); s.SetStart(P(*item[1])); s.SetEnd(P(*item[2]))
            else:
                s.SetShape(pcbnew.SHAPE_T_ARC); s.SetArcGeometry(P(*item[1]), P(*item[2]), P(*item[3]))
            b.Add(s)

    def dimension(a, c, offset, layer, size=1.2):
        """Aligned dimension between enclosure points a and c, drawn `offset` mm to the side."""
        d = pcbnew.PCB_DIM_ALIGNED(b, pcbnew.PCB_DIM_ALIGNED_T)
        d.SetLayer(layer)
        d.SetStart(P(*a)); d.SetEnd(P(*c))
        d.SetHeight(MM(offset))
        d.SetLineThickness(MM(0.2))
        d.SetUnitsMode(pcbnew.DIM_UNITS_MODE_MM)
        d.SetPrecision(pcbnew.DIM_PRECISION_X_X)
        d.SetTextSize(pcbnew.VECTOR2I(MM(size), MM(size)))
        d.SetTextThickness(MM(size * 0.15))
        d.Update()
        b.Add(d)

    for i, m in enumerate(spec.get("mounts", [])):
        fp = pcbnew.FOOTPRINT(b)
        name = m.get("name", f"H{i + 1}")
        fp.SetReference(name)
        fp.SetValue(m.get("screw", "mount"))
        fp.SetFPID(pcbnew.LIB_ID("", f"MountingHole_{m['hole']:.1f}mm"))
        pad = pcbnew.PAD(fp)
        pad.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
        pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
        pad.SetSize(pcbnew.VECTOR2I(MM(m["hole"]), MM(m["hole"])))
        pad.SetDrillSize(pcbnew.VECTOR2I(MM(m["hole"]), MM(m["hole"])))
        pad.SetLayerSet(pcbnew.PAD.UnplatedHoleMask())
        pad.SetNumber("")
        fp.Add(pad)
        if m.get("boss"):  # courtyard ring where the standoff touches: keeps parts off it on the bottom
            c = pcbnew.PCB_SHAPE(fp)
            c.SetShape(pcbnew.SHAPE_T_CIRCLE)
            c.SetLayer(pcbnew.B_CrtYd)
            c.SetWidth(MM(0.05))
            c.SetCenter(pcbnew.VECTOR2I(0, 0)); c.SetEnd(pcbnew.VECTOR2I(MM(m["boss"] / 2), 0))
            fp.Add(c)
        fp.Reference().SetVisible(False)
        fp.Value().SetVisible(False)
        fp.SetBoardOnly(True); fp.SetExcludedFromBOM(True); fp.SetExcludedFromPosFiles(True)
        b.Add(fp)
        fp.SetPosition(P(m["x"], m["y"]))

    for k in spec.get("keepouts", []):
        z = pcbnew.ZONE(b)
        z.SetIsRuleArea(True)
        z.SetDoNotAllowTracks(True); z.SetDoNotAllowVias(True); z.SetDoNotAllowPads(True); z.SetDoNotAllowFootprints(True)
        for fn in ("SetDoNotAllowZoneFills", "SetDoNotAllowCopperPour"):
            if hasattr(z, fn):
                getattr(z, fn)(True)
                break
        ls = pcbnew.LSET(); ls.addLayer(pcbnew.F_Cu); ls.addLayer(pcbnew.B_Cu)
        z.SetLayerSet(ls)
        z.SetZoneName("keepout:" + k.get("what", "boss"))
        o = z.Outline(); o.NewOutline()
        for x, y in circle_poly(k["x"], k["y"], k["d"] / 2 + k.get("clearance", 0.5)):
            o.Append(P(x, y))
        b.Add(z)

    def text(s, x, y, layer, size=1.0):
        t = pcbnew.PCB_TEXT(b)
        t.SetText(s); t.SetPosition(P(x, y)); t.SetLayer(layer)
        t.SetTextSize(pcbnew.VECTOR2I(MM(size), MM(size))); t.SetTextThickness(MM(size * 0.15))
        b.Add(t)

    for d in spec.get("drawings", []):
        if "rect" in d:
            w, h = d["rect"]
            for a, c in (((-w / 2, -h / 2), (w / 2, -h / 2)), ((w / 2, -h / 2), (w / 2, h / 2)), ((w / 2, h / 2), (-w / 2, h / 2)), ((-w / 2, h / 2), (-w / 2, -h / 2))):
                s = pcbnew.PCB_SHAPE(b); s.SetShape(pcbnew.SHAPE_T_SEGMENT); s.SetLayer(pcbnew.Dwgs_User); s.SetWidth(MM(0.3))
                s.SetStart(P(*a)); s.SetEnd(P(*c)); b.Add(s)
            if d.get("label"):
                text(d["label"], 0, h / 2 + 1.2, pcbnew.Dwgs_User)
        elif "line" in d:
            x0_, y0_, x1_, y1_ = d["line"]
            s = pcbnew.PCB_SHAPE(b); s.SetShape(pcbnew.SHAPE_T_SEGMENT); s.SetLayer(pcbnew.Dwgs_User); s.SetWidth(MM(0.3))
            s.SetStart(P(x0_, y0_)); s.SetEnd(P(x1_, y1_)); b.Add(s)
            if d.get("label"):
                text(d["label"], (x0_ + x1_) / 2, (y0_ + y1_) / 2 - (1.5 if y0_ >= 0 else -1.5), pcbnew.Dwgs_User, 0.8)  # label on the board side
        elif "circle" in d:
            x, y, dia = d["circle"]
            s = pcbnew.PCB_SHAPE(b); s.SetShape(pcbnew.SHAPE_T_CIRCLE); s.SetLayer(pcbnew.Dwgs_User); s.SetWidth(MM(0.3))
            s.SetCenter(P(x, y)); s.SetEnd(P(x + dia / 2, y)); b.Add(s)
            if d.get("label"):
                text(d["label"], x, y + dia / 2 + 1.2, pcbnew.Dwgs_User, 0.8)
    for k in spec.get("keepouts", []):
        s = pcbnew.PCB_SHAPE(b); s.SetShape(pcbnew.SHAPE_T_CIRCLE); s.SetLayer(pcbnew.Dwgs_User); s.SetWidth(MM(0.4))
        s.SetCenter(P(k["x"], k["y"])); s.SetEnd(P(k["x"] + k["d"] / 2, k["y"])); b.Add(s)
    # dimensions for a 1:1 print check: the mounting pattern, the board, and the boss pattern (to compare with the box)
    dl = print_layer if print_layer is not None else pcbnew.Dwgs_User
    ms = spec.get("mounts", [])
    if len(ms) >= 4:
        xs = sorted({m["x"] for m in ms}); ys = sorted({m["y"] for m in ms})
        dimension((xs[0], ys[0]), (xs[-1], ys[0]), -(ys[0] - (-bd["height"] / 2)) + 6, dl)      # across, inside the board
        dimension((xs[-1], ys[0]), (xs[-1], ys[-1]), -4, dl)                                   # along
    W, H = bd["width"], bd["height"]
    dimension((-W / 2, -H / 2), (W / 2, -H / 2), -8, dl)                                       # board width, below
    dimension((W / 2, -H / 2), (W / 2, H / 2), -10, dl)                                        # board height, right
    ks = spec.get("keepouts", [])
    if len(ks) >= 4:
        kx = sorted({k["x"] for k in ks}); ky = sorted({k["y"] for k in ks})
        dimension((kx[0], ky[-1]), (kx[-1], ky[-1]), 7, dl)                                     # boss pattern across, above
        dimension((kx[0], ky[0]), (kx[0], ky[-1]), 14, dl)                                      # boss pattern along, left
    # 50 mm scale bar
    sb = -H / 2 - 16
    s = pcbnew.PCB_SHAPE(b); s.SetShape(pcbnew.SHAPE_T_SEGMENT); s.SetLayer(dl); s.SetWidth(MM(0.6))
    s.SetStart(P(-25, sb)); s.SetEnd(P(25, sb)); b.Add(s)
    for xx in (-25, 25):
        s = pcbnew.PCB_SHAPE(b); s.SetShape(pcbnew.SHAPE_T_SEGMENT); s.SetLayer(dl); s.SetWidth(MM(0.6))
        s.SetStart(P(xx, sb - 2)); s.SetEnd(P(xx, sb + 2)); b.Add(s)
    text("50.0 mm scale bar: measure this first", 0, sb - 3.5, dl, 1.2)
    y = -bd["height"] / 2 - 4
    text(spec.get("name", "enclosure template"), 0, y - 18, pcbnew.Cmts_User, 1.2)
    for i, n_ in enumerate(spec.get("notes", [])):
        text(n_, 0, y - 18 - 2.2 * (i + 1), pcbnew.Cmts_User, 0.9)
    ds = b.GetDesignSettings()
    ds.SetAuxOrigin(P(-bd["width"] / 2, -bd["height"] / 2))
    save_board(b, board_path)
    print(f"{board_path}: outline {bd['width']} x {bd['height']} mm, {len(spec.get('mounts', []))} mounting holes, "
          f"{len(spec.get('keepouts', []))} keep-outs, {len(spec.get('drawings', []))} reference drawings")


def fit_check_pdf(board_path, out, parts=False):
    """1:1 A4 PDF of the template: the thick outline copy and dimensions (User.1), the reference drawings,
    notes, standoff rings and the holes at actual size. parts=True adds the placed parts' body outlines and
    references (F.Fab, F.Silkscreen), for checking a placement against the enclosure."""
    import subprocess
    from .kicad import KICAD_CLI
    layers = "User.1,Dwgs.User,Cmts.User,B.CrtYd,Edge.Cuts" + (",F.Fab,F.Silkscreen" if parts else "")
    r = subprocess.run([KICAD_CLI, "pcb", "export", "pdf", "--layers", layers, "--mode-single", "--drill-shape-opt", "2",
                        "--scale", "1", "--black-and-white", "--include-border-title", "-o", str(out), str(board_path)], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit("fit-check export failed: " + r.stderr[-500:])
    print("fit check:", out, "(print at 100 %, check the scale bar)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("enclosure", help="JSON description (see the module docstring)")
    ap.add_argument("--fit-check", metavar="PDF", help="also export a 1:1 PDF (thick outline, dimensions, scale bar) to print and cut")
    ap.add_argument("--parts", action="store_true", help="with --fit-check: include the placed parts' outlines and references")
    a = ap.parse_args(argv)
    apply(a.board, json.load(open(a.enclosure)))
    if a.fit_check:
        fit_check_pdf(a.board, a.fit_check, parts=a.parts)


if __name__ == "__main__":
    main()
