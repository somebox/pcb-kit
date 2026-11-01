"""tpplace: test pads placed where their nets ended up: a test pad needs nothing but a
spot on its net, so the routing goes first and the pad follows. Each free pad goes on its net's copper: on a track of its
own side or a via for nothing, on a track of the other side with a new via under it; where no such spot is free, beside
its net on a short stub. The spot keeps the pad's clearance on its side, stays out of every courtyard and rule area, keeps
`pitch` mm from the other test pads, and is the one nearest where the pad was.

    tpplace.py GEOM.json ROUTES.json FULL_GEOM.json FREE.json OUT.json [--clr=0.15] [--pitch=1.5] [--via=0.45,0.25]
                                                                                 [--job=JOB.json]    (the router's Python)

GEOM is the router's board (free pads out), FULL_GEOM the same with them (geom.py of moves.py's full.kicad_pcb).
FREE lists the pads (moves.py's "free"). JOB's keep-outs hold too.
OUT: {"moves": {ref: {"dx", "dy"}}, "routes": ROUTES with the new vias}."""
import sys, json, math
import numpy as np
from scipy import ndimage
from .board import Board, pad_box
from .costs import RES, DEF

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opt = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--"))
    CLR, PITCH = float(opt.get("clr", 0.15)), float(opt.get("pitch", 1.5)); COST_VIA = 3.0
    geom, routes, full, free = json.load(open(args[0])), json.load(open(args[1])), json.load(open(args[2])), json.load(open(args[3]))
    free = free["free"] if isinstance(free, dict) else free
    job = json.load(open(opt["job"])) if "job" in opt else {}
    bd = Board(geom, via=tuple(map(float, opt["via"].split(","))) if "via" in opt else None, refs=job.get("refs")); VIA_R = bd.via_r
    bd.set_clearances(job.get("clr_nets", {}))
    for r in routes:
        c = bd.code(r["net"])
        for t in r["tracks"]:
            for (x0, y0), (x1, y1) in zip(t["pts"], t["pts"][1:]): bd.paint_seg(t["layer"], x0, y0, x1, y1, t["w"], c)
        for x, y in r["vias"]: bd.add_via(x, y, c)
    keep = job.get("keep", [])
    pads = {p["ref"]: p for p in full["pads"] if p["ref"] in free}
    for ref in set(free) - set(pads): print(f"{ref}: not on the board, left where it is")
    free = [r for r in free if r in pads]
    HOLE = bd.drill_r + job.get("defaults", {}).get("hole_clr", DEF["hole_clr"])
    side = {r: "B" if "B" in p["polys"] else "F" for r, p in pads.items()}
    far = lambda r: "FB"[side[r] == "F"]
    rad = {r: max(math.dist(p["at"], q) for poly in p["polys"][side[r]] for q in poly[0]) for r, p in pads.items()}   # round the whole pad, any shape
    courts = np.zeros((bd.H, bd.W), bool)                    # no pad here: courtyards of either side, all parts
    for p in geom["parts"]:
        if bd.is_ref(p["ref"], "holes"): continue
        x0, y0, x1, y1 = p["court"]; g = 0.5
        a0, a1 = int((x0 - g - bd.X0) / RES), int((x1 + g - bd.X0) / RES) + 1
        b0, b1 = int((y0 - g - bd.Y0) / RES), int((y1 + g - bd.Y0) / RES) + 1
        courts[max(b0, 0):max(b1, 0), max(a0, 0):max(a1, 0)] = True
    for k in geom["keepouts"]:
        for poly in k["polys"]: courts |= bd.mask(poly)
    inside = {S: courts | bd.keep_mask(keep, "", S) for S in "FB"}
    no_via = bd.keep_mask(keep, "", "v")


    def own_points(net, S):
        """(x, y, extra cost) along the net's copper: track on side S and vias free, track on the other side with a via"""
        out = []
        for r in routes:
            if r["net"] != net: continue
            for t in r["tracks"]:
                for a, b in zip(t["pts"], t["pts"][1:]):
                    k = max(1, int(math.dist(a, b) / 0.1))
                    for i in range(k + 1):
                        out.append((a[0] + (b[0] - a[0]) * i / k, a[1] + (b[1] - a[1]) * i / k, 0.0 if t["layer"] == S else COST_VIA, t["layer"]))
            out += [(x, y, 0.0, "V") for x, y in r["vias"]]
        out += [(x, y, 0.0, "V") for x, y, c in bd.vias if c == bd.code(net) and not any(r["net"] == net for r in routes)]
        return out


    def place(ref, placed):
        p = pads[ref]; c = bd.code(p["net"]); r = rad[ref]; best = None; S, O = side[ref], far(ref)
        own = job.get("clr_nets", {}).get(p["net"], CLR)
        d, dO, dI = bd.gap(bd.lab[S], c, CLR, own), bd.gap(bd.lab[O], c, CLR, own), bd.gap(bd.lab_in, c, CLR, own)
        def stub_free(a, b):
            k = max(2, int(math.dist(a, b) / 0.05))
            return all(d[int((a[1] + (b[1] - a[1]) * i / k - bd.Y0) / RES + 0.5), int((a[0] + (b[0] - a[0]) * i / k - bd.X0) / RES + 0.5)] >= 0.125 + CLR + RES for i in range(k + 1))
        smd = bd.static_edt("smd"); dkv = bd.static_edt("kv"); hole = ndimage.distance_transform_edt(~bd.holes) * RES
        def via_ok(x, y, i, j):
            """a new via here clears the far side, the inner tracks, pads, rule areas, drills and the net's own vias"""
            return (min(dO[j, i], dI[j, i]) >= VIA_R + CLR + RES and smd[j, i] >= VIA_R + 0.1 + RES and dkv[j, i] >= VIA_R + RES
                    and hole[j, i] >= HOLE and not no_via[j, i] and not any(math.dist((x, y), v[:2]) < 0.6 for v in bd.vias if v[2] == c))
        for x, y, extra, kind in own_points(p["net"], S):
            i, j = int((x - bd.X0) / RES + 0.5), int((y - bd.Y0) / RES + 0.5)
            if d[j, i] < r + CLR + RES or inside[S][j, i] or bd.kt[S][j, i]: continue
            if any(math.dist((x, y), q) < PITCH for q in placed): continue
            if kind == O and not via_ok(x, y, i, j): continue
            s = math.dist((x, y), p["at"]) * 0.2 + extra
            if best is None or s < best[0]: best = (s, x, y, kind, None)
        if best: return best
        for x, y, extra, kind in own_points(p["net"], S):    # beside the copper, on a stub
            i, j = int((x - bd.X0) / RES + 0.5), int((y - bd.Y0) / RES + 0.5)
            if kind == O and not via_ok(x, y, i, j): continue
            for a in range(8):
                for L in (1.0, 1.5, 2.0, 2.5, 3.0):
                    u, v = round(x + L * math.cos(a * math.pi / 4), 2), round(y + L * math.sin(a * math.pi / 4), 2)
                    ii, jj = int((u - bd.X0) / RES + 0.5), int((v - bd.Y0) / RES + 0.5)
                    if not (0 <= ii < bd.W and 0 <= jj < bd.H) or d[jj, ii] < r + CLR + RES or inside[S][jj, ii] or bd.kt[S][jj, ii]: continue
                    if any(math.dist((u, v), q) < PITCH for q in placed) or not stub_free((x, y), (u, v)): continue
                    s = math.dist((u, v), p["at"]) * 0.2 + extra + L
                    if best is None or s < best[0]: best = (s, u, v, "S" if kind != O else "SO", (x, y))
        return best


    moves, added, stubs, placed = {}, {}, {}, [tuple(q["at"]) for q in full["pads"] if bd.is_ref(q["ref"], "test") and q["ref"] not in free]
    order = sorted(free, key=lambda r: sum(1 for t in own_points(pads[r]["net"], side[r]) if t[3] != far(r)))
    for ref in order:
        b = place(ref, placed)
        if b is None: print(f"{ref} ({pads[ref]['net']}): no spot"); continue
        _, x, y, kind, at = b; x, y = round(x, 3), round(y, 3)
        q = pads[ref]; moves[ref] = dict(dx=round(x - q["at"][0], 3), dy=round(y - q["at"][1], 3)); placed.append((x, y))
        c = bd.code(q["net"]); S = side[ref]
        if kind == far(ref): added.setdefault(q["net"], []).append([x, y]); bd.add_via(x, y, c)
        if kind == "SO": added.setdefault(q["net"], []).append(list(at)); bd.add_via(*at, c)
        if at:
            stubs.setdefault(q["net"], []).append(dict(layer=S, w=0.25, pts=[list(at), [x, y]])); bd.paint_seg(S, *at, x, y, 0.25, c)
        bd.put(bd.lab[S], bd.cdisc(x, y, rad[ref]), c)
        print(f"{ref} {q['net']}: ({q['at'][0]:.2f},{q['at'][1]:.2f}) -> ({x:.2f},{y:.2f}), on {dict(V='a via', F='F.Cu', B='B.Cu').get(kind, 'a stub')}, {math.dist((x, y), q['at']):.1f} mm")
    for n in sorted((set(added) | set(stubs)) - {r["net"] for r in routes}):     # a kept net: no route of its own yet
        routes.append(dict(net=n, tracks=[], vias=[], failed=0))
    for r in routes:
        r["vias"] = list(r["vias"]) + added.get(r["net"], []); r["tracks"] = list(r["tracks"]) + stubs.get(r["net"], [])
    json.dump(dict(moves=moves, routes=routes), open(args[4], "w"), indent=0)
    print(f"{len(moves)} of {len(free)} placed, {sum(len(v) for v in added.values())} new vias")


if __name__ == "__main__":
    main()
