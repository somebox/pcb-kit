"""What the routing left unconnected, by DRC's list. A board made by job.py keeps ground as pours, which the router doesn't
see, so a track can close off a pad: each ground pad DRC lists gets a short stub to a new via in the other side's pour,
where the via clears everything and the stub runs straight or with one bend; first it tries a stub to ground copper close
by on its own side (a QFN's corner pin to the pad under the chip). Other nets' copper can touch on the router's raster and
not for KiCad, which joins a track at its ends: a moved part beside a kept track, a track grazing a pad on its way past.
Such a pad, or two such tracks of a net, up to REACH mm apart and where that clears everything, get a short stub. Further
off it is a failed connection, and stays open. A piece of a pour the routing cut off gets a via to its net on the far side.

    repair.py FINAL.json FINAL.rpt TP.json [--clr=0.15] [--via=0.45,0.25] [--job=JOB.json]        (the router's Python)

FINAL.json is geom.py of finish.py's board, JOB's keep-outs hold for what it adds. Adds the stubs and vias to
TP.json's routes."""
import sys, json, math, re
import numpy as np
from scipy import ndimage
from .board import Board, pad_box
from .costs import RES, DEF, GROUND

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opt = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--"))
    CLR = float(opt.get("clr", 0.15)); W = 0.3; REACH = 0.5
    geom, tp = json.load(open(args[0])), json.load(open(args[2]))
    job = json.load(open(opt["job"])) if "job" in opt else {}
    keep = job.get("keep", []); GND = job.get("ground", GROUND); CLRS = job.get("clr_nets", {})
    HOLE_CLR = job.get("defaults", {}).get("hole_clr", DEF["hole_clr"])
    text = open(args[1]).read()
    orphans, others, gaps = [], [], []
    for blk in re.split(r"\n(?=\[)", text):
        if blk.startswith("[unconnected_items]"):
            pads = re.findall(r"@\(([-\d.]+) mm, ([-\d.]+) mm\): Pad (\S+) \[([^\]]+)\] of (\S+) on", blk)
            for x, y, num, net, ref in pads:
                (orphans if net == GND else others).append((num, ref, net, float(x) - 100.0, float(y) - 100.0))
            ends = re.findall(r"@\(([-\d.]+) mm, ([-\d.]+) mm\): Track \[([^\]]+)\] on (\S+), length ([\d.]+) mm", blk)
            if not pads and len(ends) == 2 and ends[0][2:4] == ends[1][2:4]: gaps.append(ends)
    orphans, others = sorted(set(orphans)), sorted(set(others))
    print("cut off:", [f"{r}.{n}" for n, r, *_ in orphans], "off their copper:", [f"{r}.{n}" for n, r, *_ in others],
          "tracks apart:", [e[0][2].split("/")[-1] for e in gaps])
    bd = Board(geom, via=tuple(map(float, opt["via"].split(","))) if "via" in opt else None, refs=job.get("refs")); c = bd.code(GND); VIA_R = bd.via_r
    bd.set_clearances(job.get("clr_nets", {}))


    def clear_of(net):
        """{layer, or "i" for the inner tracks: distance to other nets' copper, rule areas and the job's keep-outs}, for this
        net's track; each net's clearance counts, this one's and the others'"""
        me = bd.code(net); own = CLRS.get(net, CLR); out = dict(i=bd.gap(bd.lab_in, me, CLR, own))
        for L in "FB":
            walls = bd.kt[L] | bd.keep_mask(keep, net, L); d = bd.gap(bd.lab[L], me, CLR, own)
            out[L] = np.minimum(d, ndimage.distance_transform_edt(~walls) * RES) if walls.any() else d
        return out


    dist = None                                                 # tie_pad's: ground's clear_of, as of the last repair
    smd = bd.static_edt("smd"); hole = ndimage.distance_transform_edt(~bd.holes) * RES
    no_via = (bd.static_edt("kv") < VIA_R + RES) | bd.keep_mask(keep, GND, "v")


    def route_of(net):
        r = next((r for r in tp["routes"] if r["net"] == net), None)
        if r is None: r = dict(net=net, tracks=[], vias=[], failed=0); tp["routes"].append(r)
        return r


    def add_track(net, L, pts, w):
        """a stub, painted too: the repairs after it have to clear it"""
        r = route_of(net); r["tracks"] = list(r["tracks"]) + [dict(layer=L, w=w, pts=[list(q) for q in pts])]
        for a, b in zip(pts, pts[1:]): bd.paint_seg(L, *a, *b, w, bd.code(net))


    def add_via(net, v):
        nonlocal hole
        r = route_of(net); r["vias"] = list(r["vias"]) + [list(v)]; bd.add_via(*v, bd.code(net))
        hole = ndimage.distance_transform_edt(~bd.holes) * RES


    def the_pad(ref, num, x, y):
        """the pad DRC means: a part can have several of one number"""
        return min((q for q in geom["pads"] if q["ref"] == ref and q["num"] == num), key=lambda q: math.dist(q["at"], (x, y)))


    def free(L, a, b, w=W, d=None):
        k = max(2, int(math.dist(a, b) / 0.05))
        for i in range(k + 1):
            x, y = a[0] + (b[0] - a[0]) * i / k, a[1] + (b[1] - a[1]) * i / k
            if (d or dist)[L][int((y - bd.Y0) / RES + 0.5), int((x - bd.X0) / RES + 0.5)] < w / 2 + CLR + RES: return False
        return True


    def near_copper(p, L):
        """a straight stub from pad p to the nearest ground copper of its layer that isn't its own island"""
        lab, _ = ndimage.label(bd.lab[L] == c)
        ax, ay = p["at"]; i0, j0 = int((ax - bd.X0) / RES + 0.5), int((ay - bd.Y0) / RES + 0.5); own = lab[j0, i0]
        g = int(1.5 / RES); y0, x0 = max(j0 - g, 0), max(i0 - g, 0)
        ys, xs = np.nonzero((lab[y0:j0 + g, x0:i0 + g] != 0) & (lab[y0:j0 + g, x0:i0 + g] != own))
        if not len(xs): return None
        d = np.hypot(xs + x0 - i0, ys + y0 - j0)
        for k in np.argsort(d)[::4]:
            x, y = bd.X0 + (xs[k] + x0) * RES, bd.Y0 + (ys[k] + y0) * RES
            for w in (0.25, 0.2):
                if free(L, (ax, ay), (x, y), w): return [(ax, ay), (round(x, 3), round(y, 3))], w
        return None


    def bigger_pour(L):
        """the cells of L's ground pour in its largest piece"""
        m = bd.lab[L] == c
        lab, n = ndimage.label(m)
        if not n: return m
        s = ndimage.sum(m, lab, range(1, n + 1))
        return lab == 1 + int(np.argmax(s))


    pour = {L: ndimage.distance_transform_edt(bigger_pour(L)) * RES for L in "FB"}
    plane = (bd.lab["1"] == c) | (bd.lab["2"] == c)            # an inner ground plane: a via lands on it anywhere
    def tie_pad(p):
        """a ground pad cut off: a stub to ground copper close by on its own side, else a stub to a new via into the other side's pour"""
        nonlocal dist
        dist = clear_of(GND)
        ref, num = p["ref"], p["num"]
        L = p["layers"][0]; O = "FB"[L == "F"]; ax, ay = p["at"]; best = None
        st = near_copper(p, L)
        if st:
            add_track(GND, L, st[0], st[1])
            print(f"{ref}.{num}: stub to ground copper, {math.dist(*st[0]):.2f} mm at {st[1]} mm"); return
        for r in np.arange(0.5, 3.01, 0.1):
            for k in range(32):
                x, y = round(ax + r * math.cos(k * math.pi / 16), 2), round(ay + r * math.sin(k * math.pi / 16), 2)
                i, j = int((x - bd.X0) / RES + 0.5), int((y - bd.Y0) / RES + 0.5)
                if not (0 <= i < bd.W and 0 <= j < bd.H) or no_via[j, i]: continue
                if not plane[j, i] and pour[O][j, i] < VIA_R + 0.05: continue
                if min(dist[L][j, i], dist[O][j, i], dist["i"][j, i]) < VIA_R + CLR + RES: continue
                if smd[j, i] < VIA_R + 0.1 + RES or hole[j, i] < bd.drill_r + HOLE_CLR: continue
                for w in (W, 0.2):
                    if free(L, (ax, ay), (x, y), w): best = ([(ax, ay), (x, y)], (x, y), w); break
                    for e in ((x, ay), (ax, y)):
                        if free(L, (ax, ay), e, w) and free(L, e, (x, y), w): best = ([(ax, ay), e, (x, y)], (x, y), w); break
                    if best: break
                if best: break
            if best: break
        if best is None: print(f"{ref}.{num}: no spot"); return
        pts, v, w = best; add_track(GND, L, pts, w); add_via(GND, v)
        print(f"{ref}.{num}: via at ({v[0]:.2f},{v[1]:.2f}), {sum(math.dist(a, b) for a, b in zip(pts, pts[1:])):.1f} mm of stub")


    for num, ref, _, x, y in orphans: tie_pad(the_pad(ref, num, x, y))
    for num, ref, net, x, y in others:
        p = the_pad(ref, num, x, y); ax, ay = p["at"]; best = None
        for it in geom["items"]:
            if it["kind"] != "track" or it["net"] != net or it["layer"] != p["layers"][0]: continue
            x0, y0, x1, y1 = it["seg"]; dx, dy = x1 - x0, y1 - y0; L2 = dx * dx + dy * dy
            u = max(0.0, min(1.0, ((ax - x0) * dx + (ay - y0) * dy) / L2)) if L2 else 0.0
            q = (x0 + u * dx, y0 + u * dy); d = math.dist(q, (ax, ay))
            if best is None or d < best[0]: best = (d, q, it["w"])
        L = p["layers"][0]; q = best and [round(c, 3) for c in best[1]]
        if best is None or best[0] - best[2] / 2 > REACH or not free(L, (ax, ay), q, 0.2, clear_of(net)):
            print(f"{ref}.{num}: still open, {f'{best[0]:.2f} mm from its' if best else 'no'} {net} track"); continue
        add_track(net, L, [[ax, ay], q], 0.2)
        print(f"{ref}.{num}: stub to its {net} track, {best[0]:.2f} mm")


    def tie_islands(net):
        """a net's copper falling into clusters: its pours and tracks on every layer, joined through its vias and through-hole
        pads. Routing can cut a pour, and new vias a plane. Each cluster but the largest gets a via where it and the largest
        overlap, on any two layers, as deep in both as there is room"""
        code = bd.code(net); d = clear_of(net); LAYERS = "FB12"; lab, base = {}, 0
        for L in LAYERS:
            m, n = ndimage.label(bd.lab[L] == code); lab[L] = np.where(m > 0, m + base, 0); base += n
        parent = list(range(base + 1))
        def find(a):
            while parent[a] != a: parent[a] = parent[parent[a]]; a = parent[a]
            return a
        ties = [v[:2] for v in bd.vias if v[2] == code] + [p["at"] for p in geom["pads"] if p["net"] == net and len(p["layers"]) == 2]
        for x, y in ties:
            j, i = int((y - bd.Y0) / RES + 0.5), int((x - bd.X0) / RES + 0.5)
            ids = [lab[L][j, i] for L in LAYERS if lab[L][j, i]]
            for a in ids[1:]: parent[find(a)] = find(ids[0])
        root = np.array([find(a) for a in range(base + 1)]); near = {L: root[lab[L]] for L in LAYERS}
        size = sum(np.bincount(near[L].ravel(), minlength=base + 1) for L in LAYERS); size[0] = 0; main = int(size.argmax())
        spot = ~((bd.static_edt("kv") < VIA_R + RES) | bd.keep_mask(keep, net, "v"))
        spot &= (d["F"] >= VIA_R + CLR + RES) & (d["B"] >= VIA_R + CLR + RES) & (d["i"] >= VIA_R + CLR + RES)
        spot &= (smd >= VIA_R + 0.1 + RES) & (hole >= bd.drill_r + HOLE_CLR)
        deep = lambda m: ndimage.distance_transform_edt(m) * RES
        big = {L: deep(near[L] == main) for L in LAYERS if (near[L] == main).any()}
        for k in map(int, np.nonzero(size)[0]):
            area = size[k] * RES * RES
            if k == main or area < 1.0: continue                # a sliver: KiCad's own fill settings deal with those
            best = None
            for A in LAYERS:
                if not (near[A] == k).any(): continue
                here = deep(near[A] == k)
                for B, there in big.items():
                    if B == A: continue
                    q = np.where(spot, np.minimum(here, there), 0.0); j, i = np.unravel_index(int(q.argmax()), q.shape)
                    if q[j, i] >= VIA_R + 0.05 and (best is None or q[j, i] > best[0]): best = (q[j, i], i, j, A, B)
            if best is None:                                    # no room in it: its pads, if any, have to get out by a stub
                held = [p for p in geom["pads"] if p["net"] == net and any(near[L][int((p["at"][1] - bd.Y0) / RES + 0.5), int((p["at"][0] - bd.X0) / RES + 0.5)] == k for L in p["layers"])]
                on = f", with {[q['ref'] + '.' + q['num'] for q in held]} on it" if held else ""
                print(f"{net}: a piece of {area:.1f} mm2 stays apart, no spot for a via{on}")
                for q in (held if net == GND else []): tie_pad(q)
                continue
            v = [round(bd.X0 + best[1] * RES, 3), round(bd.Y0 + best[2] * RES, 3)]
            add_via(net, v); spot &= hole >= bd.drill_r + HOLE_CLR
            print(f"{net}: a piece of {area:.1f} mm2 joined by a via at ({v[0]:.2f},{v[1]:.2f}), {best[3]} to {best[4]}")


    for net in sorted(set(re.findall(r"Zone '[^']*' \[([^\]]+)\]", "\n".join(b for b in re.split(r"\n(?=\[)", text) if b.startswith("[unconnected_items]"))))):
        tie_islands(net)


    def nearest(p, s):
        """the point of segment s nearest p"""
        dx, dy = s[2] - s[0], s[3] - s[1]; L2 = dx * dx + dy * dy
        u = max(0.0, min(1.0, ((p[0] - s[0]) * dx + (p[1] - s[1]) * dy) / L2)) if L2 else 0.0
        return s[0] + u * dx, s[1] + u * dy


    def bridge(net):
        """DRC names one item on each side of a gap, not the nearest two. So: the net's tracks on a layer, grouped as KiCad joins
        them (a track's end on another's copper, on a via or on a pad), and the shortest clear stub from a group to another"""
        for L in "FB":
            segs = [(tuple(it["seg"]), it["w"]) for it in geom["items"] if it["kind"] == "track" and it["net"] == net and it["layer"] == L]
            holes_ = [(tuple(it["circle"][:2]), it["circle"][2]) for it in geom["items"] if it["kind"] == "via" and it["net"] == net]
            pads_ = [pad_box(q["polys"][L]) for q in geom["pads"] if q["net"] == net and L in q["polys"]]
            parent = list(range(len(segs) + len(holes_) + len(pads_)))
            def find(a):
                while parent[a] != a: parent[a] = parent[parent[a]]; a = parent[a]
                return a
            for a, (s, _) in enumerate(segs):
                for e in (s[:2], s[2:]):
                    for b, (t, w) in enumerate(segs):
                        if a != b and math.dist(e, nearest(e, t)) <= w / 2: parent[find(a)] = find(b)
                    for b, (c, r) in enumerate(holes_):
                        if math.dist(e, c) <= r: parent[find(a)] = find(len(segs) + b)
                    for b, bx in enumerate(pads_):
                        if bx[0] <= e[0] <= bx[2] and bx[1] <= e[1] <= bx[3]: parent[find(a)] = find(len(segs) + len(holes_) + b)
            best = None
            for a, (s, _) in enumerate(segs):
                for e in (s[:2], s[2:]):
                    for b, (t, _) in enumerate(segs):
                        if find(a) != find(b):
                            q = nearest(e, t); d = math.dist(e, q)
                            if d <= REACH and (best is None or d < best[0]) and free(L, e, q, 0.2, clear_of(net)): best = (d, list(e), [round(c, 3) for c in q])
            if best:
                add_track(net, L, [best[1], best[2]], 0.2)
                print(f"{net}: stub between its tracks, {best[0]:.2f} mm"); return
        print(f"{net}: still open, no gap of {REACH} mm or less")


    for net in sorted({e[0][2] for e in gaps}): bridge(net)
    json.dump(tp, open(args[2], "w"), indent=0)


if __name__ == "__main__":
    main()
