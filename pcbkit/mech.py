"""Enclosure template: a board outline with mounting holes and keep-outs from a small JSON description.

  pcbkit outline BOARD.kicad_pcb enclosure.json

The JSON (kept in the project, measured from the box) looks like:

  {"name": "...", "notes": ["..."],
   "origin": "centre of the mounting pattern, x along the long axis, mm",
   "board": {"width": 90, "height": 58, "corner_radius": 3,
             "notches": [{"corner": "+x+y", "width": 10, "height": 8}],      # optional rectangular corner cuts
             "cutouts": [{"x": 22.65, "y": 39.15, "boss": 7.5, "clearance": 1.0, "fillet": 1.0}]},  # or boss notches
   "mounts":   [{"x": -31.2, "y": -14.85, "hole": 2.2, "boss": 5.0, "name": "H1"}, ...],   # NPTH holes
               # optional "head": 5.5 -> the screw head's footprint on the top: F.CrtYd ring + no copper on F.Cu
   "keepouts": [{"x": -39.0, "y": -22.3, "d": 7.5, "what": "lid screw boss"}, ...],       # no parts/copper, both sides
   "drawings": [{"rect": [92, 61], "label": "plateau"}, {"rect": [123.6, 67.5], "label": "plate"},
                {"circle": [x, y, d], "label": "..."}, {"line": [x0, y0, x1, y1], "label": "..."}]}   # reference geometry on Dwgs.User

Writes into BOARD: the outline on Edge.Cuts (lines and arcs, centred at 100,100 mm), one board-only footprint per
mount with an NPTH pad (plus, with "head", an F.CrtYd ring and an F.Cu copper keep-out the size of the screw head),
a rule area per keep-out (no tracks, vias, pads, footprints or fills on F.Cu and B.Cu,
a circle grown by 0.5 mm), the reference geometry and labels on Dwgs.User, a thick copy of the outline with
dimensions and a scale bar on User.1 and the notes on Cmts.User. It also sets the drill/place origin to the
outline's lower left corner.

Everything it adds goes into one KiCad group named pcbkit:outline (click an item in KiCad and the group is
selected). A rerun removes that group and its items and nothing else, so it can be rerun after the layout starts:
what was drawn by hand or by a script stays, on these layers too (a cut-out inside the board, a note, a hole from
KiCad's library, a keep-out of your own).

A board that has an outline, keepout:* zones or MountingHole_* footprints without a library, but no such group, was
drawn by hand or outlined by pcbkit 0.5 or earlier, which marked nothing. The run then stops, writes nothing and
lists what is in the way. --take-over removes what those versions removed on every run (every drawing, text and
dimension on Edge.Cuts, Dwgs.User, Cmts.User and User.1, every keepout:* zone and every MountingHole_* footprint
without a library), once, then draws; from then on a rerun touches the group only. To keep some of it, delete the
old outline in KiCad instead and run again. Needs pcbnew.
"""
import argparse
import collections
import json
import math
import re

from . import sexp
from .kicad import ensure_pcbnew, save_board

GROUP = "pcbkit:outline"  # the KiCad group that holds everything a run adds: what a rerun may remove
LEGACY_LAYERS = ("Edge.Cuts", "Dwgs.User", "Cmts.User", "User.1")

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


OWN_UUID = re.compile(r'\(uuid\s+"?([^"\s)]+)"?\s*\)')


def own_uuid(block):
    """A top-level item's uuid: its own (uuid ...) child, not that of a pad, text or field inside it."""
    depth, i, n = 0, 0, len(block)
    while i < n:
        c = block[i]
        if c == '"':
            j = block.index('"', i + 1)
            while block[j - 1] == "\\":
                j = block.index('"', j + 1)
            i = j + 1
            continue
        if c == "(":
            depth += 1
            if depth == 2 and block.startswith("(uuid", i):
                m = OWN_UUID.match(block, i)
                return m.group(1) if m else None
        elif c == ")":
            depth -= 1
        i += 1
    return None


