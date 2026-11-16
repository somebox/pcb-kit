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

A block, placed from what its parts do (read the circuit first: what the power or signal passes through, what
hangs from each node, what sits across what):

    buck = sch.block("3.3 V buck",
                     path=[(u5, "4", "3"), l2],                          # in at VIN, out at SW, then the inductor
                     shunt={"+5V": [c8],                                 # input capacitor, before the regulator
                            "+3V3": [c9, c19, [r8, r9], [r2, d1]]},      # output capacitors, divider, LED: on the output
                     across=[(r8, [c25])],                               # feed-forward capacitor over the top resistor
                     sense=[(u5, "5")])                                  # FB is wired back from the divider, not labelled
    sch.title_block(sheet, about="Power: input, bucks, USB-C")           # the root's title, rev and date, and this line
    for again in (False, True):                                          # the second time by the measured sizes
        sch.arrange([[inp, buck5], [usb, buck]], page="A3")              # rows of blocks, centred on the page
        sch.reset(sheet)
        sch.build(sheet, 100, comps, hier, wire=25.4, blocks=B)          # each node row is one wire, one supply symbol
        sch.fields(sheet)                                                # reference and value level, beside each part
        sch.measure(sheet, B)
    sch.frames(sheet, B)                                                 # dashed frames and titles, last

`block` only sets x, y and rot; give it `at=(x, y)` when no `arrange` follows. `part(ref, lib, ...)` makes a part
without a place and `flag(ref, net)` a PWR_FLAG. `title_block` needs `configure(..., root="board.kicad_sch")`, and
`reset` keeps what it wrote. `blocks=` makes build draw every node
it laid out as a rail: one wire through the pins, named once (a signal) or with one power symbol at its free end (a
supply), and it refuses a rail that would run over another net's pin or through a part. A row can start at a
connector, `(j2, None, "2")`; a flag or a test point hangs from a rail like any part; `b.add(part)` takes in a part
placed by hand. `arrange` moves each block as a whole and gives the frames of a row one top, one height and one gap;
a block's size is an estimate until `measure` has seen it built, hence the two passes. `frames` names the parts
that are in no block.
"""
import os
import re

from .konnect import Konnect  # noqa: F401  (re-exported: sheet scripts open their own client for extra calls)
from .parts import FP, capacitor, resistor

# footprint names, re-exported for sheet scripts that build parts by hand
R0402, R0603, C0402, C0603, C0805, C1206 = (FP[k] for k in ("R0402", "R0603", "C0402", "C0603", "C0805", "C1206"))

P = ""  # project (sheet) directory with trailing slash; set with configure(), or assign P directly
ROOT_SHEET = None  # the root schematic, whose title block title_block() copies; set with configure(root=...)
# net -> (symbol, value override, native rotation of the symbol's pin)
POWER = {"GND": ("power:GND", None, 270), "+3V3": ("power:+3V3", None, 90), "+5V": ("power:+5V", None, 90),
         "+12V": ("power:+12V", None, 90), "VBUS": ("power:VBUS", None, 90)}


def configure(project_dir, power=None, root=None):
    """Set the sheet directory and add project power nets, e.g. {"VIN": ("power:VCC", "VIN", 90)}.
    root: the root schematic's file name, for title_block()."""
    global P, ROOT_SHEET
    P = project_dir if project_dir.endswith("/") else project_dir + "/"
    ROOT_SHEET = root or ROOT_SHEET
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


def part(ref, lib, val=None, fp=None, lcsc="", pins=None, mpn=None, **fields):
    """Any part, without a place: block(), chain() or put() gives it one. pins: {pin: net}; mpn and other keywords
    become symbol fields."""
    if mpn:
        fields["MPN"] = mpn
    return dict(ref=ref, lib=lib, val=val, fp=fp, x=0, y=0, rot=0, lcsc=lcsc, fields=fields, pins=dict(pins or {}))


def flag(ref, net):
    """A PWR_FLAG on `net`, to hang from a node in block(shunt=...) or taps: up from a supply, down from GND."""
    return dict(ref=ref, lib="power:PWR_FLAG", x=0, y=0, rot=0, pins={"1": net})


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


