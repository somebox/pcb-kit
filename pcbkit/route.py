"""Scripted routing on a KiCad board: a maze router that completes nets, a GND/plane via stitcher, escape vias,
neck-down and a track-end audit. Board millimetres throughout (KiCad's frame, y down).

    from pcbkit.route import writer, complete_nets, plane_vias, neckdown, audit_track_ends
    track, via = writer(board, via_size=(0.6, 0.3))
    P = dict(CLEARANCE=0.2, EDGE_CLR=0.3, VIA=(0.6, 0.3), VIA_PAD_CLR=0.15, QUICK=True, AXIAL_PADS=True)
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


def astar_ml(grids, layers, net, need, hw, sources, targets, via_ok, via_cost=6.0, layer_cost=None,
             corridor=None, max_expand=600000, turn=0.6, hweight=1.0, deadline=None):
    """A* over (cell, layer): 8-connected moves within a layer, or a via to another allowed layer where via_ok.
    sources/targets: sets of (cell, layer). corridor: (i0, j0, i1, j1) cell bounds. deadline: time.time() at which
    to give up. Returns [(cell, layer)] or None."""
    layer_cost = layer_cost or {L: 1.0 for L in layers}
    tl = list(targets)
    if not tl:
        return None
    bx0 = min(t[0][0] for t in tl); bx1 = max(t[0][0] for t in tl)
    by0 = min(t[0][1] for t in tl); by1 = max(t[0][1] for t in tl)
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
            ng = gc + (1.4142 if dx and dy else 1.0) * layer_cost[L] + (turn if di not in (-1, d) else 0.0)
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
    heuristic): far fewer expansions, paths slightly longer than optimal."""
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
                        hweight=p.get("HWEIGHT", 1.0), deadline=deadline)
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

    def dist(a, z):
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
    for i, k in barrels:
        par[find(i)] = find(k)
    for i in range(len(items)):
        for k in range(i + 1, len(items)):
            if find(i) != find(k) and dist(items[i], items[k]) <= 1e-3:
                par[find(i)] = find(k)
    groups = {}
    for i, it in enumerate(items):
        groups.setdefault(find(i), []).append(it)
    return list(groups.values())


def complete_nets(b, p, area, plan, track, via_fn, exclude=frozenset(), keepouts=(), outline=None):
    """plan: [(net regex, [layer ids in preference order], width)]. For each matching net, join its copper groups
    one at a time: A* from the growing tree to the nearest other group, trying the layers in order.
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
                routed = False
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
                if path:
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
                    tree += rest.pop(tgt[path[-1]])
                    done += 1
                    routed = True
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
