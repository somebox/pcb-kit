"""placeopt: small parts moved and turned so that nets run direct.

Every net of the job is routed alone on the fixed copper (NetRouter, true length, the routing rules), so
a net's cost is what the placement alone asks of it: length, `via` mm per via, the rules' mm, and a failed connection
as 50 mm. Two nets' routes that cross on a layer will have to settle it with vias, so each such crossing counts too
(`cross` mm): a move that untangles nets pays even if no net alone needed it. (Overlap of the routes' copper as an
area didn't work: two nets alone often take the same path, and a move that split two such nets by a tie-break scored
500 mm, though nothing had to give way.)

Each round tries every small part in every turn and shift, routes alone the part's nets and the nets passing within
`reach` mm of where it was or will be, and takes the best moves that don't touch each other. Rounds go on until none
gains `gain` mm; from the second on only the `keep` best of the first are tried, and a part moves once. A move keeps
the courtyard clear of other parts on its side (`court_gap`), its pads `clr` from fixed copper of other nets and clear
of every via (no via in a pad), and on the board, outside rule areas. A test pad stays out from under front parts too.

    placeopt.py GEOM.json JOB.json OUT.json CONFIG.json    (the router's Python)

CONFIG: {"clr": 0.2, "movable": ["R*", "C*", "Q1"], "fixed": ["C7", "CN1"], "shifts": [0, 1, 2], "reach": 2.0, "free": [...],
         "limits": {"C4": 1.0, ...}: largest shift in mm for a part (decoupling caps stay by their pin), default 3}
OUT: {"moves": {ref: {"dx", "dy", "turn"}}, "free": CONFIG's "free", "log": [...]}, for moves.py. Parts with pads on nets
outside the job (the kept power copper) and GND don't move, except that GND pads may: the pour reaches them, and a kept
stub on such a pad goes with the move (moves.py)."""
import sys, json, math, time, io, contextlib, fnmatch, itertools, multiprocessing
import numpy as np
from scipy import ndimage
from .board import Board, pad_box
from .costs import RES, STEP, DEF, TRUE, GROUND, REFS
from .netroute import NetRouter

VIA, KEEP = None, []        # the job's via_size and keep-out rectangles
GND, RF = GROUND, REFS      # and its ground net and reference prefixes
DFLT = dict(clr=0.15, shifts=[0, 1, 2], reach=2.0, limit=3.0, gain=0.5, cross=4.0, court_gap=0.05, workers=9, rounds=12, top=6, keep=150)


class Solo:
    """NetRouter's negotiation hook for a net alone: no other net's copper, only the rules' costs"""
    def adjust(self, r):
        r.pen = np.zeros((2, r.Hn, r.Wn), np.float32); r.penv = np.zeros((r.Hn, r.Wn), np.float32)


def turned(x, y, ax, ay, t):
    """(x, y) turned by t degrees (KiCad's sense, y down) about (ax, ay)"""
    dx, dy = x - ax, y - ay
    if t == 90: dx, dy = dy, -dx
    elif t == 180: dx, dy = -dx, -dy
    elif t == 270: dx, dy = -dy, dx
    return ax + dx, ay + dy


def moved_poly(poly, ax, ay, dx, dy, t):
    return [[[round(c, 4) for c in (lambda q: (q[0] + dx, q[1] + dy))(turned(x, y, ax, ay, t))] for x, y in ring] for ring in poly]


def part_of(geom, ref):
    return next(p for p in geom["parts"] if p["ref"] == ref)


def kept_stubs(geom, ref):
    """the kept ground tracks that end on a pad of this part: they go when it moves"""
    out = []
    for p in geom["pads"]:
        if p["ref"] != ref or p["net"] != GND: continue
        box = pad_box([q for ps in p["polys"].values() for q in ps])
        for i, it in enumerate(geom["items"]):
            if it["kind"] == "track" and it["net"] == GND:
                s = it["seg"]
                if any(box[0] - 0.05 <= x <= box[2] + 0.05 and box[1] - 0.05 <= y <= box[3] + 0.05 for x, y in ((s[0], s[1]), (s[2], s[3]))):
                    out.append(i)
    return out