def _close(text, start):
    """The index of the bracket that closes the one at `start`, quoted strings skipped."""
    i, depth, quoted = start, 0, False
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
                return i
        i += 1


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
    start = text.index("(", m.start())
    i = _close(text, start)
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
    """Sheet offsets from the part's origin: ({pin: (dx, dy)}, (dx, dy) of the middle of its drawing and pins,
    (half its width, half its height))."""
    from .checks.sch_lint import apply, transform
    m = transform(c.get("rot", 0) if rot is None else rot, c.get("mirror"))
    pins, pts = geometry(c["lib"], c.get("unit", 1))
    off = {n: apply(m, x, -y) for n, (x, y) in pins.items()}
    allp = [apply(m, x, -y) for x, y in pts] + list(off.values())
    lo, hi = [min(p[i] for p in allp) for i in (0, 1)], [max(p[i] for p in allp) for i in (0, 1)]
    return off, ((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2), ((hi[0] - lo[0]) / 2, (hi[1] - lo[1]) / 2)


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

        def along(rot):  # (1 when the pin is on the edge of the part that faces away from `toward`, how far the body reaches)
            off, mid, half = _offsets(c, rot)
            d = (mid[0] - off[n][0]) * dx + (mid[1] - off[n][1]) * dy
            return round(d / max(half[0] * abs(dx) + half[1] * abs(dy), 0.01), 2), round(d, 3)
        c["rot"] = max((0, 90, 180, 270), key=lambda rot: along(rot) + (-rot,))
    off = _offsets(c)[0][n]
    c["x"], c["y"] = round(at[0] - off[0], 2), round(at[1] - off[1], 2)
    return c


def _series(comps, net=None):
    """[(part, pin_in, pin_out)] for parts in series. A two-pin part enters by the pin that shares a net with the
    part before it; the first by its pin on `net`, else by the pin that doesn't share one with the second, else
    pin 1. Give (part, pin_in, pin_out) for any other part."""
    items = [(c, None, None) if isinstance(c, dict) else tuple(c) for c in comps]
    for i, (c, pin_in, pin_out) in enumerate(items):
        if pin_out is None:
            names = sorted(c["pins"])
            assert len(names) == 2, "%s has %d pins: give (part, pin_in, pin_out)" % (c["ref"], len(names))
            if i:
                before = {items[i - 1][0]["pins"][items[i - 1][2]]}
                shared = [n for n in names if c["pins"][n] in before]
            elif net is not None:
                shared = [n for n in names if c["pins"][n] == net]
            else:
                after = set(items[1][0]["pins"].values()) if len(items) > 1 else set()
                shared = [n for n in names if c["pins"][n] not in after]
            pin_in = shared[0] if len(shared) == 1 else names[0]
            items[i] = (c, pin_in, [n for n in names if n != pin_in][0])
    return [(c, a if a is None else str(a), str(b)) for c, a, b in items]


def chain(at, comps, toward="right", gap=5.08):
    """Parts in series from `at`, each `gap` mm after the one before, so build(wire=...) joins them with straight
    wires. A two-pin part enters by the pin that shares a net with the part before it (the first: the pin that
    doesn't share one with the second, else pin 1) and leaves by the other; give (c, pin_in, pin_out) for any other
    part. Returns where the last part's leaving pin ends."""
    out = at
    for i, (c, pin_in, pin_out) in enumerate(_series(comps)):
        put(c, pin_in, at if not i else step(out, toward, gap), toward)
        out = pin(c, pin_out)
    return out


PITCH = 12.7  # between parts hung side by side from one node: room for a reference and a value beside each
FREE = 7.62   # from the last pin of a supply rail to its power symbol


MARGIN = 12.7  # from the page edge to anything drawn: KiCad's border and its grid letters
TITLE_BLOCK = (200.0, 60.0)  # the bottom right corner of the page that KiCad's title block takes, with the margin


class Block:
    """What block() placed: `title`, `parts` (the component dicts), `rails` and `box`, (x0, y0, x1, y1) around the
    parts' drawings and pins as placed (fields and power symbols come on top of that; frames() measures the built
    sheet). A rail is a node drawn as one straight wire: dict(net, pts: the pin ends on it, open: the direction of
    its free end, where its one power symbol goes when the net is a supply, or None). build(blocks=[...]) draws them,
    and wires the nets in `far` (the sense pins) however far apart their pins are.
    `frame` is the rectangle arrange() gave it, or None."""

    def __init__(self, title, parts, rails=()):
        self.title, self.parts, self.rails, self.frame, self.measured, self.far = title, parts, list(rails), None, None, set()

    def add(self, *parts):
        """Take in parts placed by hand (a connector before the path, a flag), so they move and are framed with it."""
        self.parts += [c for c in parts if c not in self.parts]
        return self

    def move(self, dx, dy):
        """Shift the whole block, parts and rails."""
        for c in self.parts:
            c["x"], c["y"] = round(c["x"] + dx, 2), round(c["y"] + dy, 2)
        for r in self.rails:
            r["pts"] = [(round(x + dx, 2), round(y + dy, 2)) for x, y in r["pts"]]
        if self.frame:
            self.frame = tuple(round(v + d, 2) for v, d in zip(self.frame, (dx, dy, dx, dy)))
        return self

    def _hull(self, pins=True):
        from .checks.sch_lint import apply, transform
        pts = []
        for c in self.parts:
            m = transform(c.get("rot", 0), c.get("mirror"))
            ends, drawing = geometry(c["lib"], c.get("unit", 1))
            pts += [apply(m, x, -y) + (c,) for x, y in (list(ends.values()) if pins or not drawing else []) + drawing]
        pts = [(dx + c["x"], dy + c["y"]) for dx, dy, c in pts]
        return tuple(round(f(q[i] for q in pts), 2) for f, i in ((min, 0), (min, 1), (max, 0), (max, 1)))

    box = property(_hull)
    body = property(lambda self: self._hull(pins=False))

    @property
    def extent(self):
        """(x0, y0, x1, y1) the block will take on the sheet, as near as can be told before it is built: the
        parts, the reference and value where fields() puts them, a power symbol or a label at every pin (a wired
        pin has neither) and the free ends of the supply rails. It is an estimate; after measure() it is exact."""
        from .checks.sch_lint import text_width
        if self.measured:  # what measure() found on a built sheet, moved to where the block is now
            x, y = self.box[:2]
            return tuple(round(v + d, 2) for v, d in zip(self.measured, (x, y, x, y)))
        boxes = [self.box]
        for c in self.parts:
            x0, y0, x1, y1 = Block("", [c]).body
            off, mid, half = _offsets(c)
            names = [str(c.get(k) or "") for k in ("ref", "val") if not str(c.get("ref", "")).startswith("#")]
            wide = max([text_width(t, 1.27) for t in names] or [0])
            ends = sorted(set(pins(c).values()))
            if len(ends) == 2 and ends[0][0] == ends[1][0]:
                boxes.append((x0, y0, x1 + 1.0 + wide, y1))             # stands: its fields to the right
            elif len(ends) == 2 and ends[0][1] == ends[1][1]:
                boxes.append(((x0 + x1 - wide) / 2, y0 - 2.5, (x0 + x1 + wide) / 2, y1 + 2.5))   # lies flat: above and below
            else:
                boxes.append((x0, y0 - 5.08, max(x1, x0 + sum(text_width(t, 1.27) for t in names) + 2.54), y1))   # over the part
            for n, net in c["pins"].items():
                if net in (None, "NC") or n not in off:
                    continue
                px, py = c["x"] + off[n][0], c["y"] + off[n][1]
                dx, dy = (off[n][0] - mid[0]) / max(half[0], 0.01), (off[n][1] - mid[1]) / max(half[1], 0.01)
                ux, uy = ((1 if dx > 0 else -1), 0) if abs(dx) >= abs(dy) else (0, (1 if dy > 0 else -1))
                w = text_width(POWER[net][1] or POWER[net][0].split(":")[1] if net in POWER else net, 1.27)
                if net in POWER:   # the symbol 3.8 mm out, its value beyond that; sideways from the part when turned
                    reach, across = (6.0, w / 2) if ux == 0 else (4.5 + w, 1.0)
                else:              # a label: its name out from the pin
                    reach, across = (w + 1.0, 1.0) if uy == 0 else (2.0, w)
                qx, qy = px + ux * reach, py + uy * reach
                boxes.append((min(px, qx) - (across if ux == 0 else 0), min(py, qy) - (across if uy == 0 else 0),
                              max(px, qx) + (across if ux == 0 else 0), max(py, qy) + (across if uy == 0 else 0)))
        for r in self.rails:
            if r["open"] and r["net"] in POWER:
                far = max(r["pts"], key=lambda q: q[0] * DIRS[r["open"]][0] + q[1] * DIRS[r["open"]][1])
                fx, fy = step(far, r["open"], FREE)
                w = text_width(POWER[r["net"]][1] or r["net"], 1.27) / 2
                boxes.append((fx - w, fy - 6.0, fx + w, fy + 6.0))
        return tuple(round(f(q[i] for q in boxes), 2) for f, i in ((min, 0), (min, 1), (max, 2), (max, 3)))


def _across(c, others, side, gap, toward):
    """Put each of `others` beside c, turned the same way, with its pins in line with c's pins on the same nets:
    above a part that lies flat, `gap[0]` mm apart; further `toward` from one that stands, `gap[1]` mm apart."""
    names = sorted(c["pins"])
    assert len(names) == 2, "across: %s has %d pins" % (c["ref"], len(names))
    a, b = pin(c, names[0]), pin(c, names[1])
    flat = abs(a[0] - b[0]) > abs(a[1] - b[1])
    side = side or ("up" if flat else toward)
    gap = gap[0] if flat else gap[1]
    assert (DIRS[side][0] == 0) == flat, "across: %s lies %s, put its partner %s" % (
        c["ref"], "flat" if flat else "upright", "up or down" if flat else "left or right")
    for k, o in enumerate(others):
        on = {net: n for n, net in o["pins"].items()}
        assert len(o["pins"]) == 2 and set(on) == set(c["pins"].values()), "across: %s is not on the two nets of %s" % (o["ref"], c["ref"])
        first, second = on[c["pins"][names[0]]], on[c["pins"][names[1]]]

        def turn(rot):
            off = _offsets(o, rot)[0]
            return (off[second][0] - off[first][0]) * (b[0] - a[0]) + (off[second][1] - off[first][1]) * (b[1] - a[1])
        o["rot"] = max((0, 90, 180, 270), key=lambda rot: (round(turn(rot), 3), -rot))
        put(o, first, step(a, side, gap * (k + 1)))


def _hang(net, entries, start, along, pitch, gap, parts, count=False, room=None):
    """Hang parts from the node of `net`: the first at `start`, the next `pitch` further `along`, each turned to one
    side of that line. Returns how many places along the line were used, and the points the parts hang from."""
    sides = ("down", "up") if DIRS[along][1] == 0 else ("right", "left")
    used = {s: 0 for s in sides + (along,)}
    pts = []
    for e in entries:
        side = None
        if isinstance(e, tuple) and len(e) == 2 and isinstance(e[1], str):
            e, side = e
        if isinstance(e, dict) and len(e["pins"]) == 1:  # a flag, a test point
            series = [(e,) + tuple(e["pins"]) * 2]
        else:
            series = _series(e if isinstance(e, list) else [e], net)
        c, n, _ = series[0]
        assert c["pins"].get(n) == net, "%s has no pin on %s (pin %s is on %s)" % (c["ref"], net, n, c["pins"].get(n))
        if side is None:  # to ground: down; to a supply: up; where the line itself runs up or down, the first side
            other = series[-1][0]["pins"][series[-1][2]]
            want = "up" if other in POWER and other != "GND" else "down"
            side = want if want in sides or (want == along and not used[along]) else sides[0]
        assert side in sides or (side == along and not used[along]), "%s: hang it %s or %s from a line that runs %s (one part may carry straight on)" % (
            c["ref"], sides[0], sides[1], along)
        at = step(start, along, pitch * used[side])
        used[side] += 1 + (room or {}).get(id(c), 0)  # and the places of the parts across it
        pts.append(at)
        for i, (c, n, out) in enumerate(series):
            put(c, n, at if not i else step(at, side, gap), side)
            at = pin(c, out)
            parts.append(c)
    return used if count else max(used.values()), sorted(set(pts), key=pts.index)


def block(title, at=(0, 0), path=(), shunt=None, taps=(), across=(), sense=(), toward="right", pitch=PITCH, gap=5.08, out=7.62):
    """Place one functional block from the roles of its parts, and return a Block (for frames(), and for its box).

      at     where the block starts; leave it out when arrange() sets the page out.
      path   the parts the signal or the power passes through, in order, as chain() takes them. The first one's
             entering pin lands on `at` (after the shunts of its net, if any) and the row runs `toward`. A row
             can start at a source: (connector, None, pin) puts the connector before `at`, its pin on it.
      shunt  {net: [parts]}: parts hanging from a node of the path, on the net between two path parts or at
             either end. They stand `pitch` apart along the row and the path makes room for them. A part to GND
             hangs down, one to a supply up; (part, "up") says so. A list is parts in series from the node, as a
             divider is: [r_top, r_bottom].
      taps   [(part, pin, [parts])]: parts on a pin that is not on the path (a pull-up, a gate resistor, the
             decoupling of one supply pin). They hang from a node `out` mm outside that pin, the same way; from
             a pin that points up or down the sides are "right" and "left", and a part to GND below a pin that
             points down carries straight on;
             (part, pin, [parts], mm) sets the distance.
      sense  [(part, pin)]: a pin that senses a node elsewhere in the block (the feedback pin of a regulator whose
             divider hangs from the output). build(blocks=...) draws that net as a wire however far it has to
             go, round what lies between, where it would otherwise get a label at each end.
      across [(part, [parts])]: parts in parallel with a placed two-pin part (a feed-forward capacitor over a
             divider resistor, a clamp over a gate resistor). They sit beside it, pins in line: 7.62 mm above
             a part that lies flat; in the next places along the row after one that stands, which the row keeps
             free. (part, [parts], "down") or another side otherwise.

    Only x, y and rot of the parts change; build(wire=...) then draws what lines up. Nothing checks that two rows
    don't run into each other: `pcbkit sch-lint` on the built sheet does."""
    import math
    assert toward in ("right", "left"), "a block's path runs right or left"
    back = {"right": "left", "left": "right", "up": "down", "down": "up"}
    shunt = {k: list(v) for k, v in (shunt or {}).items() if v}
    series = _series(path)
    parts, rails, entry, rail = [], [], at, None
    beside = {id(item[0]): len(item[1]) for item in across if len(item) < 3 or item[2] == toward}
    first = series[0][0]["pins"].get(series[0][1]) if series[0][1] else None
    if first in shunt:
        n, pts = _hang(first, shunt.pop(first), at, toward, pitch, gap, parts, room=beside)
        entry = step(at, toward, pitch * n)
        rail = dict(net=first, pts=pts, open=back[toward])
        rails.append(rail)
    for c, pin_in, pin_out in series:
        put(c, pin_in or pin_out, entry, toward if pin_in else back[toward])  # a source lies before its pin
        parts.append(c)
        if rail:  # the node before this part runs on to its entering pin
            rail["pts"].append(pin(c, pin_in))
        end, net, rail = pin(c, pin_out), c["pins"].get(pin_out), None
        room = gap
        if net and net != "NC" and net not in POWER:  # a signal between two parts is named on its wire: room for the name
            from .checks.sch_lint import text_width
            room = max(gap, math.ceil((text_width(net, 1.27) + 2.54) / 2.54) * 2.54)
        entry = step(end, toward, room)
        if net in shunt:
            n, pts = _hang(net, shunt.pop(net), step(end, toward, pitch), toward, pitch, gap, parts, room=beside)
            entry = step(end, toward, pitch * (n + 1))
            rail = dict(net=net, pts=[end] + pts, open=None)
            rails.append(rail)
    if rail:
        rail["open"] = toward
    assert not shunt, "no path part enters or leaves on %s" % ", ".join(sorted(shunt))
    for tap in taps:
        c, n, entries = tap[:3]
        off, mid, half = _offsets(c)  # the side of the part the pin is on: where it reaches the edge
        dx, dy = (off[str(n)][0] - mid[0]) / max(half[0], 0.01), (off[str(n)][1] - mid[1]) / max(half[1], 0.01)
        along = ("right" if dx > 0 else "left") if abs(dx) >= abs(dy) else ("down" if dy > 0 else "up")
        net = c["pins"][str(n)]
        used, pts = _hang(net, entries, step(pin(c, n), along, tap[3] if len(tap) > 3 else out), along, pitch, gap, parts, count=True)
        rails.append(dict(net=net, pts=[pin(c, n)] + pts, open=None if used[along] else along))  # a part straight on closes the end
    for item in across:
        _across(item[0], item[1], item[2] if len(item) > 2 else None, (7.62, pitch), toward)
        parts += item[1]
        for n in item[0]["pins"]:  # beside a part that lies flat, each pin is straight above or below its partner's
            col = [pin(item[0], n)] + [q for o in item[1] for m, q in pins(o).items() if o["pins"][m] == item[0]["pins"][n]]
            if len({q[0] for q in col}) == 1:
                rails.append(dict(net=item[0]["pins"][n], pts=col, open=None))
    for r in rails:  # a part put across one that hangs from a rail has a pin on the rail's line too: run the rail to it
        flat = len({q[1] for q in r["pts"]}) == 1
        for o in [o for item in across for o in item[1]]:
            r["pts"] += [q for m, q in pins(o).items() if o["pins"].get(m) == r["net"] and q not in r["pts"]
                         and (q[1] == r["pts"][0][1] if flat else q[0] == r["pts"][0][0])]
    b = Block(title, parts, rails)
    b.far = {c["pins"][str(n)] for c, n in sense}
    return b


def arrange(rows, origin=(25.4, 30.48), gap=7.62, pad=2.54, page=None):
    """Set blocks out on the page: each list in `rows` is a row of blocks, left to right, the rows one under the
    other from `origin`. Every block is moved as a whole. Its frame is its `extent` plus `pad`; the frames of a row
    get the same height and stand `gap` apart, and every row starts at the same left edge. Call it after the
    blocks are made (and after Block.add) and before build. Returns (x0, y0, x1, y1) of the lot.
    page: a paper size ("A3", as reset() takes it) or (width, height) in mm: the lot is centred on that page instead
    of starting at `origin`, and kept clear of the title block in the bottom right corner.

    A block's extent is an estimate until the sheet has been built once. For frames that line up exactly, build
    twice: arrange, build, fields, then measure(sheet, blocks), and arrange, build, fields again, then frames."""
    import math
    from .checks.sch_lint import text_width
    up = lambda v: round(math.ceil(v / 1.27 - 1e-6) * 1.27, 2)  # noqa: E731
    y, right = origin[1], origin[0]
    for row in rows:
        ext = [b.extent for b in row]
        # a block moves by whole grid steps, so it can sit up to one step inside its frame: that step is added
        size = [(up(max(e[2] - e[0] + 2 * pad + 1.27, text_width(b.title, 2.0) + 1.27)), up(e[3] - e[1] + 2 * pad + 1.27)) for b, e in zip(row, ext)]
        height, x = max(h for _, h in size), origin[0]
        for b, e, (w, _) in zip(row, ext, size):
            b.move(up(x + pad - e[0]), up(y + pad - e[1]))
            b.frame = (round(x, 2), round(y, 2), round(x + w, 2), round(y + height, 2))
            x += w + gap
        right, y = max(right, x - gap), y + height + gap + 5.08  # and room for the next row's titles
    box = (origin[0], origin[1], round(right, 2), round(y - gap - 5.08, 2))
    if page:
        from .checks.sch_lint import PAPER
        W, H = PAPER[page] if isinstance(page, str) else page
        near = lambda v: round(round(v / 1.27) * 1.27, 2)  # noqa: E731
        top = 5.08  # the titles stand above the first row
        dx = near((W - (box[2] - box[0])) / 2 - box[0])
        dy = near((H - (box[3] - box[1]) + top) / 2 - box[1])
        if box[2] + dx > W - TITLE_BLOCK[0] and box[3] + dy > H - TITLE_BLOCK[1]:  # onto the title block: up, as far as the page has room
            dy = near(max(MARGIN + top - box[1], H - TITLE_BLOCK[1] - box[3]))
        for b in (b for row in rows for b in row):
            b.move(dx, dy)
        box = tuple(round(v + d, 2) for v, d in zip(box, (dx, dy, dx, dy)))
    return box


def _field_at(m, left, mid_y, w, h, align):
    """Where a field's anchor goes, with its angle and justify, so the text reads left to right in the box that
    starts at `left` (align "left"), ends there ("right") or is centred on it ("center"), at height mid_y: the
    file keeps the angle and justify as they were before the symbol was turned or mirrored."""
    from .checks.sch_lint import _text_box, apply
    for ang in (0, 90):
        for hj in (("center",) if align == "center" else ("left", "right")):
            x0, y0, x1, y1 = _text_box(0, 0, w, h, hj, "center", ang)
            (ax, ay), (bx, by) = apply(m, x0, y0), apply(m, x1, y1)
            d = (min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))
            if d[2] - d[0] < d[3] - d[1]:
                continue  # reads up the page
            edge = {"left": d[0], "right": d[2], "center": (d[0] + d[2]) / 2}[align]
            if abs(edge) < 0.01:
                return round(left, 2), round(mid_y - (d[1] + d[3]) / 2, 2), ang, hj
    raise AssertionError("no way to set the field level")


