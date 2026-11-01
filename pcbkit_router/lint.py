"""Routing lint: the faults people's eyes reject in a route, counted on what a routing added to its base board.

    lint.py JOB.json BASE.json NAME=ROUTES.json ... [--json=OUT.json]

BASE.json is geom.py of the board before the routing. A routing is nroute's output, or another geom.py dump (a hand
routed board): then what it adds is its copper that isn't on the base. Only the job's nets are counted; the base's
copper still connects. Per routing:
- back_mm: track on B.Cu, which a two-layer board's GND pour would rather keep;
- necks: a run narrower than the net's width that touches no pad (mid), or one reaching more than 1 mm from its pad;
- tees: three or more tracks of a net meeting more than 0.3 mm from its pads and off its vias;
- corners of 90 degrees and of 135 or more (two bends less than 0.2 mm apart count as one); jogs: two opposite
  bends less than 1 mm apart;
- vias: in an IC's pin area (6 SMD pads or more, the pads' box plus 0.25 mm), within 0.25 mm of another net's pad,
  and the track length to the nearest pad of the net (dog-bone within 1 mm, 1-3 mm, beyond that mid-route);
- hug: track length closer than 0.25 mm to another net's pad;
- under: track length on the far side of an IC, inside its pads' box (wide nets, the job's power, apart);
- detour: length over 1.3 x the net's spanning tree (octile), summed;
- cross-backs: a track that crosses others and later crosses them back (cross.py), counted when the detour saves 0.5 mm
  or more and is one of the job's nets.
`pcbkit reroute bench --lint` prints these for bench runs. Run it with the router's Python.
"""
import sys, json, math, heapq, collections
import numpy as np
from . import cross

EPS = 0.003
COLS = ["vias", "mm", "back_mm", "necks", "necks_mid", "necks_long", "neck_mm", "tees", "corners90", "acute", "jogs", "via_in_ic",
        "via_crowd", "via_dogbone", "via_1_3", "via_mid", "hug_mm", "under_mm", "under_power_mm", "belly_mm", "detour_mm",
        "crossbacks"]


def octile_mst(pts):
    pts = list(dict.fromkeys(pts))
    if len(pts) < 2: return 0.0
    f = lambda p, q: max(abs(p[0] - q[0]), abs(p[1] - q[1])) + 0.414 * min(abs(p[0] - q[0]), abs(p[1] - q[1]))
    d = {p: f(p, pts[0]) for p in pts[1:]}; total = 0.0
    while d:
        p = min(d, key=d.get); total += d.pop(p)
        for q in d: d[q] = min(d[q], f(p, q))
    return total


def seg_dist(px, py, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0; L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0, min(1, ((px - x0) * dx + (py - y0) * dy) / L2))
    return math.hypot(px - x0 - t * dx, py - y0 - t * dy)


def clip_len(x0, y0, x1, y1, r):
    """length of the segment inside the rectangle r (Liang-Barsky)"""
    dx, dy = x1 - x0, y1 - y0; t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x0 - r[0]), (dx, r[2] - x0), (-dy, y0 - r[1]), (dy, r[3] - y0)):
        if p == 0:
            if q < 0: return 0.0
            continue
        t = q / p
        if p < 0: t0 = max(t0, t)
        else: t1 = min(t1, t)
        if t0 > t1: return 0.0
    return (t1 - t0) * math.hypot(dx, dy)


class Pad:
    def __init__(self, it):
        self.net, self.L = it["net"], it["layer"]
        self.edges = []
        xs, ys = [], []
        for poly in it["polys"]:
            o = np.array(poly[0], float); q = np.roll(o, -1, axis=0)
            self.edges.append(np.hstack([o, q])); xs += list(o[:, 0]); ys += list(o[:, 1])
        self.edges = np.vstack(self.edges) if self.edges else np.zeros((0, 4))
        self.box = (min(xs), min(ys), max(xs), max(ys))

    def dist(self, x, y):
        e = self.edges; dx, dy = e[:, 2] - e[:, 0], e[:, 3] - e[:, 1]
        L2 = dx * dx + dy * dy; L2[L2 == 0] = 1e-12
        t = np.clip(((x - e[:, 0]) * dx + (y - e[:, 1]) * dy) / L2, 0, 1)
        d = np.hypot(x - e[:, 0] - t * dx, y - e[:, 1] - t * dy).min()
        cross = ((e[:, 1] > y) != (e[:, 3] > y)) & (x < e[:, 0] + (y - e[:, 1]) * dx / np.where(dy == 0, 1e-12, dy))
        return 0.0 if cross.sum() % 2 else float(d)


