"""Turn EasyEDA Std exports (schematic + PCB JSON) into a linked KiCad project.

  pcbkit import-easyeda SCH.json PCB.json OUT_DIR --name BOARD [--lib LIBDIR] [--lib-name NICK]
                        [--title T] [--rev R] [--date D] [--company C] [--force] [--keep-courtyards]

KiCad imports EasyEDA PCBs from the command line, but schematics only in the GUI, and a GUI import leaves the
symbols unlinked from the footprints. This converts the schematic itself, keeping the EasyEDA layout:
  * one KiCad symbol per distinct EasyEDA symbol (name + pin geometry; rotated placements become variants, since
    EasyEDA stores pins already rotated), built from the pin geometry so the original wires land on their pins;
  * every sheet is converted: one sheet stays flat, several become a root page of sheet symbols, one file each;
  * net ports and net labels become global labels (EasyEDA joins both by name across all sheets), GND/VCC/+5V
    flags become power symbols named after the flag;
  * every footprint is linked to its symbol, the PCB nets are renamed to the schematic's, and any connectivity
    difference between the EasyEDA schematic and PCB is reported (and stops the run unless --force): a PCB part
    with nets but no symbol, a pad on a net with no schematic pin, nets joined or split. Pegs and holes (pads with
    no number, NPTH) are not compared; a pad the symbol lacks that is unconnected on the PCB too is only listed;
  * footprints go into LIBDIR/<nick>.pretty, each copied from one placement chosen the same way every run (right-
    angle rotations first, then top side, unrotated, lowest reference), and symbols into LIBDIR/<nick>.kicad_sym,
    with project lib tables. Both are merged with what the library has, so several boards can share it: a symbol
    of the same name and pins, or a footprint of the same name and pads, is the same part and the library's is kept
    as it is; one that differs takes the next free name_2, name_3;
  * what KiCad's PCB importer gets wrong is corrected: -100 mm paste margins, every designator shown, duplicate
    vias, EasyEDA layer names, zone fill minimums, the copper-pour keep-outs on User.4 it makes of EasyEDA's pin
    outlines, Edge.Cuts ends a few microns apart; the zones are refilled and the board saved filled;
  * each part's body is drawn on F.Fab/B.Fab from EasyEDA's component-shape layer (99), which KiCad's importer
    drops, leaving the body only in silk drawn well off it;
  * courtyards that miss a part's pads or body, or sit on the other side from its pads (KiCad's importer often
    leaves them turned against the pads, a sliver, or round the pins and not the body), are redrawn round the pads
    and the body (its fab outline, or its silk without one) + 0.25 mm, on the board and in the library
    (--keep-courtyards: listed only; `pcbkit courtyards` checks and redraws them later).
Loose pads and holes on the PCB (no symbol) become board-only footprints, out of the BOM and placement files; one
with a net (a GND test pad) is listed. A schematic part with no footprint on the PCB is listed as schematic-only and
gets an empty Footprint field. Sheet files are named after the EasyEDA sheets, without EasyEDA's " copy"
(ESP32+USB copy -> esp32-usb.kicad_sch). Writes OUT_DIR/BOARD.kicad_{pro,sch,pcb} and runs ERC and DRC with the
schematic parity check. Harvested from dodeca's panels/tools/easyeda_to_kicad.py.
"""
import argparse
import base64
import json
import math
import os
import re
import struct
import subprocess
import sys
import tempfile
import uuid
from collections import Counter, defaultdict
from pathlib import Path

from .kicad import KICAD_CLI, KICAD_PYTHON, KICAD_SYMBOLS, ensure_pcbnew, quiet

POWER_LIB = Path(KICAD_SYMBOLS) / "power.kicad_sym"
U = 0.254  # EasyEDA schematic unit (10 mil) in mm
NS = uuid.UUID("6f1c2a52-5d1e-4c8e-9a51-1b7d0c0d2a46")
PCB_RULES = {  # JLC 2-layer minimums with a little margin; edit in Board Setup afterwards
    "min_clearance": 0.127, "min_track_width": 0.127, "min_via_diameter": 0.45, "min_via_annular_width": 0.1,
    "min_through_hole_diameter": 0.3, "min_hole_to_hole": 0.25, "min_hole_clearance": 0.25,
    "min_copper_edge_clearance": 0.2, "min_silk_clearance": 0.0, "min_text_height": 0.6,
    "min_text_thickness": 0.1, "solder_mask_to_copper_clearance": 0.0, "allow_blind_buried_vias": False,
    "allow_microvias": False, "min_resolved_spokes": 1, "max_error": 0.005,
}
# An imported board is a record of what was built: parts placed inside another part's courtyard (a devkit over
# jumpers) are reported, not failed. Tighten in Board Setup for the next revision.
RULE_SEVERITIES = {"lib_footprint_mismatch": "ignore", "courtyards_overlap": "warning", "pth_inside_courtyard": "warning"}
NET_CLASS = {"name": "Default", "clearance": 0.15, "track_width": 0.254, "via_diameter": 0.6, "via_drill": 0.3,
             "microvia_diameter": 0.3, "microvia_drill": 0.1, "diff_pair_width": 0.2, "diff_pair_gap": 0.25,
             "diff_pair_via_gap": 0.25, "wire_width": 6, "bus_width": 12, "line_style": 0,
             "pcb_color": "rgba(0, 0, 0, 0.000)", "schematic_color": "rgba(0, 0, 0, 0.000)", "priority": 2147483647}
NOTES = Counter()


def note(msg):
    NOTES[msg] += 1


def uid(*key):
    return str(uuid.uuid5(NS, "/".join(map(str, key))))