def fields(sheet, refs=None, gap=1.0):
    """Put the reference and value of every two-pin part (or of `refs`) where they read: level, beside a part that
    stands (reference over value, to its right, or to its left when something is in the way there) and above and
    below a part that lies flat. A part with more pins keeps its library positions unless it was turned so that
    they read up the page; then they go over it, or under. Run after build; it edits the file. Returns how many
    parts it set."""
    from .checks.sch_lint import Sheet, overlap, seg_in_box, shrink, text_width
    S = sheet_path(sheet)
    sh = Sheet(S)
    with open(S, encoding="utf-8") as f:
        text = f.read()
    whole = lambda s: (min([s["body"][0]] + [p[0] for _, p in s["pins"]]), min([s["body"][1]] + [p[1] for _, p in s["pins"]]),  # noqa: E731
                       max([s["body"][2]] + [p[0] for _, p in s["pins"]]), max([s["body"][3]] + [p[1] for _, p in s["pins"]]))
    done = 0
    for sym in sh.symbols:
        mine = {t["field"]: t for t in sh.texts if t["owner"] is sym and t.get("field") in ("Reference", "Value")}
        if sym["power"] or not mine or not any(ch.isdigit() for ch in sym["ref"]) \
                or (refs is not None and sym["ref"] not in refs) or text.count('(property "Reference" "%s"' % sym["ref"]) != 1:
            continue
        ends = sorted({p for _, p in sym["pins"]})
        two = len(ends) == 2 and (ends[0][0] == ends[1][0] or ends[0][1] == ends[1][1])
        if not two and all(t["box"][2] - t["box"][0] >= t["box"][3] - t["box"][1] for t in mine.values()):
            continue  # a part with more pins whose fields read level already: leave them where the library has them
        x0, y0, x1, y1 = sym["body"]
        cx, cy, order = (x0 + x1) / 2, (y0 + y1) / 2, [k for k in ("Reference", "Value") if k in mine]
        wide = {k: text_width(mine[k]["text"], 1.27) for k in order}

        def boxes(option):
            if option in ("right", "left"):  # the part stands: a column beside it
                ys = [cy] if len(order) == 1 else [cy - 1.27, cy + 1.27]
                edge = x1 + gap if option == "right" else x0 - gap
                return {k: ((edge, yy - 0.7, edge + wide[k], yy + 0.7) if option == "right" else (edge - wide[k], yy - 0.7, edge, yy + 0.7), option == "right" and "left" or "right", edge, yy)
                        for k, yy in zip(order, ys)}
            if option in ("over", "under"):  # a part with more pins: two lines from its left edge
                ys = [y0 - gap - 0.7 - 2.0, y0 - gap - 0.7] if option == "over" else [y1 + gap + 0.7, y1 + gap + 0.7 + 2.0]
                return {k: ((x0, yy - 0.7, x0 + wide[k], yy + 0.7), "left", x0, yy) for k, yy in zip(order, ys[-len(order):] if option == "over" else ys)}
            ys = [y0 - gap - 0.7, y1 + gap + 0.7] if option == "around" else [y0 - gap - 0.7 - 2.0, y0 - gap - 0.7]
            return {k: ((cx - wide[k] / 2, yy - 0.7, cx + wide[k] / 2, yy + 0.7), "center", cx, yy) for k, yy in zip(order, ys[-len(order):] if option == "above" else ys)}

        def free(option):
            for box, _, _, _ in boxes(option).values():
                small = shrink(box, 0.1)
                if any(overlap(small, whole(o)) for o in sh.symbols if o is not sym) or any(seg_in_box(w, small) for w in sh.wires) \
                        or any(overlap(small, t["box"]) for t in sh.texts if t["owner"] is not sym):
                    return False
            return True
        options = ("over", "under") if not two else ("right", "left") if ends[0][0] == ends[1][0] else ("around", "above")
        best = next((o for o in options if free(o)), options[0])
        for k, (box, align, edge, yy) in boxes(best).items():
            fx, fy, ang, hj = _field_at(sym["m"], edge, yy, wide[k], 1.27, align)
            i = text.index('(property "%s"' % k, text.index('(property "Reference" "%s"' % sym["ref"]))
            j = _close(text, i)
            prop = re.sub(r"\(at [^)]*\)", "(at %s %s %d)" % (fx, fy, ang), text[i:j + 1], count=1)
            prop = re.sub(r"\s*\(justify[^)]*\)", "", prop)
            if hj != "center":
                e = prop.index("(effects")
                e = _close(prop, e)
                prop = prop[:e].rstrip() + " (justify %s)" % hj + prop[e:]
            text = text[:i] + prop + text[j + 1:]
            mine[k]["box"] = box  # the next part sees it where it is now
        done += 1
    with open(S, "w", encoding="utf-8") as f:
        f.write(text)
    return done