def find_ics(items):
    """(ref, side, box): as board.Board.parts"""
    by = collections.defaultdict(list)
    for it in items:
        if it["kind"] == "pad": by[it["ref"]].append(it)
    out = []
    for ref, its in by.items():
        smd = [it for it in its if it.get("smd")]
        if len(smd) < 6 or len(smd) * 2 <= len(its) - len(smd): continue
        bs = [Pad(it).box for it in smd]
        out.append((ref, smd[0]["layer"], (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))))
    return out


def base_keys(geom):
    keys = set()
    for it in geom["items"]:
        if it["kind"] == "track":
            a, c = tuple(round(v, 3) for v in it["seg"][:2]), tuple(round(v, 3) for v in it["seg"][2:])
            keys.add(("t", it["net"], it["layer"], min(a, c), max(a, c)))
        elif it["kind"] == "via":
            keys.add(("v", it["net"], round(it["circle"][0], 3), round(it["circle"][1], 3)))
    return keys


def copper(base, routing, via_r=0.225):
    """{net: segments [layer, x0, y0, x1, y1, w, fixed]}, {net: vias [x, y, r, fixed]}: the base's and what's added"""
    segs, vias = collections.defaultdict(list), collections.defaultdict(list)
    def seg(n, L, s, w, fx):
        if L in "FB" and math.hypot(s[2] - s[0], s[3] - s[1]) > 1e-4: segs[n].append([L, *s, w, fx])
    if isinstance(routing, dict):                   # another board's geom.py dump: its own copper, marked against the base
        keys = base_keys(base)
        for it in routing["items"]:
            if it["kind"] == "track":
                a, c = tuple(round(v, 3) for v in it["seg"][:2]), tuple(round(v, 3) for v in it["seg"][2:])
                seg(it["net"], it["layer"], it["seg"], it["w"], ("t", it["net"], it["layer"], min(a, c), max(a, c)) in keys)
            elif it["kind"] == "via":
                x, y, r = it["circle"]
                vias[it["net"]].append((x, y, r, ("v", it["net"], round(x, 3), round(y, 3)) in keys))
        return segs, vias
    for it in base["items"]:
        if it["kind"] == "track": seg(it["net"], it["layer"], it["seg"], it["w"], True)
        elif it["kind"] == "via": vias[it["net"]].append((*it["circle"], True))
    for r in routing:
        n = r["net"]
        for t in r["tracks"]:
            for a, b in zip(t["pts"], t["pts"][1:]): seg(n, t["layer"], [*a, *b], t["w"], False)
        for x, y in r["vias"]: vias[n].append((x, y, via_r, False))
    return segs, vias