def apply_move(geom, ref, dx, dy, t):
    """a new geom with the part moved (the parts' other data shared)"""
    part = part_of(geom, ref); ax, ay = part["at"]
    drop = set(kept_stubs(geom, ref))
    items = []
    for i, it in enumerate(geom["items"]):
        if i in drop: continue
        if it.get("ref") == ref and "polys" in it:
            it = dict(it, polys=[moved_poly(p, ax, ay, dx, dy, t) for p in it["polys"]])
        elif it.get("ref") == ref and "circle" in it:     # a through-hole pad's drill
            x, y = turned(*it["circle"][:2], ax, ay, t)
            it = dict(it, circle=[round(x + dx, 4), round(y + dy, 4), it["circle"][2]])
        items.append(it)
    pads = []
    for p in geom["pads"]:
        if p["ref"] == ref:
            x, y = turned(*p["at"], ax, ay, t)
            p = dict(p, at=[round(x + dx, 4), round(y + dy, 4)],
                     polys={L: [moved_poly(q, ax, ay, dx, dy, t) for q in v] for L, v in p["polys"].items()})
        pads.append(p)
    c = part["court"]; q = [turned(x, y, ax, ay, t) for x, y in ((c[0], c[1]), (c[2], c[3]))]
    court = [min(q[0][0], q[1][0]) + dx, min(q[0][1], q[1][1]) + dy, max(q[0][0], q[1][0]) + dx, max(q[0][1], q[1][1]) + dy]
    parts = [dict(p, at=[ax + dx, ay + dy], court=court) if p["ref"] == ref else p for p in geom["parts"]]
    return dict(geom, items=items, pads=pads, parts=parts)


def court_ok(geom, ref, gap):
    me = part_of(geom, ref); a = me["court"]
    x0, y0, x1, y1 = [p for poly in geom["outline"] for p in [(min(q[0] for q in poly[0]), min(q[1] for q in poly[0]),
                                                              max(q[0] for q in poly[0]), max(q[1] for q in poly[0]))]][0]
    if a[0] < x0 + 0.3 or a[1] < y0 + 0.3 or a[2] > x1 - 0.3 or a[3] > y1 - 0.3: return False
    for p in geom["parts"]:                             # a back-side test pad doesn't go under a front part either
        if p["ref"] == ref or p["ref"].startswith(tuple(RF["holes"])) or (p["side"] != me["side"] and not ref.startswith(tuple(RF["test"]))): continue
        b = p["court"]
        if a[0] < b[2] + gap and b[0] < a[2] + gap and a[1] < b[3] + gap and b[1] < a[3] + gap: return False
    for k in geom["keepouts"]:                          # the module's antenna, on any layer
        if not k["polys"]: continue
        bx = pad_box(k["polys"])
        if a[0] < bx[2] and bx[0] < a[2] and a[1] < bx[3] and bx[1] < a[3]: return False
    for k in KEEP:
        bx = k["rect"]
        if me["side"] in k.get("layers", "FB") and a[0] < bx[2] and bx[0] < a[2] and a[1] < bx[3] and bx[1] < a[3]: return False
    return True


def pads_ok(bd, geom, ref, clr):
    """the part's pads clear of other nets' fixed copper by clr, and of every via"""
    for p in geom["pads"]:
        if p["ref"] != ref: continue
        for L, ps in p["polys"].items():
            c = bd.code(p["net"]) if p["net"] else -3
            for poly in ps:
                m = bd.cmask(poly)
                if not m: return False
                (sy, sx), u = m; g = int(clr / RES) + 3
                y0, y1, x0, x1 = max(sy.start - g, 0), min(sy.stop + g, bd.H), max(sx.start - g, 0), min(sx.stop + g, bd.W)
                lab = bd.lab[L][y0:y1, x0:x1]; mm = np.zeros(lab.shape, bool)
                mm[sy.start - y0:sy.stop - y0, sx.start - x0:sx.stop - x0] = u
                d = ndimage.distance_transform_edt(~mm) * RES
                if (((lab != 0) & (lab != c)) & (d < clr + RES)).any(): return False
                if bd.kt[L][y0:y1, x0:x1][mm].any(): return False
        bx = pad_box([q for ps in p["polys"].values() for q in ps])
        for x, y, vc in bd.vias:
            if bx[0] - bd.via_r - 0.05 < x < bx[2] + bd.via_r + 0.05 and bx[1] - bd.via_r - 0.05 < y < bx[3] + bd.via_r + 0.05: return False
    return True


def route_nets(bd, specs):
    """{net: (cost, tracks, vias, failed)}: each net alone on bd's fixed copper"""
    bd.freeze(); out = {}
    for spec in specs:
        bd.watch()
        r = NetRouter(bd, spec, neg=Solo())
        with contextlib.redirect_stdout(io.StringIO()): failed = r.route()
        out[spec["net"]] = cost_of(bd, spec, r.out_tracks, r.out_vias, len(failed))
        bd.thaw()
    return out


