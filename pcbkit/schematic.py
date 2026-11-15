"""Build a schematic sheet from Python data through konnect.

    from pcbkit import schematic as sch
    sch.configure("boards/controller/", power={"VLED": ("power:VCC", "VLED", 90)})
    comps = [sch.R("R1", "10k", 50, 40, "SDA", "+3V3"), sch.C("C1", "100nF", 60, 40, "+3V3"), ...]
    sch.build("mcu.kicad_sch", base=100, comps=comps, hier={"SDA": "bidirectional"})

A project keeps a `scripts/builder.py` that configures this module and then *becomes* it, so legacy scripts that do
`import builder; builder.P = ...` and `from builder import *` keep working:

    from pcbkit import schematic as _m
    _m.configure(ROOT + "boards/controller/", power={...}); _m.ROOT = ROOT
    sys.modules[__name__] = _m

A component is a dict: ref, lib, val, fp, x, y, rot, lcsc, fields, pins ({pin: net}), optional unit/mirror.
A pin's net is a power symbol (in POWER), "NC", a hierarchical label (in `hier`), a global label (in `glob`), or a
local net, which gets a label on each pin (batch_connect_to_net). Power symbols are placed once per pin position and
numbered from `base`. Harvested from the projects' builder.py; the sign, siebenuhr and spotlight variants are merged.

`build(..., wire=25.4)` draws the small local nets instead: where two pins of a net are within that many mm and a
straight, L or Z run between them is clear of every symbol, text, other pin and other wire, it is drawn as a wire,
and the net is named once, on the wire. Pins it can't reach keep their labels, so the netlist is the same either
way; `pcbkit sch-lint` counts what was wired. Place the parts of a block so their pins line up and the wires follow.

Placing by pin, so they do line up (pin positions are read from the symbol libraries, before anything is placed):

    u = part("U5", "Regulator_Switching:TLV62569DBV", ..., x=200.66, y=165.1, pins={...})
    sch.pin(u, "3")                                   # where U5's SW pin ends, on the sheet
    sch.put(l2, "1", sch.step(sch.pin(u, "3"), "right", 7.62), toward="right")   # L2 in line with it, body to the right
    end = sch.chain(sch.step(sch.pin(j, "2"), "right"), [f1, q5], toward="right")   # fuse, then FET, in series
    sch.put(r9, "1", sch.step(sch.pin(r8, "2"), "down"), toward="down")          # a divider, one under the other

`put` turns the part so its body lies `toward` that side of the pin and moves it so the pin lands on the point.
`chain` does that for parts in series, each entering by the pin that shares a net with the part before.
"""
import os
import re

from .konnect import Konnect  # noqa: F401  (re-exported: sheet scripts open their own client for extra calls)
from .parts import FP, capacitor, resistor

# footprint names, re-exported for sheet scripts that build parts by hand
R0402, R0603, C0402, C0603, C0805, C1206 = (FP[k] for k in ("R0402", "R0603", "C0402", "C0603", "C0805", "C1206"))

P = ""  # project (sheet) directory with trailing slash; set with configure(), or assign P directly
# net -> (symbol, value override, native rotation of the symbol's pin)
POWER = {"GND": ("power:GND", None, 270), "+3V3": ("power:+3V3", None, 90), "+5V": ("power:+5V", None, 90),
         "+12V": ("power:+12V", None, 90), "VBUS": ("power:VBUS", None, 90)}


def configure(project_dir, power=None):
    """Set the sheet directory and add project power nets, e.g. {"VIN": ("power:VCC", "VIN", 90)}."""
    global P
    P = project_dir if project_dir.endswith("/") else project_dir + "/"
    if power:
        POWER.update(power)


def sheet_path(sheet):
    """P + sheet, or the sheet itself when it is already an absolute path."""
    return sheet if os.path.isabs(sheet) else P + sheet