def _content(sh, b):
    """(x0, y0, x1, y1) of what a block put on a built sheet: its parts with their fields, the wires from their
    pins and the ones that carry on from those, and the power symbols and labels on them."""
    from .checks.sch_lint import on_seg
    key = lambda p: (round(p[0], 2), round(p[1], 2))  # noqa: E731
    refs = {c["ref"] for c in b.parts}
    own = [s for s in sh.symbols if s["ref"] in refs]
    assert own, "none of %s is on %s" % (sorted(refs), sh.path)
    at = {key(p) for s in own for _, p in s["pins"]}
    wires, grew = [], True
    while grew:
        more = [w for w in sh.wires if w not in wires and any(key(q) in at or any(on_seg(q, v) for v in wires) for q in w)]
        wires += more
        grew = bool(more)
    at |= {key(q) for w in wires for q in w}
    on = lambda q: key(q) in at or any(on_seg(q, w) for w in wires)  # noqa: E731
    own += [s for s in sh.symbols if s["power"] and s["ref"] not in refs and any(on(p) for _, p in s["pins"])]
    boxes = [s["body"] for s in own] + [(q[0], q[1], q[0], q[1]) for q in at]
    boxes += [t["box"] for t in sh.texts if any(t["owner"] is s for s in own) or (t["owner"] is None and t["kind"] != "text" and on(t["at"]))]
    return tuple(f(q[i] for q in boxes) for f, i in ((min, 0), (min, 1), (max, 2), (max, 3)))