def cost_of(bd, spec, tracks, vias, failed):
    o = dict(DEF, **spec); h = RES * STEP; c = 0.0
    for t in tracks:
        u = bd.under[t["layer"]]
        for a, b in zip(t["pts"], t["pts"][1:]):
            L = math.dist(a, b); k = int(L / h) + 1; f = np.arange(k + 1) / k
            ix = np.round((a[0] + (b[0] - a[0]) * f - bd.X0) / h).astype(int); iy = np.round((a[1] + (b[1] - a[1]) * f - bd.Y0) / h).astype(int)
            c += L * (1 + o["under"] * u[iy, ix].mean())
    node = lambda v, v0: int(round((v - v0) / h))
    c += o["via"] * len(vias) + o["pin_via"] * sum(bool(bd.pin_area[node(y, bd.Y0), node(x, bd.X0)]) for x, y in vias) + 50.0 * failed
    return c, tracks, vias, failed


def segments(tracks):
    return [(t["layer"], a, b) for t in tracks for a, b in zip(t["pts"], t["pts"][1:])]


def crosses(p, q):
    """do the segments p and q of one layer cross, properly?"""
    o = lambda a, b, c: (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    a, b = p; c, d = q
    if max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0]) or \
            max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1]): return False
    return o(a, b, c) * o(a, b, d) < 0 and o(c, d, a) * o(c, d, b) < 0


def crossings(a, b):
    """how many times the routes a and b (segments) cross on a layer"""
    return sum(1 for La, p, q in a for Lb, r, s in b if La == Lb and crosses((p, q), (r, s)))


def board(geom, cached=None):
    bd = Board(geom, via=VIA, refs=RF)
    if cached:
        bd._edt = dict(cached)
    for k in ("ktF", "ktB", "kv"): bd.static_edt(k)
    bd.static_edt("smd")
    return bd


def seg_near(tracks, vias, box):
    x0, y0, x1, y1 = box
    for t in tracks:
        for a, b in zip(t["pts"], t["pts"][1:]):
            if max(a[0], b[0]) >= x0 and min(a[0], b[0]) <= x1 and max(a[1], b[1]) >= y0 and min(a[1], b[1]) <= y1: return True
    return any(x0 <= x <= x1 and y0 <= y <= y1 for x, y in vias)


class Search:
    def __init__(self, geom, job, cfg):
        self.cfg, self.job = cfg, job
        d = dict(job.get("defaults", {})); d.update(clr=cfg["clr"], via=DEF["via"], **TRUE)
        self.specs = {s["net"]: {**d, **s} for s in job["nets"]}
        self.rebase(geom)

    def rebase(self, geom):
        self.geom = geom; self.bd = bd = board(geom)
        self.edt = {k: v for k, v in bd._edt.items() if k != "smd"}
        res = route_all(geom, list(self.specs.values()), self.edt, self.cfg["workers"])
        self.res = res; self.segs = {n: segments(r[1]) for n, r in res.items()}
        names = sorted(self.segs)
        self.over = sum(crossings(self.segs[m], self.segs[n]) for i, m in enumerate(names) for n in names[i + 1:])
        self.total = sum(r[0] for r in res.values()) + self.cfg["cross"] * self.over

    def candidates(self):
        cfg, g, out = self.cfg, self.geom, []
        nets = set(self.specs)
        for p in g["parts"]:
            ref = p["ref"]
            if not any(fnmatch.fnmatch(ref, m) for m in cfg["movable"]) or ref in cfg.get("fixed", []): continue
            pn = {q["net"] for q in g["pads"] if q["ref"] == ref}
            if not pn or pn - nets - {GND, ""}: continue
            lim = cfg.get("limits", {}).get(ref, cfg["limit"])
            for t in (0, 90, 180, 270):
                if len(pn) == 1 and t: continue               # a test pad: turning does nothing
                for dx, dy in itertools.product(cfg["shifts"] + [-s for s in cfg["shifts"] if s], repeat=2):
                    if not (dx or dy or t): continue
                    if math.hypot(dx, dy) > lim + 1e-6: continue
                    out.append((ref, dx, dy, t))
        return out


_S = None


def _route_share(a):
    geom, specs, edt = a
    bd = board(geom, edt); return route_nets(bd, specs)


def route_all(geom, specs, edt, workers):
    specs = sorted(specs, key=lambda s: s["net"])
    with multiprocessing.get_context("fork").Pool(workers) as pool:
        res = pool.map(_route_share, [(geom, specs[i::workers], edt) for i in range(workers)])
    return {n: r for part in res for n, r in part.items()}


