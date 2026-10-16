"""Turn EasyEDA Std exports (schematic + PCB JSON) into a linked KiCad project.

  pcbkit import-easyeda SCH.json PCB.json OUT_DIR --name BOARD [--lib LIBDIR] [--lib-name NICK]
                        [--title T] [--rev R] [--date D] [--company C] [--force]

KiCad imports EasyEDA PCBs from the command line, but schematics only in the GUI, and a GUI import leaves the
symbols unlinked from the footprints. This converts the schematic itself, keeping the EasyEDA layout:
  * one KiCad symbol per distinct EasyEDA symbol (name + pin geometry; rotated placements become variants, since
    EasyEDA stores pins already rotated), built from the pin geometry so the original wires land on their pins;
  * every sheet is converted: one sheet stays flat, several become a root page of sheet symbols, one file each;
  * net ports and net labels become global labels (EasyEDA joins both by name across all sheets), GND/VCC/+5V
    flags become power symbols named after the flag;
  * every footprint is linked to its symbol, the PCB nets are renamed to the schematic's, and any connectivity
    difference between the EasyEDA schematic and PCB is reported (and stops the run unless --force);
  * footprints go into LIBDIR/<nick>.pretty and symbols into LIBDIR/<nick>.kicad_sym, with project lib tables;
  * what KiCad's PCB importer gets wrong is corrected: -100 mm paste margins, every designator shown, duplicate
    vias, EasyEDA layer names, zone fill minimums; the zones are refilled and the board saved filled.
Loose pads and holes on the PCB (no symbol) become board-only footprints. Writes OUT_DIR/BOARD.kicad_{pro,sch,pcb}
and runs ERC and DRC with the schematic parity check. Harvested from dodeca's panels/tools/easyeda_to_kicad.py.
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

from .kicad import KICAD_APP, KICAD_CLI, KICAD_PYTHON, ensure_pcbnew, quiet

POWER_LIB = Path(KICAD_APP) / "Contents/SharedSupport/symbols/power.kicad_sym"
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


def register_symbols(sch, nick):
    """One library symbol per distinct (name, geometry). Returns ({ref: symbol name}, {name: definition})."""
    defs, geos, by_ref = {}, {}, {}
    for p in (p for s in sch["sheets"] for p in s["parts"]):
        base = safe_name(p["para"].get("spiceSymbolName") or p["para"].get("name") or p["fields"].get("N", {}).get("text", "part"))
        geo = symbol_geometry(p)
        fp = safe_name(p["para"].get("package", "")) if p["para"].get("package") else ""
        name, k = base, 1
        while name in geos and geos[name] != geo:
            k += 1
            name = f"{base}_{k}"
        if name not in geos:
            geos[name] = geo
            defs[name] = symbol_def(name, p, nick, fp)
        by_ref[p["fields"]["P"]["text"]] = name
    return by_ref, defs


def sheet_files(sheets, project):
    """(sheet name, file name) per EasyEDA sheet: unique, and no file clashing with the root PROJECT.kicad_sch."""
    names, files, out = set(), {f"{project}.kicad_sch".lower()}, []
    for i, s in enumerate(sheets):
        base = (s["name"].strip() or f"Sheet {i + 1}").replace("/", "_")  # KiCad sheet names can't hold a /
        name, k = base, 1
        while name in names:
            k += 1
            name = f"{base} {k}"
        stem = safe_name(name).lower()
        file, k = f"{stem}.kicad_sch", 1
        while file.lower() in files:
            k += 1
            file = f"{stem}_{k}.kicad_sch"
        names.add(name)
        files.add(file.lower())
        out.append((name, file))
    return out


def write_schematic(sch, out_dir, sym_of, defs, power, project, nick, meta):
    """OUT_DIR/PROJECT.kicad_sch. One EasyEDA sheet is the root itself; several become a root page of sheet symbols,
    one file each. Net ports and labels are global labels, so nets join by name across sheets as in EasyEDA.
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
                                     project, nick, meta, power_refs, power_nets).items():
            links[ref] = dict(link, path=f"/{link['uuid']}", sheetname="/", sheetfile=file)
        return root, subs, links
    pages = []
    for i, (sheet, (name, file)) in enumerate(zip(sch["sheets"], sheet_files(sch["sheets"], project))):
        su = uid(project, "sheet", i)
        for ref, link in write_sheet(sheet, out_dir / file, uid(project, "sheet-file", i), f"/{root}/{su}",
                                     ("page", i) if i else (), sym_of, defs, power, project, nick, meta, power_refs,
                                     power_nets if i == 0 else ()).items():
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
                power_nets):
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
        fp = f"{nick}:{safe_name(para['package'])}" if para.get("package") else ""
        u = sid("sym", ref)
        links[ref] = dict(uuid=u, lcsc=para.get("Supplier Part", ""), value=p["fields"].get("N", {}).get("text", ""),
                          mpn=para.get("Manufacturer Part", ""), manufacturer=para.get("Manufacturer", ""),
                          desc=para.get("name", ""), footprint=safe_name(para.get("package", "")))
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
    o, a = fp.GetPosition(), math.radians(fp.GetOrientationDegrees())
    pads = []
    for p in fp.Pads():
        dx, dy = pcbnew.ToMM(p.GetPosition().x - o.x), pcbnew.ToMM(p.GetPosition().y - o.y)
        x, y = dx * math.cos(a) - dy * math.sin(a), dx * math.sin(a) + dy * math.cos(a)
        if fp.IsFlipped():
            x = -x
        layer = pcbnew.B_Cu if fp.IsFlipped() else pcbnew.F_Cu
        size = p.GetSize(layer)
        pads.append((p.GetNumber(), round(x, 3) + 0.0, round(y, 3) + 0.0, round(pcbnew.ToMM(size.x), 3),
                     round(pcbnew.ToMM(size.y), 3), p.GetShape(layer)))
    return tuple(sorted(pads))


