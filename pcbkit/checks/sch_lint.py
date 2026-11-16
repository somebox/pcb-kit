"""How a schematic reads, measured from the file.  pcbkit sch-lint SCHEMATIC.kicad_sch [...]

ERC says whether a schematic is legal; this says whether a person can read it. It reads the .kicad_sch files (the
root and every sheet below it) with pcbkit's own parser, so it needs neither KiCad nor konnect. Findings:

  text-overlap   two visible texts (references, values, labels, notes) on top of each other
  text-on-symbol a text across another symbol's body
  text-on-wire   a wire through a text
  wire-on-symbol a wire through the middle of a symbol
  field-far      a reference or value more than --field-distance from its own symbol
  label-hop      a small local net joined by labels where its pins are within --hop-distance: draw the wire
  label-island   the same, with the pins further apart: the part sits away from what it works with
  off-page       a symbol or text outside the sheet
  title-block    a sheet with no title

and per sheet, how many of its small local nets are drawn as wires. A small local net is one with two to
--net-pins pins on the sheet (power symbols aside) and no global or hierarchical label: a feedback divider, a
series resistor, a switch node. Buses, shared controls and nets that leave the sheet are left to labels.

Text extents are estimated from the character count and the font size (KiCad's stroke font is not read), and boxes
are shrunk a little before they are compared, so a finding is an overlap a reader would see. Exit status is 0
unless --strict. The ideas are those of github.com/Ausanx/modular-schematic-layout; the checks are pcb-kit's.
"""
import argparse
import collections
import json
import math
import os
import sys

from .. import sexp

PAPER = {"A5": (210, 148), "A4": (297, 210), "A3": (420, 297), "A2": (594, 420), "A1": (841, 594), "A0": (1189, 841),
         "A": (279.4, 215.9), "B": (431.8, 279.4), "C": (558.8, 431.8), "D": (863.6, 558.8), "E": (1117.6, 863.6),
         "USLetter": (279.4, 215.9), "USLegal": (355.6, 215.9), "USLedger": (431.8, 279.4)}
NARROW, WIDE = set("iIl1.,:;'|!j "), set("mwMW@%")  # advance in font sizes: 0.5 and 1.2; lower case 0.85, the rest 0.97
TEXT_SHRINK, BODY_SHRINK, MIN_OVERLAP = 0.15, 0.3, 0.3  # mm


# ---- geometry -------------------------------------------------------------------------------------------------------

def text_width(s, size):
    s = s.replace("~{", "").replace("}", "")
    return size * sum(0.5 if ch in NARROW else 1.2 if ch in WIDE else 0.85 if ch.islower() else 0.97 for ch in s)


def norm(x0, y0, x1, y1):
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def shrink(b, d):
    x0, y0, x1, y1 = b
    dx, dy = min(d, (x1 - x0) / 2), min(d, (y1 - y0) / 2)
    return (x0 + dx, y0 + dy, x1 - dx, y1 - dy)


def overlap(a, b):
    """Width and height of the intersection of two boxes, or None."""
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    return (w, h) if w > 0 and h > 0 else None


def gap(a, b):
    dx, dy = max(a[0] - b[2], b[0] - a[2], 0), max(a[1] - b[3], b[1] - a[3], 0)
    return math.hypot(dx, dy)


def seg_in_box(seg, b):
    """Does the segment pass through the box? (Liang-Barsky)"""
    (xa, ya), (xb, yb) = seg
    t0, t1 = 0.0, 1.0
    for p, q in ((xa - xb, xa - b[0]), (xb - xa, b[2] - xa), (ya - yb, ya - b[1]), (yb - ya, b[3] - ya)):
        if p == 0:
            if q <= 0:
                return False
        else:
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
    return t0 < t1


def on_seg(p, seg, eps=0.01):
    (xa, ya), (xb, yb) = seg
    if abs((xb - xa) * (p[1] - ya) - (yb - ya) * (p[0] - xa)) > eps * max(1.0, math.hypot(xb - xa, yb - ya)):
        return False
    return min(xa, xb) - eps <= p[0] <= max(xa, xb) + eps and min(ya, yb) - eps <= p[1] <= max(ya, yb) + eps