def R(ref, val, x, y, p1, p2, rot=0, pkg="0402"):
    fp, lcsc = resistor(val, pkg)
    return dict(ref=ref, lib="Device:R", val=val, fp=fp, x=x, y=y, rot=rot, lcsc=lcsc, pins={"1": p1, "2": p2})


def C(ref, val, x, y, p1, p2="GND", rot=0, pkg=None):
    fp, lcsc = capacitor(val, pkg)
    return dict(ref=ref, lib="Device:C", val=val, fp=fp, x=x, y=y, rot=rot, lcsc=lcsc, pins={"1": p1, "2": p2})


def P2(ref, lib, val, fp, lcsc, x, y, p1, p2, rot=0, fields=None, mpn=None):
    """Generic 2-pin part (R, C, L, D, ferrite). fields: extra symbol fields; mpn sets the MPN field."""
    f = dict(fields or {})
    if mpn:
        f["MPN"] = mpn
    return dict(ref=ref, lib=lib, val=val, fp=fp, x=x, y=y, rot=rot, lcsc=lcsc, fields=f, pins={"1": p1, "2": p2})


DIRS = {"right": (1, 0), "left": (-1, 0), "up": (0, -1), "down": (0, 1)}  # on the sheet, y grows downwards
_GEOMETRY = {}


def _library(nick):
    """The file (or KiCad 10 directory) of a symbol library: the sheet folder's sym-lib-table first, then KiCad's own."""
    from . import sexp
    from .kicad import KICAD_SYMBOLS
    table = os.path.join(P, "sym-lib-table")
    if P and os.path.exists(table):
        with open(table, encoding="utf-8") as f:
            for lib in sexp.find(sexp.parse(f.read()), "lib"):
                if sexp.first(lib, "name")[1] == nick:
                    return os.path.normpath(sexp.first(lib, "uri")[1].replace("${KIPRJMOD}", P.rstrip("/")))
    for ext in (".kicad_sym", ".kicad_symdir"):
        if os.path.exists(os.path.join(KICAD_SYMBOLS, nick + ext)):
            return os.path.join(KICAD_SYMBOLS, nick + ext)
    raise FileNotFoundError("symbol library %r: not in %s nor in %s" % (nick, table, KICAD_SYMBOLS))


def _symbol(path, name):
    """One symbol of a library, parsed: only its own text is read, the stock libraries are megabytes."""
    from . import sexp
    if os.path.isdir(path):
        path = os.path.join(path, name + ".kicad_sym")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r'^\s*\(symbol "%s"\s' % re.escape(name), text, re.M)
    if not m:
        raise KeyError("symbol %r is not in %s" % (name, path))
    i = start = text.index("(", m.start())
    depth, quoted = 0, False
    while True:
        ch = text[i]
        if quoted:
            i += ch == "\\"
            quoted = ch != '"'
        elif ch == '"':
            quoted = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if not depth:
                break
        i += 1
    sym = sexp.parse(text[start:i + 1])
    parent = sexp.first(sym, "extends")
    return _symbol(path, parent[1]) if parent else sym


def geometry(lib_id, unit=1):
    """({pin: (x, y)}, [(x, y), ...] of the drawing) of a library symbol, in the library's own coordinates (y up)."""
    if (P, lib_id, unit) not in _GEOMETRY:
        from .checks.sch_lint import _lib_symbols
        nick, name = lib_id.split(":", 1)
        pins, pts = {}, []
        subs, = _lib_symbols(["lib", ["lib_symbols", _symbol(_library(nick), name)]]).values()  # a parent's, if derived
        for u, style, gp, pn in subs:
            if u in (0, unit) and style in (0, 1):
                pts += gp
                pins.update({num: (x, y) for num, x, y in pn})
        _GEOMETRY[(P, lib_id, unit)] = (pins, pts)
    return _GEOMETRY[(P, lib_id, unit)]


