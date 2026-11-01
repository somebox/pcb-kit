"""Cross-backs: a track that crosses one or more others and later crosses them back: it drops through two tracks, runs
under them and climbs back through both, where running above them it would cross nothing. A cross-back says the track is
on the wrong side of the ones it crossed: it, or they, could move or swap places.

Each net's copper (tracks and vias, pads as its terminals) is cut into chains: runs between pads, branch points and
ends, with the vias along the way. Two chains of different nets cross where their plan views do (they are on
different layers there, or it would be a short). Two crossings of chain A with chain B, with no crossing of B
between them along A, make a cross-back. Of the two, the chain whose stretch between the crossings is longer is the
detour; the other is what it crossed. Nested cross-backs on the same detour are one group. For each group: the detour's stretch (mm, vias) against the longest stretch it crossed, so `excess`
is about what the detour would save by running beside the tracks it crossed.

    cross.py GEOM.json [--routes=ROUTES.json] [--nets=JOB.json] [--png=OUT.png] [--min=0.5]
GEOM is geom.py's output; ROUTES (nroute's output) is added to it when GEOM is the board before the routing.
--nets keeps only the groups whose detour is a net of the job; --min drops groups with less excess (mm)."""
import sys, json, math, itertools
from collections import defaultdict

SNAP = 0.002            # mm: track ends closer than this are one point


def short(n): return n.split("/")[-1]


def inside(poly, x, y):
    c = False
    for (x0, y0), (x1, y1) in zip(poly, poly[1:] + poly[:1]):
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0): c = not c
    return c


def copper(geom, routes=()):
    """{net: {"segs": [(x0, y0, x1, y1, layer)], "vias": [(x, y)], "pads": [(name, x, y, {layer: polys})]}}"""
    nets = defaultdict(lambda: {"segs": [], "vias": [], "pads": []})
    for it in geom["items"]:
        if not it["net"]: continue
        if it["kind"] == "track" and it["layer"] in "FB": nets[it["net"]]["segs"].append((*it["seg"], it["layer"]))
        elif it["kind"] == "via": nets[it["net"]]["vias"].append(tuple(it["circle"][:2]))
    for p in geom["pads"]:
        nets[p["net"]]["pads"].append((f'{p["ref"]}.{p["num"]}', *p["at"], p["polys"]))
    for r in routes:
        for t in r["tracks"]:
            for a, b in zip(t["pts"], t["pts"][1:]): nets[r["net"]]["segs"].append((*a, *b, t["layer"]))
        nets[r["net"]]["vias"] += [tuple(v) for v in r["vias"]]
    return nets


def chains(net, c):
    """the net's copper cut into chains: [{"net", "pts": [(x, y)], "layers": [per segment], "vias": [index of a via
    vertex], "ends": (name, name)}] between pads, branch points and ends"""
    key = lambda x, y: (round(x / SNAP), round(y / SNAP))
    node, xy = {}, {}
    def at(x, y):
        k = key(x, y)
        if k not in node: node[k] = len(node); xy[node[k]] = (x, y)
        return node[k]
    vias = {at(x, y) for x, y in c["vias"]}
    segs = [s for s in c["segs"] if math.dist(s[:2], s[2:4]) > SNAP]
    # a track that ends on another one's middle (a tap): split that one there
    ends = [(s[0], s[1], s[4]) for s in segs] + [(s[2], s[3], s[4]) for s in segs]
    out = []
    for x0, y0, x1, y1, L in segs:
        cut = [0.0, 1.0]; dx, dy = x1 - x0, y1 - y0; ll = dx * dx + dy * dy
        for x, y, L2 in ends:
            if L2 != L: continue
            u = ((x - x0) * dx + (y - y0) * dy) / ll
            if 1e-3 < u < 1 - 1e-3 and math.hypot(x0 + u * dx - x, y0 + u * dy - y) < SNAP: cut.append(u)
        cut = sorted(set(cut))
        for u0, u1 in zip(cut, cut[1:]):
            out.append((x0 + u0 * dx, y0 + u0 * dy, x0 + u1 * dx, y0 + u1 * dy, L))
    adj = defaultdict(list)
    for i, (x0, y0, x1, y1, L) in enumerate(out):
        a, b = at(x0, y0), at(x1, y1)
        if a != b: adj[a].append((b, i, L)); adj[b].append((a, i, L))
    pad_of = {}
    for name, px, py, polys in c["pads"]:
        for n, (x, y) in xy.items():
            if math.hypot(x - px, y - py) < 3 and any(inside(poly[0], x, y) for ps in polys.values() for poly in ps):
                pad_of[n] = name
    brk = {n for n in adj if len(adj[n]) != 2 or n in pad_of}
    seen, res = set(), []
    def walk(n0, e0):
        pts, lay, vi = [xy[n0]], [], []; e = e0
        while True:
            m, i, L = e; seen.add(i); pts.append(xy[m]); lay.append(L)
            if m in brk or m == n0: break
            if m in vias: vi.append(len(pts) - 1)
            nxt = [f for f in adj[m] if f[1] != i and f[1] not in seen]
            if not nxt: break
            e = nxt[0]
        return {"net": net, "pts": pts, "layers": lay, "vias": vi,
                "ends": (pad_of.get(n0, "via" if n0 in vias else "end"), pad_of.get(m, "via" if m in vias else "end"))}
    for n in sorted(brk) + sorted(adj):
        for e in adj[n]:
            if e[1] not in seen: res.append(walk(n, e))
    for ch in res:
        s = [0.0]
        for a, b in zip(ch["pts"], ch["pts"][1:]): s.append(s[-1] + math.dist(a, b))
        ch["s"] = s
    return res