def transform(rot, mirror):
    """Symbol-local (sheet-oriented, y down) -> sheet offset. Rotation first, then the mirror, as eeschema does."""
    m = {0: (1, 0, 0, 1), 90: (0, 1, -1, 0), 180: (-1, 0, 0, -1), 270: (0, -1, 1, 0)}[int(rot) % 360]
    if mirror == "x":
        m = (m[0], m[1], -m[2], -m[3])
    elif mirror == "y":
        m = (-m[0], -m[1], m[2], m[3])
    return m


def apply(m, x, y):
    return (m[0] * x + m[1] * y, m[2] * x + m[3] * y)


def unapply(m, x, y):  # the matrices are orthogonal: the inverse is the transpose
    return (m[0] * x + m[2] * y, m[1] * x + m[3] * y)


# ---- reading a sheet ------------------------------------------------------------------------------------------------

def _at(node):
    a = sexp.first(node, "at")
    return (float(a[1]), float(a[2]), float(a[3]) if len(a) > 3 else 0.0) if a else (0.0, 0.0, 0.0)


def _effects(node):
    """(font size, horizontal justify, vertical justify, hidden)"""
    size, hj, vj, hidden = 1.27, "center", "center", False
    e = sexp.first(node, "effects")
    if e:
        f = sexp.first(e, "font")
        s = sexp.first(f, "size") if f else None
        if s:
            size = float(s[1])
        j = sexp.first(e, "justify")
        for tok in (j or [])[1:]:
            if tok in ("left", "right"):
                hj = tok
            elif tok in ("top", "bottom"):
                vj = tok
        h = sexp.first(e, "hide")
        hidden = "hide" in [c for c in e if not isinstance(c, list)] or bool(h and (len(h) < 2 or h[1] == "yes"))
    h = sexp.first(node, "hide")
    return size, hj, vj, hidden or bool(h and (len(h) < 2 or h[1] == "yes"))


def _lib_symbols(root):
    """lib name -> {"subs": [(unit, style, points, pins)]}: points (x, y) of the graphics and pins (number, x, y),
    in the library's own coordinates (y up)."""
    out = {}
    for sym in sexp.find(sexp.first(root, "lib_symbols") or [], "symbol"):
        subs = []
        for sub in sexp.find(sym, "symbol"):
            try:
                unit, style = (int(v) for v in sub[1].rsplit("_", 2)[1:])
            except ValueError:
                unit, style = 0, 0
            pts, pins = [], []
            for g in sub[2:]:
                if not isinstance(g, list):
                    continue
                if g[0] == "rectangle":
                    pts += [tuple(map(float, sexp.first(g, k)[1:3])) for k in ("start", "end")]
                elif g[0] in ("polyline", "bezier"):
                    pts += [(float(p[1]), float(p[2])) for p in sexp.find(sexp.first(g, "pts") or [], "xy")]
                elif g[0] == "arc":
                    pts += [tuple(map(float, sexp.first(g, k)[1:3])) for k in ("start", "mid", "end") if sexp.first(g, k)]
                elif g[0] == "circle":
                    (cx, cy), r = map(float, sexp.first(g, "center")[1:3]), float(sexp.first(g, "radius")[1])
                    pts += [(cx - r, cy - r), (cx + r, cy + r)]
                elif g[0] == "pin":
                    x, y, _ = _at(g)
                    num = sexp.first(g, "number")
                    pins.append((num[1] if num else "", x, y))
            subs.append((unit, style, pts, pins))
        out[sym[1]] = subs
    return out