def measure(sheet, blocks):
    """Note on each block what it takes on the built sheet, so the next arrange() sets the blocks out by their real
    sizes. Returns the boxes."""
    from .checks.sch_lint import Sheet
    sh, out = Sheet(sheet_path(sheet)), []
    for b in blocks:
        box, (x, y) = _content(sh, b), b.box[:2]
        b.measured = tuple(round(v - d, 2) for v, d in zip(box, (x, y, x, y)))
        out.append(box)
    return out


def frames(sheet, blocks, margin=2.54, size=2.0):
    """Draw a dashed frame around each block on a built sheet, with its title above the top left corner. The frame
    is the one arrange() gave the block, widened where needed (it says so) to take in its parts with their fields,
    the wires from their pins, and the power symbols and labels on those. Run last (it edits the file, as mark_dnp
    does); it prints the parts that are in no block. Returns the frames, [(x0, y0, x1, y1)]."""
    import math
    import uuid
    from .checks.sch_lint import Sheet, text_width
    S = sheet_path(sheet)
    sh = Sheet(S)
    grid = lambda v, f: round(f(v / 1.27 + (1e-6 if f is math.floor else -1e-6)) * 1.27, 2)  # noqa: E731
    add, out, framed = [], [], set()
    for b in blocks:
        framed |= {c["ref"] for c in b.parts}
        box = _content(sh, b)
        x0, y0 = (grid(box[i] - margin, math.floor) for i in (0, 1))
        x1, y1 = (grid(box[i] + margin, math.ceil) for i in (2, 3))
        if b.frame:
            x0, y0, x1, y1 = min(x0, b.frame[0]), min(y0, b.frame[1]), max(x1, b.frame[2]), max(y1, b.frame[3])
            if (x0, y0, x1, y1) != tuple(b.frame):
                print("frames: %r needs %s, arrange() gave it %s" % (b.title, (x0, y0, x1, y1), tuple(b.frame)))
        x1 = max(x1, grid(x0 + text_width(b.title, size) + 1.27, math.ceil))
        out.append((x0, y0, x1, y1))
        add.append('\t(rectangle\n\t\t(start %s %s)\n\t\t(end %s %s)\n\t\t(stroke (width 0) (type dash))\n\t\t(fill (type none))\n'
                   '\t\t(uuid "%s")\n\t)\n' % (x0, y0, x1, y1, uuid.uuid4()))
        if b.title:
            add.append('\t(text "%s"\n\t\t(exclude_from_sim no)\n\t\t(at %s %s 0)\n\t\t(effects (font (size %s %s) (bold yes)) (justify left bottom))\n'
                       '\t\t(uuid "%s")\n\t)\n' % (b.title.replace("\\", "\\\\").replace('"', '\\"'), x0, round(y0 - 1.0, 2), size, size, uuid.uuid4()))
    loose = sorted({s["ref"] for s in sh.symbols if s["ref"] not in framed and (not s["power"] or s["ref"].startswith("#FLG"))})
    if loose:
        print("frames: in no block:", " ".join(loose))
    with open(S, encoding="utf-8") as f:
        text = f.read()
    i = text.rindex(")")
    with open(S, "w", encoding="utf-8") as f:
        f.write(text[:i].rstrip("\n") + "\n" + "".join(add) + ")\n")
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