def n(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def font(size=1.27, justify=None, hide=False, bold=False):
    b = " (bold yes)" if bold else ""
    j = f" (justify {justify})" if justify else ""
    h = " (hide yes)" if hide else ""
    return f"{h} (effects (font (size {n(size)} {n(size)}){b}){j})"


def safe_name(s):
    return re.sub(r"[\s/:\\]+", "_", s.strip()) or "unnamed"


# ---------------------------------------------------------------- s-expression text

_TOK = re.compile(r'"(?:[^"\\]|\\.)*"|[()]')
_HEAD = re.compile(r'\(\s*([^\s()"]*)')


def block_end(text, i):
    """Index just past the list that opens at text[i]."""
    depth = 0
    for m in _TOK.finditer(text, i):
        t = m.group(0)
        if t == "(":
            depth += 1
        elif t == ")":
            depth -= 1
            if depth == 0:
                return m.end()
    raise ValueError("unbalanced s-expression")


def child_blocks(text, start=None):
    """(start, end, head) of each list directly inside the list that opens at text[start] (default: the root)."""
    start = text.index("(") if start is None else start
    out, depth, s = [], 0, None
    for m in _TOK.finditer(text, start):
        t = m.group(0)
        if t == "(":
            depth += 1
            if depth == 2:
                s = m.start()
        elif t == ")":
            if depth == 2:
                out.append((s, m.end(), _HEAD.match(text, s).group(1)))
            depth -= 1
            if depth == 0:
                break
    return out


def cut(text, spans):
    """text without the given (start, end) spans, each with the whitespace before it."""
    out, i = [], 0
    for s, e in sorted(spans):
        j = s
        while j > i and text[j - 1] in " \t\n":
            j -= 1
        out.append(text[i:j])
        i = e
    return "".join(out) + text[i:]


def strip_user_keepouts(path):
    """Remove keep-out rule areas that lie only on User.* layers, on the board and inside its footprints, from a
    saved .kicad_pcb or .kicad_mod (on the text: BOARD.Remove() corrupts the bindings). KiCad's EasyEDA importer
    turns layer-100 "cutout" regions (the pin outlines in LCSC footprints) into copper-pour keep-outs on User.4,
    which DRC then reports as items_not_allowed. Returns how many were removed."""
    path = Path(path)
    text = path.read_text()
    drop = []

    def check(s, e):
        kids = {h: (a, b) for a, b, h in child_blocks(text, s)}
        if "keepout" not in kids or "(enabled yes)" in text[slice(*kids.get("placement", (0, 0)))]:
            return
        lay = kids.get("layers") or kids.get("layer")
        names = re.findall(r'"([^"]*)"', text[lay[0]:lay[1]]) if lay else []
        if names and all(x.startswith("User.") for x in names):
            drop.append((s, e))

    for s, e, head in child_blocks(text):
        if head == "zone":
            check(s, e)
        elif head == "footprint":
            for s2, e2, h2 in child_blocks(text, s):
                if h2 == "zone":
                    check(s2, e2)
    if drop:
        path.write_text(cut(text, drop))
    return len(drop)


# ---------------------------------------------------------------- EasyEDA schematic parsing

def cpara(s):
    p = s.split("`")
    return dict(zip(p[0::2], p[1::2]))


def parse_text(k):
    """T~mark~x~y~rot~color~font~size~weight~style~~type~text~visible~anchor~id..."""
    return dict(x=float(k[2]), y=float(k[3]), rot=int(float(k[4] or 0)), size=k[7], bold=k[8] == "bold",
                text=k[12].replace("\\n", "\n"), vis=k[13] != "0", anchor=k[14] or "start")


def svg_path_points(d):
    """Polylines from an SVG path using only M, L and Z (anything else is skipped with a note)."""
    toks = re.findall(r"[MLZmlz]|-?\d*\.?\d+(?:e-?\d+)?", d)
    out, cur, i, cmd = [], [], 0, None
    while i < len(toks):
        t = toks[i]
        if t in "MLZmlz":
            cmd = t.upper()
            i += 1
            if cmd == "Z":
                if cur:
                    cur.append(cur[0])
                    out.append(cur)
                    cur = []
            continue
        if cmd is None or cmd not in "ML" or i + 1 >= len(toks):
            break
        x, y = float(toks[i]), float(toks[i + 1])
        i += 2
        if cmd == "M" and cur:
            out.append(cur)
            cur = []
        cur.append((x, y))
    if cur:
        out.append(cur)
    if re.search(r"[AaCcQqSsTtHhVv]", d):
        note("symbol path with arcs or curves drawn as straight segments")
    return [c for c in out if len(c) > 1]


def parse_part(s):
    segs = s.split("#@$")
    h = segs[0].split("~")
    part = dict(x=float(h[1]), y=float(h[2]), para=cpara(h[3]), rot=h[4] or "0", pins=[], gfx=[], fields={})
    for g in segs[1:]:
        k = g.split("~")
        if k[0] == "T":
            if k[1] in ("P", "N"):
                part["fields"][k[1]] = parse_text(k)
        elif k[0] == "P":
            sub = g.split("^^")
            f = sub[0].split("~")
            cx, cy = map(float, sub[1].split("~")[:2])
            m = sub[2].split("~")[0].split()  # M x y h|v d
            sx, sy, d = float(m[1]), float(m[2]), float(m[4])
            ex, ey = (sx + d, sy) if m[3] == "h" else (sx, sy + d)
            bx, by = (ex, ey) if math.isclose(sx, cx) and math.isclose(sy, cy) else (sx, sy)
            name = sub[3].split("~")
            part["pins"].append(dict(num=f[3], x=cx, y=cy, bx=bx, by=by, length=abs(d), name=name[4]))
        elif k[0] in ("R", "E", "PL", "PG"):
            part["gfx"].append(k)
        elif k[0] == "PT":
            for pts in svg_path_points(k[1]):
                part["gfx"].append(["PL", " ".join(f"{x} {y}" for x, y in pts)])
        elif k[0] not in ("Pimage", "A"):
            note(f"symbol graphic {k[0]} not converted")
    return part


def parse_flag(s):
    sub = s.split("^^")
    h = sub[0].split("~")
    ax, ay = map(float, sub[1].split("~")[:2])
    t = sub[2].split("~")
    dx, dy = float(t[2]) - ax, float(t[3]) - ay
    side = ("right" if dx > 0 else "left") if abs(dx) > abs(dy) else ("down" if dy > 0 else "up")
    return dict(kind=h[1], x=ax, y=ay, name=t[0], side=side)


def parse_netlabel(s):
    """N~x~y~rot~color~name~id~anchor~tx~ty~...: a net label on a wire end. Like a net port it joins every item of
    that name in the project, on any sheet. Rotation is clockwise (y down); the text runs from the point."""
    k = s.split("~")
    side = {0: "right", 90: "down", 180: "left", 270: "up"}.get(int(float(k[3] or 0)) % 360, "right")
    if k[7] == "end":
        side = {"right": "left", "left": "right", "up": "down", "down": "up"}[side]
    return dict(kind="netLabel", x=float(k[1]), y=float(k[2]), name=k[5], side=side)


def load_schematic(path, pin_fixes=()):
    """Every sheet of the export, in page order.
    pin_fixes: ("REF", "PIN NAME", "NEW NUMBER") for EasyEDA symbol typos (two pins with one number)."""
    d = json.load(open(path))
    sheets = []
    for page in d["schematics"]:
        data = page["dataStr"]
        sheet = load_sheet(json.loads(data) if isinstance(data, str) else data, pin_fixes)
        sheet["name"] = page.get("title", "")
        sheets.append(sheet)
    refs = Counter(p["fields"]["P"]["text"] for s in sheets for p in s["parts"])
    if any(c > 1 for c in refs.values()):
        sys.exit("more than one part with reference " + ", ".join(sorted(r for r, c in refs.items() if c > 1))
                 + "; annotate the EasyEDA schematic first")
    return dict(sheets=sheets, title=d.get("title", ""))


def load_sheet(sheet, pin_fixes):
    out = dict(parts=[], wires=[], flags=[], nc=[], texts=[], images=[], rects=[], junctions=[], bbox=sheet["BBox"])
    for s in sheet["shape"]:
        kind = s.split("~", 1)[0]
        if kind == "LIB":
            if "package`NONE`" not in s.split("#@$", 1)[0]:  # NONE = the sheet frame
                part = parse_part(s)
                ref = part["fields"].get("P", {}).get("text")
                for fref, pname, num in pin_fixes:
                    if fref == ref:
                        hits = [p for p in part["pins"] if p["name"] == pname]
                        if not hits:
                            sys.exit(f"--pin {fref}:{pname}={num}: {fref} has no pin named {pname}")
                        for p in hits:
                            p["num"] = num
                        note(f"pin renumbered: {fref} {pname} -> {num}")
                out["parts"].append(part)
        elif kind == "W":
            pts = list(map(float, s.split("~")[1].split()))
            out["wires"] += [((pts[i], pts[i + 1]), (pts[i + 2], pts[i + 3])) for i in range(0, len(pts) - 2, 2)]
        elif kind == "F":
            out["flags"].append(parse_flag(s))
        elif kind == "N":
            label = parse_netlabel(s)
            if label["name"]:
                out["flags"].append(label)
            else:
                note("net label without a name skipped")
        elif kind == "O":
            k = s.split("~")
            out["nc"].append((float(k[1]), float(k[2])))
        elif kind == "J":
            k = s.split("~")
            out["junctions"].append((float(k[1]), float(k[2])))
        elif kind == "T":
            out["texts"].append(parse_text(s.split("~")))
        elif kind == "R":
            k = s.split("~")
            out["rects"].append((float(k[1]), float(k[2]), float(k[5]), float(k[6])))
        elif kind == "I":
            k = s.split("~")
            out["images"].append(dict(x=float(k[1]), y=float(k[2]), w=float(k[3]), h=float(k[4]), href=k[6]))
        else:
            note(f"sheet item {kind} not converted")
    return out


# ---------------------------------------------------------------- symbols

def pin_angle(p):
    dx, dy = p["bx"] - p["x"], -(p["by"] - p["y"])  # towards the body, y up
    return (0 if dx > 0 else 180) if abs(dx) > abs(dy) else (90 if dy > 0 else 270)


def symbol_geometry(part):
    """Pins and graphics relative to the part origin, in mm, y up: the symbol's identity."""
    ox, oy = part["x"], part["y"]
    rel = lambda x, y: (round((x - ox) * U, 4), round(-(y - oy) * U, 4))
    pins = tuple(sorted((p["num"], p["name"], *rel(p["x"], p["y"]), pin_angle(p), round(p["length"] * U, 4))
                        for p in part["pins"]))
    gfx = []
    for k in part["gfx"]:
        if k[0] == "R":
            x, y, w, h = map(float, (k[1], k[2], k[5], k[6]))
            gfx.append(("R", *rel(x, y), *rel(x + w, y + h)))
        elif k[0] == "E":
            gfx.append(("E", *rel(float(k[1]), float(k[2])), round(float(k[3]) * U, 4), k[8] not in ("", "none")))
        elif k[0] in ("PL", "PG"):
            v = list(map(float, k[1].split()))
            pts = [rel(v[i], v[i + 1]) for i in range(0, len(v) - 1, 2)]
            if k[0] == "PG" and pts and pts[0] != pts[-1]:
                pts.append(pts[0])
            gfx.append(("PL", tuple(pts)))
    return pins, tuple(gfx)


def field_rel(part, key):
    f = part["fields"][key]
    return (f["x"] - part["x"]) * U, -(f["y"] - part["y"]) * U, f["anchor"]


JUSTIFY = {"start": "left bottom", "end": "right bottom", "middle": "bottom"}


def symbol_def(name, part, nick, fp_name):
    pins, gfx = symbol_geometry(part)
    ref_text = part["fields"].get("P", {}).get("text", "U?")
    ref = (re.match(r"[A-Za-z]+", ref_text) or re.match(r".", "U")).group(0)
    rx, ry, ra = field_rel(part, "P") if "P" in part["fields"] else (0, 2.54, "start")
    vx, vy, va = field_rel(part, "N") if "N" in part["fields"] else (0, -2.54, "start")
    para = part["para"]
    value = part["fields"].get("N", {}).get("text", para.get("name", name))
    props = [("Reference", ref, rx, ry, False, JUSTIFY[ra]), ("Value", value, vx, vy, False, JUSTIFY[va]),
             ("Footprint", f"{nick}:{fp_name}" if fp_name else "", 0, 0, True, None),
             ("Datasheet", para.get("link", ""), 0, 0, True, None),
             ("Description", para.get("name", ""), 0, 0, True, None),
             ("LCSC", para.get("Supplier Part", ""), 0, 0, True, None),
             ("MPN", para.get("Manufacturer Part", ""), 0, 0, True, None),
             ("Manufacturer", para.get("Manufacturer", ""), 0, 0, True, None)]
    plain = all(p[0] == p[1] for p in pins) and len(pins) <= 2  # R, C: numbers and names say nothing
    out = [f'(symbol {q(name)}',
           f'  (pin_numbers{" (hide yes)" if plain else ""})',
           f'  (pin_names (offset 0.254){" (hide yes)" if plain else ""})',
           '  (exclude_from_sim no) (in_bom yes) (on_board yes)']
    for k, v, x, y, hide, j in props:
        out.append(f'  (property {q(k)} {q(v)} (at {n(x)} {n(y)} 0){font(justify=j, hide=hide)})')
    out.append(f'  (symbol {q(name + "_0_1")}')
    for g in gfx:
        if g[0] == "R":
            out.append(f'    (rectangle (start {n(g[1])} {n(g[2])}) (end {n(g[3])} {n(g[4])}) '
                       '(stroke (width 0.254) (type default)) (fill (type background)))')
        elif g[0] == "E":
            out.append(f'    (circle (center {n(g[1])} {n(g[2])}) (radius {n(g[3])}) (stroke (width 0) (type default)) '
                       f'(fill (type {"outline" if g[4] else "none"})))')
        else:
            xy = " ".join(f"(xy {n(x)} {n(y)})" for x, y in g[1])
            out.append(f'    (polyline (pts {xy}) (stroke (width 0.254) (type default)) (fill (type none)))')
    out.append("  )")
    out.append(f'  (symbol {q(name + "_1_1")}')
    for num, pname, x, y, ang, length in pins:
        out.append(f'    (pin passive line (at {n(x)} {n(y)} {ang}) (length {n(length)}) '
                   f'(name {q(pname)} (effects (font (size 1.016 1.016)))) (number {q(num)} (effects (font (size 1.016 1.016)))))')
    out += ["  )", "  (embedded_fonts no)", ")"]
    return "\n".join(out)


def power_symbols():
    """GND, VCC and PWR_FLAG from KiCad's power library: definition text and Value offset."""
    text = POWER_LIB.read_text()
    out = {}
    for name in ("GND", "VCC", "PWR_FLAG"):
        m = re.search(rf'^\t\(symbol "{name}"\n.*?^\t\)\n', text, re.S | re.M)
        body = m.group(0).replace(f'\t(symbol "{name}"', f'\t(symbol "power:{name}"', 1)
        at = re.search(rf'\(property "Value" "{name}"\s*\(at ([-\d.]+) ([-\d.]+)', body)
        out[name] = (body, float(at.group(1)), float(at.group(2)))
    return out


# ---------------------------------------------------------------- schematic writer

POWER_ROT = {"GND": {"down": 0, "right": 90, "up": 180, "left": 270},
             "VCC": {"up": 0, "left": 90, "down": 180, "right": 270}}
LABEL_ROT = {"right": (0, "left"), "up": (90, "left"), "left": (180, "right"), "down": (270, "right")}


def rot_offset(x, y, rot):
    x, y = x, -y
    return {0: (x, y), 90: (y, -x), 180: (-x, -y), 270: (-y, x)}[rot]


def split_wires(wires, points):
    """Break wires wherever another item's connection point lands mid-segment."""
    out = []
    for a, b in wires:
        inner = []
        for p in points:
            if p in (a, b):
                continue
            cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
            dot = (p[0] - a[0]) * (b[0] - a[0]) + (p[1] - a[1]) * (b[1] - a[1])
            if abs(cross) < 1e-6 and 0 < dot < (b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2:
                inner.append((dot, p))
        chain = [a] + [p for _, p in sorted(set(inner))] + [b]
        out += list(zip(chain, chain[1:]))
    return out


def png_size(data):
    w, h = struct.unpack(">II", data[16:24])
    ppi = 300.0
    i = data.find(b"pHYs")
    if i > 0:
        px, _py, unit = struct.unpack(">IIB", data[i + 4:i + 13])
        if unit == 1 and px:
            ppi = px * 0.0254
    return w, h, ppi


_STR = r'"((?:[^"\\]|\\.)*)"'
_NUM = r"([-+\d.eE]+)"


def symbol_pins(text):
    """The pins of a library symbol definition as sorted (number, name, x, y, angle, length): what wires land on.
    Reads the importer's own definitions and KiCad-saved ones alike, list by list, so text in a property that reads
    like a pin is not taken for one: the pins of each unit, and of each pin its at, length, name and number."""
    out = []
    root = text.index("(")
    for s, _, head in child_blocks(text, root):
        if head not in ("symbol", "pin"):
            continue
        for ps in [s] if head == "pin" else [ps for ps, _, h in child_blocks(text, s) if h == "pin"]:
            part = {}
            for cs, ce, h in child_blocks(text, ps):
                part.setdefault(h, text[cs:ce])
            at = re.match(r"\(at\s+" + _NUM + r"\s+" + _NUM + r"(?:\s+" + _NUM + ")?", part.get("at", ""))
            length = re.match(r"\(length\s+" + _NUM, part.get("length", ""))
            name = re.match(r"\(name\s+" + _STR, part.get("name", ""))
            num = re.match(r"\(number\s+" + _STR, part.get("number", ""))
            x, y, a = (float(at.group(1)), float(at.group(2)), float(at.group(3) or 0)) if at else (0.0, 0.0, 0.0)
            out.append((num.group(1) if num else "", name.group(1) if name else "", round(x, 4), round(y, 4),
                        int(a) % 360, round(float(length.group(1)), 4) if length else 2.54))
    return tuple(sorted(out))


def unq(s):
    """A string as q() wrote it, without its quotes and escapes: the name it stands for."""
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) == "n" else m.group(1), s)


