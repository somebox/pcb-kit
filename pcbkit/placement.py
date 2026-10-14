"""Score a placement by its ratsnest, and search placements of footprint groups, before routing anything.

  pcbkit place-score BOARD [--study STUDY.py] [--planes GND,VLED] [--any-side] [--search] [--rounds 3]
                           [--start PLACEMENT.json] [--write PLACEMENT.json] [--svg OUT.svg] [--png OUT.png]

A full route can take most of an hour; a ratsnest score takes milliseconds, so placements are compared by it and
only the winner is routed. BOARD is a placed board, routed or not (tracks are ignored). The score adds up:
  * crossings between different nets' ratsnest edges (each net's minimum spanning tree over its pads), counted
    only where the two could meet on one layer: an edge between front-only pads never meets one between back-only
    pads; through-hole pads, and edges that change sides, meet everything (--any-side counts every crossing);
  * the ratsnest length, mm;
  * zones: courtyard area (mm^2) over the study's penalty polygons, e.g. where the other side's vias will go;
  * the study's free choices (gate or pin assignments), each solved per candidate (see assign()).
Plane nets (--planes, or the study's) stay out: their pads only need a via. A placement is invalid when courtyards
on one side overlap (a through-hole pad counts on both sides), a pad's copper comes closer than edge_clr to the
board outline (a connector body may overhang), or a courtyard enters a forbidden zone or a rule area that keeps
footprints out. Only pairs that involve a moved footprint are checked: the board as placed is the reference.

Without --study it scores the board as placed; with --svg it draws the ratsnest for review: outline, courtyards
(front solid, back dashed), pads, edges by side (front red, back blue, both purple), crossings circled.

A study is a Python file in the project (it can import the project's own layout code) that defines STUDY, or a
function study(board) returning one:

    from pcbkit.placement import Study, Group, Zone, Perm, grid, assign
    STUDY = Study(
        planes={"GND", "VLED"},
        groups=[Group("buck", ["U4", "L2", "C25"], grid(dx=(-6, 6, 1), dy=(-6, 6, 1), rot=(0, 90, 180, 270))),
                Group("qwiic", ["J2", "R12"], range(40, 145, 5), place=layout.qwiic, home=90)],
        zones=[Zone([(x, y), ...], weight=2.0, side="B")],
        perms=[Perm("gates", gates, nets=["ROW0_G", ...])],
        weights=dict(cross=10.0, length=0.2))

A rigid group moves by (dx, dy, rot) from where the board has it, rotating about its first footprint. A group with
place= calls the project's code with the candidate and gets {ref: (x, y, orientation)} back, so the board follows
the project's own generator; home= is its current parameter. --search runs coordinate descent: every candidate of
one group with the others held, group after group, until a round improves nothing. --write saves
{"groups": {name: params}, "choices": {perm: choice}, "score": {...}} for the project's generator to read;
--start reads one back. The board file is never written. Runs under KiCad's Python (re-runs itself there).

Harvested from Orbitchron's scripts/drv_place_score.py (sided crossings, via-field zones, solved free choices,
coordinate descent), siebenuhr controller-v2's scripts/layout/score.py (MST length and crossings per variant) and
rgb-spotlight's scripts/ratsnest.py (the review picture). See docs/HARVEST.md.
"""
import argparse
import importlib.util
import itertools
import json
import math
import os
import subprocess
import sys
from collections import defaultdict

from .kicad import ensure_pcbnew

WEIGHTS = dict(cross=10.0, length=0.2)
SIDE_COLOUR = {"F": "#d9480f", "B": "#1971c2", "X": "#9c36b5"}


# ---------------------------------------------------------------- the study

def grid(dx=(0, 0, 1), dy=(0, 0, 1), rot=(0,), r=None):
    """Candidates (dx, dy, rot) for a rigid group: (start, stop, step) ranges in mm, both ends included; r keeps
    only offsets within that distance."""
    def rng(a):
        lo, hi, st = a
        return [round(lo + k * st, 6) for k in range(int(round((hi - lo) / st)) + 1)] if st else [lo]
    return [(x, y, float(t)) for x in rng(dx) for y in rng(dy) if r is None or math.hypot(x, y) <= r + 1e-9
            for t in rot]