def _offsets(c, rot=None):
    """Sheet offsets from the part's origin: ({pin: (dx, dy)}, (dx, dy) of the middle of its drawing and pins)."""
    from .checks.sch_lint import apply, transform
    m = transform(c.get("rot", 0) if rot is None else rot, c.get("mirror"))
    pins, pts = geometry(c["lib"], c.get("unit", 1))
    off = {n: apply(m, x, -y) for n, (x, y) in pins.items()}
    allp = [apply(m, x, -y) for x, y in pts] + list(off.values())
    mid = ((min(p[0] for p in allp) + max(p[0] for p in allp)) / 2, (min(p[1] for p in allp) + max(p[1] for p in allp)) / 2)
    return off, mid


def pins(c):
    """{pin: (x, y)} on the sheet for a component dict, from its lib, x, y, rot, mirror and unit."""
    return {n: (round(c["x"] + dx, 2), round(c["y"] + dy, 2)) for n, (dx, dy) in _offsets(c)[0].items()}


def pin(c, n):
    return pins(c)[str(n)]


def step(point, toward, d=5.08):
    """The point d mm from `point` towards "right", "left", "up" or "down"."""
    dx, dy = DIRS[toward]
    return (round(point[0] + d * dx, 2), round(point[1] + d * dy, 2))


def put(c, n, at, toward=None):
    """Move component c so its pin n lands on `at`. With `toward`, first turn it so the rest of the part lies on
    that side of the pin: a resistor put by pin 1 toward "down" hangs below the point. Returns c."""
    n = str(n)
    if toward:
        dx, dy = DIRS[toward]

        def along(rot):
            off, mid = _offsets(c, rot)
            return (mid[0] - off[n][0]) * dx + (mid[1] - off[n][1]) * dy
        c["rot"] = max((0, 90, 180, 270), key=lambda rot: (round(along(rot), 3), -rot))
    off = _offsets(c)[0][n]
    c["x"], c["y"] = round(at[0] - off[0], 2), round(at[1] - off[1], 2)
    return c


def chain(at, comps, toward="right", gap=5.08):
    """Parts in series from `at`, each `gap` mm after the one before, so build(wire=...) joins them with straight
    wires. A two-pin part enters by the pin that shares a net with the part before it (the first: the pin that
    doesn't share one with the second, else pin 1) and leaves by the other; give (c, pin_in, pin_out) for any other
    part. Returns where the last part's leaving pin ends."""
    items = [(c, None, None) if isinstance(c, dict) else tuple(c) for c in comps]
    out = at
    for i, (c, pin_in, pin_out) in enumerate(items):
        if pin_in is None:
            names = sorted(c["pins"])
            assert len(names) == 2, "%s has %d pins: give (part, pin_in, pin_out)" % (c["ref"], len(names))
            if i:
                before = set(items[i - 1][0]["pins"].values())
                shared = [n for n in names if c["pins"][n] in before]
            else:
                after = set(items[1][0]["pins"].values()) if len(items) > 1 else set()
                shared = [n for n in names if c["pins"][n] not in after]
            pin_in = shared[0] if len(shared) == 1 else names[0]
            pin_out = [n for n in names if n != pin_in][0]
        put(c, pin_in, at if not i else step(out, toward, gap), toward)
        out = pin(c, pin_out)
    return out


WIRE_PINS = 6  # the largest local net build(wire=...) draws; a wider fan-out keeps its labels


def _runs(a, b):
    """Candidate wire runs from a to b, fewest corners first: straight, the two Ls, the two Zs."""
    (x1, y1), (x2, y2) = a, b
    if x1 == x2 or y1 == y2:
        return [[a, b]]
    snap = lambda v: round(round(v / 1.27) * 1.27, 2)  # noqa: E731
    xm, ym = snap((x1 + x2) / 2), snap((y1 + y2) / 2)
    out = [[a, (x2, y1), b], [a, (x1, y2), b]]
    if xm not in (x1, x2):
        out.append([a, (xm, y1), (xm, y2), b])
    if ym not in (y1, y2):
        out.append([a, (x1, ym), (x2, ym), b])
    return out