def _try(cand):
    """the candidate's total, or None when it isn't allowed"""
    S = _S; ref, dx, dy, t = cand; g = S.geom; cfg = S.cfg
    g2 = apply_move(g, ref, dx, dy, t)
    if not court_ok(g2, ref, cfg["court_gap"]): return None
    bd = board(g2, S.edt)
    if not pads_ok(bd, g2, ref, cfg["clr"]): return None
    old = [pad_box([q for ps in p["polys"].values() for q in ps]) for p in g["pads"] if p["ref"] == ref]
    new = [pad_box([q for ps in p["polys"].values() for q in ps]) for p in g2["pads"] if p["ref"] == ref]
    r = cfg["reach"]; boxes = [(b[0] - r, b[1] - r, b[2] + r, b[3] + r) for b in old + new]
    own = {p["net"] for p in g["pads"] if p["ref"] == ref} & set(S.specs)
    near = {n for n, x in S.res.items() if any(seg_near(x[1], x[2], bx) for bx in boxes)}
    todo = sorted(own | near)
    res = route_nets(bd, [S.specs[n] for n in todo])
    total = sum(x[0] for n, x in S.res.items() if n not in res) + sum(x[0] for x in res.values())
    segs = dict(S.segs); old = new = 0; rest = [n for n in segs if n not in res]
    fresh = {n: segments(res[n][1]) for n in todo}
    for i, n in enumerate(todo):
        old += sum(crossings(S.segs[n], segs[m]) for m in rest) + sum(crossings(S.segs[n], S.segs[m]) for m in todo[i + 1:])
        new += sum(crossings(fresh[n], segs[m]) for m in rest) + sum(crossings(fresh[n], fresh[m]) for m in todo[i + 1:])
    return total + cfg["cross"] * (S.over - old + new), cand, todo, boxes, S.over - old + new


def main():
    global _S, VIA, KEEP, GND, RF
    gp, jp, outp, cp = sys.argv[1:5]
    geom, job, cfg = json.load(open(gp)), json.load(open(jp)), dict(DFLT, **json.load(open(cp)))
    VIA, KEEP = job.get("via_size"), job.get("keep", [])
    GND, RF = job.get("ground", GROUND), dict(REFS, **job.get("refs", {}))
    t0 = time.time(); S = _S = Search(geom, job, cfg); log = []; moves = {}
    print(f"start: {S.total:.1f} ({sum(r[0] for r in S.res.values()):.1f} alone + {S.over} crossings), {time.time() - t0:.0f}s", flush=True)
    shortlist = None
    for rnd in range(cfg["rounds"]):
        # after the first round only the best of the last one are tried again: one move rarely revives the others
        cands = S.candidates() if shortlist is None else [c for c in shortlist if c[0] not in moves]; t1 = time.time()
        with multiprocessing.get_context("fork").Pool(cfg["workers"]) as pool:
            res = [r for r in pool.imap_unordered(_try, cands, chunksize=4) if r]
        res.sort(key=lambda r: (r[0], r[1])); shortlist = [r[1] for r in res[:cfg["keep"]]]
        print(f"round {rnd + 1}: {len(cands)} candidates, {len(res)} allowed, best {res[0][0] - S.total if res else 0:+.1f}, {time.time() - t1:.0f}s", flush=True)
        taken, used, g = [], [], S.geom
        json.dump([[round(r[0] - S.total, 1), r[1], r[4]] for r in res[:400]], open(outp.replace(".json", f"-r{rnd + 1}.json"), "w"))
        for total, cand, todo, boxes, _ in res:
            if total > S.total - cfg["gain"]: break
            if len(taken) >= cfg["top"]: break
            # the moves of a round touch neither the same nets nor the same place
            if any(set(todo) & u or any(a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3] for a in boxes for b in ub) for u, ub in used) or \
                    any(c[0] == cand[0] for c in taken): continue
            taken.append(cand); used.append((set(todo), boxes))
        if not taken: break
        t00 = S.total
        for ref, dx, dy, t in taken:
            # a turn is about the part's anchor as it stands, so moves add up: turn first, then the shift
            m = moves.setdefault(ref, dict(dx=0, dy=0, turn=0)); m["dx"] += dx; m["dy"] += dy; m["turn"] = (m["turn"] + t) % 360
            g = apply_move(g, ref, dx, dy, t)
        S.rebase(g); _S = S
        log.append(dict(round=rnd + 1, moves=taken, total=round(S.total, 1)))
        print(f"  took {taken}: {t00:.1f} -> {S.total:.1f} ({S.over} crossings)", flush=True)
        json.dump(dict(moves=moves, free=cfg.get("free", []), log=log), open(outp, "w"), indent=1)
    json.dump(dict(moves=moves, free=cfg.get("free", []), log=log, net_cost={n: round(r[0], 1) for n, r in S.res.items()}), open(outp, "w"), indent=1)
    print(f"{len(moves)} parts moved, total {S.total:.1f}, {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