def _detours(a, b, far=50.8):
    """Runs from a to b that go round what lies between: a short stub off each end, then a channel above, below,
    left or right of both, up to `far` mm away. Shortest first, fewer corners before more."""
    steps, runs = (0, 2.54, -2.54, 5.08, -5.08, 7.62, -7.62), []
    for flip in (False, True):  # channels that run level, then (x and y swapped) channels that run up the page
        (ax, ay), (bx, by) = (a, b) if not flip else (a[::-1], b[::-1])
        for k in range(1, int(far / 2.54) + 1):
            for yc in (max(ay, by) + 2.54 * k, min(ay, by) - 2.54 * k):
                for d in steps:
                    for e in steps:
                        pts = [(ax, ay), (ax + d, ay), (ax + d, yc), (bx + e, yc), (bx + e, by), (bx, by)]
                        pts = [(round(x, 2), round(y, 2)) for x, y in pts]
                        pts = [q for i, q in enumerate(pts) if not i or q != pts[i - 1]]
                        if any(p0 == p2 for p0, p2 in zip(pts, pts[2:])):
                            continue  # doubles back on itself
                        length = sum(abs(p[0] - q[0]) + abs(p[1] - q[1]) for p, q in zip(pts, pts[1:]))
                        runs.append((length + 2.54 * len(pts), [q[::-1] for q in pts] if flip else pts))
    return [r for _, r in sorted(runs)]


def _touch(s, t):
    """Do two wire segments meet or cross? Exact for horizontal and vertical ones."""
    return (min(s[0][0], s[1][0]) <= max(t[0][0], t[1][0]) + 0.01 and min(t[0][0], t[1][0]) <= max(s[0][0], s[1][0]) + 0.01
            and min(s[0][1], s[1][1]) <= max(t[0][1], t[1][1]) + 0.01 and min(t[0][1], t[1][1]) <= max(s[0][1], s[1][1]) + 0.01)