def _touch(s, t):
    """Do two wire segments meet or cross? Exact for horizontal and vertical ones."""
    return (min(s[0][0], s[1][0]) <= max(t[0][0], t[1][0]) + 0.01 and min(t[0][0], t[1][0]) <= max(s[0][0], s[1][0]) + 0.01
            and min(s[0][1], s[1][1]) <= max(t[0][1], t[1][1]) + 0.01 and min(t[0][1], t[1][1]) <= max(s[0][1], s[1][1]) + 0.01)


def plan_wires(nets, boxes, points, segments, reach, max_pins=WIRE_PINS):
    """Wires for small local nets. Pure geometry, no konnect.

    nets      {name: [(ref, pin, (x, y)), ...]}, the pins of each local net
    boxes     what a wire may not pass through: symbol outlines (body and pins) and visible texts, (x0, y0, x1, y1)
    points    every pin endpoint on the sheet, {(x, y): net or None}: a wire may only touch its own net's
    segments  wires already there, [((x1, y1), (x2, y2))]
    reach     the longest run between two pins, mm (along the wire)

    Returns {name: {"wires": [segment], "junctions": [(x, y)], "label": (x, y, rotation) or None,
    "loose": [(ref, pin)]}}: the pins in `loose` could not be reached and still need a label each; `label` names
    the wired part, and is None when nothing was wired."""
    from .checks.sch_lint import on_seg, seg_in_box, shrink, text_width
    boxes = [shrink(b, 0.1) for b in boxes]
    taken = list(segments)
    out = {}
    for name, pins in nets.items():
        at = {}
        for ref, pin, p in pins:
            at.setdefault(p, (ref, pin))
        pts = sorted(at)
        res = out[name] = dict(wires=[], junctions=[], label=None, loose=[at[p] for p in pts])
        if not 2 <= len(pts) <= max_pins:
            continue
        group = {p: p for p in pts}

        def root(p):
            while group[p] != p:
                p = group[p]
            return p

        def clear(run):
            for seg in zip(run, run[1:]):
                if any(seg_in_box(seg, b) for b in boxes) or any(_touch(seg, t) for t in taken):
                    return False
                if any(net != name and on_seg(q, seg) for q, net in points.items()):
                    return False
            return True

        mine = []
        pairs = sorted((abs(a[0] - b[0]) + abs(a[1] - b[1]), a, b) for i, a in enumerate(pts) for b in pts[i + 1:])
        for d, a, b in pairs:
            if d > reach or root(a) == root(b):
                continue
            run = next((r for r in _runs(a, b) if clear(r)), None)
            if run:
                mine += list(zip(run, run[1:]))
                group[root(a)] = root(b)
        if not mine:
            continue
        # cut the runs where they meet, so a stretch two runs share is drawn once
        ends = {p for seg in mine for p in seg}
        cut = set()
        for s0, s1 in mine:
            on = sorted(p for p in ends if on_seg(p, (s0, s1)))
            cut |= {pair for pair in zip(on, on[1:])}
        mine = sorted(cut)
        wired = {p for p in pts if p in ends}
        res["wires"], res["loose"] = mine, [at[p] for p in pts if p not in wired]
        # a dot where three or more things meet: wire ends, and the pin if there is one
        for p in sorted(ends):
            if sum(1 for seg in mine if p in seg) + (p in at) >= 3:
                res["junctions"].append(p)
        # the name, on the longest run that holds it: along a horizontal one, else up a vertical one.
        # Wired pins left in separate groups each get a label on their own pin instead.
        roots = {root(p) for p in wired}
        if len(roots) > 1:
            res["loose"] = [at[p] for p in pts]
        else:
            need = text_width(name, 1.27) + 2.54
            length = lambda s: abs(s[0][0] - s[1][0]) + abs(s[0][1] - s[1][1])  # noqa: E731
            horiz = [s for s in mine if s[0][1] == s[1][1] and length(s) >= need]
            s = max(horiz or mine, key=length)
            if s[0][1] == s[1][1]:
                res["label"] = (round(min(s[0][0], s[1][0]) + 1.27, 2), s[0][1], 0)
            else:
                res["label"] = (s[0][0], round(max(s[0][1], s[1][1]) - 1.27, 2), 90)
        taken += mine
    return out