def crossings(chs, cell=1.0):
    """[(i, si, j, sj, x, y)]: chain i (at arc length si) crosses chain j (at sj), different nets, both orders"""
    grid = defaultdict(list)
    for ci, ch in enumerate(chs):
        for k, ((x0, y0), (x1, y1)) in enumerate(zip(ch["pts"], ch["pts"][1:])):
            for gx in range(int(min(x0, x1) // cell), int(max(x0, x1) // cell) + 1):
                for gy in range(int(min(y0, y1) // cell), int(max(y0, y1) // cell) + 1):
                    grid[gx, gy].append((ci, k))
    out, done = [], set()
    for items in grid.values():
        for (ci, k), (cj, m) in itertools.combinations(items, 2):
            a, b = chs[ci], chs[cj]
            if a["net"] == b["net"] or (ci, k, cj, m) in done: continue
            done.add((ci, k, cj, m)); done.add((cj, m, ci, k))
            (x0, y0), (x1, y1) = a["pts"][k], a["pts"][k + 1]
            (x2, y2), (x3, y3) = b["pts"][m], b["pts"][m + 1]
            d = (x1 - x0) * (y3 - y2) - (y1 - y0) * (x3 - x2)
            if abs(d) < 1e-12: continue
            u = ((x2 - x0) * (y3 - y2) - (y2 - y0) * (x3 - x2)) / d
            v = ((x2 - x0) * (y1 - y0) - (y2 - y0) * (x1 - x0)) / d
            if not (-1e-9 <= u <= 1 + 1e-9 and -1e-9 <= v <= 1 + 1e-9): continue
            si = a["s"][k] + u * (a["s"][k + 1] - a["s"][k]); sj = b["s"][m] + v * (b["s"][m + 1] - b["s"][m])
            x, y = x0 + u * (x1 - x0), y0 + u * (y1 - y0)
            out.append((ci, si, cj, sj, x, y)); out.append((cj, sj, ci, si, x, y))
    # a crossing through a shared vertex is found once per segment: keep one
    out.sort(); ded = []
    for c in out:
        if ded and ded[-1][0] == c[0] and ded[-1][2] == c[2] and abs(ded[-1][1] - c[1]) < 0.01 and abs(ded[-1][3] - c[3]) < 0.01:
            continue
        ded.append(c)
    return ded


def vias_between(ch, s0, s1):
    return sum(1 for i in ch["vias"] if s0 < ch["s"][i] < s1)


def crossbacks(chs, xs):
    """groups of nested cross-backs: [{"detour": chain index, "s": (s0, s1), "over": {chain: (t0, t1)}, ...}]"""
    by = defaultdict(list)
    for ci, si, cj, sj, x, y in xs: by[ci].append((si, cj, sj, x, y))
    pairs = []
    for ci, lst in by.items():
        lst.sort()
        per = defaultdict(list)
        for si, cj, sj, x, y in lst: per[cj].append((si, sj, x, y))
        for cj, ps in per.items():
            for (s0, t0, x0, y0), (s1, t1, x1, y1) in zip(ps, ps[1:]):
                la, lb = s1 - s0, abs(t1 - t0)
                # each cross-back is seen from both chains: the longer stretch is the detour
                if la > lb or (la == lb and ci < cj):
                    pairs.append(dict(detour=ci, over=cj, s=(s0, s1), t=(min(t0, t1), max(t0, t1)), at=((x0, y0), (x1, y1))))
    groups = []
    for ci in sorted({p["detour"] for p in pairs}):
        ps = sorted((p for p in pairs if p["detour"] == ci), key=lambda p: (p["s"][0], -p["s"][1]))
        for p in ps:
            g = groups[-1] if groups and groups[-1]["detour"] == ci and p["s"][0] < groups[-1]["s"][1] else None
            if g is None:
                g = dict(detour=ci, s=list(p["s"]), over={}, at=list(p["at"])); groups.append(g)
            g["s"][1] = max(g["s"][1], p["s"][1]); g["over"][p["over"]] = p["t"]
    for g in groups:
        a = chs[g["detour"]]; s0, s1 = g["s"]
        g["len"] = s1 - s0; g["vias"] = vias_between(a, s0, s1)
        g["crossed_len"] = max(t1 - t0 for t0, t1 in g["over"].values())
        g["crossed_vias"] = sum(vias_between(chs[j], *t) for j, t in g["over"].items())
        g["excess"] = g["len"] - g["crossed_len"]
        g["nets"] = sorted({short(chs[j]["net"]) for j in g["over"]})
        k0 = next(i for i, s in enumerate(a["s"]) if s >= s0 - 1e-9); k1 = max(i for i, s in enumerate(a["s"]) if s <= s1 + 1e-9)
        g["box"] = [min(p[0] for p in a["pts"][max(k0 - 1, 0):k1 + 2]), min(p[1] for p in a["pts"][max(k0 - 1, 0):k1 + 2]),
                    max(p[0] for p in a["pts"][max(k0 - 1, 0):k1 + 2]), max(p[1] for p in a["pts"][max(k0 - 1, 0):k1 + 2])]
    return groups


def find(geom, routes=(), nets=None, min_excess=0.0):
    """(chains, groups) for a board (and routes on it); nets: only the groups whose detour is one of these"""
    chs = [ch for n, c in copper(geom, routes).items() for ch in chains(n, c)]
    gs = [g for g in crossbacks(chs, crossings(chs))
          if g["excess"] >= min_excess and (nets is None or short(chs[g["detour"]]["net"]) in nets)]
    gs.sort(key=lambda g: -(g["excess"] + 4 * g["vias"]))
    return chs, gs


def draw(geom, chs, gs, out, box=None, S=14, title=None):
    from PIL import Image, ImageDraw
    xs = [p[0] for ch in chs for p in ch["pts"]]; ys = [p[1] for ch in chs for p in ch["pts"]]
    x0, y0, x1, y1 = box or (min(xs) - 1, min(ys) - 1, max(xs) + 1, max(ys) + 1)
    W, H = int((x1 - x0) * S), int((y1 - y0) * S) + 30
    im = Image.new("RGB", (W, H), "white"); d = ImageDraw.Draw(im)
    P = lambda p: ((p[0] - x0) * S, (p[1] - y0) * S + 30)
    for p in geom["pads"]:
        for L, ps in p["polys"].items():
            for poly in ps: d.polygon([P(q) for q in poly[0]], fill=(215, 215, 215))
    for ch in chs:
        for (a, b), L in zip(zip(ch["pts"], ch["pts"][1:]), ch["layers"]):
            d.line([P(a), P(b)], fill=(240, 200, 180) if L == "F" else (190, 210, 235), width=max(2, S // 10))
    for n, g in enumerate(gs, 1):
        for j in g["over"]:
            ch = chs[j]
            for (a, b) in zip(ch["pts"], ch["pts"][1:]): d.line([P(a), P(b)], fill=(40, 120, 40), width=max(4, S // 5))
        a = chs[g["detour"]]; s0, s1 = g["s"]; k5 = max(5, S // 4); rv = max(4, S // 6)
        for k, (p, q) in enumerate(zip(a["pts"], a["pts"][1:])):
            hot = a["s"][k + 1] > s0 and a["s"][k] < s1
            d.line([P(p), P(q)], fill=(220, 0, 160) if hot else (120, 0, 90), width=k5 if hot else max(3, S // 8))
        for ch in [a] + [chs[j] for j in g["over"]]:
            for i in ch["vias"]:
                q = P(ch["pts"][i]); d.ellipse([q[0] - rv, q[1] - rv, q[0] + rv, q[1] + rv], fill=(30, 30, 30))
        for x, y in g["at"]:
            q = P((x, y)); d.ellipse([q[0] - rv - 3, q[1] - rv - 3, q[0] + rv + 3, q[1] + rv + 3], outline=(220, 0, 160), width=2)
        q = P((g["box"][0], g["box"][1])); d.text((q[0] - 14, q[1] - 14), str(n), fill=(0, 0, 0))
    d.text((6, 6), title or f"{len(gs)} cross-backs: detour magenta (the stretch between the crossings bright), "
                            f"what it crossed green", fill=(0, 0, 0))
    im.save(out)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opt = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--") and "=" in a)
    geom = json.load(open(args[0]))
    routes = json.load(open(opt["routes"])) if "routes" in opt else ()
    nets = {short(s["net"]) for s in json.load(open(opt["nets"]))["nets"]} if "nets" in opt else None
    chs, gs = find(geom, routes, nets, float(opt.get("min", 0.5)))
    print(f"{len(chs)} chains, {len(gs)} cross-backs (excess >= {opt.get('min', 0.5)} mm)")
    print(f"{'#':>3} {'detour':<14} {'crossed':<34} {'mm':>6} {'vias':>4} {'crossed mm':>10} {'vias':>4} {'excess':>6}  where")
    for n, g in enumerate(gs, 1):
        a = chs[g["detour"]]
        print(f"{n:3d} {short(a['net']):<14} {','.join(g['nets'])[:34]:<34} {g['len']:6.1f} {g['vias']:4d} "
              f"{g['crossed_len']:10.1f} {g['crossed_vias']:4d} {g['excess']:6.1f}  "
              f"({g['box'][0]:.1f},{g['box'][1]:.1f})-({g['box'][2]:.1f},{g['box'][3]:.1f})")
    print(f"total excess {sum(g['excess'] for g in gs):.0f} mm, {sum(g['vias'] for g in gs)} vias on the detours")
    if "png" in opt: draw(geom, chs, gs, opt["png"]); print(opt["png"])


if __name__ == "__main__":
    main()