class Group:
    """Footprints that move together. candidates: a list of parameters, or a function (current) -> list.
    Without place: parameters are (dx, dy[, rot]) from the board's placement, rotating about refs[0]. With place:
    place(parameter) -> {ref: (x, y, orientation degrees)} for footprints of refs, and home is the board's current
    parameter."""

    def __init__(self, name, refs, candidates, place=None, home=None):
        if place is not None and home is None:
            raise ValueError(f"group {name}: place= needs home= (the parameter the board is placed with)")
        self.name, self.refs, self.place = name, list(refs), place
        self.candidates = candidates if callable(candidates) else list(candidates)
        self.home = home if place is not None else (0.0, 0.0, 0.0) if home is None else home

    def options(self, current):
        return list(self.candidates(current)) if callable(self.candidates) else self.candidates


class Zone:
    """A polygon [(x, y)] in board mm. A penalty zone costs weight x courtyard mm^2 inside it; forbid makes any
    overlap invalid. side "F", "B" or None (both); refs: the footprints it applies to (default: every moved one)."""

    def __init__(self, polygon, weight=1.0, side=None, forbid=False, refs=None, name=""):
        self.polygon, self.weight, self.side, self.forbid = [tuple(p) for p in polygon], weight, side, forbid
        self.refs, self.name = set(refs) if refs else None, name


class Perm:
    """A free choice solved for every candidate, e.g. which register output drives which gate: solve(ctx) returns
    (cost, choice) or (cost, choice, edges). nets: the nets the choice decides, kept out of the base ratsnest (the
    solver prices their edges itself, usually with assign())."""

    def __init__(self, name, solve, nets=()):
        self.name, self.solve, self.nets = name, solve, set(nets)


class Study:
    def __init__(self, groups=(), zones=(), perms=(), planes=(), weights=None, any_side=False, edge_clr=0.3):
        self.groups, self.zones, self.perms = list(groups), list(zones), list(perms)
        self.planes, self.any_side, self.edge_clr = set(planes), any_side, edge_clr
        self.weights = dict(WEIGHTS, **(weights or {}))