def same_pads(a, b, tol=0.02):
    return len(a) == len(b) and all(p[0] == r[0] and p[5] == r[5] and all(abs(u - v) <= tol for u, v in zip(p[1:5], r[1:5]))
                                    for p, r in zip(a, b))


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


def export_footprints(pcb_path, items, libdir):
    """Copy footprints from a saved board into the library, unrotated and on top. Runs in its own process:
    after a footprint save, the pcbnew bindings start returning untyped objects."""
    import pcbnew
    io = pcbnew.PCB_IO_MGR.FindPlugin(pcbnew.PCB_IO_MGR.KICAD_SEXP)
    b = pcbnew.LoadBoard(pcb_path)
    fps = {f.GetReference(): f for f in b.GetFootprints()}
    Path(libdir).mkdir(parents=True, exist_ok=True)
    keep = []
    for ref, name in items:
        c = pcbnew.FOOTPRINT(fps[ref])
        keep.append(c)
        if c.IsFlipped():
            c.Flip(c.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        c.SetOrientationDegrees(0)
        c.SetPosition(pcbnew.VECTOR2I(0, 0))
        c.SetFPID(pcbnew.LIB_ID("", name))
        c.SetReference("REF**")
        c.SetPath(pcbnew.KIID_PATH())
        io.FootprintSave(libdir, c)
        drop_fields(Path(libdir) / f"{name}.kicad_mod", r"JLC_3D[^\"]*|LCSC|MPN|Manufacturer")
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


LAYER_NAMES = {"F_Cu": "F.Cu", "B_Cu": "B.Cu", "F_SilkS": "F.SilkS", "B_SilkS": "B.SilkS", "F_Mask": "F.Mask",
               "B_Mask": "B.Mask", "F_Paste": "F.Paste", "B_Paste": "B.Paste", "Edge_Cuts": "Edge.Cuts",
               "F_Fab": "F.Fab", "B_Fab": "B.Fab", "F_CrtYd": "F.CrtYd", "B_CrtYd": "B.CrtYd", "Cmts_User": "Cmts.User",
               "Dwgs_User": "Dwgs.User", "In1_Cu": "In1.Cu", "In2_Cu": "In2.Cu"}


def update_board(pcb_json, pcb_path, links, sch_pins, nick, meta, force):
    ensure_pcbnew()
    import pcbnew
    shown = easyeda_text_visibility(pcb_json)
    b = pcbnew.LoadBoard(str(pcb_path))
    problems, board_only, fp_refs, paste_fixed = [], [], set(), 0
    signatures, exports = {}, []
    for fp in b.GetFootprints():
        ref = fp.GetReference()
        if ref not in links:
            pads = list(fp.Pads())
            if not pads or all(p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH for p in pads) or all(p.GetNetCode() == 0 for p in pads):
                fp.SetBoardOnly(True)
                fp.SetExcludedFromBOM(True)
                fp.SetExcludedFromPosFiles(True)
                board_only.append(ref)
                continue
            problems.append(f"{ref} on the PCB has no symbol (and carries nets)")
            continue
        fp_refs.add(ref)
        link = links[ref]
        name = link["footprint"] or safe_name(str(fp.GetFPID().GetLibItemName()))
        sig = footprint_signature(pcbnew, fp)
        if name not in signatures:
            signatures[name] = sig
            exports.append((ref, name))
        elif not same_pads(signatures[name], sig):
            k = 2
            while f"{name}_{k}" in signatures and not same_pads(signatures[f"{name}_{k}"], sig):
                k += 1
            name = f"{name}_{k}"
            if name not in signatures:
                signatures[name] = sig
                exports.append((ref, name))
            note("a footprint name covers two land patterns; the second one got a _2 suffix")
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
        for p in fp.Pads():
            m = p.GetLocalSolderPasteMargin()
            if m is not None and m < -pcbnew.FromMM(1):
                p.SetLocalSolderPasteMargin(None)
                paste_fixed += 1
    problems += [f"{r} in the schematic has no footprint on the PCB" for r in sorted(set(links) - fp_refs)]

    old_to_new, new_to_old, pads = defaultdict(set), defaultdict(set), []
    for fp in b.GetFootprints():
        if fp.GetReference() not in links:
            continue
        for p in fp.Pads():
            new = sch_pins.get((fp.GetReference(), p.GetNumber()))
            pads.append((p, new))
            if new is None:
                problems.append(f"{fp.GetReference()} pad {p.GetNumber()} has no schematic pin")
            elif p.GetNetCode() > 0:
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
    return dict(renamed=renamed, paste_fixed=paste_fixed, duplicate_vias=len(dupes), board_only=board_only, exports=exports,
                problems=problems)


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


def convert(sch_json, pcb_json, out_dir, name, lib_dir=None, nick=None, meta=None, force=False, pin_fixes=()):
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    lib_dir = Path(lib_dir).resolve() if lib_dir else (out_dir.parent.parent / "lib")
    nick = nick or lib_dir.parent.name
    lib_rel = os.path.relpath(lib_dir, out_dir)
    sch = load_schematic(sch_json, pin_fixes)
    meta = {**dict(title=sch["title"] or name, rev="", date="", company=""), **(meta or {})}
    power = power_symbols()
    sym_of, defs = register_symbols(sch, nick)
    lib_dir.mkdir(parents=True, exist_ok=True)
    (lib_dir / f"{nick}.kicad_sym").write_text(
        '(kicad_symbol_lib (version 20241209) (generator "pcbkit_import_easyeda") (generator_version "10.0")\n'
        + "\n".join(defs[k] for k in sorted(defs)) + "\n)\n")
    sch_path = out_dir / f"{name}.kicad_sch"
    root, subs, links = write_schematic(sch, out_dir, sym_of, defs, power, name, nick, meta)
    write_project(out_dir, name, root, subs, nick, lib_rel)
    pcb_path = out_dir / f"{name}.kicad_pcb"
    r = subprocess.run([KICAD_CLI, "pcb", "import", "-o", str(pcb_path), str(pcb_json)], capture_output=True, text=True)
    if r.returncode or not pcb_path.exists():
        sys.exit("kicad-cli pcb import failed:\n" + quiet(r.stdout + r.stderr)[-800:])
    fixes = update_board(pcb_json, pcb_path, links, read_netlist(sch_path), nick, meta, force)
    subprocess.run([KICAD_PYTHON, "-c", "import sys; from pcbkit.import_easyeda import export_footprints; "
                    "export_footprints(sys.argv[1], __import__('json').loads(sys.argv[2]), sys.argv[3])",
                    str(pcb_path), json.dumps(fixes["exports"]), str(lib_dir / f"{nick}.pretty")],
                   check=True, capture_output=True, env=dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1])))
    count = lambda k: sum(len(s[k]) for s in sch["sheets"])
    sheets = f"{len(subs)} sheets, " if subs else ""
    print(f"{name}: {sheets}{count('parts')} parts, {len(defs)} symbols, {len(fixes['exports'])} footprints, {count('wires')} wires, "
          f"{count('flags')} labels/flags; {fixes['renamed']} PCB nets renamed, {fixes['paste_fixed']} pads given paste, "
          f"{fixes['duplicate_vias']} duplicate vias removed, {len(fixes['board_only'])} board-only footprints "
          f"(loose pads, holes)")
    for msg, c in NOTES.most_common():
        print(f"  note: {msg} (x{c})")
    print("lib:", lib_dir / f"{nick}.kicad_sym", "and", lib_dir / f"{nick}.pretty")
    from .checks import drc, erc
    v = erc.run(str(sch_path))
    print("ERC:", dict(Counter((x["severity"], x["type"]) for x in v)) or "clean")
    d = drc.run(str(pcb_path), refill=True, save=True, parity=True)  # refill and save: the import's stored fill is stale
    print("DRC:", dict(Counter((x["severity"], x["type"]) for x in d["violations"])) or "clean")
    print("parity:", dict(Counter(x["type"] for x in d.get("schematic_parity", []))) or "clean", "| unconnected:", len(d["unconnected_items"]))
    return fixes


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
    convert(a.schematic, a.pcb, a.out_dir, a.name, a.lib, a.lib_name, meta, a.force, fixes)


if __name__ == "__main__":
    main()