def build(sheet, base, comps, hier, wires=(), junctions=(), extra_power=(), skip=(), glob=None, wire=None):
    """Place comps on P+sheet, connect their pins, annotate LCSC and other fields. Returns pin locations.
    wire: draw small local nets as wires where two pins are within this many mm (see the module docstring)."""
    glob = glob or {}
    S = sheet_path(sheet)
    k = Konnect()
    k.load("sch_batch", "sch_components", "sch_wiring")
    place = []
    for c in comps:
        e = {"lib_id": c["lib"], "reference": c["ref"], "x": c["x"], "y": c["y"], "rotation": c.get("rot", 0)}
        if c.get("val"):
            e["value"] = c["val"]
        if c.get("fp"):
            e["footprint"] = c["fp"]
        if c.get("mirror"):
            e["mirror"] = c["mirror"]
        if c.get("unit"):
            e["unit"] = c["unit"]
        place.append(e)
    r = k.call("batch_place_components", {"schematic": S, "components": place})
    assert not r["errors"] and not r["failures"], r
    locs = k.call("batch_get_schematic_pin_locations", {"schematic": S, "references": sorted({c["ref"] for c in comps})})
    L = {}
    for comp in locs["components"]:
        for p in comp["pins"]:
            L[(comp["reference"], p["number"])] = (round(p["x"], 2), round(p["y"], 2), p["orientation_degrees"])
    pw, nc, local, seen, hier_done, n = [], [], {}, set(), set(), [0]

    def pwr(net, x, y, th):
        if (x, y) in seen:
            return
        seen.add((x, y))
        lib, val, nat = POWER[net]
        n[0] += 1
        e = {"lib_id": lib, "reference": f"#PWR{base + n[0]:04d}", "x": x, "y": y, "rotation": (th - nat) % 360}
        if val:
            e["value"] = val
        pw.append(e)

    for c in comps:
        for pin, net in c["pins"].items():
            if (c["ref"], pin) in skip or net is None:
                continue
            x, y, th = L[(c["ref"], pin)]
            if net == "NC":
                if (x, y) not in seen:
                    seen.add((x, y))
                    nc.append({"x": x, "y": y})
            elif net in POWER:
                pwr(net, x, y, th)
            elif net in hier and net not in hier_done and (x, y) not in seen:
                seen.add((x, y))
                hier_done.add(net)
                k.call("add_schematic_net_label", {"schematic": S, "net": net, "label_type": "hierarchical_label",
                                                   "shape": hier[net], "x": x, "y": y, "rotation": th})
            elif net in glob:
                if (x, y) in seen:
                    continue
                seen.add((x, y))
                k.call("add_schematic_net_label", {"schematic": S, "net": net, "label_type": "global_label",
                                                   "shape": glob[net], "x": x, "y": y, "rotation": th})
            else:
                if (x, y) in seen:
                    continue
                seen.add((x, y))
                local.setdefault(net, []).append({"reference": c["ref"], "pin_number": pin})
    for net, x, y, th in extra_power:
        pwr(net, x, y, th)
    if pw:
        r = k.call("batch_place_components", {"schematic": S, "components": pw})
        assert not r["errors"] and not r["failures"], r
    if nc:
        k.call("batch_add_no_connect", {"schematic": S, "positions": nc})
    drawn = 0
    if wire:
        from .checks import sch_lint
        page = sch_lint.Sheet(S)  # as placed so far: parts, power symbols, their texts
        boxes = [t["box"] for t in page.texts]
        for sym in page.symbols:
            xs, ys = zip(*([p for _, p in sym["pins"]] + [sym["body"][:2], sym["body"][2:]]))
            boxes.append((min(xs), min(ys), max(xs), max(ys)))
        key = lambda x, y: (round(x, 2), round(y, 2))  # noqa: E731
        points = {key(*p): None for sym in page.symbols for _, p in sym["pins"]}
        nets = {}
        for net, pins in local.items():
            nets[net] = [(p["reference"], p["pin_number"], key(*L[(p["reference"], p["pin_number"])][:2])) for p in pins]
            points.update({p: net for _, _, p in nets[net]})
        plan = plan_wires(nets, boxes, points, [((a, b), (c_, d)) for a, b, c_, d in wires], wire)
        for net, r in plan.items():
            local[net] = [{"reference": ref, "pin_number": pin} for ref, pin in r["loose"]]
            if not r["wires"]:
                continue
            drawn += 1
            k.call("batch_add_wire", {"schematic": S, "wires": [dict(x1=a[0], y1=a[1], x2=b[0], y2=b[1]) for a, b in r["wires"]]})
            if r["junctions"]:
                k.call("batch_add_junction", {"schematic": S, "positions": [dict(x=x, y=y) for x, y in r["junctions"]]})
            if r["label"]:
                x, y, rot = r["label"]
                k.call("add_schematic_net_label", {"schematic": S, "net": net, "label_type": "net_label",
                                                   "x": x, "y": y, "rotation": rot})
    for net, pins in local.items():
        if pins:
            k.call("batch_connect_to_net", {"schematic": S, "net_name": net, "pins": pins})
    if wires:
        k.call("batch_add_wire", {"schematic": S, "wires": [dict(x1=a, y1=b, x2=c_, y2=d) for a, b, c_, d in wires]})
    if junctions:
        k.call("batch_add_junction", {"schematic": S, "positions": [dict(x=a, y=b) for a, b in junctions]})
    done = set()
    for c in comps:
        if c["ref"].startswith("#") or c["ref"] in done:
            continue
        done.add(c["ref"])
        f = {"LCSC": c.get("lcsc", "")}
        f.update(c.get("fields", {}))
        for key, val in f.items():
            k.call("add_component_annotation", {"schematic": S, "reference": c["ref"], "key": key, "value": val})
    print(sheet, "placed", len(comps), "global", len(glob), "power", len(pw), "nc", len(nc), "local nets", len(local), "wired", drawn,
          "hier", len(hier_done), "missing hier", set(hier) - hier_done)
    return L