def read_symbol_lib(path):
    """{name: definition text} of every top-level symbol in a .kicad_sym, in file order ({} if there is none)."""
    path = Path(path)
    if not path.exists():
        return {}
    text = path.read_text()
    out = {}
    for s, e, head in child_blocks(text):
        m = re.match(r"\(symbol\s+" + _STR, text[s:e]) if head == "symbol" else None
        if m:
            out[unq(m.group(1))] = text[s:e]
    return out


def symbol_units(text):
    """The (unit, body style) of each drawing unit of a library symbol: its NAME_U_S sub-symbols."""
    out = set()
    for s, e, head in child_blocks(text):
        m = re.match(r"\(symbol\s+" + _STR, text[s:e]) if head == "symbol" else None
        u = re.search(r"_(\d+)_(\d+)$", unq(m.group(1))) if m else None
        if u:
            out.add((int(u.group(1)), int(u.group(2))))
    return out


def same_symbol(old, new):
    """Whether a library symbol can stand for a new one of the same name: the same pins (a symbol drawn without
    pins, the same text). A derived symbol (extends) never does: the schematic's copy has to be flat. Nor does
    one with a second unit or body style: the schematic places unit 1 only, so pins moved to unit 2 would be
    left off."""
    if any(h == "extends" for _, _, h in child_blocks(old)) or any(u > 1 or s > 1 for u, s in symbol_units(old)):
        return False
    a = symbol_pins(old)
    return a == symbol_pins(new) and (bool(a) or old.strip() == new.strip())


def write_symbol_lib(path, existing, defs):
    """The library: every symbol it had, plus this import's new ones. A library that exists keeps its text as it is
    (KiCad's header and tabs, if KiCad saved it last): one this import adds nothing to is not written, and a new
    symbol goes in before the first symbol whose name sorts after it, so a sorted library stays sorted. A new
    library has the symbols sorted by name."""
    path = Path(path)
    new = {k: v for k, v in defs.items() if k not in existing}
    if not path.exists():
        path.write_text('(kicad_symbol_lib (version 20241209) (generator "pcbkit_import_easyeda") (generator_version '
                        '"10.0")\n' + "\n".join(new[k] for k in sorted(new)) + "\n)\n")
        return
    if not new:
        return
    text = path.read_text()
    root = text.index("(")
    spots = [(unq(re.match(r"\(symbol\s+" + _STR, text[s:e]).group(1)), s)
             for s, e, head in child_blocks(text, root) if head == "symbol"]
    close = block_end(text, root) - 1

    def indent(i):
        """The white space before text[i] on its line; None if there is something else."""
        before = text[text.rfind("\n", 0, i) + 1:i]
        return None if before.strip() else before
    own = next((indent(s) for _, s in spots if indent(s) is not None), "\t")  # the library's symbol indent
    out, last = [], 0
    for name in sorted(new):
        at = next((s for other, s in spots if other > name), close)
        ind = indent(at)
        if ind is None:  # not at the start of a line: give the symbol lines of its own
            out.append(text[last:at] + "\n" + own + new[name] + "\n")
            last = at
        else:
            out.append(text[last:at - len(ind)] + own + new[name] + "\n")
            last = at - len(ind)
    path.write_text("".join(out) + text[last:])


def register_symbols(sch, nick, existing=None):
    """One library symbol per distinct (name, geometry), merged with the symbols the library already has
    (existing, {name: definition}): one with the same name and the same pins is used as it is, so another board
    imported into the same library keeps its symbols; one with the same name and other pins makes this one take
    the next free name_N. Returns ({ref: symbol name}, {name: definition}, stats)."""
    existing = existing or {}
    defs, geos, by_ref = {}, {}, {}
    stats = dict(new=0, kept=0, renamed=set())
    for p in (p for s in sch["sheets"] for p in s["parts"]):
        base = safe_name(p["para"].get("spiceSymbolName") or p["para"].get("name") or p["fields"].get("N", {}).get("text", "part"))
        geo = symbol_geometry(p)
        fp = safe_name(p["para"].get("package", "")) if p["para"].get("package") else ""
        name, k = base, 1
        while True:
            if name in geos:
                if geos[name] == geo:
                    break
            else:
                text = symbol_def(name, p, nick, fp)
                if name not in existing:
                    geos[name], defs[name] = geo, text
                    stats["new"] += 1
                    break
                if same_symbol(existing[name], text):
                    geos[name], defs[name] = geo, existing[name]
                    stats["kept"] += 1
                    break
                stats["renamed"].add(name)
            k += 1
            name = f"{base}_{k}"
        by_ref[p["fields"]["P"]["text"]] = name
    return by_ref, defs, stats


def sheet_title(title, i):
    """A KiCad sheet name from an EasyEDA sheet title: without the " copy" EasyEDA appends to a duplicated sheet
    ("POWER copy", "LEDs copy copy"), and without a / (KiCad sheet names can't hold one)."""
    t = re.sub(r"(?:\s+copy(?:\s*\(?\d+\)?)?)+\s*$", "", title.strip(), flags=re.I).strip()
    return (t or f"Sheet {i + 1}").replace("/", "_")


def sheet_stem(name, i):
    """A file name stem for a sheet: lower case, every run of characters other than letters, digits and _ one -
    ("ESP32+USB" -> esp32-usb)."""
    return re.sub(r"[^\w]+", "-", name.lower()).strip("-_") or f"sheet-{i + 1}"


def sheet_files(sheets, project):
    """(sheet name, file name) per EasyEDA sheet: unique, and no file clashing with the root PROJECT.kicad_sch."""
    names, files, out = set(), {f"{project}.kicad_sch".lower()}, []
    for i, s in enumerate(sheets):
        base = sheet_title(s["name"], i)
        name, k = base, 1
        while name in names:
            k += 1
            name = f"{base} {k}"
        stem = sheet_stem(name, i)
        file, k = f"{stem}.kicad_sch", 1
        while file.lower() in files:
            k += 1
            file = f"{stem}_{k}.kicad_sch"
        names.add(name)
        files.add(file.lower())
        out.append((name, file))
    return out


def write_schematic(sch, out_dir, sym_of, defs, power, project, nick, meta, pcb_refs=None):
    """OUT_DIR/PROJECT.kicad_sch. One EasyEDA sheet is the root itself; several become a root page of sheet symbols,
    one file each. Net ports and labels are global labels, so nets join by name across sheets as in EasyEDA.
    pcb_refs: the references the PCB has; a part not among them gets no footprint (its footprint is in no library).
    Returns (root uuid, [(sheet uuid, sheet name)], {ref: link to the footprint})."""
    root = uid(project, "root")
    flags = [f for s in sch["sheets"] for f in s["flags"]]
    # PWR_FLAGs: every pin is passive, so tell ERC where each power net is driven from.
    power_nets = []
    for f in flags:
        if f["kind"] != "part_netLabel_netPort" and f["kind"].startswith("part_netLabel_") and f["name"] and f["name"] not in power_nets:
            power_nets.append(f["name"])
    power_nets = [(p, "GND" if any(f["kind"] == "part_netLabel_gnD" and f["name"] == p for f in flags) else "VCC")
                  for p in power_nets]
    power_refs, links, subs = [], {}, []
    if len(sch["sheets"]) == 1:
        file = f"{project}.kicad_sch"
        for ref, link in write_sheet(sch["sheets"][0], out_dir / file, root, "/" + root, (), sym_of, defs, power,
                                     project, nick, meta, power_refs, power_nets, pcb_refs).items():
            links[ref] = dict(link, path=f"/{link['uuid']}", sheetname="/", sheetfile=file)
        return root, subs, links
    pages = []
    for i, (sheet, (name, file)) in enumerate(zip(sch["sheets"], sheet_files(sch["sheets"], project))):
        su = uid(project, "sheet", i)
        for ref, link in write_sheet(sheet, out_dir / file, uid(project, "sheet-file", i), f"/{root}/{su}",
                                     ("page", i) if i else (), sym_of, defs, power, project, nick, meta, power_refs,
                                     power_nets if i == 0 else (), pcb_refs).items():
            links[ref] = dict(link, path=f"/{su}/{link['uuid']}", sheetname=name, sheetfile=file)
        subs.append((su, name))
        pages.append((su, name, file))
    write_root(out_dir / f"{project}.kicad_sch", root, pages, project, meta)
    return root, subs, links