def lint(job, base, routing):
    dflt = job["defaults"]["w"]
    width = {n["net"]: n.get("w", dflt) for n in job["nets"]}
    power = {n for n, w in width.items() if w > dflt + 1e-6}
    pads = [Pad(it) for it in base["items"] if it["kind"] == "pad" and it["polys"]]
    grid = collections.defaultdict(list)
    for p in pads:
        for gx in range(int(p.box[0] // 1) - 1, int(p.box[2] // 1) + 2):
            for gy in range(int(p.box[1] // 1) - 1, int(p.box[3] // 1) + 2): grid[(p.L, gx, gy)].append(p)
    ics = find_ics(base["items"])

    def pad_gap(x, y, L, net, other=False):
        """gap from (x, y) to the nearest pad of net (or of any other net) on layer L, within about a millimetre"""
        best = 9e9
        for p in grid.get((L, int(x // 1), int(y // 1)), ()):
            if (p.net != net) != other or not p.net: continue
            if x < p.box[0] - 0.6 or x > p.box[2] + 0.6 or y < p.box[1] - 0.6 or y > p.box[3] + 0.6: continue
            best = min(best, p.dist(x, y))
        return best

    segs, vias = copper(base, routing, job.get("via_size", [0.45])[0] / 2)
    out = collections.Counter(); stub = []
    for n, S in segs.items():
        if n not in width: continue
        wN = width[n]
        # split segments where another segment of the net ends on their middle (a T on a track)
        ends = {(s[0], round(x, 3), round(y, 3)) for s in S for x, y in ((s[1], s[2]), (s[3], s[4]))}
        split = []
        for s in S:
            cut = [(0.0, s[1], s[2]), (1.0, s[3], s[4])]
            L2 = (s[3] - s[1]) ** 2 + (s[4] - s[2]) ** 2
            for (L, x, y) in ends:
                if L == s[0] and seg_dist(x, y, *s[1:5]) < EPS:
                    t = ((x - s[1]) * (s[3] - s[1]) + (y - s[2]) * (s[4] - s[2])) / L2
                    if EPS < t * math.sqrt(L2) < math.sqrt(L2) - EPS: cut.append((t, x, y))
            cut.sort()
            for a, c in zip(cut, cut[1:]): split.append([s[0], a[1], a[2], c[1], c[2], s[5], s[6]])
        S = split
        key = lambda L, x, y: (L, round(x, 3), round(y, 3))
        adj = collections.defaultdict(list)
        for i, s in enumerate(S):
            a, c = key(s[0], s[1], s[2]), key(s[0], s[3], s[4])
            adj[a].append((i, c)); adj[c].append((i, a))
        vset = vias[n]
        at_via = lambda k: any(math.hypot(k[1] - x, k[2] - y) <= r for x, y, r, _ in vset)
        gap = {k: pad_gap(k[1], k[2], k[0], n) for k in adj}
        on_pad = {k: g <= 0.05 for k, g in gap.items()}
        # tees
        for k, e in adj.items():
            if len(e) >= 3 and gap[k] > 0.3 and not at_via(k) and not all(S[i][6] for i, _ in e): out["tees"] += 1
        # chains between branch points, pads and vias: corners and jogs
        stop = lambda k: len(adj[k]) != 2 or on_pad[k] or at_via(k)
        seen = set()
        for k0 in list(adj):
            if not stop(k0): continue
            for i, k1 in adj[k0]:
                if i in seen: continue
                pts, new = [k0], False
                while True:
                    seen.add(i); pts.append(k1); new |= not S[i][6]
                    if stop(k1): break
                    nxt = [(j, kk) for j, kk in adj[k1] if j != i]
                    if not nxt or nxt[0][0] in seen: break
                    i, k1 = nxt[0]
                if not new: continue
                turns, along = [], 0.0                      # (signed angle, distance along from the start)
                for p, q, r in zip(pts, pts[1:], pts[2:]):
                    a1 = math.atan2(q[2] - p[2], q[1] - p[1]); a2 = math.atan2(r[2] - q[2], r[1] - q[1])
                    along += math.hypot(q[1] - p[1], q[2] - p[2])
                    d = (math.degrees(a2 - a1) + 180) % 360 - 180
                    if abs(d) > 2: turns.append([d, along])
                merged = []
                for d, at in turns:
                    if merged and at - merged[-1][1] < 0.2 and (d > 0) == (merged[-1][0] > 0): merged[-1][0] += d; merged[-1][1] = at
                    else: merged.append([d, at])
                for d, at in merged:
                    if abs(d) >= 120: out["acute"] += 1
                    elif abs(d) >= 70: out["corners90"] += 1
                for (d1, a1), (d2, a2) in zip(merged, merged[1:]):
                    if (d1 > 0) != (d2 > 0) and a2 - a1 < 1.0: out["jogs"] += 1
        # necks: runs narrower than the net's width
        par = list(range(len(S)))
        def find(i):
            while par[i] != i: par[i] = par[par[i]]; i = par[i]
            return i
        thin = [i for i, s in enumerate(S) if s[5] < wN - 0.01 and not s[6]]
        for k, e in adj.items():
            t = [i for i, _ in e if S[i][5] < wN - 0.01 and not S[i][6]]
            for i in t[1:]: par[find(i)] = find(t[0])
        runs = collections.defaultdict(list)
        for i in thin: runs[find(i)].append(i)
        for r in runs.values():
            ks = {key(S[i][0], S[i][1], S[i][2]) for i in r} | {key(S[i][0], S[i][3], S[i][4]) for i in r}
            L = sum(math.hypot(S[i][3] - S[i][1], S[i][4] - S[i][2]) for i in r)
            out["neck_mm"] += L; out["necks"] += 1
            if not any(on_pad[k] for k in ks): out["necks_mid"] += 1
            elif L > 1.0: out["necks_long"] += 1
        # vias: where, and how far from the pad they serve
        G = collections.defaultdict(list)
        for s in S:
            a, c = key(s[0], s[1], s[2]), key(s[0], s[3], s[4]); L = math.hypot(s[3] - s[1], s[4] - s[2])
            G[a].append((c, L)); G[c].append((a, L))
        for x, y, r, fx in vset:
            if fx: continue
            out["vias"] += 1
            if any(bb[0] - 0.25 <= x <= bb[2] + 0.25 and bb[1] - 0.25 <= y <= bb[3] + 0.25 for _, _, bb in ics): out["via_in_ic"] += 1
            if min(pad_gap(x, y, "F", n, True), pad_gap(x, y, "B", n, True)) - r < 0.25: out["via_crowd"] += 1
            if min(pad_gap(x, y, "F", n), pad_gap(x, y, "B", n)) - r <= 0.05: stub.append(0.0); continue
            src = [k for k in G if math.hypot(k[1] - x, k[2] - y) <= r]
            dist = {k: 0.0 for k in src}; h = [(0.0, k) for k in src]; got = None
            while h:
                d, k = heapq.heappop(h)
                if d > dist.get(k, 9e9): continue
                if on_pad[k]: got = d; break
                if d > 0 and at_via(k): continue                   # don't pass through another via
                for kk, L in G[k]:
                    if d + L < dist.get(kk, 9e9): dist[kk] = d + L; heapq.heappush(h, (d + L, kk))
            stub.append(got if got is not None else 99.0)
        # hugging other nets' pads, and running under ICs
        for s in S:
            if s[6]: continue
            L = math.hypot(s[3] - s[1], s[4] - s[2]); k = max(1, int(L / 0.1))
            hug = sum(1 for j in range(k + 1) if pad_gap(s[1] + (s[3] - s[1]) * j / k, s[2] + (s[4] - s[2]) * j / k,
                                                          s[0], n, True) - s[5] / 2 < 0.25)
            out["hug_mm"] += L * hug / (k + 1)
            for ref, side, bb in ics:
                if s[0] != side: out["under_power_mm" if n in power else "under_mm"] += clip_len(s[1], s[2], s[3], s[4], bb)
                elif bb[2] - bb[0] > 1.8 and bb[3] - bb[1] > 1.8:
                    out["belly_mm"] += clip_len(s[1], s[2], s[3], s[4], (bb[0] + 0.9, bb[1] + 0.9, bb[2] - 0.9, bb[3] - 0.9))
            out["mm"] += L
            if s[0] == "B": out["back_mm"] += L
        pts = [tuple(p["at"]) for p in base["pads"] if p["net"] == n]
        out["detour_mm"] += max(0.0, sum(math.hypot(s[3] - s[1], s[4] - s[2]) for s in S) - 1.3 * octile_mst(pts))
    out["via_dogbone"] = sum(1 for d in stub if d <= 1.0)
    out["via_1_3"] = sum(1 for d in stub if 1.0 < d <= 3.0)
    out["via_mid"] = sum(1 for d in stub if d > 3.0)
    out["crossbacks"] = len(cross.find(routing, nets=set(width), min_excess=0.5)[1] if isinstance(routing, dict)
                            else cross.find(base, routing, nets=set(width), min_excess=0.5)[1])
    return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in out.items()}


def show(res, label="routing"):
    print(label.ljust(28) + "".join(c[:10].rjust(11) for c in COLS))
    for p, r in res.items():
        print(p[:27].ljust(28) + "".join(str(r.get(c, 0)).rjust(11) for c in COLS))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opt = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
    job, base = json.load(open(args[0])), json.load(open(args[1])); res = {}
    for a in args[2:]:
        name, path = a.split("=", 1) if "=" in a else (a.split("/")[-1], a)
        res[name] = lint(job, base, json.load(open(path)))
    show(res)
    if "json" in opt: json.dump(res, open(opt["json"], "w"), indent=1)
