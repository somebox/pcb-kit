"""Scripted routing on a KiCad board: a maze router that completes nets, a GND/plane via stitcher, escape vias,
fine-pitch fan-out, neck-down and a track-end audit. Board millimetres throughout (KiCad's frame, y down).

    from pcbkit.route import writer, complete_nets, fanout, plane_vias, neckdown, audit_track_ends
    track, via = writer(board, via_size=(0.6, 0.3))
    P = dict(CLEARANCE=0.2, EDGE_CLR=0.3, VIA=(0.6, 0.3), VIA_PAD_CLR=0.15, QUICK=True, AXIAL_PADS=True)
    placed, failed = fanout(board, "U4", P, area, track, via, skip_nets=("GND", "+3V3"), outline=poly)
    P.update(DIR={F_Cu: "h", B_Cu: "v"}, ORDER="longest")       # optional: route discipline, see below
    done, failed = complete_nets(board, P, area, [(r"USB_D[PN]", [F_Cu, B_Cu], 0.25), ...], track, via, outline=poly)

complete_nets(plan): for each net matching a pattern, join its copper islands one at a time with A* on a 0.1 mm
grid over (cell, layer), vias where clear; a connection that fails in its corridor is left (QUICK) for hand routing.
Parameters (dict P): CLEARANCE, EDGE_CLR, VIA (diameter, drill), VIA_PAD_CLR, QUICK, AXIAL_PADS (tracks end on pad
centres), HWEIGHT (greedier A*), LAYER_COST ({layer id: cost}, e.g. make B.Cu dear to keep a GND pour whole),
IN1_COST (old name for the In1 cost), HOLE_CLEARANCE (unplated holes keep this from copper when it is larger than
CLEARANCE), CONN_SECONDS (time for one connection, all its widths and corridors; a failed connection otherwise uses
its whole expansion budget, which dominates a run: 40 s each in QUICK mode, minutes without), DEADLINE (time.time()
after which complete_nets tries nothing more and reports the rest as failed). Pads are obstacles on every copper
layer they reach, with their shape on that layer (see pad_items), unplated holes on all of them.
Opt-in route discipline (left out, the router behaves as before):
- DIR ({layer id: "h" | "v"}): a preferred direction per layer, e.g. {F_Cu: "h", B_Cu: "v"} on a board whose inner
  layers are planes, so the two outer layers cross instead of wandering round chips. A move along the layer's
  direction costs DIR_COST[0] per cell, across it DIR_COST[1], at 45 degrees DIR_COST[2] (x its length); default
  (1.0, 4.0, 2.0), values below 1 count as 1. Layers not in DIR, LAYER_COST, the turn and via costs work as before.
  complete_nets and tie_pads take it from P.
- ORDER = "longest": complete_nets routes every connection of the plan longest first (straight-line distance between
  the two copper groups it joins), across nets and plan entries, instead of net by net in plan order.
fanout(ref): dog-bone fan-out of a fine-pitch chip before routing: a via per signal pad, out from the package in two
staggered rows (`dist`, `dist + stagger` beyond the pad edge), so the router starts from vias that leave channels.
`outline`: rectangle (x0, y0, x1, y1), circle (cx, cy, r) or polygon [(x, y)].
`keepouts`: [(shape, allowed net or None, layer)], shape a rectangle (x0, y0, x1, y1) or a polygon [(x, y)]; track
and via edges stay out (keepout_hit tests a disc or a capsule against them, e.g. for hand-placed vias).

Harvested 2026-10-12 from scripts/route_strip.py of rp2354-messages-sign and rgb-spotlight (identical apart from
spotlight's round outlines) and the plane-via, neck-down and audit steps of the sign's scripts/route_controller.py;
the LED-strip fan-in and its pin lists stay in those projects. See docs/HARVEST.md.
"""
import heapq
import math
import os as _os
import re as _re
import time as _time
from collections import Counter


def writer(b, via_size=(0.6, 0.3)):
    """track(a, z, width, layer, net) and via(xy, net, size=None) that add copper to board b (mm, board frame)."""
    import pcbnew
    mm = pcbnew.FromMM

    def track(a, z, w, layer, net):
        t = pcbnew.PCB_TRACK(b); t.SetStart(pcbnew.VECTOR2I_MM(*a)); t.SetEnd(pcbnew.VECTOR2I_MM(*z))
        t.SetWidth(mm(w)); t.SetLayer(layer); t.SetNet(net); b.Add(t)
        return t

    def via(xy, net, size=None):
        d, dr = size or via_size
        v = pcbnew.PCB_VIA(b); v.SetPosition(pcbnew.VECTOR2I_MM(*xy)); v.SetViaType(pcbnew.VIATYPE_THROUGH)
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu); v.SetWidth(mm(d)); v.SetDrill(mm(dr)); v.SetNet(net); b.Add(v)
        return v
    return track, via


def _in_poly(p, poly):
    x, y, inside = p[0], p[1], False
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            inside = not inside
    return inside


def _poly_edge_dist(p, poly):
    return min(_seg_dist(p, a, z) for a, z in zip(poly, poly[1:] + poly[:1]))


def _shape_bbox(shape):
    """Keep-out shape: a rectangle (x0, y0, x1, y1) or a polygon [(x, y), ...]."""
    if isinstance(shape, list):
        xs, ys = [q[0] for q in shape], [q[1] for q in shape]
        return (min(xs), min(ys), max(xs), max(ys))
    return tuple(shape)


def _shape_dist(p, shape):
    """Signed distance from p to a keep-out shape's boundary: negative inside."""
    if isinstance(shape, list):
        return _poly_edge_dist(p, shape) * (-1 if _in_poly(p, shape) else 1)
    x0, y0, x1, y1 = shape
    if x0 <= p[0] <= x1 and y0 <= p[1] <= y1:
        return -min(p[0] - x0, x1 - p[0], p[1] - y0, y1 - p[1])
    return _rect_dist(p, shape)


def _poly_seg_dist(a, z, poly):
    """Distance from segment az to a polygon's edges (0 if it crosses one)."""
    return min(_seg_seg(a, z, e0, e1) for e0, e1 in zip(poly, poly[1:] + poly[:1]))


def _seg_shape_dist(a, z, shape):
    """Distance from segment az to a keep-out shape: 0 if it touches or enters it."""
    if isinstance(shape, list):
        return 0.0 if _in_poly(a, shape) or _in_poly(z, shape) else _poly_seg_dist(a, z, shape)
    return _seg_rect(a, z, shape)


def keepout_hit(keepouts, c, r, layers, net=None, end=None):
    """True if a disc (centre c, radius r), or with `end` the capsule from c to end, touches a keep-out
    [(shape, allowed net or None, layer)] on any of `layers` ("all" for every layer)."""
    z = c if end is None else end
    for shape, allowed, L in keepouts:
        if (net is not None and allowed == net) or (layers != "all" and L not in layers):
            continue
        x0, y0, x1, y1 = _shape_bbox(shape)
        if min(c[0], z[0]) > x1 + r or max(c[0], z[0]) < x0 - r or min(c[1], z[1]) > y1 + r or max(c[1], z[1]) < y0 - r:
            continue
        if (_shape_dist(c, shape) if end is None else _seg_shape_dist(c, end, shape)) < r:
            return True
    return False


def chamfer(pts, c):
    """Replace each 90-degree corner of an orthogonal polyline with a 45-degree segment of leg c (capped at
    0.45 x the shorter neighbouring segment, so nested routes keep their clearance)."""
    if c <= 0 or len(pts) < 3:
        return pts
    seg = lambda a, z: ((z[0] - a[0]) ** 2 + (z[1] - a[1]) ** 2) ** 0.5
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, m, z = pts[i - 1], pts[i], pts[i + 1]
        la, lz = seg(a, m), seg(m, z)
        if la < 1e-9 or lz < 1e-9 or abs((m[0] - a[0]) * (z[0] - m[0]) + (m[1] - a[1]) * (z[1] - m[1])) > 1e-9:
            out.append(m)                                  # not a right angle: keep
            continue
        k = min(c, 0.45 * la, 0.45 * lz)
        out.append((m[0] - (m[0] - a[0]) / la * k, m[1] - (m[1] - a[1]) / la * k))
        out.append((m[0] + (z[0] - m[0]) / lz * k, m[1] + (z[1] - m[1]) / lz * k))
    out.append(pts[-1])
    return out