def own_items(text):
    """What earlier runs marked as theirs: the spans (start, end) of every pcbkit:outline group and of the top-level
    items that are its members, the members of a group made inside it (items of the outline grouped again in
    KiCad) among them; None when the board has no such group."""
    blocks = top_level_blocks(text)
    groups, members, spans = {}, set(), []           # groups: a group's own uuid -> (its span, its members)
    for s, e, head in blocks:
        if head == "group":
            node = sexp.parse(text[s:e])
            inside = {str(m) for m in (sexp.first(node, "members") or [])[1:]}
            groups[own_uuid(text[s:e])] = ((s, e), inside)
            if len(node) > 1 and str(node[1]) == GROUP:
                members |= inside
                spans.append((s, e))
    if not spans:
        return None
    todo = [m for m in members if m in groups]
    while todo:                                      # a member that is a group: it goes, with what is in it
        span, inside = groups.pop(todo.pop())
        if span not in spans:
            spans.append(span)
        todo += [m for m in inside - members if m in groups]
        members |= inside
    if members:
        named = re.compile("|".join(re.escape(m) for m in members))
        spans += [(s, e) for s, e, head in blocks
                  if head != "group" and named.search(text, s, e) and own_uuid(text[s:e]) in members]
    return sorted(spans)


def legacy_items(text):
    """What `pcbkit outline` removed on every run before it marked its own items (pcbkit 0.5 and earlier), whoever
    drew them: [(start, end, what)] of every top-level drawing, text and dimension on Edge.Cuts, Dwgs.User,
    Cmts.User and User.1 ("lines on Edge.Cuts"), every zone named keepout:* and every MountingHole_* footprint
    without a library (with its reference as a fourth item)."""
    kinds = {"gr_line": "lines", "gr_arc": "arcs", "gr_circle": "circles", "gr_rect": "rectangles",
             "gr_poly": "polygons", "gr_text": "texts", "dimension": "dimensions"}
    out = []
    for s, e, head in top_level_blocks(text):
        blk = text[s:e]
        if head in kinds:
            layer = next((l for l in LEGACY_LAYERS if f'(layer "{l}")' in blk), None)
            if layer:
                out.append((s, e, f"{kinds[head]} on {layer}"))
        elif head == "zone" and '(name "keepout:' in blk:
            out.append((s, e, "keepout:* zones"))
        elif head == "footprint" and blk.startswith('(footprint "MountingHole_'):
            ref = re.search(r'\(property "Reference" "([^"]*)"', blk)
            out.append((s, e, "MountingHole_* footprints without a library", ref.group(1) if ref else "?"))
    return out


def in_the_way(items):
    """Of legacy_items(), those a new outline would be drawn over or beside: an outline, keep-out zones, holes.
    Drawings on the other layers alone are nobody's outline, and stay."""
    return [x for x in items if x[2].endswith("on Edge.Cuts") or "zones" in x[2] or "footprints" in x[2]]


def _counts(items):
    """Items by kind, the hole footprints by reference: "12 lines on Edge.Cuts, 5 MountingHole_* ... (H1, H2)"."""
    n = collections.Counter(x[2] for x in items)
    refs = [x[3] for x in items if len(x) > 3]
    return ", ".join(f"{c} {what}" + (f" ({', '.join(refs)})" if "footprints" in what else "")
                     for what, c in sorted(n.items(), key=lambda kv: kv[0]))