class Sheet:
    """One .kicad_sch file: symbols with their bodies and pins, visible texts, wires, labels."""

    def __init__(self, path):
        self.path = path
        with open(path, encoding="utf-8") as f:
            root = sexp.parse(f.read())
        libs = _lib_symbols(root)
        paper = sexp.first(root, "paper")
        self.paper = PAPER.get(paper[1]) if paper and len(paper) == 2 else None
        if paper and len(paper) > 3 and paper[1] == "User":
            self.paper = (float(paper[2]), float(paper[3]))
        tb = sexp.first(root, "title_block")
        t = sexp.first(tb, "title") if tb else None
        self.title = t[1] if t and len(t) > 1 else ""
        self.symbols, self.texts, self.wires, self.labels, self.children = [], [], [], [], []
        self.junctions = [_at(j)[:2] for j in sexp.find(root, "junction")]
        for w in sexp.find(root, "wire") + sexp.find(root, "bus"):
            p = [(float(q[1]), float(q[2])) for q in sexp.find(sexp.first(w, "pts"), "xy")]
            self.wires += list(zip(p, p[1:]))
        for s in sexp.find(root, "symbol"):
            self._symbol(s, libs)
        for kind in ("label", "global_label", "hierarchical_label"):
            for lb in sexp.find(root, kind):
                self._label(lb, kind)
        for tx in sexp.find(root, "text"):
            x, y, ang = _at(tx)
            size, hj, vj, hidden = _effects(tx)
            if not hidden and tx[1].strip():
                lines = tx[1].replace("\\n", "\n").split("\n")
                w, h = max(text_width(ln, size) for ln in lines), size * 1.6 * len(lines)
                self.texts.append(dict(kind="text", text=lines[0][:30], at=(x, y), owner=None,
                                       box=_text_box(x, y, w, h, hj, vj, ang)))
        for sh in sexp.find(root, "sheet"):
            f = sexp.prop(sh, "Sheetfile")
            if f:
                self.children.append(os.path.normpath(os.path.join(os.path.dirname(path), f)))

    def _symbol(self, s, libs):
        x, y, rot = _at(s)
        mir = sexp.first(s, "mirror")
        m = transform(rot, mir[1] if mir else None)
        lib_id = sexp.first(s, "lib_id")[1]
        lib_name = sexp.first(s, "lib_name")
        unit = sexp.first(s, "unit")
        unit = int(unit[1]) if unit else 1
        style = sexp.first(s, "body_style") or sexp.first(s, "convert")
        style = int(style[1]) if style and str(style[1]).isdigit() else 1
        ref = sexp.prop(s, "Reference") or "?"
        pts, pins = [], []
        for u, st, gp, pn in libs.get(lib_name[1] if lib_name else lib_id, []):
            if u in (0, unit) and st in (0, style):
                pts += gp
                pins += pn
        sheet_pt = lambda px, py: tuple(round(v + o, 3) for v, o in zip(apply(m, px, -py), (x, y)))  # noqa: E731
        pins = [(num, sheet_pt(px, py)) for num, px, py in pins]
        corners = [sheet_pt(px, py) for px, py in pts] or [p for _, p in pins] or [(x, y)]
        body = norm(min(c[0] for c in corners), min(c[1] for c in corners),
                    max(c[0] for c in corners), max(c[1] for c in corners))
        sym = dict(ref=ref, lib=lib_id, at=(x, y), m=m, body=body, pins=pins,
                   power=ref.startswith("#") or lib_id.startswith("power:"))
        self.symbols.append(sym)
        for p in sexp.find(s, "property"):
            size, hj, vj, hidden = _effects(p)
            if hidden or not str(p[2]).strip():
                continue
            fx, fy, fang = _at(p)
            lx, ly = unapply(m, fx - x, fy - y)
            w, h = text_width(str(p[2]), size), size
            b = _text_box(lx, ly, w, h, hj, vj, fang)
            c = [apply(m, cx, cy) for cx, cy in ((b[0], b[1]), (b[2], b[3]))]
            box = norm(c[0][0] + x, c[0][1] + y, c[1][0] + x, c[1][1] + y)
            self.texts.append(dict(kind="field", field=p[1], text=str(p[2]), at=(fx, fy), owner=sym, box=box))

    def _label(self, lb, kind):
        x, y, ang = _at(lb)
        size, _, _, hidden = _effects(lb)
        if hidden:
            return
        w = text_width(lb[1], size) + {"label": 0, "global_label": 2 * size, "hierarchical_label": size}[kind]
        h = size * 1.2
        lo, hi = (-h, 0) if kind == "label" else (-h / 2, h / 2)  # a local label sits above its wire
        box = {0: (x, y + lo, x + w, y + hi), 180: (x - w, y + lo, x, y + hi),
               90: (x + lo, y - w, x + hi, y), 270: (x + lo, y, x + hi, y + w)}[int(ang) % 360 // 90 * 90]
        self.labels.append(dict(kind=kind, name=lb[1], at=(x, y)))
        self.texts.append(dict(kind=kind, text=lb[1], at=(x, y), owner=None, box=norm(*box)))


def _text_box(x, y, w, h, hj, vj, ang):
    x0 = {"left": 0, "center": -w / 2, "right": -w}[hj]
    y0 = {"bottom": -h, "center": -h / 2, "top": 0}[vj]
    c = [(x0, y0), (x0 + w, y0 + h)]
    if int(ang) % 180 == 90:  # reads bottom to top: local +x becomes sheet -y
        c = [(cy, -cx) for cx, cy in c]
    return norm(x + c[0][0], y + c[0][1], x + c[1][0], y + c[1][1])


# ---- the checks -----------------------------------------------------------------------------------------------------

def _name(t):
    if t["kind"] == "field":
        return "%s %s '%s'" % (t["owner"]["ref"], t["field"].lower(), t["text"])
    return "%s '%s'" % (t["kind"].replace("_", " "), t["text"])


def _nets(sheet):
    """Connected groups on one sheet, before labels join them: point -> group id, and a finder."""
    parent = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        parent[find(a)] = find(b)

    key = lambda p: (round(p[0], 2), round(p[1], 2))  # noqa: E731
    points = {key(p) for s in sheet.symbols for _, p in s["pins"]}
    points |= {key(lb["at"]) for lb in sheet.labels} | {key(j) for j in sheet.junctions}
    points |= {key(p) for seg in sheet.wires for p in seg}
    for seg in sheet.wires:
        union(key(seg[0]), key(seg[1]))
        for p in points:
            if on_seg(p, seg):
                union(p, key(seg[0]))
    return lambda p: find(key(p))


def lint(sheet, hop_distance=25.4, field_distance=5.08, net_pins=4):
    """Findings [(type, (x, y), text)] and the sheet's numbers."""
    out = []
    add = lambda kind, at, msg: out.append((kind, (round(at[0], 2), round(at[1], 2)), msg))  # noqa: E731
    texts, syms = sheet.texts, sheet.symbols
    for i, a in enumerate(texts):
        sa = shrink(a["box"], TEXT_SHRINK)
        for b in texts[i + 1:]:
            if a["text"] == b["text"] and a["at"] == b["at"]:
                continue
            o = overlap(sa, shrink(b["box"], TEXT_SHRINK))
            if o and min(o) > MIN_OVERLAP:
                add("text-overlap", a["at"], "%s over %s" % (_name(a), _name(b)))
        for s in syms:
            if s is a["owner"]:
                continue  # KiCad's own resistor carries its value inside the body
            o = overlap(sa, shrink(s["body"], BODY_SHRINK))
            if o and min(o) > MIN_OVERLAP:
                add("text-on-symbol", a["at"], "%s across %s" % (_name(a), s["ref"]))
        for seg in sheet.wires:
            if a["kind"] != "field" and a["kind"] != "text" and (on_seg(a["at"], seg, 0.05)):
                continue  # the wire a label names
            if seg_in_box(seg, shrink(a["box"], TEXT_SHRINK + MIN_OVERLAP)):
                add("text-on-wire", a["at"], "wire through %s" % _name(a))
                break
        if sheet.paper and not (0 <= a["box"][0] and 0 <= a["box"][1]
                                and a["box"][2] <= sheet.paper[0] and a["box"][3] <= sheet.paper[1]):
            add("off-page", a["at"], _name(a))
        if a["kind"] == "field" and a["field"] in ("Reference", "Value") and not a["owner"]["power"]:
            d = gap(a["box"], a["owner"]["body"])
            if d > field_distance:
                add("field-far", a["at"], "%s is %.1f mm from its symbol" % (_name(a), d))
    for s in syms:
        b = s["body"]  # the middle half: a body's box is wider than its drawing (an LED's arrows, a pin's stub)
        b = shrink(b, max(BODY_SHRINK, (b[2] - b[0]) / 4)), shrink(b, max(BODY_SHRINK, (b[3] - b[1]) / 4))
        b = (b[0][0], b[1][1], b[0][2], b[1][3])
        if b[2] - b[0] > MIN_OVERLAP and b[3] - b[1] > MIN_OVERLAP:
            for seg in sheet.wires:
                if seg_in_box(seg, b):
                    add("wire-on-symbol", s["at"], "wire through %s" % s["ref"])
                    break
        if sheet.paper and not (0 <= s["body"][0] and 0 <= s["body"][1]
                                and s["body"][2] <= sheet.paper[0] and s["body"][3] <= sheet.paper[1]):
            add("off-page", s["at"], s["ref"])
    if not sheet.title:
        add("title-block", (0, 0), "no title in the title block")

    # small local nets: wired, or joined by labels?
    group = _nets(sheet)
    pins_of = collections.defaultdict(list)       # group -> [(ref, pin, point)] of real parts
    for s in syms:
        if not s["power"]:
            for num, p in s["pins"]:
                pins_of[group(p)].append((s["ref"], num, p))
    by_name, leaves = collections.defaultdict(set), set()
    for lb in sheet.labels:
        by_name[lb["name"]].add(group(lb["at"]))
        if lb["kind"] != "label":
            leaves.add(lb["name"])
    labelled = set().union(*by_name.values()) if by_name else set()
    wired = sum(1 for g, pins in pins_of.items() if g not in labelled and 2 <= len(pins) <= net_pins)
    hops = islands = 0
    for name, groups in sorted(by_name.items()):
        pins = [p for g in groups for p in pins_of[g]]
        if name in leaves or not 2 <= len(pins) <= net_pins:
            continue
        parts = sorted(sorted(pins_of[g]) for g in groups if pins_of[g])
        if len(parts) < 2:
            wired += 1
            continue
        # the furthest any piece of the net is from its nearest other piece
        worst, pair = 0.0, None
        for i, a in enumerate(parts):
            best = min(((math.hypot(p[2][0] - q[2][0], p[2][1] - q[2][1]), p, q)
                        for j, b in enumerate(parts) if j != i for p in a for q in b), key=lambda t: t[0])
            if best[0] > worst:
                worst, pair = best[0], best
        _, p, q = pair
        msg = "net %s: %d pins in %d pieces, %s.%s and %s.%s are %.1f mm apart" % (
            name, len(pins), len(parts), p[0], p[1], q[0], q[1], worst)
        if worst <= hop_distance:
            hops += 1
            add("label-hop", p[2], msg)
        else:
            islands += 1
            add("label-island", p[2], msg)
    stats = dict(symbols=sum(1 for s in syms if not s["power"]), wires=len(sheet.wires),
                 local_nets=wired + hops + islands, wired=wired, label_hop=hops, label_island=islands)
    return out, stats


def sheets(paths, recurse=True):
    """Every sheet file under the given ones, each once, in the order found."""
    seen, todo, out = set(), [os.path.abspath(p) for p in paths], []
    while todo:
        p = todo.pop(0)
        if p in seen:
            continue
        seen.add(p)
        s = Sheet(p)
        out.append(s)
        if recurse:
            todo += [c for c in s.children if os.path.exists(c)]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("schematic", nargs="+")
    ap.add_argument("--no-recurse", action="store_true", help="only the files named, not the sheets below them")
    ap.add_argument("--only", help="comma-separated finding types to list")
    ap.add_argument("--summary", action="store_true", help="the counts per sheet, not each finding")
    ap.add_argument("--hop-distance", type=float, default=25.4, help="mm; label-hop below, label-island above (25.4)")
    ap.add_argument("--field-distance", type=float, default=5.08, help="mm from a symbol to its reference or value (5.08)")
    ap.add_argument("--net-pins", type=int, default=4, help="largest net counted as a small local net (4)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true", help="exit 1 when there are findings")
    a = ap.parse_args(argv)
    only = set(a.only.split(",")) if a.only else None
    report, total = [], 0
    for s in sheets(a.schematic, not a.no_recurse):
        found, stats = lint(s, a.hop_distance, a.field_distance, a.net_pins)
        if only:
            found = [f for f in found if f[0] in only]
        total += len(found)
        report.append(dict(sheet=os.path.relpath(s.path), stats=stats, counts=dict(collections.Counter(f[0] for f in found)),
                           findings=[dict(type=t, at=at, text=msg) for t, at, msg in found]))
    if a.json:
        print(json.dumps(report, indent=1))
    else:
        for r in report:
            st = r["stats"]
            print("%s: %d parts, %d wire segments; small local nets: %d wired, %d by label nearby, %d by label apart" % (
                r["sheet"], st["symbols"], st["wires"], st["wired"], st["label_hop"], st["label_island"]))
            print("  ", r["counts"] or "clean")
            if not a.summary:
                for f in sorted(r["findings"], key=lambda f: (f["type"], f["at"])):
                    print("   %-14s (%6.1f, %6.1f)  %s" % (f["type"], f["at"][0], f["at"][1], f["text"]))
    sys.exit(1 if a.strict and total else 0)


if __name__ == "__main__":
    main()