# ---- GND stitching ----------------------------------------------------------------------------------------
def _seg_dist(p, a, z):
    ax, ay = a; zx, zy = z; px, py = p
    dx, dy = zx - ax, zy - ay
    L = dx * dx + dy * dy
    t = 0 if L == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L))
    return ((px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2) ** 0.5


def _seg_seg(a, z, c, d):
    """Distance between segments az and cd (0 if they cross)."""
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    o1, o2, o3, o4 = orient(a, z, c), orient(a, z, d), orient(c, d, a), orient(c, d, z)
    if (o1 > 0) != (o2 > 0) and (o3 > 0) != (o4 > 0) and 0 not in (o1, o2, o3, o4):
        return 0.0
    return min(_seg_dist(a, c, d), _seg_dist(z, c, d), _seg_dist(c, a, z), _seg_dist(d, a, z))


def _seg_rect(a, z, r):
    """Exact distance between segment az and axis-aligned rectangle r (0 if they touch)."""
    x0, y0, x1, y1 = r
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    if any(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in (a, z)):
        return 0.0
    edges = list(zip(corners, corners[1:] + corners[:1]))
    return min(min(_seg_seg(a, z, c, d) for c, d in edges), _rect_dist(a, r), _rect_dist(z, r))


def _rect_dist(p, r):
    x0, y0, x1, y1 = r
    dx = max(x0 - p[0], 0, p[0] - x1); dy = max(y0 - p[1], 0, p[1] - y1)
    return (dx * dx + dy * dy) ** 0.5


def pad_items(q, cu_layers, cover=True):
    """pad_item for each shape a pad has on cu_layers: one, unless its padstack differs by layer (KiCad's
    front/inner/back and custom modes), then one per group of layers sharing a shape. They are one plated pad, so
    they connect (net_components joins them)."""
    import pcbnew
    groups = {}
    for L in cu_layers:
        if q.IsOnLayer(L):
            groups.setdefault(q.Padstack().EffectiveLayerFor(L), []).append(L)
    if len(groups) < 2 or q.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
        return [pad_item(q, cu_layers, cover)]
    return [pad_item(q, ls, cover, ls[0]) for ls in groups.values()]


def pad_item(q, cu_layers, cover=True, layer=None):
    """(layers, kind, geometry) of a pad, board mm. Square to the axes: its bounding box ("rect"). Round: a "disc".
    At any other angle a "seg" capsule along the long side: with cover, wide enough to cover the corners (an obstacle);
    without, inscribed in the pad (connectivity, route ends). A plated pad is on every copper layer of cu_layers it
    reaches, inner ones included, with its shape on `layer` (default its front, or its back if it has no front; see
    pad_items for a padstack that differs by layer); an unplated hole is a disc of the drill on all of cu_layers."""
    import pcbnew
    mm = pcbnew.ToMM
    if q.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
        d = q.GetDrillSize()
        c = (mm(q.GetPosition().x), mm(q.GetPosition().y))
        if d.x == d.y:
            return set(cu_layers), "disc", (c, mm(d.x) / 2)
        o = math.radians(q.GetOrientationDegrees())                  # a slot: capsule along its long side
        ux, uy = (math.cos(o), -math.sin(o)) if d.x > d.y else (math.sin(o), math.cos(o))
        hl, r = abs(mm(d.x) - mm(d.y)) / 2, min(mm(d.x), mm(d.y)) / 2
        return set(cu_layers), "seg", ((c[0] - ux * hl, c[1] - uy * hl), (c[0] + ux * hl, c[1] + uy * hl), r)
    layers = {L for L in cu_layers if q.IsOnLayer(L)}
    L = layer if layer is not None else pcbnew.B_Cu if q.IsOnLayer(pcbnew.B_Cu) and not q.IsOnLayer(pcbnew.F_Cu) \
        else pcbnew.F_Cu
    shape, ang = q.GetShape(L), q.GetOrientationDegrees() % 90
    sp = q.ShapePos(L)
    c = (mm(sp.x), mm(sp.y))
    if shape == pcbnew.PAD_SHAPE_CIRCLE:
        return layers, "disc", (c, mm(q.GetSize(L).x) / 2)
    if min(ang, 90 - ang) < 0.5 or shape not in (pcbnew.PAD_SHAPE_RECT, pcbnew.PAD_SHAPE_ROUNDRECT, pcbnew.PAD_SHAPE_OVAL,
                                                 pcbnew.PAD_SHAPE_CHAMFERED_RECT):
        r = q.GetEffectiveShape(L).BBox()                          # also trapezoids and custom shapes, any angle
        return layers, "rect", (mm(r.GetLeft()), mm(r.GetTop()), mm(r.GetRight()), mm(r.GetBottom()))
    w, h = mm(q.GetSize(L).x), mm(q.GetSize(L).y)
    o = math.radians(q.GetOrientationDegrees())
    ux, uy = (math.cos(o), -math.sin(o)) if w >= h else (math.sin(o), math.cos(o))
    long_, short = max(w, h), min(w, h)
    hl, r = (long_ - short) / 2, short / 2
    if cover and shape != pcbnew.PAD_SHAPE_OVAL:                   # an oval pad is the capsule itself
        rr = mm(q.GetRoundRectCornerRadius(L)) if shape == pcbnew.PAD_SHAPE_ROUNDRECT else 0.0
        r += (short / 2 - rr) * (math.sqrt(2) - 1) + 0.01
    elif not cover and shape == pcbnew.PAD_SHAPE_CHAMFERED_RECT and q.GetChamferPositions(L):
        r = min(r, short * (1 - q.GetChamferRectRatio(L)) / math.sqrt(2))   # inside the cut corners (ratio > 0.29)
    return layers, "seg", ((c[0] - ux * hl, c[1] - uy * hl), (c[0] + ux * hl, c[1] + uy * hl), r)


class Copper:
    """Just enough geometry to place a via + stub without asking DRC: pads as rectangles, capsules or discs (see
    pad_item), tracks as capsules, vias as discs, per copper layer, tagged with net names; bucketed on a 1 mm grid
    for fast lookups. hole_extra widens unplated holes, for a hole clearance above the copper clearance."""
    B = 1.0

    def __init__(self, b, hole_pitch=0.55, hole_extra=0.0):
        import pcbnew
        mm = pcbnew.ToMM
        self.hole_pitch = hole_pitch                         # drill 0.25 + hole-to-hole 0.25, plus margin
        self.via_pad_clr = 0.15
        self.items, self.buckets = [], {}                    # items: (layers, kind, geometry, net)
        self.pads = set()                                    # ids of the items that are pads
        cu = list(b.GetEnabledLayers().CuStack())
        for fp in b.GetFootprints():
            for q in fp.Pads():
                for ls, kind, g in pad_items(q, cu):
                    if q.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH and hole_extra > 0:
                        g = (g[0], g[1] + hole_extra) if kind == "disc" else (g[0], g[1], g[2] + hole_extra)
                    self._add((ls, kind, g, q.GetNetname()))
                    self.pads.add(id(self.items[-1]))
        for t in b.GetTracks():
            if t.Type() == pcbnew.PCB_VIA_T:
                self.add_via((mm(t.GetPosition().x), mm(t.GetPosition().y)), mm(t.GetWidth(pcbnew.F_Cu)) / 2, t.GetNetname())
            else:
                self.add_track((mm(t.GetStart().x), mm(t.GetStart().y)), (mm(t.GetEnd().x), mm(t.GetEnd().y)),
                               mm(t.GetWidth()) / 2, t.GetLayer(), t.GetNetname())

    @staticmethod
    def _bbox(kind, g):
        if kind == "rect":
            return g
        if kind == "disc":
            (x, y), r = g
            return (x - r, y - r, x + r, y + r)
        (ax, ay), (zx, zy), hw = g
        return (min(ax, zx) - hw, min(ay, zy) - hw, max(ax, zx) + hw, max(ay, zy) + hw)

    def _keys(self, bb, pad=0.0):
        B = self.B
        return [(i, j) for i in range(int((bb[0] - pad) // B), int((bb[2] + pad) // B) + 1)
                for j in range(int((bb[1] - pad) // B), int((bb[3] + pad) // B) + 1)]

    def _add(self, it):
        self.items.append(it)
        for k in self._keys(self._bbox(it[1], it[2])):
            self.buckets.setdefault(k, []).append(len(self.items) - 1)

    def add_via(self, c, r, net):
        self._add(("all", "disc", (c, r), net))

    def add_track(self, a, z, hw, layer, net):
        self._add(({layer}, "seg", (a, z, hw), net))

    def near(self, kind, g, pad):
        seen = set()
        for k in self._keys(self._bbox(kind, g), pad):
            for i in self.buckets.get(k, ()):
                if i not in seen:
                    seen.add(i)
                    yield self.items[i]

    def clear(self, shape, layers, net, clr):
        """shape: ('disc', (c, r)) or ('seg', (a, z, hw)); True if clr from all other-net copper on layers."""
        kind, g = shape
        for it in self.near(kind, g, max(clr, self.hole_pitch) + 0.05):
            ils, ik, ig, inet = it
            if kind == "disc" and inet == net and id(it) in self.pads:  # no via in or touching any pad, same net too
                c, r = g
                d = _rect_dist(c, ig) if ik == "rect" else math.hypot(c[0] - ig[0][0], c[1] - ig[0][1]) - ig[1] \
                    if ik == "disc" else _seg_dist(c, ig[0], ig[1]) - ig[2]
                if d - r < self.via_pad_clr:
                    return False
                continue
            if kind == "disc" and ik == "disc" and inet == net:        # same-net vias still need hole spacing
                if ((g[0][0] - ig[0][0]) ** 2 + (g[0][1] - ig[0][1]) ** 2) ** 0.5 < self.hole_pitch:
                    return False
                continue
            if inet == net or (ils != "all" and layers != "all" and not (ils & layers)):
                continue
            if kind == "disc":
                c, r = g
                if ik == "rect":
                    d = _rect_dist(c, ig) - r
                elif ik == "disc":
                    d = ((c[0] - ig[0][0]) ** 2 + (c[1] - ig[0][1]) ** 2) ** 0.5 - r - ig[1]
                else:
                    d = _seg_dist(c, ig[0], ig[1]) - r - ig[2]
            else:
                a, z, hw = g
                if ik == "rect":
                    d = _seg_rect(a, z, ig) - hw
                elif ik == "disc":
                    d = _seg_dist(ig[0], a, z) - hw - ig[1]
                else:
                    d = _seg_seg(a, z, ig[0], ig[1]) - hw - ig[2]
            if d < clr - 1e-6:
                return False
        return True


def _copper(b, p):
    """Copper(b) with the plan's via-to-pad clearance and, when HOLE_CLEARANCE is above CLEARANCE, unplated holes
    widened by the difference."""
    cu = Copper(b, hole_extra=max(0.0, p.get("HOLE_CLEARANCE", p["CLEARANCE"]) - p["CLEARANCE"]))
    cu.via_pad_clr = p.get("VIA_PAD_CLR", 0.15)
    return cu


def stitch_gnd(b, fps, p, area, track, via, skip=lambda ref: False):
    """Give every GND pad (outside `skip`) a via to the GND plane or pour: try spots around the pad, outward
    from its footprint first, and keep the first one clear of other-net copper on every layer and inside `area`."""
    import pcbnew
    mm = pcbnew.ToMM
    cu = _copper(b, p)
    vr, clr = p["VIA"][0] / 2, p["CLEARANCE"]
    stub_w = p["GND_STUB_W"]
    placed, failed = 0, []
    for ref, fp in sorted(fps.items()):
        if skip(ref):
            continue
        fx, fy = mm(fp.GetPosition().x), mm(fp.GetPosition().y)
        for q in fp.Pads():
            if q.GetNetname() != "GND":
                continue
            layer = pcbnew.B_Cu if q.IsOnLayer(pcbnew.B_Cu) else pcbnew.F_Cu
            px, py = mm(q.GetPosition().x), mm(q.GetPosition().y)
            bb = q.GetBoundingBox()
            hw, hh = mm(bb.GetWidth()) / 2, mm(bb.GetHeight()) / 2
            ox, oy = px - fx, py - fy                       # outward from the footprint centre
            n = (ox * ox + oy * oy) ** 0.5 or 1.0
            r2 = 0.5 ** 0.5
            dirs = [(ox / n, oy / n), (0, -1), (0, 1), (-1, 0), (1, 0),
                    (r2, r2), (-r2, r2), (r2, -r2), (-r2, -r2), (-ox / n, -oy / n)]
            ok = False
            for gap in (0.25, 0.5, 0.9, 1.4):
                for dx, dy in dirs:
                    reach = abs(dx) * hw + abs(dy) * hh + vr + gap
                    c = (px + dx * reach, py + dy * reach)
                    if not (area[0] + vr <= c[0] <= area[2] - vr and area[1] + vr <= c[1] <= area[3] - vr):
                        continue
                    if not cu.clear(("disc", (c, vr)), "all", "GND", clr):
                        continue
                    edge = (px + dx * (reach - vr), py + dy * (reach - vr))
                    if not cu.clear(("seg", ((px, py), c, stub_w / 2)), {layer}, "GND", clr):
                        continue
                    via(c, q.GetNet()); track((px, py), c, stub_w, layer, q.GetNet())
                    cu.add_via(c, vr, "GND"); cu.add_track((px, py), c, stub_w / 2, layer, "GND")
                    ok = True
                    break
                if ok:
                    break
            if ok:
                placed += 1
            else:
                failed.append(f"{ref}.{q.GetNumber()}")
    return placed, failed



# ---- escape vias --------------------------------------------------------------------------------------------
def escape_vias(b, fps, p, area, track, via, pins, commit=True, radius=6.0, step=0.25, layer=None):
    """A via for every escape pin: the nearest grid spot within `radius` whose via is clear of other-net copper
    on all layers and whose straight F.Cu stub from the pad is clear. The most constrained pin goes first;
    after each placement only the neighbours' candidate lists are re-checked. Returns (placed, failed)."""
    import pcbnew
    mm = pcbnew.ToMM
    cu = _copper(b, p)
    vr, clr, sw = p["VIA"][0] / 2, p["CLEARANCE"], p["ESCAPE_W"]
    layer = pcbnew.F_Cu if layer is None else layer
    ok = lambda net, pxy, c: (cu.clear(("disc", (c, vr)), "all", net, clr)
                              and cu.clear(("seg", (pxy, c, sw / 2)), {layer}, net, clr))
    todo = []
    for ref, n in pins:
        q = next(q for q in fps[ref].Pads() if q.GetNumber() == n)
        if q.GetNetname() and not q.GetNetname().startswith("unconnected"):
            todo.append([ref, n, q, (mm(q.GetPosition().x), mm(q.GetPosition().y)), None])
    k = int(radius / step)
    for it in todo:
        px, py = it[3]
        cands = []
        for ix in range(-k, k + 1):
            for iy in range(-k, k + 1):
                c = (px + ix * step, py + iy * step)
                d = ((c[0] - px) ** 2 + (c[1] - py) ** 2) ** 0.5
                if 0.5 <= d <= radius and area[0] + vr <= c[0] <= area[2] - vr and area[1] + vr <= c[1] <= area[3] - vr:
                    cands.append((d, c))
        it[4] = sorted(x for x in cands if ok(it[2].GetNetname(), it[3], x[1]))
    placed, failed = {}, []
    while todo:
        todo.sort(key=lambda it: len(it[4]))
        ref, n, q, pxy, cands = todo.pop(0)
        if not cands:
            failed.append(f"{ref}.{n} ({q.GetNetname().split('/')[-1]})")
            continue
        c = cands[0][1]
        placed[(ref, n)] = c
        cu.add_via(c, vr, q.GetNetname()); cu.add_track(pxy, c, sw / 2, layer, q.GetNetname())
        if commit:
            via(c, q.GetNet()); track(pxy, c, sw, layer, q.GetNet())
        for it in todo:                                  # re-check only candidates near the new via and stub
            if abs(it[3][0] - c[0]) < 2 * radius + 1 and abs(it[3][1] - c[1]) < 2 * radius + 1:
                it[4] = [x for x in it[4] if ok(it[2].GetNetname(), it[3], x[1])]
    return placed, failed


# ---- fan-out ------------------------------------------------------------------------------------------------
def _pad_size(q, layer):
    """A pad's width and height in its own axes and its copper's area (mm2) on layer. A custom pad's size is only its
    anchor's (0.005 mm in footprints from EasyEDA), so its width and height come from its copper, twice the farthest
    reach from the anchor each way (the pad's edge is never nearer than the copper's), and its area is the copper's."""
    import pcbnew
    T = pcbnew.ToMM
    if q.GetShape(layer) != pcbnew.PAD_SHAPE_CUSTOM:
        w, h = T(q.GetSize(layer).x), T(q.GetSize(layer).y)
        return w, h, w * h
    th = math.radians(q.GetOrientationDegrees())
    cx, cy = T(q.GetPosition().x), T(q.GetPosition().y)
    poly, xs, ys = q.GetEffectivePolygon(layer), [0.0], [0.0]
    for k in range(poly.OutlineCount()):
        o = poly.Outline(k)
        for i in range(o.PointCount()):
            dx, dy = T(o.CPoint(i).x) - cx, T(o.CPoint(i).y) - cy
            xs.append(abs(dx * math.cos(th) - dy * math.sin(th)))
            ys.append(abs(dx * math.sin(th) + dy * math.cos(th)))
    return 2 * max(xs), 2 * max(ys), poly.Area() / pcbnew.FromMM(1) ** 2


def _outline_ok(c, r, outline, edge_clr):
    """A disc (centre c, radius r) inside the outline (rectangle, circle or polygon) and edge_clr from its edge."""
    if not outline:
        return True
    e = r + edge_clr
    if isinstance(outline, list):
        return _in_poly(c, outline) and _poly_edge_dist(c, outline) >= e
    if len(outline) == 3:
        return math.hypot(c[0] - outline[0], c[1] - outline[1]) <= outline[2] - e
    return outline[0] + e <= c[0] <= outline[2] - e and outline[1] + e <= c[1] <= outline[3] - e


def _net_skipped(net, skip_nets):
    """net in skip_nets: an exact name, or a regex matched in full ("+3V3" is a name, not a broken regex)."""
    for s in ((skip_nets,) if isinstance(skip_nets, str) else skip_nets):
        if net == s:
            return True
        try:
            if _re.fullmatch(s, net):
                return True
        except _re.error:
            pass
    return False


def fanout(b, ref, p, area, track, via, pins=None, dist=1.6, stagger=0.7, width=0.2, layer=None, skip_nets=(),
           outline=None, keepouts=(), commit=True, lateral=1.6):
    """Dog-bone fan-out of one fine-pitch footprint (QFN/QFP: four sides; SOIC/TSSOP: two): each signal pad gets a
    via out along its outward normal (away from the package, on the side the pad sits on), its centre `dist` mm
    beyond the pad's outer edge, neighbouring pads alternating `dist` and `dist + stagger` so the vias fit at fine
    pitch and leave channels between them, joined to the pad centre by a stub of `width` (at most the pad's narrow
    side): straight, or where the via sits to the side a 45-degree jog, ending at the via or, if that is blocked,
    earlier and then straight on, so the stubs of the outer row pass the inner row's vias (at 0.4 mm pitch they have
    to). The via must clear other-net copper on every layer (as escape_vias), the stub on the pad's layer, both
    checked with Copper; the stub from the pad's edge out. A blocked spot gives way to the next one: first along
    the normal (half-stagger steps, out to the other row and beyond), then offset sideways in 0.2 mm steps up to
    `lateral` mm, toward the end of the row first; each side is taken from its middle outward, so the middle pads go
    straight and the outer ones fan toward the corners.
    Skipped: pads without a net or whose net has no other pad, nets in skip_nets (names, or regexes matched in full;
    power and plane nets get plane_vias instead), exposed and thermal pads (four times the median pad area or more),
    through-hole pads and pads that already have a track or via of their net. pins: pad numbers to limit it to.
    layer: the pads' copper layer (default the footprint's side). outline / keepouts as for complete_nets (EDGE_CLR
    from the outline). commit=False plans without adding copper.
    Returns (placed {(ref, pad): (x, y)}, failed ["ref.pad (net)"])."""
    import pcbnew
    T = pcbnew.ToMM
    fp = next((f for f in b.GetFootprints() if f.GetReference() == ref), None)
    if fp is None:
        raise ValueError(f"fanout: no footprint {ref}")
    layer = (pcbnew.B_Cu if fp.IsFlipped() else pcbnew.F_Cu) if layer is None else layer
    cu = _copper(b, p)
    vr, clr, edge_clr = p["VIA"][0] / 2, p["CLEARANCE"], p.get("EDGE_CLR", 0.3)
    pins = None if pins is None else {str(n) for n in pins}
    npads = Counter(q.GetNetname() for f in b.GetFootprints() for q in f.Pads())
    pads = [q for q in fp.Pads() if q.GetAttribute() == pcbnew.PAD_ATTRIB_SMD and q.IsOnLayer(layer) and q.GetNumber()]
    if not pads:
        return {}, []
    areas = sorted(_pad_size(q, layer)[2] for q in pads)
    med = areas[len(areas) // 2]
    pads = [q for q in pads if _pad_size(q, layer)[2] < 4 * med]                         # no exposed pad
    # the footprint's own axes on the board (KiCad: y down, angles counter-clockwise on screen)
    th = math.radians(fp.GetOrientationDegrees())
    ux, uy = (math.cos(th), -math.sin(th)), (math.sin(th), math.cos(th))
    fx, fy = T(fp.GetPosition().x), T(fp.GetPosition().y)
    loc = {}
    for q in pads:
        dx, dy = T(q.GetPosition().x) - fx, T(q.GetPosition().y) - fy
        loc[q.GetNumber()] = (dx * ux[0] + dy * ux[1], dx * uy[0] + dy * uy[1])
    xs, ys = [v[0] for v in loc.values()], [v[1] for v in loc.values()]
    cx, cy, hx, hy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2, (max(xs) - min(xs)) / 2, (max(ys) - min(ys)) / 2
    side, info = {}, {}
    for q in pads:
        lx, ly = loc[q.GetNumber()]
        sx, sy, _ = _pad_size(q, layer)                      # a custom pad by its copper, not its anchor
        ph = math.radians(q.GetOrientationDegrees() - fp.GetOrientationDegrees())    # the pad in the footprint
        ax = (math.cos(ph), -math.sin(ph)) if sx >= sy else (math.sin(ph), math.cos(ph))  # its long axis
        if abs(sx - sy) > 0.05 * max(sx, sy):
            along_x = abs(ax[0]) >= abs(ax[1])               # a pad points out of the package along its long side
        else:
            along_x = abs(lx - cx) * (hy or 1) >= abs(ly - cy) * (hx or 1)
        o = (lx - cx) if along_x else (ly - cy)
        if abs(o) < 1e-3:
            continue                                         # a pad in the middle: no side
        sgn = 1 if o > 0 else -1
        key = ("x" if along_x else "y", sgn)
        n_loc = (sgn, 0.0) if along_x else (0.0, sgn)
        s = (ly - cy) if along_x else (lx - cx)              # position along the row, from its middle
        long_, short = max(sx, sy), min(sx, sy)
        half = (abs(ax[0]) if along_x else abs(ax[1])) * long_ / 2 + (abs(ax[1]) if along_x else abs(ax[0])) * short / 2
        side.setdefault(key, []).append(s)
        info[q.GetNumber()] = (q, key, n_loc, s, half, short)
    rows = {}
    for key, ss in side.items():                             # pitch of each row, for the alternation
        ss = sorted(ss)
        gaps = [z - a for a, z in zip(ss, ss[1:]) if z - a > 0.05]
        rows[key] = (ss[0], min(gaps) if gaps else 1.0, max(abs(ss[0]), abs(ss[-1])) or 1.0)
    board_vec = lambda v: (v[0] * ux[0] + v[1] * uy[0], v[0] * ux[1] + v[1] * uy[1])
    ends, wires = {}, {}                                     # copper already on the pads: track ends and vias, and
    for t in b.GetTracks():                                  # the whole copper, for a track through a pad
        is_via = t.Type() == pcbnew.PCB_VIA_T
        if not is_via and t.GetLayer() != layer:
            continue
        ends.setdefault(t.GetNetname(), []).extend([t.GetPosition()] if is_via else [t.GetStart(), t.GetEnd()])
        wires.setdefault(t.GetNetname(), []).append(
            ("all", "disc", ((T(t.GetPosition().x), T(t.GetPosition().y)), T(t.GetWidth(pcbnew.F_Cu)) / 2)) if is_via
            else ({layer}, "seg", ((T(t.GetStart().x), T(t.GetStart().y)), (T(t.GetEnd().x), T(t.GetEnd().y)),
                                   T(t.GetWidth()) / 2)))
    todo, placed, failed = [], {}, []
    for num, (q, key, n_loc, s, half, short) in info.items():
        net = q.GetNetname()
        if pins is not None and num not in pins:
            continue
        if not net or net.startswith("unconnected") or npads[net] < 2 or _net_skipped(net, skip_nets):
            continue
        if any(q.HitTest(e) for e in ends.get(net, ())) or any(
                _item_gap(pi, w_) <= 1e-3 for pi in pad_items(q, [layer], cover=False) for w_ in wires.get(net, ())):
            continue                                         # wired already: a track ends on it or runs through it
        s0, pitch, smax = rows[key]
        k = int(round((s - s0) / pitch))
        todo.append((abs(s) / smax, key, s, num, q, n_loc, half, short, dist + (stagger if k % 2 else 0.0)))
    todo.sort(key=lambda it: it[:3])
    h = stagger / 2 if stagger > 0 else 0.35
    for _, key, s, num, q, n_loc, half, short, D in todo:
        net = q.GetNetname()
        n = board_vec(n_loc)
        t = board_vec((0.0, 1.0) if key[0] == "x" else (1.0, 0.0))   # along the row, towards larger s
        out = 1 if s >= 0 else -1                            # towards the end of the row
        w = min(width, short)
        P0 = (T(q.GetPosition().x), T(q.GetPosition().y))
        at = lambda r, l: (P0[0] + n[0] * r + t[0] * l, P0[1] + n[1] * r + t[1] * l)
        lo = -int((D - vr - cu.via_pad_clr) / h + 1e-9)     # as close as a via may come to the pad
        deltas = sorted((m * h for m in range(lo, 4)), key=lambda x: (abs(x), x < 0))
        lats = [0.0] + [sg * 0.2 * i for i in range(1, int(lateral / 0.2 + 1e-9) + 1) for sg in (out, -out)]
        got = None
        for l in lats:
            for de in deltas:
                R = half + D + de                            # via centre, from the pad centre along the normal
                c = at(R, l)
                if not (area[0] + vr <= c[0] <= area[2] - vr and area[1] + vr <= c[1] <= area[3] - vr):
                    continue
                if not _outline_ok(c, vr, outline, edge_clr) or keepout_hit(keepouts, c, vr + 0.02, "all", net) \
                        or not cu.clear(("disc", (c, vr)), "all", net, clr):
                    continue
                # the stub: straight out of the pad; offset sideways, a 45-degree jog that ends at the via (late)
                # or, to pass the vias of the inner row, earlier and then straight on to the via
                kinks = [None] if l == 0 else [R - abs(l) - 0.2 * i for i in range(int((R - abs(l) - half) / 0.2) + 1)
                                               if R - abs(l) - 0.2 * i >= half + 0.1]
                for kn in kinks:
                    pts = [P0, c] if kn is None else [P0, at(kn, 0.0), at(kn + abs(l), l), c]
                    pts = [x for i, x in enumerate(pts) if i == 0 or math.hypot(x[0] - pts[i - 1][0], x[1] - pts[i - 1][1]) > 1e-6]
                    segs = [(at(half, 0.0), pts[1])] + list(zip(pts[1:], pts[2:]))   # checked from the pad's edge
                    if any(keepout_hit(keepouts, a, w / 2 + 0.02, {layer}, net, end=z) for a, z in segs):
                        continue
                    # every corner of the stub (not its start, on the pad) clears the edge and stays in `area`, as the
                    # via does: enough for a rectangle or a circle, both convex; a polygon's edges are checked against
                    # each segment too
                    if not all(_outline_ok(z, w / 2, outline, edge_clr) and area[0] + w / 2 <= z[0] <= area[2] - w / 2
                               and area[1] + w / 2 <= z[1] <= area[3] - w / 2 for _, z in segs):
                        continue
                    if isinstance(outline, list) and any(_poly_seg_dist(a, z, outline) < w / 2 + edge_clr for a, z in segs):
                        continue
                    # other-net copper against the whole stub, from the pad centre: behind a rounded tip a stub as
                    # wide as the pad leaves the pad's copper at its corners
                    if all(cu.clear(("seg", (a, z, w / 2)), {layer}, net, clr) for a, z in zip(pts, pts[1:])):
                        got = (c, list(zip(pts, pts[1:])))
                        break
                if got:
                    break
            if got:
                break
        if not got:
            failed.append(f"{ref}.{num} ({net.split('/')[-1]})")
            continue
        c, stub = got
        placed[(ref, num)] = c
        cu.add_via(c, vr, net)
        for a, z in stub:
            cu.add_track(a, z, w / 2, layer, net)
        if commit:
            via(c, q.GetNet())
            for a, z in stub:
                track(a, z, w, layer, q.GetNet())
    return placed, failed


# ---- maze router: complete nets -----------------------------------------------------------------------------
_VERBOSE, _T0 = bool(_os.environ.get("ROUTE_VERBOSE")), _time.time()


def _reach(clr, *radii):
    """Grid reach for a clearance and the widest half-track or via radius routed on it: copper farther from a cell
    than this is not recorded, so it must cover the largest clearance + radius that blocked() is asked about."""
    return max(0.8, clr + max(radii) + 0.05)


class Grid:
    """0.1 mm routing grid over `area` for one copper layer. Each cell keeps (net, distance) for copper within
    `reach`, so one raster serves every track width up to it (see _reach): a cell is blocked for (net, half-width)
    if other-net copper is closer than clearance + half-width."""
    def __init__(self, cu, layer, area, g=0.1, reach=0.8, outline=None, edge_clr=0.3):
        self.layer, self.area, self.g, self.reach = layer, area, g, reach
        self.outline, self.edge_clr = outline, edge_clr
        self.edge_d = {}                                 # cell -> signed outline distance (polygon only)
        self.nx = int((area[2] - area[0]) / g) + 1
        self.ny = int((area[3] - area[1]) / g) + 1
        self.near = {}                                   # cell -> list[(net, dist)]
        self.keepout = []                                # (shape, allowed_net, bbox): see add_keepout
        self.ko_d = {}                                   # (keep-out index, cell) -> signed distance
        for ils, ik, ig, inet in cu.items:
            if ils == "all" or layer in ils:
                self.add(ik, ig, inet)

    def xy(self, i, j):
        return (self.area[0] + i * self.g, self.area[1] + j * self.g)

    def add_keepout(self, shape, allowed=None):
        """No copper of other nets within the shape (rectangle or polygon), track and via edges included."""
        self.keepout.append((shape, allowed, _shape_bbox(shape)))

    def add(self, kind, geom, net):
        x0, y0, x1, y1 = Copper._bbox(kind, geom)
        r, g, A = self.reach, self.g, self.area
        for i in range(max(0, int((x0 - r - A[0]) / g)), min(self.nx, int((x1 + r - A[0]) / g) + 2)):
            for j in range(max(0, int((y0 - r - A[1]) / g)), min(self.ny, int((y1 + r - A[1]) / g) + 2)):
                p = self.xy(i, j)
                if kind == "rect":
                    d = _rect_dist(p, geom)
                elif kind == "disc":
                    d = math.hypot(p[0] - geom[0][0], p[1] - geom[0][1]) - geom[1]
                else:
                    d = _seg_dist(p, geom[0], geom[1]) - geom[2]
                if d < r:
                    self.near.setdefault((i, j), []).append((net, d))

    def blocked(self, c, net, need, hw=0.0):
        for n, d in self.near.get(c, ()):
            if n != net and d < need:
                return True
        p = self.xy(*c)
        if self.outline:                                     # copper-to-edge: centre + half-width + clearance
            o, e = self.outline, self.edge_clr + hw
            if isinstance(o, list):                          # polygon [(x, y), ...]: inside and e from every edge
                d = self.edge_d.get(c)                       # signed distance to the outline, cached per cell
                if d is None:
                    d = self.edge_d[c] = _poly_edge_dist(p, o) * (1 if _in_poly(p, o) else -1)
                if d < e:
                    return True
            elif len(o) == 3:                                # round board: (cx, cy, r)
                if math.hypot(p[0] - o[0], p[1] - o[1]) > o[2] - e:
                    return True
            elif p[0] < o[0] + e or p[0] > o[2] - e or p[1] < o[1] + e or p[1] > o[3] - e:
                return True
        for k, (shape, allowed, (x0, y0, x1, y1)) in enumerate(self.keepout):
            if allowed == net or p[0] < x0 - hw or p[0] > x1 + hw or p[1] < y0 - hw or p[1] > y1 + hw:
                continue
            d = self.ko_d.get((k, c))
            if d is None:
                d = self.ko_d[(k, c)] = _shape_dist(p, shape)
            if d < hw + 0.02:                                # the copper edge, not the centre line
                return True
        return False

    def inside(self, c, net):
        return any(n == net and d <= 0 for n, d in self.near.get(c, ()))


DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]
DIR_COST = (1.0, 4.0, 2.0)          # preferred direction: step-cost multipliers along, across, at 45 degrees


def _step_costs(layers, layer_cost, direction=None, dir_cost=None):
    """Cost of each move in DIRS on each layer: its length (1 or 1.4142) x the layer's cost, and on a layer with a
    preferred direction ({layer: "h" | "v"}) x dir_cost (along, across, diagonal; DIR_COST by default, each raised
    to 1 at least so the A* heuristic stays a lower bound)."""
    direction = direction or {}
    m = [max(1.0, float(x)) for x in (dir_cost or DIR_COST)]
    out = {}
    for L in layers:
        d = direction.get(L)
        d = d.lower() if isinstance(d, str) else d
        if d not in (None, "h", "v"):
            raise ValueError(f"DIR: layer {L} has {d!r}; use 'h' or 'v'")
        row = []
        for dx, dy in DIRS:
            k = 1.0 if d is None else m[2] if dx and dy else m[0] if (dy == 0) == (d == "h") else m[1]
            row.append((1.4142 if dx and dy else 1.0) * k * layer_cost[L])   # k = 1: exactly the plain cost
        out[L] = row
    return out


def astar_ml(grids, layers, net, need, hw, sources, targets, via_ok, via_cost=6.0, layer_cost=None,
             corridor=None, max_expand=600000, turn=0.6, hweight=1.0, deadline=None, direction=None, dir_cost=None):
    """A* over (cell, layer): 8-connected moves within a layer, or a via to another allowed layer where via_ok.
    sources/targets: sets of (cell, layer). corridor: (i0, j0, i1, j1) cell bounds. deadline: time.time() at which
    to give up. direction: {layer: "h" | "v"}, a preferred direction per layer, its moves priced by dir_cost
    (along, across, diagonal multipliers; DIR_COST by default). Returns [(cell, layer)] or None."""
    layer_cost = layer_cost or {L: 1.0 for L in layers}
    step = _step_costs(layers, layer_cost, direction, dir_cost)
    tl = list(targets)
    if not tl:
        return None
    bx0 = min(t[0][0] for t in tl); bx1 = max(t[0][0] for t in tl)
    by0 = min(t[0][1] for t in tl); by1 = max(t[0][1] for t in tl)
    if direction and any(L in direction for L in layers):
        # octile distance with the cheapest straight and diagonal step of any layer: still a lower bound on the
        # cost to go (vias and turns only add), and much closer to it than the plain distance when the layers'
        # directions make diagonals dear (with the default costs it is the Manhattan distance)
        s_min = min(min(step[L][:4]) for L in layers)
        d_min = min(min(step[L][4:]) for L in layers)

        # a straight run can be made of alternating diagonals too: it costs a cell at least the cheaper of the two
        # (DIR_COST = (4, 4, 1): two cells across for 2.83, not 8)
        s_run = min(s_min, d_min)

        def h(c):
            dx, dy = max(bx0 - c[0], 0, c[0] - bx1), max(by0 - c[1], 0, c[1] - by1)
            a, b_ = max(dx, dy), min(dx, dy)
            return hweight * (b_ * min(d_min, 2 * s_min) + (a - b_) * s_run)
    else:
        h = lambda c: hweight * math.hypot(max(bx0 - c[0], 0, c[0] - bx1), max(by0 - c[1], 0, c[1] - by1))
    G0 = grids[layers[0]]
    i0, j0, i1, j1 = corridor or (0, 0, G0.nx - 1, G0.ny - 1)
    openq, best, parent = [], {}, {}
    for s_ in sources:
        if grids[s_[1]].blocked(s_[0], net, need, hw):       # the track starts here too: its first cap must clear
            continue
        best[(s_, -1)] = 0.0
        heapq.heappush(openq, (h(s_[0]), 0.0, s_, -1))
    n = 0
    while openq and n < max_expand:
        f, gc, st, di = heapq.heappop(openq)
        if gc > best.get((st, di), 1e18):
            continue
        n += 1
        if deadline and not n & 1023 and _time.time() > deadline:
            return None
        if st in targets:
            path, k = [st], (st, di)
            while k in parent:
                k = parent[k]
                path.append(k[0])
            return path[::-1]
        c, L = st
        G = grids[L]
        for d, (dx, dy) in enumerate(DIRS):
            nc = (c[0] + dx, c[1] + dy)
            if not (i0 <= nc[0] <= i1 and j0 <= nc[1] <= j1):
                continue
            ns = (nc, L)
            if G.blocked(nc, net, need, hw):                 # targets too: a wide track must clear neighbours
                continue
            if dx and dy and (G.blocked((c[0] + dx, c[1]), net, need, hw) or G.blocked((c[0], c[1] + dy), net, need, hw)):
                continue
            ng = gc + step[L][d] + (turn if di not in (-1, d) else 0.0)
            if ng < best.get((ns, d), 1e18):
                best[(ns, d)] = ng
                parent[(ns, d)] = (st, di)
                heapq.heappush(openq, (ng + h(nc), ng, ns, d))
        if via_ok(c):
            for L2 in layers:
                if L2 != L and not grids[L2].blocked(c, net, need, hw):   # the track leaving the via must clear too
                    ns = (c, L2)
                    ng = gc + via_cost
                    if ng < best.get((ns, -1), 1e18):
                        best[(ns, -1)] = ng
                        parent[(ns, -1)] = (st, di)
                        heapq.heappush(openq, (ng + h(c), ng, ns, -1))
    return None


def _route_once(Gs, layers, net, clr, width, src, tgt, via_ok, G0, p, deadline=None):
    """p["QUICK"]: give up early (corridors of 4 and 12 mm, 250k expansions each, no whole-board search), so a hard
    connection fails in seconds and is left for hand routing. p["HWEIGHT"] > 1 makes the A* greedier (weighted
    heuristic): far fewer expansions, paths slightly longer than optimal. p["DIR"] / p["DIR_COST"]: preferred
    direction per layer (see astar_ml)."""
    import pcbnew
    allc = [s_[0] for s_ in src] + [t[0] for t in tgt]
    quick = p.get("QUICK", False)
    for m in ((40, 120) if quick else (40, 120, None)):  # corridor margin in cells, widening on failure
        if deadline and _time.time() > deadline:
            return None
        cor = None if m is None else (max(0, min(c[0] for c in allc) - m), max(0, min(c[1] for c in allc) - m),
                                      min(G0.nx - 1, max(c[0] for c in allc) + m), min(G0.ny - 1, max(c[1] for c in allc) + m))
        path = astar_ml(Gs, layers, net, clr + width / 2 + 0.02, width / 2, src, tgt, via_ok,
                        layer_cost={L: p.get("LAYER_COST", {}).get(L, p["IN1_COST"] if L == pcbnew.In1_Cu and "IN1_COST" in p
                                                                     else 1 + 0.25 * k) for k, L in enumerate(layers)},
                        corridor=cor, max_expand=250000 if quick else (600000 if m else 3000000),
                        hweight=p.get("HWEIGHT", 1.0), deadline=deadline,
                        direction=p.get("DIR"), dir_cost=p.get("DIR_COST"))
        if path:
            return path
    return None


def _simplify(path):
    pts = [path[0]]
    for k in range(1, len(path) - 1):
        a, m, z = pts[-1], path[k], path[k + 1]
        if (m[0] - a[0]) * (z[1] - m[1]) != (m[1] - a[1]) * (z[0] - m[0]):
            pts.append(m)
    pts.append(path[-1])
    return pts


def _item_gap(a, z):
    """Gap between two copper items (layers, kind, geometry) as net_components sees them: <= 0 when they touch,
    1.0 when they share no layer (or two rectangles don't overlap)."""
    (la, ka, ga), (lz, kz, gz) = a, z
    if la != "all" and lz != "all" and not (la & lz):
        return 1.0
    if ka == "seg" and kz == "seg":
        return _seg_seg(ga[0], ga[1], gz[0], gz[1]) - ga[2] - gz[2]
    if ka == "rect" and kz == "rect":
        return 0.0 if (ga[0] <= gz[2] and gz[0] <= ga[2] and ga[1] <= gz[3] and gz[1] <= ga[3]) else 1.0
    if kz == "rect":
        a, z = z, a; (la, ka, ga), (lz, kz, gz) = a, z
    if ka == "rect":
        return (_rect_dist(gz[0], ga) - gz[1]) if kz == "disc" else (_seg_rect(gz[0], gz[1], ga) - gz[2])
    if ka == "disc" and kz == "disc":
        return math.hypot(ga[0][0] - gz[0][0], ga[0][1] - gz[0][1]) - ga[1] - gz[1]
    if ka == "seg":
        a, z = z, a; (la, ka, ga), (lz, kz, gz) = a, z
    return _seg_dist(ga[0], gz[0], gz[1]) - ga[1] - gz[2]


def _group_points(grp, step=1.0):
    """Points standing for a group of copper items: pad and via centres, track ends and points every `step` mm."""
    pts = []
    for _ls, k, g in grp:
        if k == "rect":
            pts.append(((g[0] + g[2]) / 2, (g[1] + g[3]) / 2))
        elif k == "disc":
            pts.append(g[0])
        else:
            (ax, ay), (zx, zy) = g[0], g[1]
            n = max(1, int(math.hypot(zx - ax, zy - ay) / step))
            pts += [(ax + (zx - ax) * i / n, ay + (zy - ay) * i / n) for i in range(n + 1)]
    return pts


def _spanning_pairs(groups):
    """The pairs of a net's copper groups that a shortest spanning tree joins (Prim's, by the straight-line distance
    between their nearest points, see _group_points): [(distance, i, k)]."""
    pts = [_group_points(g) for g in groups]
    gap = lambda i, k: min(math.hypot(a[0] - c[0], a[1] - c[1]) for a in pts[i] for c in pts[k])
    best = {k: (gap(0, k), 0) for k in range(1, len(groups))}
    out = []
    while best:
        k = min(best, key=lambda m: (best[m][0], m))
        d, i = best.pop(k)
        out.append((d, i, k))
        for m in best:
            dm = gap(k, m)
            if dm < best[m][0]:
                best[m] = (dm, k)
    return out


def net_components(b, net_name, exclude):
    """Connected groups of a net's copper (pads as inscribed shapes, vias, tracks); zones ignored. Returns list of
    item lists."""
    import pcbnew
    mm = pcbnew.ToMM
    items, barrels = [], []                              # barrels: (i, k), shapes of one pad on different layers
    cu = list(b.GetEnabledLayers().CuStack())
    for fp in b.GetFootprints():
        for q in fp.Pads():
            if q.GetNetname() == net_name and (fp.GetReference(), q.GetNumber()) not in exclude:
                its = pad_items(q, cu, cover=False)
                barrels += [(len(items), len(items) + k) for k in range(1, len(its))]
                items += its
    for t in b.GetTracks():
        if t.GetNetname() != net_name:
            continue
        if t.Type() == pcbnew.PCB_VIA_T:
            items.append(("all", "disc", ((mm(t.GetPosition().x), mm(t.GetPosition().y)), mm(t.GetWidth(pcbnew.F_Cu)) / 2)))
        else:
            items.append(({t.GetLayer()}, "seg", ((mm(t.GetStart().x), mm(t.GetStart().y)),
                                                  (mm(t.GetEnd().x), mm(t.GetEnd().y)), mm(t.GetWidth()) / 2)))
    par = list(range(len(items)))

    def find(k):                                         # iterative: a net's chain of tracks can be thousands long
        while par[k] != k:
            par[k] = par[par[k]]
            k = par[k]
        return k
    for i, k in barrels:
        par[find(i)] = find(k)
    for i in range(len(items)):
        for k in range(i + 1, len(items)):
            if find(i) != find(k) and _item_gap(items[i], items[k]) <= 1e-3:
                par[find(i)] = find(k)
    groups = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append(it)
    return list(groups.values())


def complete_nets(b, p, area, plan, track, via_fn, exclude=frozenset(), keepouts=(), outline=None):
    """plan: [(net regex, [layer ids in preference order], width)]. For each matching net, join its copper groups
    one at a time: A* from the growing tree to the nearest other group, trying the layers in order.
    p["ORDER"] = "longest": first collect every connection of the whole plan (for each net, the pairs of copper groups
    a shortest spanning tree joins), then route them longest first by the straight-line distance between the two
    groups, whatever their net or plan entry, each from the copper its first group has by then to its second's; a
    connection whose groups another route has joined already is skipped, and a net matching two patterns is routed
    with the first. p["DIR"]: a preferred direction per layer (see astar_ml).
    Returns (routed connections, [unrouted net names])."""
    cu = _copper(b, p)
    clr = p["CLEARANCE"]
    grids = {}
    reach = _reach(clr, p["VIA"][0] / 2, *(w / 2 for _, _, w in plan))

    def grid(layer):
        if layer not in grids:
            grids[layer] = Grid(cu, layer, area, reach=reach, outline=outline, edge_clr=p["EDGE_CLR"])
            for shape, allowed, lyr in keepouts:
                if lyr == layer:
                    grids[layer].add_keepout(shape, allowed)
        return grids[layer]
    names = sorted({n.GetNetname() for n in b.GetNetsByNetcode().values() if n.GetNetname()})
    cu_layers = list(b.GetEnabledLayers().CuStack())
    done, failed = 0, []
    stop = p.get("DEADLINE")
    order = p.get("ORDER")
    if order not in (None, "plan", "longest"):
        raise ValueError(f"ORDER: {order!r}; use 'longest' (or leave it out)")

    def connect(net, netobj, pad_mids, layers, width, tree, rest):
        """One connection of `net`: A* from the copper of `tree` to the nearest group of `rest`; its tracks and vias
        go on the board and into `tree`. Returns (index in rest of the group reached, or None; its deadline)."""
        Gs = {L: grid(L) for L in layers}
        G0 = Gs[layers[0]]
        src, tgt = set(), {}
        ax = p.get("AXIAL_PADS", False)
        for L in layers:
            is_pad = lambda it: it[1] != "seg" or _mid(it) in pad_mids
            src |= {(c, L) for it in tree if it[0] == "all" or L in it[0] for c in _cells(Gs[L], it, ax and is_pad(it))}
            for gi, grp in enumerate(rest):
                for it in grp:
                    if it[0] == "all" or L in it[0]:
                        for c in _cells(Gs[L], it, ax and is_pad(it)):
                            tgt[(c, L)] = gi
        vr = p["VIA"][0] / 2
        vcache = {}

        def via_ok(c):
            if c not in vcache:
                xy = G0.xy(*c)
                vcache[c] = all(not grid(L).blocked(c, net, clr + vr + 0.02, vr)
                                for L in cu_layers) and \
                    cu.clear(("disc", (xy, vr)), "all", net, clr)
            return vcache[c]
        path = None
        w_used = width
        dl = [t for t in (p.get("CONN_SECONDS") and _time.time() + p["CONN_SECONDS"], stop) if t]
        dl = min(dl) if dl else None
        for w_try in (width, max(0.3, round(width * 0.6, 2)), 0.2):   # neck down onto small pins
            if w_try > width or (p.get("QUICK") and w_try not in (width, 0.2)):
                continue
            w_used = w_try
            path = _route_once(Gs, layers, net, clr, w_try, src, tgt, via_ok, G0, p, dl) if src and tgt else None
            if path or (dl and _time.time() > dl):
                break
        if not path:
            return None, dl

        def centre(items, st):                       # pad / via centre of the item holding this cell
            xy, L = Gs[st[1]].xy(*st[0]), st[1]
            for ls, k, g in items:
                if ls != "all" and L not in ls:
                    continue
                if k == "rect" and g[0] <= xy[0] <= g[2] and g[1] <= xy[1] <= g[3]:
                    return ((g[0] + g[2]) / 2, (g[1] + g[3]) / 2)
                if k == "disc" and math.hypot(xy[0] - g[0][0], xy[1] - g[0][1]) <= g[1]:
                    return g[0]
                if k == "seg" and _mid((ls, k, g)) in pad_mids and _seg_dist(xy, g[0], g[1]) <= g[2]:
                    return _mid((ls, k, g))      # a pad at an angle: its centre
            return None
        c_start = centre(tree, path[0])
        c_end = centre(rest[tgt[path[-1]]], path[-1])
        runs, cur = [], [path[0]]
        for st in path[1:]:
            if st[1] != cur[-1][1]:
                runs.append(cur); cur = [st]
                xy = G0.xy(*st[0])
                via_fn(xy, netobj)
                cu.add_via(xy, vr, net)
                for gr in grids.values():
                    gr.add("disc", (xy, vr), net)
                tree.append(("all", "disc", (xy, vr)))
            else:
                cur.append(st)
        runs.append(cur)

        def lay(a, z, w, L):
            track(a, z, w, L, netobj)
            seg = (a, z, w / 2)
            cu.add_track(a, z, w / 2, L, net)
            grids[L].add("seg", seg, net)
            tree.append(({L}, "seg", seg))

        def to_centre(a, c, L):                      # finish exactly on the pad / via centre, necked if needed
            if not c or math.hypot(a[0] - c[0], a[1] - c[1]) < 1e-6:
                return
            for w in (w_used, max(0.2, round(w_used * 0.6, 2)), 0.2):
                if cu.clear(("seg", (a, c, w / 2)), {L}, net, clr):
                    lay(a, c, w, L)
                    return
        for ri, run in enumerate(runs):
            L = run[0][1]
            pts = [Gs[L].xy(*c) for c in _simplify([st[0] for st in run])]
            pts = [q for i, q in enumerate(pts) if i == 0 or q != pts[i - 1]]
            for a, z in zip(pts, pts[1:]):
                lay(a, z, w_used, L)
            if ri == 0:
                to_centre(pts[0], c_start, L)
            if ri == len(runs) - 1:
                to_centre(pts[-1], c_end, L)
        return tgt[path[-1]], dl

    if order == "longest":
        jobs, nets = [], {}
        for pattern, layers, width in plan:
            for net in [n for n in names if _re.fullmatch(pattern, n)]:
                if net in nets:
                    continue
                groups = net_components(b, net, exclude)
                if len(groups) < 2:
                    continue
                nets[net] = dict(layers=layers, width=width, groups=groups, par=list(range(len(groups))),
                                 obj=b.FindNet(net), mids=_capsule_pad_mids(b, net, cu_layers))
                jobs += [(-d, len(jobs), net, i, k) for d, i, k in _spanning_pairs(groups)]
        jobs.sort()
        if _VERBOSE:
            print(f"   {_time.time() - _T0:6.1f}s {len(jobs)} connections in {len(nets)} nets, longest first", flush=True)

        def find(s, k):
            while s["par"][k] != k:
                k = s["par"][k]
            return k
        for negd, _, net, i, k in jobs:
            s = nets[net]
            ri, rk = find(s, i), find(s, k)
            if ri == rk:                                 # joined already, by a route that crossed it
                continue
            if stop and _time.time() > stop:
                if _VERBOSE:
                    print(f"   {_time.time() - _T0:6.1f}s {net} ({-negd:.1f} mm): not tried, out of time", flush=True)
                continue
            tree = s["groups"][ri]
            n0 = len(tree)
            gi, dl = connect(net, s["obj"], s["mids"], s["layers"], s["width"], tree, [s["groups"][rk]])
            if gi is not None:
                done += 1
                new = tree[n0:]
                tree += s["groups"][rk]
                s["par"][rk] = ri
                for m in range(len(s["groups"])):         # a route can cross the net's other groups: join them too
                    rm = find(s, m)
                    if rm != ri and any(_item_gap(a, z) <= 1e-3 for a in new for z in s["groups"][rm]):
                        tree += s["groups"][rm]
                        s["par"][rm] = ri
            if _VERBOSE:
                why = "ok" if gi is not None else "FAILED" + (" (time)" if dl and _time.time() > dl else "")
                print(f"   {_time.time() - _T0:6.1f}s {net} ({-negd:.1f} mm): {why}", flush=True)
        # the nets still in more than one piece, not the pairs that failed: a later route can cross both groups of a
        # failed pair and join them
        return done, [net for net, s in nets.items() if len({find(s, m) for m in range(len(s["groups"]))}) > 1]

    for pattern, layers, width in plan:
        for net in [n for n in names if _re.fullmatch(pattern, n)]:
            groups = net_components(b, net, exclude)
            if len(groups) < 2:
                continue
            if stop and _time.time() > stop:                 # out of time: report, don't try
                failed.append(net)
                if _VERBOSE:
                    print(f"   {_time.time() - _T0:6.1f}s {net}: not tried, out of time ({len(groups) - 1} connections)", flush=True)
                continue
            netobj = b.FindNet(net)
            pad_mids = _capsule_pad_mids(b, net, cu_layers)   # centres of its pads at an angle
            tree, rest = groups[0], groups[1:]
            while rest:
                if stop and _time.time() > stop:             # out of time within the net: the rest is not tried
                    failed.append(net)
                    if _VERBOSE:
                        print(f"   {_time.time() - _T0:6.1f}s {net}: out of time ({len(rest)} groups left)", flush=True)
                    break
                gi, dl = connect(net, netobj, pad_mids, layers, width, tree, rest)
                routed = gi is not None
                if routed:
                    tree += rest.pop(gi)
                    done += 1
                if _VERBOSE:
                    why = "ok" if routed else "FAILED" + (" (time)" if dl and _time.time() > dl else "")
                    print(f"   {_time.time() - _T0:6.1f}s {net}: {why} ({len(rest)} groups left)", flush=True)
                if not routed:                               # this tree is stuck: keep it, grow a new one
                    failed.append(net)
                    tree, rest = rest[0], rest[1:]
    return done, failed


def _mid(it):
    (a, z, _r) = it[2]
    return (round((a[0] + z[0]) / 2, 4), round((a[1] + z[1]) / 2, 4))


def _capsule_pad_mids(b, net, cu):
    """Centres of the net's pads that pad_item models as capsules (pads at an angle), to tell them from tracks."""
    return {_mid(it) for f in b.GetFootprints() for q in f.Pads() if q.GetNetname() == net
            for it in pad_items(q, cu, cover=False) if it[1] == "seg"}


def _cells(G, it, axial=False):
    """Grid cells lying inside an item's copper (with a small inset), for A* sources/targets.
    axial: for pads, only cells on the pad's long centre line (both lines for a square pad; the capsule's spine for a
    pad at an angle), so a route enters along the axis and its last segment to the pad centre stays inside the pad:
    every track then ends on the centre."""
    ls, kind, geom = it
    x0, y0, x1, y1 = Copper._bbox(kind, geom)
    out, g, A = [], G.g, G.area
    cx, cy, wx, wy = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0
    for i in range(max(0, int((x0 - A[0]) / g)), min(G.nx, int((x1 - A[0]) / g) + 2)):
        for j in range(max(0, int((y0 - A[1]) / g)), min(G.ny, int((y1 - A[1]) / g) + 2)):
            p = G.xy(i, j)
            if kind == "rect":
                ok = geom[0] + 0.05 <= p[0] <= geom[2] - 0.05 and geom[1] + 0.05 <= p[1] <= geom[3] - 0.05
                if ok and axial:
                    on_h = abs(p[1] - cy) <= g * 0.51 and wx >= wy * 0.9    # horizontal centre line (wide pad)
                    on_v = abs(p[0] - cx) <= g * 0.51 and wy >= wx * 0.9    # vertical centre line (tall pad)
                    ok = on_h or on_v
            elif kind == "disc":
                ok = math.hypot(p[0] - geom[0][0], p[1] - geom[0][1]) <= geom[1] - 0.05
            else:
                ok = _seg_dist(p, geom[0], geom[1]) <= (min(geom[2] - 0.02, g * 0.51) if axial else geom[2] - 0.02)
            if ok:
                out.append((i, j))
    return out


# ---- leftovers -------------------------------------------------------------------------------------------------
def tie_pads(b, p, area, pads, net, layers, width, track, via_fn, outline=None):
    """Route each (ref, pad) of `net` to the nearest existing via of that net (e.g. GND pads the stitcher could not
    serve): one A* per pad, from the pad to any via of the net. Returns (tied, failed)."""
    import pcbnew
    mm = pcbnew.ToMM
    cu = _copper(b, p)
    clr, vr = p["CLEARANCE"], p["VIA"][0] / 2
    reach = _reach(clr, width / 2)
    Gs = {L: Grid(cu, L, area, reach=reach, outline=outline, edge_clr=p["EDGE_CLR"]) for L in layers}
    G0 = Gs[layers[0]]
    netobj = b.FindNet(net)
    vias = [(("all", "disc", ((mm(t.GetPosition().x), mm(t.GetPosition().y)), mm(t.GetWidth(pcbnew.F_Cu)) / 2)))
            for t in b.GetTracks() if t.Type() == pcbnew.PCB_VIA_T and t.GetNetname() == net]
    tied, failed = 0, []
    for ref, num in pads:
        q = next(q for f in b.GetFootprints() if f.GetReference() == ref for q in f.Pads() if q.GetNumber() == num)
        src = {(c, L) for it in pad_items(q, layers, cover=False) for L in it[0] for c in _cells(Gs[L], it)}
        tgt = {(c, L): 0 for L in layers for v in vias for c in _cells(Gs[L], v)}
        path = _route_once(Gs, layers, net, clr, width, src, tgt, lambda c: False, G0, p) if src and tgt else None
        if not path:
            failed.append(f"{ref}.{num}"); continue
        L = path[0][1]
        pts = [Gs[L].xy(*c) for c in _simplify([st[0] for st in path])]
        pc = (mm(q.GetPosition().x), mm(q.GetPosition().y))
        end = min(vias, key=lambda v: math.hypot(v[2][0][0] - pts[-1][0], v[2][0][1] - pts[-1][1]))[2][0]
        pts = [pc] + pts + [end]
        pts = [x for i, x in enumerate(pts) if i == 0 or x != pts[i - 1]]
        for a, z in zip(pts, pts[1:]):
            track(a, z, width, L, netobj)
            cu.add_track(a, z, width / 2, L, net)
            Gs[L].add("seg", (a, z, width / 2), net)
        tied += 1
    return tied, failed


# ---- plane vias, neck-down, audit (from the sign's route_controller.py) ---------------------------------------
def plane_vias(b, p, area, net_name, size, track, via, per_pad=lambda q: 1, radius=3.0, step=0.2, layer=None, skip=(),
               keepouts=(), outline=None):
    """A via (to the net's plane or pour) beside every SMD pad of the net on `layer` (F.Cu): the nearest clear spot
    within `radius`, joined by a straight stub as wide as the pad's narrow side (0.5 mm at most).
    skip: (ref, pad) pairs to leave out. keepouts/outline as for complete_nets (a polygon outline is honoured with
    EDGE_CLR). Returns (placed, [ref.pad without a via])."""
    import pcbnew
    T = pcbnew.ToMM
    layer = pcbnew.F_Cu if layer is None else layer
    cu = _copper(b, p)
    vr, clr, netobj = size[0] / 2, p["CLEARANCE"], b.FindNet(net_name)
    placed, failed = 0, []
    pads = [(f.GetReference(), q) for f in b.GetFootprints() for q in f.Pads()
            if q.GetNetname() == net_name and q.GetAttribute() == pcbnew.PAD_ATTRIB_SMD and q.IsOnLayer(layer)
            and (f.GetReference(), q.GetNumber()) not in skip]
    for ref, q in pads:
        c0 = (T(q.GetPosition().x), T(q.GetPosition().y))
        sw = min(0.5, T(min(q.GetSize(layer).x, q.GetSize(layer).y)))
        k = int(radius / step)
        cands = sorted((math.hypot(ix * step, iy * step), (c0[0] + ix * step, c0[1] + iy * step))
                       for ix in range(-k, k + 1) for iy in range(-k, k + 1) if 0.4 <= math.hypot(ix, iy) * step <= radius)
        got = 0
        for d, c in cands:
            if not (area[0] + 0.6 <= c[0] <= area[2] - 0.6 and area[1] + 0.6 <= c[1] <= area[3] - 0.6):
                continue
            if isinstance(outline, list) and (not _in_poly(c, outline) or _poly_edge_dist(c, outline) < vr + p["EDGE_CLR"]
                                              or _poly_seg_dist(c0, c, outline) < sw / 2 + p["EDGE_CLR"]):
                continue                                 # the stub too: it can cut across a notch
            if keepout_hit(keepouts, c, vr + 0.02, "all", net_name) or \
                    keepout_hit(keepouts, c0, sw / 2 + 0.02, {layer}, net_name, end=c):
                continue
            if cu.clear(("disc", (c, vr)), "all", net_name, clr) and cu.clear(("seg", (c0, c, sw / 2)), {layer}, net_name, clr):
                via(c, netobj, size); track(c0, c, sw, layer, netobj)
                cu.add_via(c, vr, net_name); cu.add_track(c0, c, sw / 2, layer, net_name)
                got += 1
                if got >= per_pad(q):
                    break
        placed += got
        if not got:
            failed.append(f"{ref}.{q.GetNumber()}")
    return placed, failed


def neckdown(b, fine_refs=(), min_w=0.2, clr=0.2):
    """Tracks touching a pad of a fine-pitch footprint (fine_refs) become min_w; any other track closer than clr to
    another net's pad is narrowed in 0.1 mm steps until it clears (down to min_w). Locked tracks keep their width.
    Returns the number of changes."""
    import pcbnew
    mm = pcbnew.FromMM
    fine = [q for f in b.GetFootprints() if f.GetReference() in fine_refs for q in f.Pads()]
    allpads = [q for f in b.GetFootprints() for q in f.Pads()]
    fixed = 0
    for t in b.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T or t.IsLocked() or t.GetWidth() <= mm(min_w):
            continue
        if any(q.GetNetname() == t.GetNetname() and (q.HitTest(t.GetStart()) or q.HitTest(t.GetEnd())) for q in fine):
            t.SetWidth(mm(min_w)); fixed += 1
            continue
        for q in allpads:
            if q.GetNetname() == t.GetNetname() or not q.IsOnLayer(t.GetLayer()):
                continue
            while t.GetWidth() > mm(min_w) and q.GetEffectiveShape(t.GetLayer()).Collide(t.GetEffectiveShape(), mm(clr)):
                t.SetWidth(max(mm(min_w), t.GetWidth() - mm(0.1))); fixed += 1
    return fixed


def snap_to_pad_centres(b, track, clr=0.2, min_w=0.2):
    """Finish each track end that audit_track_ends calls off-centre (alone, inside a same-net pad, not on its centre)
    with a segment to the pad centre on the track's layer: at the track's width if that keeps clr from other-net
    copper, else necked down (min_w at the narrowest), else the end stays as it is. complete_nets ends on centres itself;
    this is for routes from elsewhere. Returns the number of segments added."""
    import pcbnew
    T = pcbnew.ToMM
    cu = Copper(b)
    key = lambda x, y: (round(x / 2000), round(y / 2000))
    tracks = [t for t in b.GetTracks() if t.Type() != pcbnew.PCB_VIA_T]
    ends = Counter((key(e.x, e.y), t.GetLayer()) for t in tracks for e in (t.GetStart(), t.GetEnd()))
    vias = {key(v.GetPosition().x, v.GetPosition().y) for v in b.GetTracks() if v.Type() == pcbnew.PCB_VIA_T}
    pads, by_net = {}, {}
    for f in b.GetFootprints():
        for q in f.Pads():
            pads.setdefault(q.GetNetname(), []).append(q)
    for t in tracks:
        by_net.setdefault((t.GetNetname(), t.GetLayer()), []).append(t)
    added = 0
    for t in tracks:
        for e in (t.GetStart(), t.GetEnd()):
            k = key(e.x, e.y)
            if ends[(k, t.GetLayer())] > 1 or k in vias:
                continue
            if any(s is not t and s.HitTest(e, 0) for s in by_net[(t.GetNetname(), t.GetLayer())]):
                continue                                     # a T-junction onto another track: joined already
            for q in pads.get(t.GetNetname(), ()):
                c = q.GetPosition()
                if q.IsOnLayer(t.GetLayer()) and key(c.x, c.y) != k and q.HitTest(e):
                    a, z, L, w0 = (T(e.x), T(e.y)), (T(c.x), T(c.y)), t.GetLayer(), T(t.GetWidth())
                    for w in (w0, max(min_w, round(w0 * 0.6, 2)), min_w):   # as complete_nets' to_centre
                        if w <= w0 and cu.clear(("seg", (a, z, w / 2)), {L}, t.GetNetname(), clr):
                            track(a, z, w, L, t.GetNet())
                            cu.add_track(a, z, w / 2, L, t.GetNetname())
                            added += 1
                            break
                    break
    return added


def audit_track_ends(b):
    """Classify every track end: pad centre, via, track joint, T-junction; or inside a pad but off its centre, or
    dangling. Returns (counts, [off-centre], [dangling]) with net and position (board mm) for the last two."""
    import pcbnew
    T = pcbnew.ToMM
    key = lambda x, y: (round(x / 2000), round(y / 2000))   # 2 um buckets: pads and tracks round mm -> nm differently
    cu = list(b.GetEnabledLayers().CuStack())
    ends, segs = {}, []
    for t in b.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            continue
        segs.append(t)
        for e in (t.GetStart(), t.GetEnd()):
            ends.setdefault((key(e.x, e.y), t.GetLayer()), []).append(t)
    vias = {key(v.GetPosition().x, v.GetPosition().y) for v in b.GetTracks() if v.Type() == pcbnew.PCB_VIA_T}
    pad_c = {(key(q.GetPosition().x, q.GetPosition().y), L) for f in b.GetFootprints() for q in f.Pads() for L in cu if q.IsOnLayer(L)}
    cls = {"pad centre": 0, "via": 0, "track joint": 0, "T-junction": 0}
    off_centre, dangling = [], []
    for (k, L), ts in ends.items():
        t = ts[0]
        if (k, L) in pad_c:
            cls["pad centre"] += 1; continue
        if k in vias:
            cls["via"] += 1; continue
        if len(ts) > 1:
            cls["track joint"] += 1; continue
        x, y = k[0] * 2000, k[1] * 2000
        if any(s is not t and s.GetLayer() == L and s.GetNetname() == t.GetNetname() and s.HitTest(pcbnew.VECTOR2I(int(x), int(y)), 0)
               for s in segs):
            cls["T-junction"] += 1; continue
        inside = [q for f in b.GetFootprints() for q in f.Pads() if q.IsOnLayer(L) and q.HitTest(pcbnew.VECTOR2I(int(x), int(y)))]
        (off_centre if inside else dangling).append(f"{t.GetNetname().split('/')[-1]} @({x / 1e6:.2f},{y / 1e6:.2f})")
    return cls, off_centre, dangling