def strip_template(board_path, take_over=False):
    """Remove what an earlier `pcbkit outline` run added: its pcbkit:outline group and that group's items, on the
    file text. A board without the group that has an outline, keep-outs or holes of unmarked origin stops the run
    (SystemExit, nothing written) unless take_over, which removes legacy_items() instead, as every run did before
    the items were marked. Returns how many top-level items were removed, the group itself aside."""
    from pathlib import Path
    path = Path(board_path)
    text = path.read_text(encoding="utf-8")
    drop = own_items(text)
    if drop is not None:
        if take_over:
            raise SystemExit(f"{board_path}: its outline is in a {GROUP} group already, so there is nothing to take "
                             "over; run without --take-over, and only that group is replaced")
    else:
        old = legacy_items(text)
        if not in_the_way(old):
            return 0                   # no outline yet: drawings on the other layers are kept
        if not take_over:
            raise SystemExit(
                f"{board_path}: nothing written. It has an outline, keep-outs or holes that no {GROUP} group marks "
                f"as this command's (drawn by hand, or by pcbkit 0.5 or earlier): {_counts(in_the_way(old))}.\n"
                f"--take-over removes what those versions removed on every run, then draws the new outline as its "
                f"own: {_counts(old)}.\nTo keep some of that, delete the old outline in KiCad instead and run again.")
        drop = [x[:2] for x in old]
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
    path.write_text("".join(out), encoding="utf-8")
    return len(drop) - sum(1 for s, e in drop if text.startswith("(group", s))     # the groups themselves aside


def circle_poly(x, y, r, n=48):
    return [(x + r * math.cos(2 * math.pi * k / n), y + r * math.sin(2 * math.pi * k / n)) for k in range(n)]


def apply(board_path, spec, take_over=False):
    ensure_pcbnew()
    import pcbnew
    MM = pcbnew.FromMM
    P = lambda x, y: pcbnew.VECTOR2I(MM(CX + x), MM(CY - y))  # enclosure y up -> KiCad y down
    bd = spec["board"]
    # before anything is removed: a description that doesn't fit stops here, with the board as it was
    outline = outline_points(bd["width"], bd["height"], bd.get("corner_radius", 2), bd.get("notches"), bd.get("cutouts"))
    # an earlier run's items go on the text: BOARD.Remove() corrupts the bindings. From here the file is without
    # its outline until the new one is saved, so whatever goes wrong before that (a mount without its "hole", a
    # drawing with a number missing) puts the file back as it was
    with open(board_path, encoding="utf-8") as fh:
        before = fh.read()
    removed = strip_template(board_path, take_over)
    try:
        _draw(pcbnew, board_path, spec, outline, removed, P, MM)
    except BaseException:
        with open(board_path, "w", encoding="utf-8") as fh:
            fh.write(before)
        raise


