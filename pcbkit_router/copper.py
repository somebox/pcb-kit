"""A routed net's copper as a graph, joined where KiCad joins it: chains between pads and branch points, runs between width
changes, dead ends. A track's end anywhere on another track's copper or on a via is a join, and so are two tracks of the
net that cross: the router lands a join anywhere on the target's copper, so a graph of exact end points misses joins (the
first shortcuts took out a +3V3 via two pieces met on off-centre, and left one of them on nothing)."""
import math
from collections import defaultdict
import numpy as np
from .board import pad_box
from .costs import RES, STEP, DEF
from .netroute import NetRouter


class Copper:
    """Negotiation's methods on a net's routed copper"""
    def graph(self, n):
        """net n's routed copper as a graph, joined where the copper is: a track's end on another track of the net
        (anywhere on its copper, not only its centre line) or on a via, a via on a track, and tracks of the net that
        cross on a layer (KiCad joins those too). Returns (pieces [(a, b, layer, w)], edges [(u, v, "t"|"v", index)],
        nodes {u: ((x, y), layer)}, fixed: the nodes on a pad or the net's other fixed copper); u, v are merged nodes"""
        s, bd = self.st[n], self.bd; c = bd.code(n); E, T = 1e-3, 0.03       # T: the raster's slack
        segs = [(tuple(a), tuple(b), t["layer"], t["w"]) for t in s["tracks"] for a, b in zip(t["pts"], t["pts"][1:])
                if math.dist(a, b) > E]
        vias = [tuple(v) for v in s["vias"]]
        cuts = [{0.0, 1.0} for _ in segs]; same = []           # same: pairs of (point, layer) that are one node
        at = lambda a, b, u: (round(a[0] + u * (b[0] - a[0]), 4), round(a[1] + u * (b[1] - a[1]), 4))
        def proj(p, a, b):
            dx, dy = b[0] - a[0], b[1] - a[1]
            u = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy)))
            return u, math.hypot(a[0] + u * dx - p[0], a[1] + u * dy - p[1])
        def touch(i, p, L2, r):                    # p (on layer L2) within r of segment i's centre line: one node
            u, d = proj(p, segs[i][0], segs[i][1])
            if d <= r: cuts[i].add(u); same.append(((at(segs[i][0], segs[i][1], u), segs[i][2]), (p, L2)))
        grow = max([w for *_, w in segs] + [2 * bd.via_r]) / 2 + T
        box = [(min(a[0], b[0]) - grow, min(a[1], b[1]) - grow, max(a[0], b[0]) + grow, max(a[1], b[1]) + grow)
               for a, b, _, _ in segs]
        near = lambda bx, p: bx[0] <= p[0] <= bx[2] and bx[1] <= p[1] <= bx[3]
        for i, (a, b, L, w) in enumerate(segs):
            for j in range(i + 1, len(segs)):
                c0, d0, L2, w2 = segs[j]; bi, bj = box[i], box[j]
                if L2 != L or bi[0] > bj[2] or bj[0] > bi[2] or bi[1] > bj[3] or bj[1] > bi[3]: continue
                for p in (c0, d0): touch(i, p, L, w / 2 + T)
                for p in (a, b): touch(j, p, L, w2 / 2 + T)
                o = lambda p, q, r: (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
                o1, o2, o3, o4 = o(a, b, c0), o(a, b, d0), o(c0, d0, a), o(c0, d0, b)
                if o1 * o2 < 0 and o3 * o4 < 0:      # the centre lines cross
                    u = o3 / (o3 - o4); cuts[i].add(u); q = at(a, b, u)
                    touch(j, q, L, E)
            for v in vias:
                if near(box[i], v):
                    touch(i, v, L, w / 2 + T)              # a via on the track
                    for p in (a, b):
                        if math.dist(p, v) <= bd.via_r + T: same.append(((p, L), (v, L)))
        pieces = []
        for (a, b, L, w), cu in zip(segs, cuts):
            q = [at(a, b, u) for u in sorted(cu)]
            for p0, p1 in zip(q, q[1:]):
                if math.dist(p0, p1) > E: pieces.append((p0, p1, L, w))
                else: same.append(((p0, L), (p1, L)))
        idx, xy, parent = {}, [], []
        def node(p, L):
            k = (round(p[0] / E), round(p[1] / E), L)
            if k not in idx: idx[k] = len(xy); xy.append((p, L)); parent.append(len(parent))
            return idx[k]
        def find(a):
            while parent[a] != a: parent[a] = parent[parent[a]]; a = parent[a]
            return a
        for (p, L), (q, L2) in same: parent[find(node(p, L))] = find(node(q, L2))
        raw = [(node(a, L), node(b, L), "t", i) for i, (a, b, L, _) in enumerate(pieces)]
        raw += [(node(v, "F"), node(v, "B"), "v", i) for i, v in enumerate(vias)]
        edges = [(find(u), find(v), k, i) for u, v, k, i in raw]
        fixed = set()
        for k, ((x, y), L) in enumerate(xy):
            i, j = int((x - bd.X0) / RES + 0.5), int((y - bd.Y0) / RES + 0.5)
            if 0 <= j < bd.H and 0 <= i < bd.W and bd.lab0[L][j, i] == c: fixed.add(find(k))
        return pieces, edges, {find(k): xy[k] for k in range(len(xy))}, fixed

    def chains(self, n):
        """net n's routed copper cut into chains, as cross.py does: runs between pads and the net's other fixed
        copper, branch points and ends. [{"pieces": [(a, b, layer, w)], "vias": [(x, y)], "keep": (tracks, vias)
        without the chain, "cost": length + `via` mm per via, "excess": cost - the straight line between its ends,
        "ends"}]"""
        s = self.st[n]
        pieces, edges, at, fixed = self.graph(n)
        adj = defaultdict(list)
        for e, (u, v, _, _) in enumerate(edges):
            if u != v: adj[u].append(e); adj[v].append(e)
        brk = {k for k in adj if len(adj[k]) != 2 or k in fixed}
        vnodes = {m for u, v, kind, _ in edges if kind == "v" for m in (u, v)}
        boxes = self.pad_boxes(n)
        def tee(k):
            (x, y), _ = at[k]
            return len(adj[k]) >= 3 and k not in fixed and k not in vnodes and \
                not any(b[0] - 0.3 <= x <= b[2] + 0.3 and b[1] - 0.3 <= y <= b[3] + 0.3 for b in boxes)
        seen, out = set(), []
        def walk(k0, e):
            es, k = [], k0
            while True:
                seen.add(e); es.append(e); u, v, _, _ = edges[e]; k = v if u == k else u
                if k in brk or k == k0: break
                nxt = [f for f in adj[k] if f not in seen]
                if not nxt: break
                e = nxt[0]
            return k0, k, es
        for k in sorted(brk) + sorted(adj):
            for e in adj[k]:
                if e not in seen: out.append(walk(k, e))
        res = []
        for k0, k1, es in out:
            # a via where the chain ends stays (tidy takes it if nothing needs it): the pieces left at it may only touch
            # on the raster, which the router counts as joined and KiCad doesn't
            ti = {edges[e][3] for e in es if edges[e][2] == "t"}
            vi = {edges[e][3] for e in es if edges[e][2] == "v" and edges[e][0] not in brk and edges[e][1] not in brk}
            if not ti and not vi: continue
            cost = sum(math.dist(*pieces[i][:2]) for i in ti) + DEF["via"] * len(vi)
            (x0, y0), (x1, y1) = at[k0][0], at[k1][0]; dx, dy = abs(x1 - x0), abs(y1 - y0)
            line = max(dx, dy) + (math.sqrt(2) - 1) * min(dx, dy)
            keep = ([{"layer": L, "w": w, "pts": [a, b]} for i, (a, b, L, w) in enumerate(pieces) if i not in ti],
                    [v for i, v in enumerate(s["vias"]) if i not in vi])
            res.append(dict(pieces=[pieces[i] for i in sorted(ti)], vias=[s["vias"][i] for i in sorted(vi)], keep=keep,
                            cost=cost, excess=cost - line, over=cost - DEF["via"] * len(vi) - line, ends=(at[k0][0], at[k1][0]),
                            tees=[at[k][0] for k in (k0, k1) if tee(k)]))
        return res

    def pad_boxes(self, n):
        return self.cache.setdefault(("pad_boxes", n), [pad_box([q for ps in p["polys"].values() for q in ps])
                                                        for p in self.geom["pads"] if p["net"] == n])

    def tees(self, n):
        """how many T's net n has away from its pads and vias"""
        return len({p for ch in self.chains(n) for p in ch["tees"]})

    def tidy(self, n):
        """take out net n's routed copper that leads nowhere: track ends and via sides on nothing (repeatedly, so a
        dead branch goes whole), and pieces joined to no pad or fixed copper. True when something went"""
        s = self.st[n]
        pieces, edges, at, fixed = self.graph(n)
        alive = {e for e, (u, v, _, _) in enumerate(edges) if u != v}
        loops = [e for e, (u, v, _, _) in enumerate(edges) if u == v]
        deg = defaultdict(int)
        for e in alive: deg[edges[e][0]] += 1; deg[edges[e][1]] += 1
        free = [k for k in deg if deg[k] == 1 and k not in fixed]
        while free:
            k = free.pop()
            if deg[k] != 1: continue
            e = next(e for e in alive if k in edges[e][:2]); alive.discard(e)
            for m in edges[e][:2]:
                deg[m] -= 1
                if deg[m] == 1 and m not in fixed: free.append(m)
        adj = defaultdict(list)
        for e in alive: adj[edges[e][0]].append(e); adj[edges[e][1]].append(e)
        seen = set()
        for k0 in list(adj):                       # pieces joined to nothing fixed
            if k0 in seen: continue
            comp, es, todo = {k0}, set(), [k0]
            while todo:
                k = todo.pop()
                for e in adj[k]:
                    es.add(e)
                    for m in edges[e][:2]:
                        if m not in comp: comp.add(m); todo.append(m)
            seen |= comp
            if not comp & fixed: alive -= es
        live = {m for e in alive for m in edges[e][:2]} | fixed
        alive |= {e for e in loops if edges[e][0] in live}
        if len(alive) == len(edges): return False
        tracks = [{"layer": L, "w": w, "pts": [a, b]} for e, (_, _, k, i) in enumerate(edges) if e in alive and k == "t"
                  for a, b, L, w in [pieces[i]]]
        vias = [s["vias"][i] for e, (_, _, k, i) in enumerate(edges) if e in alive and k == "v"]
        self.place(n, tracks, vias, s["failed"], s["secs"])
        return True

    def pad_groups(self, n):
        """how many groups net n's pads fall into on its copper as it stands, fixed and routed (the router's view:
        copper that touches on the raster, layers joined through vias and through-hole pads)"""
        s, bd = self.st[n], self.bd; c = bd.code(n)
        fixed = self.cache.get(("fixed_box", n))
        if fixed is None:
            fixed = self.cache[("fixed_box", n)] = []
            for L in "FB12":
                ys, xs = np.nonzero(bd.lab0[L] == c)
                if len(xs): fixed += [(bd.X0 + xs.min() * RES, bd.Y0 + ys.min() * RES), (bd.X0 + xs.max() * RES, bd.Y0 + ys.max() * RES)]
        bd.watch()
        for t in s["tracks"]:
            for (x0, y0), (x1, y1) in zip(t["pts"], t["pts"][1:]): bd.paint_seg(t["layer"], x0, y0, x1, y1, t["w"], c)
        for x, y in s["vias"]: bd.add_via(x, y, c)
        r = NetRouter(bd, s["spec"])
        pts = [p["at"] for p in r.pads] + fixed + [q for t in s["tracks"] for q in t["pts"]] + [tuple(v) for v in s["vias"]]
        r.win = j0, j1, i0, i1 = bd.window(pts, 1.0)
        r.Hn, r.Wn = len(range(0, j1 - j0, STEP)), len(range(0, i1 - i0, STEP))
        comp = r.components()
        groups = {r.pad_comp(comp, p) for p in r.pads + r.trunks} - {0}
        bd.thaw()
        return len(groups)

    def runs(self, n):
        """net n's track as runs [(layer, width, points, end on a pad at the start, at the end)], cut at its pads and
        other fixed copper, vias, branch points and width changes"""
        pieces, edges, at, fixed = self.graph(n)
        adj, vnodes = defaultdict(list), set()
        for e, (u, v, kind, _) in enumerate(edges):
            if kind == "v": vnodes |= {u, v}
            elif u != v: adj[u].append(e); adj[v].append(e)
        wid = lambda e: pieces[edges[e][3]][3]
        stop = {k for k, es in adj.items() if len(es) != 2 or k in fixed or k in vnodes or wid(es[0]) != wid(es[1])}
        seen, out = set(), []
        for k0 in sorted(stop):
            for e in adj[k0]:
                if e in seen: continue
                k, pts = k0, None
                while True:
                    seen.add(e); u, v, _, i = edges[e]; a, b, L, w = pieces[i]
                    a, b, k = (a, b, v) if u == k else (b, a, u)
                    # pieces join anywhere on each other's copper: a step between them stays, or the run would swing
                    pts = [a, b] if pts is None else pts + ([a] if math.dist(pts[-1], a) > 1e-6 else []) + [b]
                    if k in stop: break
                    nxt = [f for f in adj[k] if f not in seen]
                    if not nxt: break
                    e = nxt[0]
                out.append((L, w, pts, k0 in fixed, k in fixed))
        return out
