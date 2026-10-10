"""Check finished routing for what DRC doesn't flag.

  pcbkit check BOARD.kicad_pcb [--power-classes PWR_HI,PWR_SW]

For every net it traces the copper (tracks, vias, pads, filled zone islands, using their real shapes so thermal
spokes count) into connected groups, and reports:
  - floating copper: groups with no pad (stray traces, orphan vias, dead zone islands);
  - stubs: track runs from a via, pad or junction to a dead end, with their full length (DRC only flags the last
    segment);
  - loops: signal nets routed twice between the same points;
  - pours that reach only one pad of a net that has several (not stitched to the plane);
  - power pads fed only through thin track: on the power classes, pads that lose their connection when unlocked
    tracks narrower than the net class are removed. Scripted necks are locked, so they count as wide. The power
    classes default to every net class whose track width is 0.5 mm or more.

Run on a board whose zones are filled, e.g. after `pcbkit drc --save`. Reads net classes from the sibling
.kicad_pro. Harvested from dodeca v1.3's route_check.py.
"""
import argparse
import collections
import json
import math
from pathlib import Path

from ..kicad import ensure_pcbnew

TOL = 1000  # nm


def seg_dist(p, a, b):
    dx, dy = b.x - a.x, b.y - a.y
    l2 = dx * dx + dy * dy
    t = 0 if l2 == 0 else max(0, min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / l2))
    return math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy))


class UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, i):
        while self.p[i] != i:
            self.p[i] = self.p[self.p[i]]
            i = self.p[i]
        return i

    def union(self, a, b):
        self.p[self.find(a)] = self.find(b)