def reset(sheet, paper="A3"):
    """Blank a child sheet in place, keeping its own uuid so hierarchy links survive."""
    S = sheet_path(sheet)
    txt = open(S).read()
    uuid = re.search(r'\(uuid "?([0-9a-f-]+)"?\)', txt).group(1)
    open(S, "w").write(f'(kicad_sch\n\t(version 20260306)\n\t(generator "eeschema")\n\t(generator_version "10.0")\n'
                       f'\t(uuid "{uuid}")\n\t(paper "{paper}")\n\t(lib_symbols)\n\t(embedded_fonts no)\n)\n')


def labels(sheet, items):
    """Plain net labels: items = [(net, x, y, rotation), ...]."""
    k = Konnect()
    k.load("sch_wiring")
    for net, x, y, rot in items:
        k.call("add_schematic_net_label", {"schematic": sheet_path(sheet), "net": net, "label_type": "net_label",
                                           "x": x, "y": y, "rotation": rot})


def mark_dnp(sheet, refs):
    """Set (dnp yes) and (in_bom no) on placed symbols by reference (text edit; run after build).
    Patches the symbol header between "(symbol" and its Reference property, so it works however the
    symbol's closing parens are laid out (add_component_annotation leaves '))' on the last field's line)."""
    S = sheet_path(sheet)
    s = open(S).read()
    for ref in refs:
        i = s.index(f'(property "Reference" "{ref}"')
        a = s.rindex("\n\t(symbol\n", 0, i)
        head = s[a:i].replace("(in_bom yes)", "(in_bom no)").replace("(dnp no)", "(dnp yes)")
        assert "(dnp yes)" in head and "(in_bom no)" in head, f"mark_dnp: {ref}"
        s = s[:a] + head + s[i:]
    open(S, "w").write(s)