def _draw(pcbnew, board_path, spec, outline, removed, P, MM):
    """apply()'s drawing, on a board whose earlier outline is already off: every item into the pcbkit:outline group,
    then saved."""
    bd = spec["board"]
    board = pcbnew.LoadBoard(str(board_path))
    made = []                          # every item added to the board: they become the pcbkit:outline group

    def add(item):
        board.Add(item)
        made.append(item)

    for item in outline:
        s = pcbnew.PCB_SHAPE(board)
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(MM(0.1))
        if item[0] == "line":
            s.SetShape(pcbnew.SHAPE_T_SEGMENT)
            s.SetStart(P(*item[1])); s.SetEnd(P(*item[2]))
        else:
            s.SetShape(pcbnew.SHAPE_T_ARC)
            s.SetArcGeometry(P(*item[1]), P(*item[2]), P(*item[3]))
        add(s)

    print_layer = getattr(pcbnew, "User_1", None)
    if print_layer is not None:  # a thick copy of the outline for 1:1 prints (Edge.Cuts stays thin for the fab)
        for item in outline:
            s = pcbnew.PCB_SHAPE(board)
            s.SetLayer(print_layer)
            s.SetWidth(MM(0.5))
            if item[0] == "line":
                s.SetShape(pcbnew.SHAPE_T_SEGMENT); s.SetStart(P(*item[1])); s.SetEnd(P(*item[2]))
            else:
                s.SetShape(pcbnew.SHAPE_T_ARC); s.SetArcGeometry(P(*item[1]), P(*item[2]), P(*item[3]))
            add(s)

    def dimension(a, c, offset, layer, size=1.2):
        """Aligned dimension between enclosure points a and c, drawn `offset` mm to the side."""
        d = pcbnew.PCB_DIM_ALIGNED(board, pcbnew.PCB_DIM_ALIGNED_T)
        d.SetLayer(layer)
        d.SetStart(P(*a)); d.SetEnd(P(*c))
        d.SetHeight(MM(offset))
        d.SetLineThickness(MM(0.2))
        d.SetUnitsMode(pcbnew.DIM_UNITS_MODE_MM)
        d.SetPrecision(pcbnew.DIM_PRECISION_X_X)
        d.SetTextSize(pcbnew.VECTOR2I(MM(size), MM(size)))
        d.SetTextThickness(MM(size * 0.15))
        d.Update()
        add(d)

    for i, m in enumerate(spec.get("mounts", [])):
        fp = pcbnew.FOOTPRINT(board)
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
        if m.get("head"):  # the screw head on the top: courtyard ring for parts, rule area for copper (pads allowed)
            c = pcbnew.PCB_SHAPE(fp)
            c.SetShape(pcbnew.SHAPE_T_CIRCLE)
            c.SetLayer(pcbnew.F_CrtYd)
            c.SetWidth(MM(0.05))
            c.SetCenter(pcbnew.VECTOR2I(0, 0)); c.SetEnd(pcbnew.VECTOR2I(MM(m["head"] / 2), 0))
            fp.Add(c)
            z = pcbnew.ZONE(board)
            z.SetIsRuleArea(True)
            z.SetDoNotAllowTracks(True); z.SetDoNotAllowVias(True); z.SetDoNotAllowPads(False); z.SetDoNotAllowFootprints(False)
            for fn in ("SetDoNotAllowZoneFills", "SetDoNotAllowCopperPour"):
                if hasattr(z, fn):
                    getattr(z, fn)(True)
                    break
            z.SetLayer(pcbnew.F_Cu)
            z.SetZoneName("keepout:screw head " + name)
            o = z.Outline(); o.NewOutline()
            for x, y in circle_poly(m["x"], m["y"], m["head"] / 2):
                o.Append(P(x, y))
            add(z)
        fp.Reference().SetVisible(False)
        fp.Value().SetVisible(False)
        fp.SetBoardOnly(True); fp.SetExcludedFromBOM(True); fp.SetExcludedFromPosFiles(True)
        add(fp)
        fp.SetPosition(P(m["x"], m["y"]))

    for k in spec.get("keepouts", []):
        z = pcbnew.ZONE(board)
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
        add(z)

    def text(s, x, y, layer, size=1.0):
        t = pcbnew.PCB_TEXT(board)
        t.SetText(s); t.SetPosition(P(x, y)); t.SetLayer(layer)
        t.SetTextSize(pcbnew.VECTOR2I(MM(size), MM(size))); t.SetTextThickness(MM(size * 0.15))
        add(t)

    for d in spec.get("drawings", []):
        if "rect" in d:
            w, h = d["rect"]
            for a, c in (((-w / 2, -h / 2), (w / 2, -h / 2)), ((w / 2, -h / 2), (w / 2, h / 2)), ((w / 2, h / 2), (-w / 2, h / 2)), ((-w / 2, h / 2), (-w / 2, -h / 2))):
                s = pcbnew.PCB_SHAPE(board); s.SetShape(pcbnew.SHAPE_T_SEGMENT); s.SetLayer(pcbnew.Dwgs_User); s.SetWidth(MM(0.3))
                s.SetStart(P(*a)); s.SetEnd(P(*c)); add(s)
            if d.get("label"):
                text(d["label"], 0, h / 2 + 1.2, pcbnew.Dwgs_User)
        elif "line" in d:
            x0_, y0_, x1_, y1_ = d["line"]
            s = pcbnew.PCB_SHAPE(board); s.SetShape(pcbnew.SHAPE_T_SEGMENT); s.SetLayer(pcbnew.Dwgs_User); s.SetWidth(MM(0.3))
            s.SetStart(P(x0_, y0_)); s.SetEnd(P(x1_, y1_)); add(s)
            if d.get("label"):
                text(d["label"], (x0_ + x1_) / 2, (y0_ + y1_) / 2 - (1.5 if y0_ >= 0 else -1.5), pcbnew.Dwgs_User, 0.8)  # label on the board side
        elif "circle" in d:
            x, y, dia = d["circle"]
            s = pcbnew.PCB_SHAPE(board); s.SetShape(pcbnew.SHAPE_T_CIRCLE); s.SetLayer(pcbnew.Dwgs_User); s.SetWidth(MM(0.3))
            s.SetCenter(P(x, y)); s.SetEnd(P(x + dia / 2, y)); add(s)
            if d.get("label"):
                text(d["label"], x, y + dia / 2 + 1.2, pcbnew.Dwgs_User, 0.8)
    for k in spec.get("keepouts", []):
        s = pcbnew.PCB_SHAPE(board); s.SetShape(pcbnew.SHAPE_T_CIRCLE); s.SetLayer(pcbnew.Dwgs_User); s.SetWidth(MM(0.4))
        s.SetCenter(P(k["x"], k["y"])); s.SetEnd(P(k["x"] + k["d"] / 2, k["y"])); add(s)
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
    s = pcbnew.PCB_SHAPE(board); s.SetShape(pcbnew.SHAPE_T_SEGMENT); s.SetLayer(dl); s.SetWidth(MM(0.6))
    s.SetStart(P(-25, sb)); s.SetEnd(P(25, sb)); add(s)
    for xx in (-25, 25):
        s = pcbnew.PCB_SHAPE(board); s.SetShape(pcbnew.SHAPE_T_SEGMENT); s.SetLayer(dl); s.SetWidth(MM(0.6))
        s.SetStart(P(xx, sb - 2)); s.SetEnd(P(xx, sb + 2)); add(s)
    text("50.0 mm scale bar: measure this first", 0, sb - 3.5, dl, 1.2)
    y = -bd["height"] / 2 - 4
    text(spec.get("name", "enclosure template"), 0, y - 18, pcbnew.Cmts_User, 1.2)
    for i, n_ in enumerate(spec.get("notes", [])):
        text(n_, 0, y - 18 - 2.2 * (i + 1), pcbnew.Cmts_User, 0.9)
    group = pcbnew.PCB_GROUP(board)    # what a rerun removes: this group's items, nobody else's
    group.SetName(GROUP)
    for item in made:
        group.AddItem(item)
    board.Add(group)
    ds = board.GetDesignSettings()
    ds.SetAuxOrigin(P(-bd["width"] / 2, -bd["height"] / 2))
    save_board(board, board_path)
    print(f"{board_path}: outline {bd['width']} x {bd['height']} mm, {len(spec.get('mounts', []))} mounting holes, "
          f"{len(spec.get('keepouts', []))} keep-outs, {len(spec.get('drawings', []))} reference drawings; "
          f"{len(made)} items in the {GROUP} group"
          + (f", {removed} of an earlier run removed" if removed else ""))