def plan_wires(nets, boxes, points, segments, reach, max_pins=WIRE_PINS, far=()):
    """Wires for small local nets. Pure geometry, no konnect.

    nets      {name: [(ref, pin, (x, y)), ...]}, the pins of each local net
    boxes     what a wire may not pass through: symbol outlines (body and pins) and visible texts, (x0, y0, x1, y1)
    points    every pin endpoint on the sheet, {(x, y): net or None}: a wire may only touch its own net's
    segments  wires already there, [((x1, y1), (x2, y2))]
    reach     the longest run between two pins, mm (along the wire)
    far       nets to wire however far apart their pins are: where no straight, L or Z run is clear, a run that goes
              round (see _detours) is taken. For the pin that senses a node elsewhere in its block.

    Returns {name: {"wires": [segment], "junctions": [(x, y)], "label": (x, y, rotation) or None,
    "loose": [(ref, pin)]}}: the pins in `loose` could not be reached and still need a label each; `label` names
    the wired part, and is None when nothing was wired."""
    from .checks.sch_lint import on_seg, seg_in_box, shrink, text_width
    wide = [shrink(b, -1.0) for b in boxes]
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
            if (d > reach and name not in far) or root(a) == root(b):
                continue
            run = next((r for r in _runs(a, b) if clear(r)), None)
            if not run and name in far:  # round what lies between, the channel a clear millimetre from everything
                run = next((r for r in _detours(a, b) if clear(r) and not any(
                    seg_in_box(seg, w) for seg in list(zip(r, r[1:]))[1:-1] for w in wide)), None)
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
                res["label"] = (s[0][0], round(max(s[0][1], s[1][1]) - 1.27, 2), 0)  # level, beside an upright wire
        taken += mine
    return out


def _name_spot(name, segs, boxes):
    """(x, y, rotation) for a net label on one of the wire segments: the longest one where the name is clear of
    `boxes` (texts and symbol bodies), else the longest. On an upright wire the name still reads level."""
    from .checks.sch_lint import overlap, text_width
    w, spots = text_width(name, 1.27), []
    for a, b in sorted(segs, key=lambda q: -(abs(q[0][0] - q[1][0]) + abs(q[0][1] - q[1][1]))):
        if a[1] == b[1]:
            x = round(min(a[0], b[0]) + 1.27, 2)
            spots.append(((x, a[1], 0), (x, a[1] - 1.9, x + w, a[1] - 0.2)))
        else:
            y = round(max(a[1], b[1]) - 1.27, 2)  # level, beside an upright wire
            spots.append(((a[0], y, 0), (a[0] + 0.2, y - 1.9, a[0] + w, y - 0.2)))
    return next((spot for spot, box in spots if not any(overlap(box, q) for q in boxes)), spots[0][0])


def _rails(blocks, L, pins_net, comps=()):
    """The rails of blocks, checked against the pin ends as placed and the drawings of `comps`. L: {(ref, pin):
    (x, y, ...)}; pins_net: {(ref, pin): net}. Returns [dict(net, wires: [segment], pins: [(ref, pin)] on it, open: (x, y) or None)]."""
    from .checks.sch_lint import on_seg, seg_in_box, shrink
    out, bodies = [], [(c["ref"], shrink(Block("", [c]).body, 0.2)) for c in comps]
    for b in blocks:
        for r in b.rails:
            pts = sorted(set(r["pts"]))
            free = None
            if r["open"] and r["net"] in POWER:
                dx, dy = DIRS[r["open"]]
                free = step(max(pts, key=lambda q: q[0] * dx + q[1] * dy), r["open"], FREE)
                pts = sorted(set(pts + [free]))
            segs = list(zip(pts, pts[1:]))
            on = [(k, v[:2]) for k, v in L.items() if any(on_seg(v[:2], seg) for seg in segs)]
            wrong = sorted(k for k, _ in on if pins_net.get(k) != r["net"])
            assert not wrong, "block %r: the %s rail runs over %s" % (b.title, r["net"], ", ".join("%s.%s" % k for k in wrong))
            through = sorted(ref for ref, box in bodies if any(seg_in_box(seg, box) for seg in segs))
            assert not through, "block %r: the %s rail runs through %s" % (b.title, r["net"], ", ".join(through))
            out.append(dict(net=r["net"], wires=segs, pins=[k for k, _ in on], at=[q for _, q in on],
                            open=free))
    for i, a in enumerate(out):
        for b_ in out[i + 1:]:
            assert a["net"] == b_["net"] or not any(_touch(s, t) for s in a["wires"] for t in b_["wires"]), \
                "the %s rail and the %s rail of two blocks touch" % (a["net"], b_["net"])
    return out