def load_study(path, board):
    path = os.path.abspath(path)
    sys.path.insert(0, os.path.dirname(path))              # the study may import the project's layout code
    spec = importlib.util.spec_from_file_location("pcbkit_study", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if hasattr(mod, "study"):
        return mod.study(board)
    return mod.STUDY


# ---------------------------------------------------------------- ratsnest geometry

def mst(pts):
    """Prim's minimum spanning tree over points [(x, y, ...)]: [(i, j)]."""
    if len(pts) < 2:
        return []
    d = {i: math.hypot(pts[0][0] - pts[i][0], pts[0][1] - pts[i][1]) for i in range(1, len(pts))}
    par, out = {i: 0 for i in d}, []
    while d:
        i = min(d, key=d.get)
        out.append((par[i], i))
        del d[i]
        for j in d:
            dj = math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
            if dj < d[j]:
                d[j], par[j] = dj, i
    return out


def edge(net, a, c):
    """Ratsnest edge between pads a, c (x, y, side, ref, num): (net, (x, y), (x, y), side). Side F or B when both
    pads are on that side only, X otherwise (it can change sides through a via, or a pad is through-hole)."""
    return (net, (a[0], a[1]), (c[0], c[1]), a[2] if a[2] == c[2] and a[2] != "X" else "X")


def net_edges(net, pads):
    return [edge(net, pads[i], pads[j]) for i, j in mst(pads)]


def length(edges):
    return sum(math.hypot(e[1][0] - e[2][0], e[1][1] - e[2][1]) for e in edges)


def _segx(p1, p2, p3, p4):
    o = lambda a, b, c: (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return o(p1, p2, p3) * o(p1, p2, p4) < 0 and o(p3, p4, p1) * o(p3, p4, p2) < 0


def _bbox(e):
    return min(e[1][0], e[2][0]), min(e[1][1], e[2][1]), max(e[1][0], e[2][0]), max(e[1][1], e[2][1])


def crosses(e, f, any_side=False):
    """Different nets, sides that can meet on one layer, and the segments intersect."""
    if e[0] == f[0] or (not any_side and {e[3], f[3]} == {"F", "B"}):
        return False
    a, b = _bbox(e), _bbox(f)
    if a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1]:
        return False
    return _segx(e[1], e[2], f[1], f[2])


class EdgeIndex:
    """Edges bucketed on a coarse grid, for counting crossings against a fixed set quickly."""
    CELL = 4.0

    def __init__(self, edges, any_side=False):
        self.edges, self.any_side, self.buckets = list(edges), any_side, defaultdict(list)
        for k, e in enumerate(self.edges):
            for key in self._keys(e):
                self.buckets[key].append(k)

    def _keys(self, e):
        x0, y0, x1, y1 = _bbox(e)
        C = self.CELL
        return [(i, j) for i in range(int(x0 // C), int(x1 // C) + 1) for j in range(int(y0 // C), int(y1 // C) + 1)]

    def count(self, edges, points=None):
        """Crossings of `edges` with the indexed ones (points: a list to collect where they cross)."""
        n = 0
        for e in edges:
            seen = set()
            for key in self._keys(e):
                for k in self.buckets.get(key, ()):
                    if k not in seen:
                        seen.add(k)
                        if crosses(e, self.edges[k], self.any_side):
                            n += 1
                            if points is not None:
                                points.append(_meet_point(e, self.edges[k]))
        return n

    def count_within(self, points=None):
        """Crossings among the indexed edges themselves, each pair once."""
        n = 0
        for k, e in enumerate(self.edges):
            seen = set()
            for key in self._keys(e):
                for m in self.buckets.get(key, ()):
                    if m > k and m not in seen:
                        seen.add(m)
                        if crosses(e, self.edges[m], self.any_side):
                            n += 1
                            if points is not None:
                                points.append(_meet_point(e, self.edges[m]))
        return n


def count_among(edges, any_side=False, points=None):
    n = 0
    for i, e in enumerate(edges):
        for f in edges[i + 1:]:
            if crosses(e, f, any_side):
                n += 1
                if points is not None:
                    points.append(_meet_point(e, f))
    return n


def _meet_point(e, f):
    (x1, y1), (x2, y2), (x3, y3), (x4, y4) = e[1], e[2], f[1], f[2]
    d = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / d if d else 0.5
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


# ---------------------------------------------------------------- free choices

class NetDict(dict):
    """Nets by full name ("/Rows/ROW2_G") or, when it is unique, by the last part of it ("ROW2_G")."""

    def __missing__(self, key):
        hits = [k for k in self if k.split("/")[-1] == key]
        if len(hits) != 1:
            raise KeyError(f"net {key}" + (f" is ambiguous: {hits}" if hits else " is not on the board (or is a plane)"))
        return self[hits[0]]


class Ctx:
    """What a Perm's solver sees: pads per net (planes left out; full or short net names) as (x, y, side, ref, num),
    the base ratsnest (every net but the study's free ones), weights, and crossing counts that honour the side
    rule."""

    def __init__(self, pads, base_index, extra, weights, any_side, pad_of):
        self.pads, self.weights, self.any_side = pads, weights, any_side
        self._index, self._extra, self._pad_of = base_index, extra, pad_of

    @property
    def base(self):
        return self._index.edges + self._extra

    def pad(self, ref, num):
        """A pad of any net, planes included: (x, y, side, ref, num)."""
        return self._pad_of(ref, num)

    def edge(self, net, a, c):
        return edge(net, a, c)

    def mst(self, net, pads):
        return net_edges(net, pads)

    def cross(self, edges, others=None):
        """Crossings of edges with the base ratsnest, or with `others` (each pair once, different nets only)."""
        if others is not None:
            return sum(1 for e in edges for f in others if crosses(e, f, self.any_side))
        return self._index.count(edges) + sum(1 for e in edges for f in self._extra if crosses(e, f, self.any_side))


def assign(slots, options, edges, ctx, exact_up_to=9):
    """Best one-to-one choice of an option per slot (more options than slots is fine). edges(slot, option) -> the
    ratsnest edges that choice makes. Cost: length weight x mm + crossing weight x (crossings with the base ratsnest
    and among the chosen edges). Exact branch and bound up to exact_up_to slots, else a greedy start improved by
    swaps. Returns (cost, {slot: option}, [chosen edges])."""
    wc, wl = ctx.weights["cross"], ctx.weights["length"]
    slots, options = list(slots), list(options)
    es = {(s, o): edges(s, o) for s in slots for o in options}
    single = {k: wl * length(v) + wc * ctx.cross(v) for k, v in es.items()}
    pair_cache = {}

    def pair(a, b):
        k = (a, b) if a <= b else (b, a)
        if k not in pair_cache:
            pair_cache[k] = wc * ctx.cross(es[a], es[b])
        return pair_cache[k]

    def cost_of(choice):
        ks = [(s, choice[s]) for s in slots]
        return sum(single[k] for k in ks) + sum(pair(ks[i], ks[j]) for i in range(len(ks)) for j in range(i + 1, len(ks)))

    if len(slots) <= exact_up_to:
        best = [math.inf, None]
        order = sorted(slots, key=lambda s: min(single[(s, o)] for o in options))

        def dfs(i, used, chosen, c):
            if c >= best[0]:
                return
            if i == len(order):
                best[0], best[1] = c, dict(chosen)
                return
            s = order[i]
            for o in sorted(options, key=lambda o: single[(s, o)]):
                if o in used:
                    continue
                add = single[(s, o)] + sum(pair((s, o), (t, chosen[t])) for t in chosen)
                chosen[s] = o
                used.add(o)
                dfs(i + 1, used, chosen, c + add)
                used.discard(o)
                del chosen[s]
        dfs(0, set(), {}, 0.0)
        choice = best[1]
    else:
        choice, used = {}, set()
        for s in sorted(slots, key=lambda s: min(single[(s, o)] for o in options)):
            o = min((o for o in options if o not in used), key=lambda o: single[(s, o)])
            choice[s] = o
            used.add(o)
        cur, improved = cost_of(choice), True
        while improved:
            improved = False
            for s, t in itertools.combinations(slots, 2):
                trial = dict(choice)
                trial[s], trial[t] = choice[t], choice[s]
                c = cost_of(trial)
                if c < cur - 1e-9:
                    choice, cur, improved = trial, c, True
            for s in slots:
                for o in options:
                    if o not in choice.values():
                        trial = dict(choice)
                        trial[s] = o
                        c = cost_of(trial)
                        if c < cur - 1e-9:
                            choice, cur, improved = trial, c, True
    return cost_of(choice), choice, [e for s in slots for e in es[(s, choice[s])]]


# ---------------------------------------------------------------- scoring a board

class Scorer:
    def __init__(self, board, study):
        import pcbnew
        self.pcbnew, self.b, self.s = pcbnew, board, study
        self.T = pcbnew.ToMM
        self.fps = {f.GetReference(): f for f in board.GetFootprints()}
        self.home = {r: (f.GetPosition(), f.GetOrientation()) for r, f in self.fps.items()}
        seen = {}
        for g in study.groups:
            for r in g.refs:
                if r not in self.fps:
                    raise SystemExit(f"group {g.name}: {r} is not on the board")
                if r in seen:
                    raise SystemExit(f"{r} is in groups {seen[r]} and {g.name}")
                seen[r] = g.name
        self.group_of = seen
        self.moving = set(seen)
        for z in study.zones:
            missing = sorted((z.refs or set()) - set(self.fps))
            if missing:
                raise SystemExit(f"zone {z.name or '(unnamed)'}: {', '.join(missing)} not on the board")
        self._courts, self._blk = {}, {}                  # per footprint, dropped when it moves
        self.net_refs = defaultdict(set)
        for r, f in self.fps.items():
            for q in f.Pads():
                if self._signal(q.GetNetname()):
                    self.net_refs[q.GetNetname()].add(r)
        names = NetDict({n: n for n in self.net_refs})
        self.perm_nets = {names[n] for p in study.perms for n in p.nets}
        self.pads_of = {r: self._read_pads(r) for r in self.fps}
        outline = pcbnew.SHAPE_POLY_SET()
        board.GetBoardPolygonOutlines(outline, False)
        self.outline = outline
        self.inner = pcbnew.SHAPE_POLY_SET(outline)
        self.inner.Deflate(pcbnew.FromMM(study.edge_clr), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, pcbnew.FromMM(0.005))
        self.zones = [(z, self._poly(z.polygon)) for z in study.zones]
        for ra in board.Zones():                          # rule areas that keep footprints out
            if ra.GetIsRuleArea() and ra.GetDoNotAllowFootprints():
                for side, L in (("F", pcbnew.F_Cu), ("B", pcbnew.B_Cu)):
                    if ra.GetLayerSet().Contains(L):
                        self.zones.append((Zone([], side=side, forbid=True, name=ra.GetZoneName() or "rule area"),
                                           pcbnew.SHAPE_POLY_SET(ra.Outline())))

    # -- board access
    def _signal(self, net):
        return bool(net) and not net.startswith("unconnected") and net not in self.s.planes \
            and net.split("/")[-1] not in self.s.planes

    def _side(self, q):
        fr, bk = q.IsOnLayer(self.pcbnew.F_Cu), q.IsOnLayer(self.pcbnew.B_Cu)
        return "X" if fr == bk else "F" if fr else "B"

    def _read_pads(self, ref):
        T, out = self.T, []
        for q in self.fps[ref].Pads():
            if self._signal(q.GetNetname()):
                p = q.GetPosition()
                out.append((q.GetNetname(), (T(p.x), T(p.y), self._side(q), ref, q.GetNumber())))
        return out

    def pad(self, ref, num):
        q = self.fps[ref].FindPadByNumber(str(num))
        if q is None:
            raise KeyError(f"{ref} has no pad {num}")
        p = q.GetPosition()
        return (self.T(p.x), self.T(p.y), self._side(q), ref, q.GetNumber())

    def _poly(self, pts):
        pcbnew = self.pcbnew
        sp = pcbnew.SHAPE_POLY_SET()
        sp.NewOutline()
        for x, y in pts:
            sp.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
        return sp

    def net_pads(self, nets):
        out = {}
        for n in nets:
            out[n] = [p for r in sorted(self.net_refs[n]) for nn, p in self.pads_of[r] if nn == n]
        return out

    # -- moving
    def apply_group(self, g, v):
        pcbnew = self.pcbnew
        if g.place is not None:
            for ref, (x, y, o) in g.place(v).items():
                if ref not in g.refs:
                    raise SystemExit(f"group {g.name}: place() moved {ref}, which is not in its refs")
                f = self.fps[ref]
                f.SetPosition(pcbnew.VECTOR2I_MM(x, y))
                f.SetOrientationDegrees(o)
        else:
            v = tuple(v) + (0.0,) * (3 - len(v))
            anchor = self.home[g.refs[0]][0]
            for ref in g.refs:
                f = self.fps[ref]
                f.SetPosition(self.home[ref][0])
                f.SetOrientation(self.home[ref][1])
                if v[2]:
                    f.Rotate(anchor, pcbnew.EDA_ANGLE(float(v[2]), pcbnew.DEGREES_T))
                if v[0] or v[1]:
                    f.Move(pcbnew.VECTOR2I_MM(v[0], v[1]))
        for ref in g.refs:
            self.pads_of[ref] = self._read_pads(ref)
            self._courts.pop(ref, None)
            self._blk.pop(ref, None)

    def apply(self, params):
        for g in self.s.groups:
            self.apply_group(g, params[g.name])

    # -- validity and zones
    def _court(self, ref):
        if ref not in self._courts:
            pcbnew, f = self.pcbnew, self.fps[ref]
            f.BuildCourtyardCaches()
            cy = f.GetCourtyard(pcbnew.B_CrtYd if f.IsFlipped() else pcbnew.F_CrtYd)
            if cy.OutlineCount():
                self._courts[ref] = pcbnew.SHAPE_POLY_SET(cy)
            else:                                         # no courtyard: the footprint's box
                try:
                    bb = f.GetBoundingBox(False)
                except TypeError:
                    bb = f.GetBoundingBox()
                T = self.T
                self._courts[ref] = self._poly([(T(bb.GetLeft()), T(bb.GetTop())), (T(bb.GetRight()), T(bb.GetTop())),
                                                (T(bb.GetRight()), T(bb.GetBottom())), (T(bb.GetLeft()), T(bb.GetBottom()))])
        return self._courts[ref]

    def _blockers(self, ref):
        """{side: (box, shapes)} of a footprint: its courtyard on its own side, its through-hole pads on the other."""
        if ref not in self._blk:
            pcbnew, f = self.pcbnew, self.fps[ref]
            own = "B" if f.IsFlipped() else "F"
            shapes = {own: [self._court(ref)]}
            for q in f.Pads():
                if q.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
                    shapes.setdefault("F" if own == "B" else "B", []).append(
                        pcbnew.SHAPE_POLY_SET(q.GetEffectivePolygon(pcbnew.F_Cu)))
            out = {}
            for side, ss in shapes.items():
                boxes = [s.BBox() for s in ss]
                out[side] = ((min(b.GetLeft() for b in boxes), min(b.GetTop() for b in boxes),
                              max(b.GetRight() for b in boxes), max(b.GetBottom() for b in boxes)), ss)
            self._blk[ref] = out
        return self._blk[ref]

    @staticmethod
    def _overlap(a, b):
        ba, bb = a.BBox(), b.BBox()
        if ba.GetRight() < bb.GetLeft() or bb.GetRight() < ba.GetLeft() or ba.GetBottom() < bb.GetTop() or bb.GetBottom() < ba.GetTop():
            return 0.0
        i = a.__class__(a)
        i.BooleanIntersection(b)
        return i.Area() / 1e12

    def check(self, refs):
        """Invalid reason for the moved footprints refs, or ''."""
        pcbnew = self.pcbnew
        for ref in sorted(refs):
            for q in self.fps[ref].Pads():
                if q.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                    continue
                L = pcbnew.B_Cu if q.IsOnLayer(pcbnew.B_Cu) and not q.IsOnLayer(pcbnew.F_Cu) else pcbnew.F_Cu
                ol = q.GetEffectivePolygon(L).Outline(0)
                if not all(self.inner.Contains(ol.CPoint(i)) for i in range(ol.PointCount())):
                    return f"{ref} pad {q.GetNumber()} off the board"
        refs = set(refs)
        checked = set()
        for r in sorted(refs):
            for o in self.fps:
                if o == r or (o, r) in checked:
                    continue
                checked.add((r, o))
                for side, (ba, shapes) in self._blockers(r).items():
                    if side not in self._blockers(o):
                        continue
                    bo, oshapes = self._blockers(o)[side]
                    if ba[2] < bo[0] or bo[2] < ba[0] or ba[3] < bo[1] or bo[3] < ba[1]:
                        continue
                    if any(self._overlap(a, c) > 1e-3 for a in shapes for c in oshapes):
                        return f"{r} overlaps {o} ({side})"
        for z, poly in self.zones:
            if z.forbid:
                for r in refs:
                    if (z.refs is None or r in z.refs) and z.side in (None, "B" if self.fps[r].IsFlipped() else "F") \
                            and self._overlap(self._court(r), poly) > 1e-3:
                        return f"{r} in {z.name or 'a forbidden zone'}"
        return ""

    def zone_cost(self):
        c = 0.0
        for z, poly in self.zones:
            if z.forbid:
                continue
            for r in (z.refs or self.moving):
                if z.side in (None, "B" if self.fps[r].IsFlipped() else "F"):
                    c += z.weight * self._overlap(self._court(r), poly)
        return c

    # -- the score
    def _ratsnest(self, nets):
        out = []
        for n, ps in self.net_pads(nets).items():
            out += net_edges(n, ps)
        return out

    def _finish(self, cross, length_, base_index, extra):
        w, s = self.s.weights, self.s
        zc = self.zone_cost()
        perms, choices, perm_edges = {}, {}, []
        if s.perms:
            ctx = Ctx(NetDict(self.net_pads(list(self.net_refs))), base_index, extra, w, s.any_side, self.pad)
            for p in s.perms:
                r = p.solve(ctx)
                perms[p.name], choices[p.name] = r[0], r[1]
                if len(r) > 2:
                    perm_edges += r[2]
        total = w["cross"] * cross + w["length"] * length_ + zc + sum(perms.values())
        return dict(total=round(total, 2), crossings=cross, length=round(length_, 1), zones=round(zc, 2),
                    perms={k: round(v, 2) for k, v in perms.items()}, choices=choices, perm_edges=perm_edges)

    def full(self, params, points=None):
        """Apply every group's parameter and score the whole board. None and the reason if it is invalid."""
        self.apply(params)
        why = self.check(self.moving)
        if why:
            return None, why
        nets = [n for n in self.net_refs if n not in self.perm_nets]
        edges = self._ratsnest(nets)
        idx = EdgeIndex(edges, self.s.any_side)
        return self._finish(idx.count_within(points), length(edges), idx, []), ""

    def begin(self, g):
        """Hold every group but g where it is: index the edges g cannot change."""
        aff = {n for r in g.refs for n, _ in self.pads_of[r]} - self.perm_nets
        fixed = self._ratsnest([n for n in self.net_refs if n not in aff and n not in self.perm_nets])
        self._g, self._aff = g, sorted(aff)
        self._fixed = EdgeIndex(fixed, self.s.any_side)
        self._fixed_cross, self._fixed_len = self._fixed.count_within(), length(fixed)

    def trial(self, v):
        """Score the begun group at parameter v: (result, '') or (None, reason)."""
        g = self._g
        self.apply_group(g, v)
        why = self.check(set(g.refs))
        if why:
            return None, why
        moved = self._ratsnest(self._aff)
        cross = self._fixed_cross + self._fixed.count(moved) + count_among(moved, self.s.any_side)
        return self._finish(cross, self._fixed_len + length(moved), self._fixed, moved), ""


def search(sc, params, rounds=3, log=print):
    """Coordinate descent over the study's groups. Returns (params, result, [the best few (total, params)])."""
    best, why = sc.full(params)
    if best is None:
        log(f"start is invalid ({why}); searching anyway")
        best = dict(total=math.inf)
    seen = {}
    for rnd in range(rounds):
        improved = False
        for g in sc.s.groups:
            sc.apply(params)
            sc.begin(g)
            opts, valid = g.options(params[g.name]), 0
            for v in opts:
                res, _ = sc.trial(v)
                if res is None:
                    continue
                valid += 1
                trial = dict(params, **{g.name: v})
                seen[json.dumps(trial, sort_keys=True, default=str)] = res["total"]
                if res["total"] < best["total"] - 1e-6:
                    best, params, improved = res, trial, True
            log(f"round {rnd + 1} {g.name} ({valid} of {len(opts)} candidates valid): {params[g.name]} -> {_fmt(best)}")
        if not improved:
            break
    sc.apply(params)
    best, _ = sc.full(params)
    top = [(t, json.loads(k)) for k, t in sorted(seen.items(), key=lambda kv: kv[1])[:5]]
    return params, best, top


# ---------------------------------------------------------------- the picture

def draw(sc, path, label=""):
    """Ratsnest review SVG of the board as it stands: outline, courtyards, pads, edges by side, crossings.
    Returns the score of what it drew."""
    T = sc.T
    bb = sc.outline.BBox()
    x0, y0, x1, y1 = T(bb.GetLeft()) - 2, T(bb.GetTop()) - 2, T(bb.GetRight()) + 2, T(bb.GetBottom()) + 2
    S = 1600 / (x1 - x0)
    X = lambda x: f"{(x - x0) * S:.1f}"
    Y = lambda y: f"{(y - y0) * S + 40:.1f}"
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="{(y1 - y0) * S + 40:.0f}" font-family="Helvetica">',
           '<rect width="100%" height="100%" fill="white"/>']

    def path_of(poly, style):
        for k in range(poly.OutlineCount()):
            ol = poly.Outline(k)
            d = " ".join(f"{'M' if i == 0 else 'L'}{X(T(ol.CPoint(i).x))},{Y(T(ol.CPoint(i).y))}" for i in range(ol.PointCount()))
            out.append(f'<path d="{d} Z" {style}/>')
    path_of(sc.outline, 'fill="#f8f9fa" stroke="black" stroke-width="2"')
    for z, poly in sc.zones:
        path_of(poly, f'fill="{"#ffc9c9" if z.forbid else "#fff3bf"}" fill-opacity="0.6" stroke="none"')
    for r, f in sorted(sc.fps.items()):
        cy = sc._court(r)
        moved = r in sc.moving
        path_of(cy, f'fill="none" stroke="{"#2b8a3e" if moved else "#868e96"}" stroke-width="{2 if moved else 1}"'
                    + (' stroke-dasharray="6,4"' if f.IsFlipped() else ""))
        c = f.GetPosition()
        out.append(f'<text x="{X(T(c.x))}" y="{Y(T(c.y))}" font-size="9" fill="#495057" text-anchor="middle">{_esc(r)}</text>')
    nets = [n for n in sc.net_refs if n not in sc.perm_nets]
    edges = sc._ratsnest(nets)
    pts = []
    idx = EdgeIndex(edges, sc.s.any_side)
    n = idx.count_within(pts)
    res = sc._finish(n, length(edges), idx, [])
    out.insert(2, f'<text x="10" y="26" font-size="18">{_esc(label)}{": " if label else ""}{_esc(_fmt(res))}</text>')
    for e in edges + res["perm_edges"]:
        out.append(f'<line x1="{X(e[1][0])}" y1="{Y(e[1][1])}" x2="{X(e[2][0])}" y2="{Y(e[2][1])}" '
                   f'stroke="{SIDE_COLOUR[e[3]]}" stroke-width="1.5"' + (' stroke-dasharray="3,3"' if e in res["perm_edges"] else "") + '/>')
    for ps in sc.pads_of.values():
        for _, p in ps:
            out.append(f'<circle cx="{X(p[0])}" cy="{Y(p[1])}" r="2" fill="{SIDE_COLOUR[p[2]]}"/>')
    for x, y in pts:
        out.append(f'<circle cx="{X(x)}" cy="{Y(y)}" r="7" fill="none" stroke="#e03131" stroke-width="2"/>')
    out.append("</svg>")
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")
    return res


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt(r):
    if r is None or r.get("total") == math.inf:
        return "invalid"
    perms = "".join(f", {k} {v}" for k, v in r.get("perms", {}).items())
    return f"{r['total']} (crossings {r['crossings']}, length {r['length']} mm, zones {r['zones']}{perms})"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("--study", help="Python file defining STUDY (or study(board))")
    ap.add_argument("--planes", help="comma-separated plane nets to leave out (added to the study's)")
    ap.add_argument("--any-side", action="store_true", help="count every crossing, whatever the sides")
    ap.add_argument("--search", action="store_true", help="coordinate descent over the study's groups")
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--start", help="start from a placement JSON written by --write")
    ap.add_argument("--write", help="save the placement (groups, choices, score) as JSON")
    ap.add_argument("--svg", help="ratsnest review picture of the result")
    ap.add_argument("--png", help="also convert the SVG to this PNG with rsvg-convert")
    a = ap.parse_args(argv)
    ensure_pcbnew()
    import pcbnew
    b = pcbnew.LoadBoard(a.board)
    study = load_study(a.study, b) if a.study else Study()
    if a.planes:
        study.planes |= {p.strip() for p in a.planes.split(",") if p.strip()}
    study.any_side = study.any_side or a.any_side
    sc = Scorer(b, study)
    params = {g.name: g.home for g in study.groups}
    if a.start:
        params.update(json.load(open(a.start))["groups"])
    res, why = sc.full(params)
    print("current:", why and f"invalid: {why}" or _fmt(res))
    if a.search and study.groups:
        params, res, top = search(sc, params, a.rounds)
        print("best:", _fmt(res), json.dumps(params))
        same = json.loads(json.dumps(params, sort_keys=True, default=str))
        for t, p in [x for x in top if x[1] != same][:3]:
            print(f"  next: {t}", json.dumps(p))
    if a.write:
        if res is None:
            sys.exit("not writing an invalid placement")
        out = dict(groups=params, choices={k: {str(kk): vv for kk, vv in sorted(v.items(), key=lambda kv: str(kv[0]))}
                                           if isinstance(v, dict) else v for k, v in res["choices"].items()},
                   score={k: res[k] for k in ("total", "crossings", "length", "zones", "perms")})
        with open(a.write, "w") as fh:
            json.dump(out, fh, indent=1)
            fh.write("\n")
        print("wrote", a.write)
    if a.svg:
        sc.apply(params)
        draw(sc, a.svg, os.path.basename(a.board))
        if a.png:
            subprocess.run(["rsvg-convert", "-b", "white", a.svg, "-o", a.png], check=True)
        print(a.png or a.svg)


if __name__ == "__main__":
    main()