def fit_check_pdf(board_path, out, parts=False):
    """1:1 A4 PDF of the template: the thick outline copy and dimensions (User.1), the reference drawings,
    notes, standoff rings and the holes at actual size. parts=True adds the placed parts' body outlines and
    references (F.Fab, F.Silkscreen), for checking a placement against the enclosure."""
    import subprocess
    from .kicad import kicad_cli
    layers = "User.1,Dwgs.User,Cmts.User,B.CrtYd,Edge.Cuts" + (",F.Fab,F.Silkscreen" if parts else "")
    r = subprocess.run([kicad_cli(), "pcb", "export", "pdf", "--layers", layers, "--mode-single", "--drill-shape-opt", "2",
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
    ap.add_argument("--take-over", action="store_true",
                    help="for a board outlined by hand or by pcbkit 0.5 or earlier: remove every drawing on Edge.Cuts, "
                         "Dwgs.User, Cmts.User and User.1, the keepout:* zones and the MountingHole_* footprints "
                         "without a library, once, then draw")
    a = ap.parse_args(argv)
    with open(a.enclosure, encoding="utf-8") as fh:
        spec = json.load(fh)
    apply(a.board, spec, a.take_over)
    if a.fit_check:
        fit_check_pdf(a.board, a.fit_check, parts=a.parts)


if __name__ == "__main__":
    main()