def build(sheet, base, comps, hier, wires=(), junctions=(), extra_power=(), skip=(), glob=None, wire=None, blocks=()):
    """Place comps on P+sheet, connect their pins, annotate LCSC and other fields. Returns pin locations.
    wire: draw small local nets as wires where two pins are within this many mm (see the module docstring).
    blocks: what block() returned: each node row is drawn as one wire, named once, with one power symbol."""
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
    rails = _rails(blocks, L, {(c["ref"], p): net for c in comps for p, net in c["pins"].items()}, comps) if blocks else []
    wires, junctions, extra_power, skip, named = list(wires), list(junctions), list(extra_power), set(skip), []
    by_net = {}
    for r in rails:
        by_net.setdefault(r["net"], []).append(r)
        wires += [(a[0], a[1], b_[0], b_[1]) for a, b_ in r["wires"]]
        if r["net"] in POWER:  # one symbol: at the free end, else on the first pin
            skip |= set(r["pins"] if r["open"] else sorted(r["pins"])[1:])
            if r["open"]:
                extra_power.append((r["net"], r["open"][0], r["open"][1], POWER[r["net"]][2]))
        elif r["net"] in hier or r["net"] in glob:  # its label goes on the first pin, as without a rail
            skip |= set(sorted(r["pins"])[1:])
        else:
            skip |= set(r["pins"])
    for net, rs in by_net.items():
        ends = [q for r in rs for seg in r["wires"] for q in seg]
        pin_at = {k: q for r in rs for k, q in zip(r["pins"], r["at"])}
        for q in sorted(set(ends)):  # a dot where three or more things meet
            if ends.count(q) + list(pin_at.values()).count(q) + sum(q == r["open"] for r in rs) >= 3 and q not in junctions:
                junctions.append(q)
        if net not in POWER and net not in hier and net not in glob:  # one name for the rails that touch
            groups = []
            for r in rs:
                pts = {q for seg in r["wires"] for q in seg}
                hit = [g for g in groups if g[0] & pts]
                for g in hit:
                    groups.remove(g)
                groups.append((pts.union(*[g[0] for g in hit]), r["wires"] + [w for g in hit for w in g[1]]))
            named += [(net, segs) for _, segs in groups]
    # supply pins that are connected so far, (net, x, y, th): the ones on rails, then every symbol placed
    placed = [(r["net"],) + tuple(L[k]) for r in rails if r["net"] in POWER for k in r["pins"] if k in skip]

    def pwr(net, x, y, th):
        if (x, y) in seen:
            return
        seen.add((x, y))
        for net2, x2, y2, th2 in placed:  # two pins of one supply side by side: a short wire to the symbol that is there
            if net2 == net and th2 == th and 0 < abs(x - x2) + abs(y - y2) < 2.6 and (x == x2 or y == y2):
                wires.append((x2, y2, x, y))
                placed.append((net, x, y, th))
                return
        placed.append((net, x, y, th))
        lib, val, nat = POWER[net]
        n[0] += 1
        e = {"lib_id": lib, "reference": f"#PWR{base + n[0]:04d}", "x": x, "y": y, "rotation": (th - nat) % 360}
        if val:
            e["value"] = val
        pw.append(e)

    beside = {}  # net -> [(x, y, th)] of its pins that are named so far

    def joins(net, x, y, th):
        """Two pins of one net side by side on a part (a connector's doubled pins): a short wire, not a second name."""
        for x2, y2, th2 in beside.setdefault(net, []):
            if th2 == th and 0 < abs(x - x2) + abs(y - y2) < 2.6 and (x == x2 or y == y2) and (x, y) not in seen:
                seen.add((x, y))
                wires.append((x2, y2, x, y))
                beside[net].append((x, y, th))
                return True
        beside[net].append((x, y, th))
        return False

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
            elif joins(net, x, y, th):
                continue
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
        plan = plan_wires(nets, boxes, points, [((a, b), (c_, d)) for a, b, c_, d in wires], wire,
                          far={net for b in blocks for net in b.far})
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
    if named:
        from .checks import sch_lint
        page = sch_lint.Sheet(S)
        boxes = [t["box"] for t in page.texts] + [sym["body"] for sym in page.symbols]
        for net, segs in named:
            x, y, rot = _name_spot(net, segs, boxes)
            k.call("add_schematic_net_label", {"schematic": S, "net": net, "label_type": "net_label", "x": x, "y": y, "rotation": rot})
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
    """Blank a child sheet in place, keeping its own uuid so hierarchy links survive, and its title block, so what
    title_block() or a release wrote there does."""
    from .release.versions import title_block as find
    S = sheet_path(sheet)
    txt = open(S).read()
    uuid = re.search(r'\(uuid "?([0-9a-f-]+)"?\)', txt).group(1)
    b = find(txt)
    tb = "\t" + txt[b[0]:b[1]] + "\n" if b else ""
    open(S, "w").write(f'(kicad_sch\n\t(version 20260306)\n\t(generator "eeschema")\n\t(generator_version "10.0")\n'
                       f'\t(uuid "{uuid}")\n\t(paper "{paper}")\n{tb}\t(lib_symbols)\n\t(embedded_fonts no)\n)\n')


def title_block(sheet, about=None, root=None, author=None, **fields):
    """Fill a sheet's title block so every page says what it belongs to. title, date, rev and company come from the
    root schematic (`root`, or configure(root=...)): the project's name is the title of every page, and a release
    stamps the same rev and date on all of them. `about` says what this sheet holds, on the line above the title
    (comment 1); `author` goes on the next (comment 2), else the root's comment 2 and up carry over.
    Keywords (title=, date=, rev=, company=, comment3=...) set a field outright. Returns the fields written."""
    from .release.versions import _esc, title_block as find, title_field
    S = sheet_path(sheet)
    txt = open(S).read()
    vals, comments = {}, {}
    root = root or ROOT_SHEET
    if root and os.path.abspath(sheet_path(root)) != os.path.abspath(S):
        rt = open(sheet_path(root)).read()
        vals = {k: title_field(rt, k) for k in ("title", "date", "rev", "company")}
        b = find(rt)
        for n, text in re.findall(r'\(comment (\d) "((?:[^"\\]|\\.)*)"\)', rt[b[0]:b[1]] if b else ""):
            if int(n) > 1:
                comments[int(n)] = re.sub(r"\\(.)", r"\1", text)
    b = find(txt)
    if b:  # what the sheet has already and the root does not say
        old = txt[b[0]:b[1]]
        for k in ("title", "date", "rev", "company"):
            vals[k] = vals.get(k) or title_field(txt, k)
        for n, text in re.findall(r'\(comment (\d) "((?:[^"\\]|\\.)*)"\)', old):
            comments.setdefault(int(n), re.sub(r"\\(.)", r"\1", text))
    if about:
        comments[1] = about
    if author:
        comments[2] = author
    for k, v in fields.items():
        if k.startswith("comment"):
            comments[int(k[7:])] = v
        else:
            assert k in ("title", "date", "rev", "company"), "title_block: no field %r" % k
            vals[k] = v
    lines = ['\t\t(%s "%s")' % (k, _esc(vals[k])) for k in ("title", "date", "rev", "company") if vals.get(k)]
    lines += ['\t\t(comment %d "%s")' % (n, _esc(comments[n])) for n in sorted(comments) if comments[n]]
    new = "(title_block\n" + "\n".join(lines) + "\n\t)"
    if b:
        txt = txt[:b[0]] + new + txt[b[1]:]
    else:
        m = re.search(r"\n\t*\(paper [^\n]*", txt)
        assert m, "%s: no (paper ...) to put a title block after" % S
        txt = txt[:m.end()] + "\n\t" + new + txt[m.end():]
    open(S, "w").write(txt)
    return dict(vals, **{"comment%d" % n: v for n, v in comments.items()})


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