def write_root(sch_path, root, pages, project, meta):
    """The root page of a multi-sheet import: a sheet symbol per EasyEDA sheet, three to a row."""
    body = []
    for i, (su, name, file) in enumerate(pages):
        x, y = 25.4 + 76.2 * (i % 3), 50.8 + 50.8 * (i // 3)
        body += [f'(sheet (at {n(x)} {n(y)}) (size 60.96 30.48) (exclude_from_sim no) (in_bom yes) (on_board yes) '
                 f'(dnp no) (fields_autoplaced yes) (stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0.0)) '
                 f'(uuid {q(su)})',
                 f'  (property "Sheetname" {q(name)} (at {n(x)} {n(y - 0.635)} 0){font(justify="left bottom")})',
                 f'  (property "Sheetfile" {q(file)} (at {n(x)} {n(y + 31.115)} 0){font(justify="left top")})',
                 f'  (instances (project {q(project)} (path {q("/" + root)} (page {q(str(i + 2))}))))', ")"]
    body.append(f'(text "One sheet per EasyEDA sheet; net ports and net labels are global labels, joined by name '
                f'across sheets" (exclude_from_sim no) (at 25.4 38.1 0){font(justify="left")} '
                f'(uuid {q(uid(project, "root-text"))}))')
    width, height = 297, max(210, 50.8 * ((len(pages) + 2) // 3) + 80)
    head = [f'(kicad_sch (version 20250114) (generator "pcbkit_import_easyeda") (generator_version "10.0") (uuid {q(root)})',
            f'(paper "User" {width} {n(height)})', title_block(meta), "(lib_symbols)"]
    tail = ['(sheet_instances (path "/" (page "1")))', "(embedded_fonts no)", ")"]
    sch_path.write_text("\n".join(head + body + tail) + "\n")


def title_block(meta):
    return (f'(title_block (title {q(meta["title"])}) (date {q(meta["date"])}) (rev {q(meta["rev"])}) (company {q(meta["company"])})'
            f' (comment 1 "Converted from the EasyEDA Std export by pcbkit import-easyeda"))')


def write_sheet(sheet, sch_path, sheet_uuid, inst_path, tag, sym_of, defs, power, project, nick, meta, power_refs,
                power_nets, pcb_refs=None):
    """One page. sheet_uuid is the file's own, inst_path its place in the hierarchy; tag keeps the uuids of items
    unique across pages (the first page has none, as a single-sheet import). power_refs numbers #PWR/#FLG across
    pages; power_nets [(net, GND|VCC)] get their PWR_FLAGs on this page. Returns {ref: link}."""
    sid = lambda *k: uid(project, *tag, *k)
    bb = sheet["bbox"]
    ox = 10 * math.ceil(-bb["x"] / 10) + 60
    oy = 10 * math.ceil(-bb["y"] / 10) + 100
    X = lambda x: (x + ox) * U
    Y = lambda y: (y + oy) * U
    width = math.ceil((bb["width"] + 120) * U)
    height = math.ceil((bb["height"] + 140) * U + 35)
    body = []
    inst = lambda ref: f'  (instances (project {q(project)} (path {q(inst_path)} (reference {q(ref)}) (unit 1))))'
    links = {}

    for p in sheet["parts"]:
        ref = p["fields"]["P"]["text"]
        name = sym_of[ref]
        para = p["para"]
        on_pcb = pcb_refs is None or ref in pcb_refs
        fp = f"{nick}:{safe_name(para['package'])}" if para.get("package") and on_pcb else ""
        u = sid("sym", ref)
        links[ref] = dict(uuid=u, lcsc=para.get("Supplier Part", ""), value=p["fields"].get("N", {}).get("text", ""),
                          mpn=para.get("Manufacturer Part", ""), manufacturer=para.get("Manufacturer", ""),
                          desc=para.get("name", ""), footprint=safe_name(para["package"]) if para.get("package") else "",
                          package=para.get("package", ""))
        body.append(f'(symbol (lib_id {q(nick + ":" + name)}) (at {n(X(p["x"]))} {n(Y(p["y"]))} 0) (unit 1) '
                    f'(exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (uuid {q(u)})')
        for key, fname in (("P", "Reference"), ("N", "Value")):
            f = p["fields"].get(key)
            if f:
                body.append(f'  (property {q(fname)} {q(f["text"])} (at {n(X(f["x"]))} {n(Y(f["y"]))} 0)'
                            f'{font(justify=JUSTIFY[f["anchor"]], hide=not f["vis"])})')
        for k, v in (("Footprint", fp), ("Datasheet", para.get("link", "")), ("Description", links[ref]["desc"]),
                     ("LCSC", links[ref]["lcsc"]), ("MPN", links[ref]["mpn"]), ("Manufacturer", links[ref]["manufacturer"])):
            body.append(f'  (property {q(k)} {q(v)} (at {n(X(p["x"]))} {n(Y(p["y"]))} 0){font(hide=True)})')
        for pin in p["pins"]:
            body.append(f'  (pin {q(pin["num"])} (uuid {q(sid("pin", ref, pin["num"]))}))')
        body += [inst(ref), ")"]

    def power_symbol(lib, value, x, y, rot, key):
        ref = f"#FLG{len(power_refs) + 1:03d}" if lib == "PWR_FLAG" else f"#PWR{len(power_refs) + 1:03d}"
        power_refs.append(ref)
        _, vx, vy = power[lib]
        dx, dy = rot_offset(vx, vy, rot)
        u = sid("power", key)
        body.append(f'(symbol (lib_id "power:{lib}") (at {n(X(x))} {n(Y(y))} {rot}) (unit 1) '
                    f'(exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no) (uuid {q(u)})')
        body.append(f'  (property "Reference" {q(ref)} (at {n(X(x))} {n(Y(y))} 0){font(hide=True)})')
        body.append(f'  (property "Value" {q(value)} (at {n(X(x) + dx)} {n(Y(y) + dy)} 0){font()})')
        for k in ("Footprint", "Datasheet", "Description"):
            body.append(f'  (property {q(k)} "" (at {n(X(x))} {n(Y(y))} 0){font(hide=True)})')
        body.extend([f'  (pin "1" (uuid {q(sid("power-pin", key))}))', inst(ref), ")"])

    for i, f in enumerate(sheet["flags"]):
        if f["kind"] == "part_netLabel_gnD":
            power_symbol("GND", f["name"] or "GND", f["x"], f["y"], POWER_ROT["GND"][f["side"]], f"flag{i}")
        elif f["kind"].startswith("part_netLabel_") and f["kind"] != "part_netLabel_netPort":
            power_symbol("VCC", f["name"] or "VCC", f["x"], f["y"], POWER_ROT["VCC"][f["side"]], f"flag{i}")
        else:
            rot, just = LABEL_ROT[f["side"]]
            body.append(f'(global_label {q(f["name"])} (shape passive) (at {n(X(f["x"]))} {n(Y(f["y"]))} {rot}) '
                        f'(fields_autoplaced yes){font(justify=just)} (uuid {q(sid("label", i))}))')

    fx, fy = 10 * round(bb["x"] / 10) + 60, 10 * round(bb["y"] / 10) - 40
    for i, (pname, lib) in enumerate(power_nets):
        x = fx + 60 * i
        power_symbol(lib, pname, x, fy, 0, f"pwrflag-{pname}")
        power_symbol("PWR_FLAG", "PWR_FLAG", x, fy, 0 if lib == "GND" else 180, f"pwrflag-flag-{pname}")
    if power_nets:
        body.append(f'(text "Power enters through the connectors (PWR_FLAGs for ERC)" (exclude_from_sim no) '
                    f'(at {n(X(fx + 60 * len(power_nets)))} {n(Y(fy))} 0){font(justify="left")} (uuid {q(sid("pwrflag-text"))}))')

    pts = {p for w in sheet["wires"] for p in w} | {(pin["x"], pin["y"]) for p in sheet["parts"] for pin in p["pins"]} \
        | {(f["x"], f["y"]) for f in sheet["flags"]} | set(sheet["nc"])
    wires = split_wires(sheet["wires"], pts)
    ends = Counter(p for w in wires for p in w) + Counter((pin["x"], pin["y"]) for p in sheet["parts"] for pin in p["pins"])
    for i, (a, b) in enumerate(wires):
        body.append(f'(wire (pts (xy {n(X(a[0]))} {n(Y(a[1]))}) (xy {n(X(b[0]))} {n(Y(b[1]))})) '
                    f'(stroke (width 0) (type default)) (uuid {q(sid("wire", i))}))')
    junctions = {p for p, c in ends.items() if c >= 3} | set(sheet["junctions"])
    for p in sorted(junctions):
        body.append(f'(junction (at {n(X(p[0]))} {n(Y(p[1]))}) (diameter 0) (color 0 0 0 0) '
                    f'(uuid {q(sid("junction", *p))}))')
    for x, y in sheet["nc"]:
        body.append(f'(no_connect (at {n(X(x))} {n(Y(y))}) (uuid {q(sid("nc", x, y))}))')
    for i, (x, y, w, h) in enumerate(sheet["rects"]):
        body.append(f'(rectangle (start {n(X(x))} {n(Y(y))}) (end {n(X(x + w))} {n(Y(y + h))}) '
                    f'(stroke (width 0) (type default)) (fill (type none)) (uuid {q(sid("rect", i))}))')
    for i, t in enumerate(sheet["texts"]):
        size = max(1.27, round(float(t["size"].rstrip("pt")) * U * 0.8, 2)) if t["size"] else 1.27
        just = {"start": "left", "end": "right", "middle": None}[t["anchor"]]
        body.append(f'(text {q(t["text"])} (exclude_from_sim no) (at {n(X(t["x"]))} {n(Y(t["y"]) - size)} {t["rot"]})'
                    f'{font(size, justify=(just + " top") if just else "top", bold=t["bold"])} '
                    f'(uuid {q(sid("text", i))}))')
    for i, im in enumerate(sheet["images"]):
        if not im["href"].startswith("data:image/png;base64,"):
            note("non-PNG image skipped")
            continue
        b64 = im["href"].split(",", 1)[1]
        w, h, ppi = png_size(base64.b64decode(b64))
        scale = im["w"] * U / (w / ppi * 25.4)
        lines = "\n".join(f"    {b64[j:j + 76]}" for j in range(0, len(b64), 76))
        body.append(f'(image (at {n(X(im["x"] + im["w"] / 2))} {n(Y(im["y"] + im["h"] / 2))}) (scale {scale:.4f}) '
                    f'(uuid {q(sid("image", i))})\n  (data\n{lines}\n  )\n)')

    used = sorted({sym_of[r] for r in links})
    lib = [defs[k].replace('(symbol "', f'(symbol "{nick}:', 1) for k in used]
    lib += [power[k][0].strip() for k in ("GND", "VCC", "PWR_FLAG")]
    head = [f'(kicad_sch (version 20250114) (generator "pcbkit_import_easyeda") (generator_version "10.0") (uuid {q(sheet_uuid)})',
            f'(paper "User" {width} {height})', title_block(meta), "(lib_symbols", *lib, ")"]
    tail = ['(sheet_instances (path "/" (page "1")))'] if inst_path == "/" + sheet_uuid else []  # only the root has them
    sch_path.write_text("\n".join(head + body + tail + ["(embedded_fonts no)", ")"]) + "\n")
    return links


# ---------------------------------------------------------------- PCB

def footprint_signature(pcbnew, fp):
    """The pads of a footprint as the library holds it (unrotated, on top), sorted: (number, x, y, width, height, drill
    width, drill height, copper x and y from the hole, angle, shape, type, drill shape, layers, corner ratio, copper
    outline, back copper, margins, inner copper). Everything a fab makes from a pad is in it, so footprints that differ
    in a hole size, a pad type, a pad's angle, a custom pad's copper or a paste or mask margin compare different. The
    outline (each outline's points in order, with its holes', in the library's frame) is there for the shapes size and
    angle don't describe: custom pads (EasyEDA's polygon pads, whose size is only their anchor's), chamfered rectangles
    and trapezoids. Back and inner copper are there for a pad whose padstack gives other layers other copper (KiCad's
    front/inner/back and custom padstacks): each layer's shape, width, height, x and y from the hole, corner ratio and
    outline; a custom padstack's inner layers by their place counted from the library footprint's top (a flip on a board
    of more than two copper layers turns them over). KiCad keeps a custom padstack's inner layers differently on a board
    and in a library, so such a pad can compare different from its own library footprint: the import then makes a
    variant rather than reuse it. (EasyEDA's pads have no padstacks.) Margins are the pad's solder paste margin, paste
    margin ratio and solder mask margin: its own override, else the footprint's, else 0 (none, the board's own)."""
    o, a, flip = fp.GetPosition(), math.radians(fp.GetOrientationDegrees()), fp.IsFlipped()
    near, far = (pcbnew.B_Cu, pcbnew.F_Cu) if flip else (pcbnew.F_Cu, pcbnew.B_Cu)
    copper_count = fp.GetBoard().GetCopperLayerCount() if fp.GetBoard() else 32  # a library footprint: all of them
    inner_count = copper_count - 2
    turnable = (pcbnew.PAD_SHAPE_CIRCLE, pcbnew.PAD_SHAPE_RECTANGLE, pcbnew.PAD_SHAPE_OVAL, pcbnew.PAD_SHAPE_ROUNDRECT)
    drawn = (pcbnew.PAD_SHAPE_CUSTOM, pcbnew.PAD_SHAPE_CHAMFERED_RECT, pcbnew.PAD_SHAPE_TRAPEZOID)
    cornered = (pcbnew.PAD_SHAPE_ROUNDRECT, pcbnew.PAD_SHAPE_CHAMFERED_RECT)

    def local(v):
        """A board point in the library footprint's frame, mm: a flipped footprint is the library one mirrored in y."""
        dx, dy = pcbnew.ToMM(v.x - o.x), pcbnew.ToMM(v.y - o.y)
        x, y = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
        return (x, -y) if flip else (x, y)

    def ring(r):
        pts = (local(r.CPoint(i)) for i in range(r.PointCount()))
        return tuple((round(u, 3) + 0.0, round(v, 3) + 0.0) for u, v in pts)

    def copper(p, layer, swap, x, y):
        """A pad's copper on one layer: (shape, width, height, x and y from the hole, corner ratio, outline); width
        and height swapped when the pad's sides are."""
        shape, size = p.GetShape(layer), p.GetSize(layer)
        w, h = (round(pcbnew.ToMM(v), 3) for v in (size.x, size.y))
        cx, cy = local(p.ShapePos(layer))
        outline = None
        if shape in drawn:
            poly = p.GetEffectivePolygon(layer)
            outline = tuple(sorted(
                (ring(poly.Outline(k)), tuple(sorted(ring(poly.Hole(k, j)) for j in range(poly.HoleCount(k)))))
                for k in range(poly.OutlineCount())))
        return (shape, *((h, w) if swap else (w, h)), round(cx - x, 3) + 0.0, round(cy - y, 3) + 0.0,
                round(p.GetRoundRectRadiusRatio(layer), 3) if shape in cornered else None, outline)

    def inner_place(layer):
        """An inner copper layer's place counted from the library footprint's top (1 for In1): a flip on a board of
        more than two copper layers turns the inner layers over too, In1 to the last."""
        i = (int(layer) - pcbnew.In1_Cu) // 2 + 1
        return copper_count - 1 - i if flip and i <= inner_count else i

    def margins(p):
        out = []
        for get, mm in (("GetLocalSolderPasteMargin", True), ("GetLocalSolderPasteMarginRatio", False),
                        ("GetLocalSolderMaskMargin", True)):
            v = getattr(p, get)()
            v = getattr(fp, get)() if v is None else v
            out.append(0.0 if v is None else round(pcbnew.ToMM(v) if mm else v, 4) + 0.0)
        return tuple(out)

    pads = []
    for p in fp.Pads():
        x, y = local(p.GetPosition())
        shape, drill = p.GetShape(near), p.GetDrillSize()
        dw, dh = (round(pcbnew.ToMM(v), 3) for v in (drill.x, drill.y))
        angle = p.GetFPRelativeOrientation().AsDegrees()
        if flip:  # mirrored in y: the angles change sign and the layers swap sides
            angle = -angle
        # the outer copper and the other layers, then the inner copper: none, all the inner layers there are (KiCad
        # cuts a flipped pad's inner layers down to its board's, and a library footprint has all 30), or which ones,
        # counted from the library footprint's top
        layers = tuple(sorted(int(pcbnew.FlipLayer(l) if flip else l) for l in p.GetLayerSet().Seq()
                              if not pcbnew.IsInnerCopperLayer(l)))
        inner_cu = {inner_place(l) for l in p.GetLayerSet().Seq() if pcbnew.IsInnerCopperLayer(l)}
        if not inner_cu:
            layers += ("none",)
        elif set(range(1, (inner_count if inner_count > 0 else 30) + 1)) <= inner_cu:  # two layers: KiCad keeps 30
            layers += ("all",)
        else:
            layers += (tuple(sorted(inner_cu)),)
        stacked = p.Padstack().Mode() != pcbnew.PADSTACK.MODE_NORMAL
        # a rectangle, oval or circle at 180 degrees is the same pad, and at 90 the same as its sides swapped
        period = 180 if shape in turnable else 360
        angle %= period
        if period - angle < 0.05:
            angle = 0.0
        swap = shape in turnable and abs(angle - 90) < 0.05
        if swap:
            angle, dw, dh = 0.0, dh, dw
        angle = round(angle, 2)
        if shape == pcbnew.PAD_SHAPE_CIRCLE and dw == dh and not stacked:
            angle = 0.0
        _, w, h, ox, oy, corner, outline = copper(p, near, swap, x, y)
        back = copper(p, far, swap, x, y) if stacked else None
        inner = None
        if stacked and p.Padstack().Mode() == pcbnew.PADSTACK.MODE_FRONT_INNER_BACK:
            inner = ((0, copper(p, pcbnew.PADSTACK.INNER_LAYERS, swap, x, y)),)
        elif stacked:  # a custom padstack: each inner layer it defines, counted from the library footprint's top
            inner = tuple(sorted((inner_place(lay), copper(p, lay, swap, x, y)) for lay in p.Padstack().UniqueLayers()
                                 if lay not in (pcbnew.F_Cu, pcbnew.B_Cu)))
        holed = dw > 0 or dh > 0
        pads.append((p.GetNumber(), round(x, 3) + 0.0, round(y, 3) + 0.0, w, h, dw, dh, ox, oy, angle + 0.0, shape,
                     p.GetAttribute(), p.GetDrillShape() if holed else None, layers, corner, outline, back,
                     margins(p), inner))
    return tuple(sorted(pads, key=lambda t: t[:10]))


def same_pads(a, b, tol=0.02, angle_tol=0.1):
    """Whether two footprint signatures are the same pads: sizes, places, margins and outline points within tol mm,
    angles within angle_tol degrees, paste ratios within 0.001, everything else equal. Pads match one to one in any
    order (pads of one number can sort either way when their places differ by rounding), and outlines from any
    starting point and either way round (a flipped footprint's run the other way)."""
    def close(u, v):
        return all(abs(s - t) <= tol for s, t in zip(u, v))

    def same_ring(u, v):  # one closed outline: the same points in the same order, from any start, either way round
        n = len(u)
        if n != len(v):
            return False
        return n == 0 or any(all(close(u[i], w[(i + k) % n]) for i in range(n))
                             for w in (v, v[::-1]) for k in range(n) if close(u[0], w[k]))

    def matched(us, vs, same):  # one to one, in any order
        left = list(vs)
        for u in us:
            k = next((i for i, v in enumerate(left) if same(u, v)), None)
            if k is None:
                return False
            del left[k]
        return not left

    def same_copper(u, v):  # outlines, each with its holes
        return u == v or (u is not None and v is not None
                          and matched(u, v, lambda s, t: same_ring(s[0], t[0]) and matched(s[1], t[1], same_ring)))

    def same_layer(u, v):  # one layer's copper, as footprint_signature's copper() gives it
        return u == v or (u is not None and v is not None and u[0] == v[0] and u[5] == v[5]
                          and close(u[1:5], v[1:5]) and same_copper(u[6], v[6]))
    def same_inner(u, v):  # (inner layer, its copper) pairs
        return u == v or (u is not None and v is not None and [i for i, _ in u] == [i for i, _ in v]
                          and all(same_layer(s, t) for (_, s), (_, t) in zip(u, v)))

    def same_pad(p, r):
        return (p[0] == r[0] and close(p[1:9], r[1:9]) and abs(p[9] - r[9]) <= angle_tol and p[10:15] == r[10:15]
                and same_copper(p[15], r[15]) and same_layer(p[16], r[16]) and close(p[17][::2], r[17][::2])
                and abs(p[17][1] - r[17][1]) <= 0.001 and same_inner(p[18], r[18]))
    return len(a) == len(b) and (all(same_pad(p, r) for p, r in zip(a, b)) or matched(a, b, same_pad))


def ref_order(fp):
    """Sort key for footprints: by reference, numbers compared as numbers (R2 before R10), then by position.
    kicad-cli's import hands out fresh UUIDs and its footprint order changes from run to run; this one does not."""
    r, p = fp.GetReference(), fp.GetPosition()
    return [int(t) if i % 2 else t for i, t in enumerate(re.split(r"(\d+)", r))], r, p.x, p.y


def exemplar_rank(fp):
    """Which placement of a footprint goes into the library (lowest first): right-angle rotations, which un-rotate
    exactly (any other angle leaves float noise in every coordinate), then the top side, then unrotated."""
    a = round(fp.GetOrientationDegrees(), 3)
    return a % 90 != 0, fp.IsFlipped(), a % 360 != 0, ref_order(fp)


def drop_fields(path, pattern=r"JLC_3D[^\"]*"):
    """Remove footprint fields from a saved board or footprint file on the text (FOOTPRINT.Remove(field)
    corrupts the pcbnew bindings): by default EasyEDA's 3D-model ids, which mean nothing in KiCad."""
    text = Path(path).read_text()
    out, i = [], 0
    for m in re.finditer(r'\n\s*\(property "(?:' + pattern + r')"', text):
        if m.start() < i:
            continue
        depth, j = 0, m.start() + m.group(0).index("(")
        while True:
            depth += {"(": 1, ")": -1}.get(text[j], 0)
            if text[j] == '"':
                j = text.index('"', j + 1)
                while text[j - 1] == "\\":
                    j = text.index('"', j + 1)
            j += 1
            if depth == 0:
                break
        out.append(text[i:m.start()])
        i = j
    Path(path).write_text("".join(out) + text[i:])


def export_footprints(pcb_path, items, libdir, kept=(), fix=True):
    """Copy footprints from a saved board into the library, unrotated and on top. The library footprints the board
    uses as they are, kept [(reference, name)], first get the body their board copy has on fab if they have none,
    then, with fix, their courtyards redrawn if the check finds them wrong (the rest of each stays as it is).
    Prints what it did to the library as a JSON line: {"bodies": [...], "redrawn": [...], "bad": [...]}. Runs in
    its own process: after a footprint save the pcbnew bindings start returning untyped objects, so everything is
    drawn before any save."""
    import pcbnew
    from .checks import courtyards as cy
    io = pcbnew.PCB_IO_MGR.FindPlugin(pcbnew.PCB_IO_MGR.KICAD_SEXP)
    done, changed = {"bodies": [], "redrawn": [], "bad": []}, []
    if kept:
        b = pcbnew.LoadBoard(pcb_path)
        fps = {f.GetReference(): f for f in b.GetFootprints()}
        for ref, name in kept:
            fp, on_board = pcbnew.FootprintLoad(str(libdir), name), fps[ref]
            outlines = fab_outlines(pcbnew, on_board)
            drew = bool(outlines) and add_body(pcbnew, fp, footprint_pads(pcbnew, on_board), outlines,
                                               mirrored=on_board.IsFlipped())
            if drew:
                done["bodies"].append(name)
            if cy.findings(pcbnew, fp):
                done["bad"].append(name)
                if fix:
                    cy.redraw(pcbnew, fp)
                    done["redrawn"].append(name)
            if drew or name in done["redrawn"]:
                changed.append(fp)
    for fp in changed:
        io.FootprintSave(str(libdir), fp)
    b = pcbnew.LoadBoard(pcb_path)
    fps = {f.GetReference(): f for f in b.GetFootprints()}
    Path(libdir).mkdir(parents=True, exist_ok=True)
    keep = []
    board_inner = pcbnew.LSET()
    for i in range(b.GetCopperLayerCount() - 2):
        board_inner.AddLayer(pcbnew.In1_Cu + 2 * i)
    for ref, name in items:
        c = pcbnew.FOOTPRINT(fps[ref])
        keep.append(c)
        if c.IsFlipped():
            c.Flip(c.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
            # a flip on a board of more than two copper layers cuts a through-hole pad's inner copper down to the
            # board's: one that has all of those has copper on every layer, and the library footprint gets all 30
            for p in c.Pads() if board_inner.Seq() else ():
                layers = p.GetLayerSet()
                if all(layers.Contains(l) for l in board_inner.Seq()):
                    p.SetLayerSet(pcbnew.LSET(layers).AddLayerSet(pcbnew.LSET.InternalCuMask()))
        c.SetOrientationDegrees(0)
        c.SetPosition(pcbnew.VECTOR2I(0, 0))
        c.SetFPID(pcbnew.LIB_ID("", name))
        c.SetReference("REF**")
        c.SetPath(pcbnew.KIID_PATH())
        c.SetSheetname("")
        c.SetSheetfile("")
        io.FootprintSave(libdir, c)
        drop_fields(Path(libdir) / f"{name}.kicad_mod", r"JLC_3D[^\"]*|LCSC|MPN|Manufacturer")
        strip_user_keepouts(Path(libdir) / f"{name}.kicad_mod")
    print(json.dumps(done), flush=True)
    os._exit(0)


def read_netlist(sch_path):
    with tempfile.TemporaryDirectory() as t:
        out = Path(t) / "n.net"
        subprocess.run([KICAD_CLI, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", str(out), str(sch_path)],
                       check=True, capture_output=True)
        text = out.read_text()
    pins = {}
    for block in re.split(r"\(net\s+\(code", text)[1:]:
        name = re.search(r'\(name "((?:[^"\\]|\\.)*)"\)', block).group(1)
        for ref, pin in re.findall(r'\(node\s+\(ref "([^"]+)"\)\s+\(pin "([^"]+)"\)', block):
            pins[(ref, pin)] = name
    return pins


def easyeda_text_visibility(pcb_json):
    out = {}
    for x in json.load(open(pcb_json))["shape"]:
        if x.startswith("LIB~"):
            texts = {k[1]: k for k in (g.split("~") for g in x.split("#@$")) if k[0] == "TEXT" and k[1] in ("P", "N")}
            if "P" in texts:
                out[texts["P"][10]] = tuple(texts[t][12] != "none" if t in texts else False for t in ("P", "N"))
    return out


def _region_outlines(path):
    """An EasyEDA region's path ("M x y L x y ... Z", 10 mil units) as closed outlines in mm; [] for a path with
    curves, which the component-shape layer doesn't use."""
    if re.search(r"[A-DF-KN-Ya-df-z]", path):
        return []
    out = []
    for cmd, args in re.findall(r"([MLZ])([^MLZ]*)", path):
        v = [float(t) * U for t in re.findall(r"-?\d+(?:\.\d+)?(?:[eE]-?\d+)?", args)]
        if cmd == "M":
            out.append([])
        if cmd in "ML" and out:
            out[-1] += list(zip(v[0::2], v[1::2]))
    return [o for o in out if len(o) >= 3]


def easyeda_bodies(pcb_json):
    """{reference: (pads, outlines, back)} for the parts of an EasyEDA Std PCB export that draw their body on layer
    99, EasyEDA's component shape: pads {number: (x, y)} (numbers that occur once), outlines [[(x, y), ...]], in mm
    in EasyEDA's board frame, and whether the part sits on the bottom there (the LIB's layer, 2). KiCad's importer
    drops that layer, so its footprints show the body only in silk, which EasyEDA draws well off it."""
    out = {}
    with open(pcb_json) as f:
        shapes = json.load(f)["shape"]
    for x in shapes:
        if not x.startswith("LIB~"):
            continue
        ref, pads, outlines = None, defaultdict(list), []
        head = x.split("#@$")[0].split("~")
        back = len(head) > 7 and head[7] == "2"
        for k in (g.split("~") for g in x.split("#@$")[1:]):
            if k[0] == "TEXT" and len(k) > 10 and k[1] == "P":
                ref = k[10]
            elif k[0] == "PAD" and len(k) > 8 and k[8]:
                pads[k[8]].append((float(k[2]) * U, float(k[3]) * U))
            elif k[0] == "SOLIDREGION" and len(k) > 3 and k[1] == "99":
                outlines += _region_outlines(k[3])
        if ref and outlines:
            out[ref] = ({num: at[0] for num, at in pads.items() if len(at) == 1}, outlines, back)
    return out


def draw_bodies(pcbnew, footprints, bodies):
    """Draw easyeda_bodies() on the footprints with those references (add_body), mirrored when a part is on the
    other side now than in the export. Returns the references drawn."""
    return [fp.GetReference() for fp in footprints if fp.GetReference() in bodies
            and add_body(pcbnew, fp, *bodies[fp.GetReference()][:2],
                         mirrored=bodies[fp.GetReference()][2] != fp.IsFlipped())]


def footprint_pads(pcbnew, fp):
    """{number: (x, y)} in mm for the pads whose number occurs once."""
    at = defaultdict(list)
    for p in fp.Pads():
        if p.GetNumber():
            at[p.GetNumber()].append((pcbnew.ToMM(p.GetPosition().x), pcbnew.ToMM(p.GetPosition().y)))
    return {num: a[0] for num, a in at.items() if len(a) == 1}


def fit_pads(src, dst, mirrored=None, tol=0.02):
    """The rigid map (turned and shifted, and mirrored if mirrored is true) that takes pad centres src {number:
    (x, y)} onto dst, as a function of a point; None unless both have the same pads, two or more and not all in one
    place, and every one lands within tol mm: a footprint changed since (another land pattern that shares a pad or
    two) gets no body. Pads in a line fit mirrored or not alike, so the side a part changed to says which: mirrored
    None (side not known) tries unmirrored first."""
    keys = sorted(src)
    if set(src) != set(dst) or len(keys) < 2 or max(math.dist(src[keys[0]], src[k]) for k in keys) < 0.1:
        return None
    d = [dst[k] for k in keys]
    dx, dy = sum(p[0] for p in d) / len(d), sum(p[1] for p in d) / len(d)
    for m in ((1, -1) if mirrored is None else (-1,) if mirrored else (1,)):
        s = [(m * src[k][0], src[k][1]) for k in keys]
        sx, sy = sum(p[0] for p in s) / len(s), sum(p[1] for p in s) / len(s)
        a = math.atan2(sum((p[0] - sx) * (q[1] - dy) - (p[1] - sy) * (q[0] - dx) for p, q in zip(s, d)),
                       sum((p[0] - sx) * (q[0] - dx) + (p[1] - sy) * (q[1] - dy) for p, q in zip(s, d)))
        c, n = math.cos(a), math.sin(a)

        def f(p, m=m, sx=sx, sy=sy, c=c, n=n):
            x, y = m * p[0] - sx, p[1] - sy
            return dx + x * c - y * n, dy + x * n + y * c
        if max(math.dist(f(src[k]), dst[k]) for k in keys) <= tol:
            return f
    return None


def add_body(pcbnew, fp, pads, outlines, mirrored=None):
    """Draw a body's outlines on the footprint's fab layer (B.Fab if it is on the back), placed by fitting pads onto
    the footprint's own: pads {number: (x, y)} and outlines [[(x, y), ...]] in mm, in any one frame; mirrored when
    that frame is the footprint's other side (fit_pads). Nothing is drawn, and False returned, if the footprint
    already has a fab drawing or the pads don't fit."""
    if any(isinstance(g, pcbnew.PCB_SHAPE) and g.GetLayer() in (pcbnew.F_Fab, pcbnew.B_Fab)
           for g in fp.GraphicalItems()):
        return False
    f = fit_pads(pads, footprint_pads(pcbnew, fp), mirrored)
    if not f:
        return False
    for o in outlines:
        s = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_POLY)
        corners = pcbnew.VECTOR_VECTOR2I()
        for x, y in map(f, o):
            corners.append(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
        s.SetPolyPoints(corners)
        s.SetLayer(pcbnew.B_Fab if fp.IsFlipped() else pcbnew.F_Fab)
        s.SetWidth(pcbnew.FromMM(0.1))  # KiCad's libraries' fab line
        s.SetFilled(False)
        fp.Add(s)
    return True


def fab_outlines(pcbnew, fp):
    """A footprint's fab polygons as [[(x, y), ...]] in mm: what add_body drew, to copy onto another footprint."""
    out = []
    for g in fp.GraphicalItems():
        if isinstance(g, pcbnew.PCB_SHAPE) and g.GetLayer() in (pcbnew.F_Fab, pcbnew.B_Fab) \
                and g.GetShape() == pcbnew.SHAPE_T_POLY:
            ps = g.GetPolyShape()
            for i in range(ps.OutlineCount()):
                o = ps.Outline(i)
                out.append([(pcbnew.ToMM(o.CPoint(k).x), pcbnew.ToMM(o.CPoint(k).y)) for k in range(o.PointCount())])
    return out


LAYER_NAMES = {"F_Cu": "F.Cu", "B_Cu": "B.Cu", "F_SilkS": "F.SilkS", "B_SilkS": "B.SilkS", "F_Mask": "F.Mask",
               "B_Mask": "B.Mask", "F_Paste": "F.Paste", "B_Paste": "B.Paste", "Edge_Cuts": "Edge.Cuts",
               "F_Fab": "F.Fab", "B_Fab": "B.Fab", "F_CrtYd": "F.CrtYd", "B_CrtYd": "B.CrtYd", "Cmts_User": "Cmts.User",
               "Dwgs_User": "Dwgs.User", "In1_Cu": "In1.Cu", "In2_Cu": "In2.Cu"}


def close_outline(pcbnew, board, tol=0.05):
    """Join Edge.Cuts line and arc ends that miss each other by less than tol mm: EasyEDA outlines drawn in mil
    leave gaps of a hundredth of a millimetre, which DRC reports as invalid_outline. Only ends that meet no other end
    are moved, and of two ends the line's rather than the arc's, so short segments keep their length.
    Returns [(gap mm, x mm, y mm)], where each gap was closed."""
    shapes = [g for g in list(board.GetDrawings()) + [g for fp in board.GetFootprints() for g in fp.GraphicalItems()]
              if isinstance(g, pcbnew.PCB_SHAPE) and g.GetLayer() == pcbnew.Edge_Cuts
              and g.GetShape() in (pcbnew.SHAPE_T_SEGMENT, pcbnew.SHAPE_T_ARC)]
    ends = [(i, k, (p.x, p.y)) for i, g in enumerate(shapes) for k, p in ((0, g.GetStart()), (1, g.GetEnd()))]
    seen = Counter(xy for _, _, xy in ends)
    loose = [e for e in ends if seen[e[2]] == 1]
    lim = pcbnew.FromMM(tol)
    pairs = sorted((math.dist(a[2], c[2]), i, j) for i, a in enumerate(loose) for j, c in enumerate(loose[:i])
                   if a[0] != c[0])
    done, out = set(), []
    for d, i, j in pairs:
        if d > lim:
            break
        if i in done or j in done:
            continue
        done |= {i, j}
        a, c = loose[i], loose[j]
        if shapes[a[0]].GetShape() != pcbnew.SHAPE_T_SEGMENT:
            a, c = c, a
        g, to = shapes[a[0]], pcbnew.VECTOR2I(*c[2])
        if g.GetShape() == pcbnew.SHAPE_T_SEGMENT:
            (g.SetEnd if a[1] else g.SetStart)(to)
        elif a[1]:
            g.SetArcGeometry(g.GetStart(), g.GetArcMid(), to)
        else:
            g.SetArcGeometry(to, g.GetArcMid(), g.GetEnd())
        out.append((pcbnew.ToMM(int(round(d))), pcbnew.ToMM(c[2][0]), pcbnew.ToMM(c[2][1])))
    return out


def unlinked_footprint(pcbnew, fp, is_part):
    """A footprint with no symbol: (problem or None, its nets). A part of the EasyEDA PCB (is_part) that carries
    nets is a real difference from the schematic. Anything else is made board-only, out of the BOM and the
    placement files: a part with no nets (a logo, a mounting hole), and the loose pads and holes EasyEDA keeps
    outside any part, which KiCad's importer wraps in a footprint each (a GND test pad keeps its net)."""
    nets = sorted({p.GetNetname() for p in fp.Pads() if p.GetNetCode() > 0})
    if is_part and nets:
        return f"{fp.GetReference()} on the PCB has no symbol (and carries nets)", nets
    fp.SetBoardOnly(True)
    fp.SetExcludedFromBOM(True)
    fp.SetExcludedFromPosFiles(True)
    return None, nets


def pad_findings(pcbnew, fp, sch_pins):
    """Each pad of a footprint that has a symbol, against the symbol's pins: (pads [(pad, schematic net)],
    problems, unpinned pad numbers). Pads without a number and NPTH holes (a switch's or a USB-C socket's pegs)
    have nothing to connect and are skipped. A pad the symbol has no pin for is a problem when it carries a net on
    the PCB; unconnected there too (a shell pad the symbol leaves out), it is only listed."""
    ref, pads, problems, unpinned = fp.GetReference(), [], [], []
    for p in fp.Pads():
        num = p.GetNumber()
        if not num or p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
            continue
        new = sch_pins.get((ref, num))
        pads.append((p, new))
        if new is None:
            if p.GetNetCode() > 0:
                problems.append(f"{ref} pad {num} has no schematic pin (on the PCB it is on {p.GetNetname()})")
            else:
                unpinned.append(num)
    return pads, problems, unpinned


def set_footprint_fields(out_dir, links, names, nick):
    """Point the schematic's Footprint fields at the names the footprints got in the library ({ref: name}), on the
    text of the sheets written before the board was read."""
    by_file = defaultdict(list)
    for ref, name in names.items():
        by_file[links[ref]["sheetfile"]].append((links[ref]["uuid"], name))
    for file, items in by_file.items():
        path = Path(out_dir) / file
        text = path.read_text()
        for u, name in items:
            pat = re.compile(r'(\(uuid ' + re.escape(q(u)) + r'\)\n(?:  .*\n)*?  \(property "Footprint" )' + _STR)
            text = pat.sub(lambda m: m.group(1) + q(f"{nick}:{name}"), text, count=1)
        path.write_text(text)


def update_board(pcb_path, links, sch_pins, nick, meta, force, shown, pretty, keep_courtyards=False, bodies=None):
    """Link the imported board to the schematic and correct what the importer leaves. shown: {ref: (reference
    shown, value shown)} for every part of the EasyEDA PCB; pretty: the footprint library the exports go to;
    bodies: easyeda_bodies() of the PCB, drawn on each part's fab. Courtyards that miss the pads or the body are
    then redrawn (listed only, with keep_courtyards)."""
    ensure_pcbnew()
    import pcbnew
    from .checks import courtyards as cy
    b = pcbnew.LoadBoard(str(pcb_path))
    problems, board_only, loose_nets, fp_refs, paste_fixed = [], [], [], set(), 0
    signatures, placements, fp_names, lib_sigs = {}, defaultdict(list), {}, {}

    def lib_signature(name):
        """The pads of the library's footprint of that name: None if there is none, False if it won't load."""
        if name not in lib_sigs:
            lib_sigs[name] = None
            if (Path(pretty) / f"{name}.kicad_mod").exists():
                try:
                    lib_fp = pcbnew.FootprintLoad(str(pretty), name)
                except Exception:  # an IO_ERROR from a file this KiCad can't read
                    lib_fp = None
                lib_sigs[name] = footprint_signature(pcbnew, lib_fp) if lib_fp else False
        return lib_sigs[name]

    for fp in sorted(b.GetFootprints(), key=ref_order):
        ref = fp.GetReference()
        if ref not in links:
            problem, nets = unlinked_footprint(pcbnew, fp, ref in shown)
            if problem:
                problems.append(problem)
                continue
            board_only.append(ref)
            if nets:
                loose_nets.append(f"{ref} ({', '.join(nets)})")
            continue
        fp_refs.add(ref)
        link = links[ref]
        base = link["footprint"] or safe_name(str(fp.GetFPID().GetLibItemName()))
        for p in fp.Pads():  # before the signature, which holds the paste margin: the library gets the fixed pads
            m = p.GetLocalSolderPasteMargin()
            if m is not None and m < -pcbnew.FromMM(1):
                p.SetLocalSolderPasteMargin(None)
                paste_fixed += 1
        sig = footprint_signature(pcbnew, fp)
        name, k = base, 1
        while True:
            known = signatures[name] if name in signatures else lib_signature(name)
            if known is None or (known is not False and same_pads(known, sig)):
                break
            note("a footprint name covers two land patterns; the second one got a _2 suffix" if name in signatures
                 else "a footprint differs from the library's one of that name; it got a _N suffix")
            k += 1
            name = f"{base}_{k}"
        signatures.setdefault(name, sig)
        placements[name].append((exemplar_rank(fp), ref))
        if name != link["footprint"]:
            fp_names[ref] = name
        fp.SetFPID(pcbnew.LIB_ID(nick, name))
        fp.SetPath(pcbnew.KIID_PATH(link["path"]))
        fp.SetSheetfile(link["sheetfile"])
        fp.SetSheetname(link["sheetname"])
        fp.SetValue(link["value"])
        for k, v in (("Description", link["desc"]), ("LCSC", link["lcsc"]), ("MPN", link["mpn"]), ("Manufacturer", link["manufacturer"])):
            fp.SetField(k, v)
            f = fp.GetField(k)
            f.SetVisible(False)
            f.SetLayer(pcbnew.B_Fab if fp.IsFlipped() else pcbnew.F_Fab)
        ref_shown, value_shown = shown.get(ref, (True, False))
        fp.Reference().SetVisible(ref_shown)
        fp.Value().SetVisible(value_shown)
    schematic_only = sorted(set(links) - fp_refs, key=lambda r: [int(t) if i % 2 else t for i, t in enumerate(re.split(r"(\d+)", r))])
    # a footprint the library has with the same pads stays as it is: another board may have put it there
    exports = [(min(p)[1], name) for name, p in placements.items() if lib_sigs.get(name) is None]
    # the library's own, used as they are: export_footprints gives them the body and checks their courtyards
    lib_kept = sorted((min(p)[1], name) for name, p in placements.items() if lib_sigs.get(name))

    old_to_new, new_to_old, pads, unpinned = defaultdict(set), defaultdict(set), [], []
    for fp in sorted(b.GetFootprints(), key=ref_order):
        if fp.GetReference() not in fp_refs:
            continue
        fp_pads, fp_problems, fp_unpinned = pad_findings(pcbnew, fp, sch_pins)
        pads += fp_pads
        problems += fp_problems
        if fp_unpinned:
            unpinned.append(f"{fp.GetReference()} {', '.join(fp_unpinned)}")
        for p, new in fp_pads:
            if new is not None and p.GetNetCode() > 0:
                old_to_new[p.GetNetCode()].add(new)
                new_to_old[new].add(p.GetNetname())
    problems += [f"PCB net {b.FindNet(c).GetNetname()} joins schematic nets {sorted(v)}" for c, v in old_to_new.items() if len(v) > 1]
    problems += [f"schematic net {k} is split on the PCB: {sorted(v)}" for k, v in new_to_old.items() if len(v) > 1]
    if problems:
        print("connectivity problems:\n  " + "\n  ".join(problems))
        if not force:
            sys.exit("stopping; rerun with --force to write the project anyway (KiCad's Update PCB will then show the differences)")
    renamed = 0
    for code, new in old_to_new.items():
        if len(new) != 1:
            continue
        net = b.FindNet(code)
        if net.GetNetname() != next(iter(new)):
            net.SetNetname(next(iter(new)))
            renamed += 1
    for p, new in pads:
        if p.GetNetCode() == 0 and new:
            net = b.FindNet(new) or pcbnew.NETINFO_ITEM(b, new)
            if net.GetNetCode() <= 0:
                b.Add(net)
            p.SetNet(net)

    seen, dupes = set(), []
    for t in b.GetTracks():
        if t.GetClass() == "PCB_VIA":
            key = (t.GetPosition().x, t.GetPosition().y, t.GetNetCode(), t.GetWidth(pcbnew.F_Cu), t.GetDrillValue())
            (dupes.append(t) if key in seen else seen.add(key))
    for t in dupes:
        b.Delete(t)
    for z in b.Zones():
        z.SetMinThickness(pcbnew.FromMM(0.15))
        z.SetThermalReliefSpokeWidth(pcbnew.FromMM(0.2))
    # KiCad's importer drops EasyEDA's component shapes (layer 99), leaving the body only in silk drawn well off it
    drawn = draw_bodies(pcbnew, [fp for fp in sorted(b.GetFootprints(), key=ref_order)
                                 if fp.GetReference() in fp_refs], bodies or {})
    # the courtyards KiCad's importer leaves are often turned against the pads, a sliver, or round the pins and
    # not the body. Redrawn before the save, so the library exports get them too.
    bad_courtyards, removed_courtyards = [], defaultdict(list)
    for fp in sorted(b.GetFootprints(), key=ref_order):
        if fp.GetReference() in fp_refs and cy.findings(pcbnew, fp):
            bad_courtyards.append(fp.GetReference())
            if not keep_courtyards:
                for part in cy.redraw(pcbnew, fp).split(", "):
                    if part.endswith(" removed"):
                        removed_courtyards[part.split()[0]].append(fp.GetReference())
    gaps = close_outline(pcbnew, b)
    tb = b.GetTitleBlock()
    tb.SetTitle(meta["title"])
    tb.SetRevision(meta["rev"])
    tb.SetCompany(meta["company"])
    tb.SetDate(meta["date"])
    tb.SetComment(0, "Converted from the EasyEDA Std export by pcbkit import-easyeda")
    for attr, name in LAYER_NAMES.items():
        if hasattr(pcbnew, attr) and b.IsLayerEnabled(getattr(pcbnew, attr)):
            b.SetLayerName(getattr(pcbnew, attr), name)
    pcbnew.PCB_IO_MGR.Save(pcbnew.PCB_IO_MGR.KICAD_SEXP, str(pcb_path), b)
    drop_fields(pcb_path)
    keepouts = strip_user_keepouts(pcb_path)
    return dict(renamed=renamed, paste_fixed=paste_fixed, duplicate_vias=len(dupes), board_only=board_only, exports=exports,
                problems=problems, schematic_only=schematic_only, loose_nets=loose_nets, unpinned=unpinned,
                fp_names=fp_names, gaps=gaps, keepouts=keepouts, footprints=len(placements),
                courtyards=bad_courtyards, courtyards_kept=keep_courtyards, lib_kept=lib_kept,
                courtyards_removed=dict(removed_courtyards), bodies=drawn)


# ---------------------------------------------------------------- project

def write_project(kdir, project, root, subs, nick, lib_rel):
    (kdir / f"{project}.kicad_pro").write_text(json.dumps({
        "board": {"design_settings": {"rule_severities": RULE_SEVERITIES, "rules": PCB_RULES}},
        "meta": {"filename": f"{project}.kicad_pro", "version": 1},
        "net_settings": {"classes": [NET_CLASS], "meta": {"version": 3}},
        "sheets": [[root, "Root"]] + [[su, name] for su, name in subs],
    }, indent=2) + "\n")
    (kdir / "sym-lib-table").write_text(
        f'(sym_lib_table\n  (version 7)\n  (lib (name {q(nick)})(type "KiCad")(uri "${{KIPRJMOD}}/{lib_rel}/{nick}.kicad_sym")'
        '(options "")(descr "Project symbols"))\n)\n')
    (kdir / "fp-lib-table").write_text(
        f'(fp_lib_table\n  (version 7)\n  (lib (name {q(nick)})(type "KiCad")(uri "${{KIPRJMOD}}/{lib_rel}/{nick}.pretty")'
        '(options "")(descr "Project footprints"))\n)\n')


def convert(sch_json, pcb_json, out_dir, name, lib_dir=None, nick=None, meta=None, force=False, pin_fixes=(),
            keep_courtyards=False):
    ensure_pcbnew()  # first: it runs the command again under KiCad's Python, which must not find a half-merged library
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    lib_dir = Path(lib_dir).resolve() if lib_dir else (out_dir.parent.parent / "lib")
    nick = nick or lib_dir.parent.name
    lib_rel = os.path.relpath(lib_dir, out_dir)
    sch = load_schematic(sch_json, pin_fixes)
    meta = {**dict(title=sch["title"] or name, rev="", date="", company=""), **(meta or {})}
    power = power_symbols()
    sym_lib, pretty = lib_dir / f"{nick}.kicad_sym", lib_dir / f"{nick}.pretty"
    existing = read_symbol_lib(sym_lib)
    sym_of, defs, sym_stats = register_symbols(sch, nick, existing)
    lib_dir.mkdir(parents=True, exist_ok=True)
    write_symbol_lib(sym_lib, existing, defs)
    shown = easyeda_text_visibility(pcb_json)
    sch_path = out_dir / f"{name}.kicad_sch"
    root, subs, links = write_schematic(sch, out_dir, sym_of, defs, power, name, nick, meta, set(shown))
    write_project(out_dir, name, root, subs, nick, lib_rel)
    pcb_path = out_dir / f"{name}.kicad_pcb"
    r = subprocess.run([KICAD_CLI, "pcb", "import", "-o", str(pcb_path), str(pcb_json)], capture_output=True, text=True)
    if r.returncode or not pcb_path.exists():
        sys.exit("kicad-cli pcb import failed:\n" + quiet(r.stdout + r.stderr)[-800:])
    fixes = update_board(pcb_path, links, read_netlist(sch_path), nick, meta, force, shown, pretty, keep_courtyards,
                         easyeda_bodies(pcb_json))
    if fixes["fp_names"]:
        set_footprint_fields(out_dir, links, fixes["fp_names"], nick)
    r = subprocess.run([KICAD_PYTHON, "-c", "import sys, json; from pcbkit.import_easyeda import export_footprints; "
                        "export_footprints(sys.argv[1], json.loads(sys.argv[2]), sys.argv[3], json.loads(sys.argv[4]), "
                        "sys.argv[5] == '1')",
                        str(pcb_path), json.dumps(fixes["exports"]), str(pretty), json.dumps(fixes["lib_kept"]),
                        "0" if keep_courtyards else "1"],
                       check=True, capture_output=True, text=True,
                       env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])))
    fixes["lib"] = json.loads(r.stdout.strip().splitlines()[-1])
    count = lambda k: sum(len(s[k]) for s in sch["sheets"])
    sheets = f"{len(subs)} sheets, " if subs else ""
    print(f"{name}: {sheets}{count('parts')} parts, {len(defs)} symbols, {fixes['footprints']} footprints, {count('wires')} wires, "
          f"{count('flags')} labels/flags; {fixes['renamed']} PCB nets renamed, {fixes['paste_fixed']} pads given paste, "
          f"{fixes['duplicate_vias']} duplicate vias removed, {len(fixes['board_only'])} board-only footprints "
          f"(loose pads, holes)")
    for line in report_lines(fixes, sym_stats, links, sym_lib):
        print("  " + line)
    for msg, c in NOTES.most_common():
        print(f"  note: {msg} (x{c})")
    print("lib:", sym_lib, "and", pretty)
    from .checks import drc, erc
    v = erc.run(str(sch_path))
    print("ERC:", dict(Counter((x["severity"], x["type"]) for x in v)) or "clean")
    d = drc.run(str(pcb_path), refill=True, save=True, parity=True)  # refill and save: the import's stored fill is stale
    print("DRC:", dict(Counter((x["severity"], x["type"]) for x in d["violations"])) or "clean")
    print("parity:", dict(Counter(x["type"] for x in d.get("schematic_parity", []))) or "clean", "| unconnected:", len(d["unconnected_items"]))
    return fixes


def report_lines(fixes, sym_stats, links, sym_lib):
    """What the import corrected or left, beyond the counts, one line each."""
    out = []
    if sym_stats["kept"]:
        out.append(f"library: {sym_stats['kept']} symbols already in {sym_lib.name} used as they are, "
                   f"{sym_stats['new']} added; its other symbols kept")
    if fixes["footprints"] > len(fixes["exports"]):
        out.append(f"library: {fixes['footprints'] - len(fixes['exports'])} footprints already in "
                   f"{sym_lib.stem}.pretty with the same pads used as they are, {len(fixes['exports'])} added")
    if sym_stats["renamed"]:
        out.append(f"library: {', '.join(sorted(sym_stats['renamed']))} in {sym_lib.name} have other pins; "
                   "this board's symbols of that name got a _N suffix")
    if fixes["keepouts"]:
        out.append(f"removed {fixes['keepouts']} copper-pour keep-outs on User layers inside footprints "
                   "(EasyEDA's pin outlines; DRC items_not_allowed)")
    for gap, x, y in fixes["gaps"]:
        out.append(f"outline: closed a {gap:.4f} mm gap between Edge.Cuts ends at ({x:.2f}, {y:.2f})")
    if fixes["courtyards"]:
        refs = ", ".join(fixes["courtyards"])
        out.append(f"courtyards that missed pads or the body, or sat on the other side, kept as imported: {refs} "
                   "(`pcbkit courtyards BOARD --fix` redraws them)" if fixes["courtyards_kept"] else
                   "courtyards redrawn round pads and body + 0.25 mm (the imported ones missed pads or the "
                   f"body, or sat on the other side): {refs}")
    for layer, refs in sorted(fixes["courtyards_removed"].items()):
        out.append(f"{layer} removed from parts whose only courtyard it was, on the other side: " + ", ".join(refs))
    if fixes["bodies"]:
        out.append("bodies drawn on F.Fab/B.Fab from EasyEDA's component shapes (KiCad's importer drops them), so "
                   f"courtyards go by the body, not the silk: {', '.join(fixes['bodies'])}")
    lib = fixes.get("lib", {})
    if lib.get("bodies"):
        out.append(f"library: bodies drawn in footprints already in {sym_lib.stem}.pretty, from their board copy: "
                   + ", ".join(lib["bodies"]))
    if lib.get("bad"):
        names = ", ".join(lib["bad"])
        out.append(f"library: courtyards that miss pads or the body in footprints already in {sym_lib.stem}.pretty, "
                   f"kept: {names} (`pcbkit courtyards {sym_lib.stem}.pretty --fix` redraws them)"
                   if fixes["courtyards_kept"] else
                   f"library: courtyards redrawn in footprints already in {sym_lib.stem}.pretty (the rest of each "
                   f"kept): {names}")
    if fixes["loose_nets"]:
        out.append("loose pads with a net, made board-only (no symbol, not in the BOM or placement files): "
                   + ", ".join(fixes["loose_nets"]))
    if fixes["unpinned"]:
        out.append("pads the symbol has no pin for, unconnected on the PCB too: " + "; ".join(fixes["unpinned"]))
    if fixes["schematic_only"]:
        parts = [f"{r} ({links[r]['package']})" if links[r]["package"] else r for r in fixes["schematic_only"]]
        out.append("schematic-only parts, no footprint on the PCB (Footprint field left empty; DRC parity lists them "
                   "as missing_footprint): " + ", ".join(parts))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("schematic", help="EasyEDA Std schematic export (SCH_*.json)")
    ap.add_argument("pcb", help="EasyEDA Std PCB export (PCB_*.json)")
    ap.add_argument("out_dir", help="KiCad project directory, e.g. boards/<name>")
    ap.add_argument("--name", required=True, help="KiCad project name (BOARD.kicad_pro)")
    ap.add_argument("--lib", help="library directory (default: <out_dir>/../../lib)")
    ap.add_argument("--lib-name", help="library nickname (default: the lib directory's parent folder name)")
    ap.add_argument("--title"); ap.add_argument("--rev", default=""); ap.add_argument("--date", default=""); ap.add_argument("--company", default="")
    ap.add_argument("--force", action="store_true", help="write the project even with connectivity differences")
    ap.add_argument("--keep-courtyards", action="store_true",
                    help="leave the imported courtyards as they are; only list the ones that miss pads or the body")
    ap.add_argument("--pin", action="append", default=[], metavar="REF:NAME=NUM",
                    help="renumber a symbol pin by its name, for EasyEDA symbol typos (repeatable)")
    a = ap.parse_args(argv)
    fixes = []
    for s in a.pin:
        m = re.fullmatch(r"([^:]+):(.+)=(\S+)", s)
        if not m:
            ap.error(f"--pin wants REF:NAME=NUM, got {s}")
        fixes.append(m.groups())
    meta = {k: v for k, v in (("title", a.title), ("rev", a.rev), ("date", a.date), ("company", a.company)) if v}
    convert(a.schematic, a.pcb, a.out_dir, a.name, a.lib, a.lib_name, meta, a.force, fixes, a.keep_courtyards)


if __name__ == "__main__":
    main()