def build_net(pcbnew, tracks, vias, pads, islands):
    """Contacts between one net's copper pieces. Returns (items, kind, contacts, free_ends)."""
    TOUCH = pcbnew.FromMM(0.02)
    items = tracks + vias + pads + islands
    kind = ["track"] * len(tracks) + ["via"] * len(vias) + ["pad"] * len(pads) + ["island"] * len(islands)
    contacts, free_ends = [], collections.defaultdict(list)
    nt, nv, npd = len(tracks), len(vias), len(pads)
    iv, ip, ii = nt, nt + nv, nt + nv + npd
    polys = {}

    def poly(k, layer):
        if (k, layer) not in polys:
            ps = pcbnew.SHAPE_POLY_SET()
            items[k].TransformShapeToPolygon(ps, layer, TOUCH, pcbnew.FromMM(0.005), pcbnew.ERROR_OUTSIDE)
            polys[k, layer] = ps  # grown slightly: thermal spokes and abutting pours only touch
        return polys[k, layer]

    def overlaps(k, layer, island_k):
        _, zl, isl, bb = islands[island_k - ii]
        if zl != layer or not items[k].IsOnLayer(layer):
            return False
        if not items[k].GetBoundingBox().Intersects(bb):
            return False
        inter = pcbnew.SHAPE_POLY_SET(isl)
        inter.BooleanIntersection(poly(k, layer))
        return inter.Area() > 0

    for i, t in enumerate(tracks):
        layer = t.GetLayer()
        for e in (t.GetStart(), t.GetEnd()):
            hit = False
            for j, u in enumerate(tracks):
                if j != i and u.GetLayer() == layer and seg_dist(e, u.GetStart(), u.GetEnd()) <= u.GetWidth() / 2 + TOL:
                    contacts.append((i, j))
                    hit = True
            for j, v in enumerate(vias):
                if v.IsOnLayer(layer) and math.hypot(v.GetPosition().x - e.x, v.GetPosition().y - e.y) <= v.GetWidth(layer) / 2 + TOL:
                    contacts.append((i, iv + j))
                    hit = True
            for j, p in enumerate(pads):
                if p.IsOnLayer(layer) and p.HitTest(e, TOL):
                    contacts.append((i, ip + j))
                    hit = True
            for _, zl, isl, _ in islands:
                if zl == layer and isl.Collide(e, t.GetWidth() // 2):
                    hit = True
            if not hit:
                free_ends[i].append(e)
        for k in range(ii, ii + len(islands)):
            if overlaps(i, layer, k):
                contacts.append((i, k))
    for j, v in enumerate(vias):
        for jp, p in enumerate(pads):
            if p.HitTest(v.GetPosition(), 0) and any(v.IsOnLayer(l) and p.IsOnLayer(l) for l in p.GetLayerSet().CuStack()):
                contacts.append((iv + j, ip + jp))
        for k in range(ii, ii + len(islands)):
            if overlaps(iv + j, islands[k - ii][1], k):
                contacts.append((iv + j, k))
    for jp, p in enumerate(pads):
        for k in range(ii, ii + len(islands)):
            if overlaps(ip + jp, islands[k - ii][1], k):
                contacts.append((ip + jp, k))
    for a in range(ii, ii + len(islands)):  # overlapping pours of the same net
        for c in range(a + 1, ii + len(islands)):
            za, la, pa, ba = islands[a - ii]
            zc, lc, pc, bc = islands[c - ii]
            if la == lc and ba.Intersects(bc):
                inter = pcbnew.SHAPE_POLY_SET(pa)
                inter.Inflate(TOUCH, pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, pcbnew.FromMM(0.005))
                inter.BooleanIntersection(pc)
                if inter.Area() > 0:
                    contacts.append((a, c))
    return items, kind, contacts, free_ends


def groups(items, contacts, keep=lambda k: True):
    uf = UF(len(items))
    for a, c in contacts:
        if keep(a) and keep(c):
            uf.union(a, c)
    out = collections.defaultdict(list)
    for k in range(len(items)):
        if keep(k):
            out[uf.find(k)].append(k)
    return list(out.values())


def check(path, power_classes=None):
    ensure_pcbnew()
    import pcbnew
    T = pcbnew.ToMM
    b = pcbnew.LoadBoard(str(path))
    L = b.GetLayerName
    classes = {c["name"]: c for c in json.load(open(Path(path).with_suffix(".kicad_pro")))["net_settings"]["classes"]}
    if power_classes is None:
        power_classes = [n for n, c in classes.items() if c.get("track_width", 0) >= 0.5]

    def where(p):
        return f"({T(p.x):.2f}, {T(p.y):.2f})"

    def name(item):
        if isinstance(item, pcbnew.PAD):
            return f"{item.GetParentFootprint().GetReference()}.{item.GetNumber()}"
        return item.GetClass()

    by_net = collections.defaultdict(lambda: ([], [], [], []))
    for t in b.GetTracks():
        by_net[t.GetNetCode()][1 if t.GetClass() == "PCB_VIA" else 0].append(t)
    for fp in b.GetFootprints():
        for p in fp.Pads():
            by_net[p.GetNetCode()][2].append(p)
    for z in b.Zones():
        if z.GetIsRuleArea():
            continue
        for layer in z.GetLayerSet().CuStack():
            fill = z.GetFilledPolysList(layer)
            for i in range(fill.OutlineCount()):
                isl = pcbnew.SHAPE_POLY_SET()
                isl.AddOutline(fill.Outline(i))
                for h in range(fill.HoleCount(i)):
                    isl.AddHole(fill.Hole(i, h))
                by_net[z.GetNetCode()][3].append((z, layer, isl, isl.BBox()))

    report = collections.defaultdict(list)
    for code, (tracks, vias, pads, islands) in by_net.items():
        if code <= 0:
            continue
        netname = b.FindNet(code).GetNetname()
        items, kind, contacts, free_ends = build_net(pcbnew, tracks, vias, pads, islands)

        for g in groups(items, contacts):
            g_pads = [items[k] for k in g if kind[k] == "pad"]
            g_tracks = [items[k] for k in g if kind[k] == "track"]
            g_vias = [items[k] for k in g if kind[k] == "via"]
            g_isl = [items[k] for k in g if kind[k] == "island"]
            if not g_pads:
                what = []
                if g_tracks:
                    what.append(f"{len(g_tracks)} track(s) {sum(T(t.GetLength()) for t in g_tracks):.1f} mm on "
                                f"{sorted({L(t.GetLayer()) for t in g_tracks})}")
                if g_vias:
                    what.append(f"{len(g_vias)} via(s)")
                if g_isl:
                    what.append(f"{len(g_isl)} pour island(s) {sum(i[2].Area() for i in g_isl) / 1e12:.1f} mm² of "
                                f"{sorted({i[0].GetZoneName() or '(unnamed)' for i in g_isl})}")
                at = g_tracks[0].GetStart() if g_tracks else g_vias[0].GetPosition() if g_vias else g_isl[0][2].Outline(0).CPoint(0)
                report["floating"].append(f"{netname}: {', '.join(what)} at {where(at)}")
            elif g_isl and len(g_pads) == 1 and len(pads) > 1:
                report["pours reaching one pad"].append(
                    f"{netname}: {sorted({i[0].GetZoneName() or '(unnamed)' for i in g_isl})} "
                    f"({sum(i[2].Area() for i in g_isl) / 1e12:.1f} mm²) reaches only {name(g_pads[0])}")

        # stubs: from each free track end, walk back to the via, pad, pour or route it hangs off
        stops, adj = set(), collections.defaultdict(set)
        for a, c in contacts:
            if kind[a] == "track" and kind[c] == "track":
                adj[a].add(c)
                adj[c].add(a)
            elif kind[a] == "track" or kind[c] == "track":
                stops.add(a if kind[a] == "track" else c)
        for i, free in free_ends.items():
            run, cur = [i], i
            while cur not in stops:
                nxt = adj[cur] - set(run)
                if len(nxt) != 1:
                    break
                n = nxt.pop()
                if len(adj[n] - {cur}) >= 2:  # n carries on both ways: the route this stub hangs off
                    break
                run.append(n)
                cur = n
            t = tracks[i]
            report["stubs"].append(f"{netname}: dead end at {where(free[0])} on {L(t.GetLayer())}, "
                                   f"{len(run)} segment(s) {sum(T(tracks[k].GetLength()) for k in run):.1f} mm")

        if not islands and len(tracks) > 1:
            key = lambda p: (round(p.x / 5000), round(p.y / 5000))
            edges = {tuple(sorted((key(t.GetStart()) + (t.GetLayer(),), key(t.GetEnd()) + (t.GetLayer(),)))) for t in tracks
                     if key(t.GetStart()) != key(t.GetEnd())}
            nodes = {n for e in edges for n in e}
            uf = UF(len(nodes))
            ix = {nd: k for k, nd in enumerate(nodes)}
            for a, c in edges:
                uf.union(ix[a], ix[c])
            cycles = len(edges) - len(nodes) + len({uf.find(k) for k in range(len(nodes))})
            if cycles > 0:
                report["loops"].append(f"{netname}: {cycles} loop(s) on one layer, {len(tracks)} segments")

        cls = b.FindNet(code).GetNetClassName().split(",")[0]
        if cls in power_classes and len(pads) > 1:
            want = classes[cls]["track_width"]
            thin = lambda k: kind[k] == "track" and not items[k].IsLocked() and T(items[k].GetWidth()) < want - 1e-3
            gs = [g for g in groups(items, contacts, keep=lambda k: not thin(k)) if any(kind[k] == "pad" for k in g)]
            if len(gs) > 1:
                gs.sort(key=lambda g: -sum(kind[k] == "pad" for k in g))
                for g in gs[1:]:
                    report["power pads fed only by thin track"].append(
                        f"{netname} ({cls}, {want} mm): {', '.join(name(items[k]) for k in g if kind[k] == 'pad')}")
    return report


SECTIONS = ("floating", "stubs", "loops", "pours reaching one pad", "power pads fed only by thin track")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("--power-classes", help="comma-separated net classes for the thin-feed check")
    a = ap.parse_args(argv)
    report = check(a.board, a.power_classes.split(",") if a.power_classes else None)
    for section in SECTIONS:
        rows = report.get(section, [])
        print(f"== {section}: {len(rows)}")
        for r in sorted(rows):
            print("  " + r)


if __name__ == "__main__":
    main()
