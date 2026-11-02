"""One net routed by A*, on the board's fixed copper and the costs a Negotiation adds through its `neg` hook.

A net is routed by joining its copper islands (pads, kept trunks, earlier routes) one at a time onto the largest one, a
kept track with no pad on it included: a trunk the design laid has to stay in the path. Each join is a search on the 0.05 mm lattice (astar.py). Clearance comes from the 0.025 mm raster of foreign copper (Euclidean
distance transform); kicad-cli DRC has the last word. F.Cu prefers vertical runs, B.Cu horizontal ones: costs are in
millimetres of track along a layer's direction, and off-direction track gets dearer the further the route has jogged since
its last via. A spec's TRUE costs turn that off: true length, as the negotiation has after its fade (costs.py).

The rules in style(), each a fault people's eyes reject in a route (costs.py has their numbers):
- **Pin areas (`pin_via`).** A via with its centre inside an IC's pin area (the box of its pads plus 0.25 mm) costs
  `pin_via` mm more, so one goes there only when a pin has no other way out.
- **Via sites (`pin_site`).** A via at a site costs `pin_site` mm less: in line with one of the net's pads, just past
  the pad's end. An IC's pin has one site, straight out from its row, and a pitch too fine for vias side by side
  staggers them over two depths, so a net that changes layer at a pin does it in the same place each pass and the
  sites of neighbouring pins never meet. Other parts' pads have a site on each side.
- **Under an IC (`under`).** Track on the other side from an IC, inside its pads' box or (a U part) its courtyard, costs
  `under` x its length more: that copper belongs to the ground pour, and a module's edge is no lane. The same holds on the
  IC's own side, inside its pin rows (`BELLY` mm in from the pads' box).
- **Joins (`join`).** Arriving on a track instead of on a pad or via costs `join` mm.
- **Necks (`neck_r`, `neck_min`).** The narrow width is allowed within `neck_r` mm of the edge of one of the net's pads and
  nowhere else; a run keeps it from the pad out to its last narrow step, so it changes width once, and for at least
  `neck_min` mm, so a neck is a clean step and no nick.
- **Narrow steps (`neck_k`).** A step that has to be narrow costs `neck_k` x its length more, so a track rounds a pad's
  corner at full width where there is room instead of clipping it.
- **Pad entry (`entry`).** Round each of the net's pads, track off the pad's centre lines costs `entry` x its length
  more, so the last run comes in straight and centred."""
import math, time
import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree
from .astar import search
from .costs import RES, STEP, UNIT, DEF


class NetRouter:
    def __init__(self, bd, spec, neg=None):
        """neg: the hook whose adjust(router) adds the costs of other nets' copper (a Negotiation, or placeopt's Solo)"""
        self.bd = bd; self.o = dict(DEF); self.o.update(spec); self.neg = neg
        self.net = spec["net"]; self.c = bd.code(self.net)
        self.pads = [p for p in bd.g["pads"] if p["net"] == self.net]
        self.trunks = [dict(ref="track", num=i, at=[(it["seg"][0] + it["seg"][2]) / 2, (it["seg"][1] + it["seg"][3]) / 2], layers=[it["layer"]])
                       for i, it in enumerate(bd.g["items"]) if it["kind"] == "track" and it["net"] == self.net and it["layer"] in "FB"]
        self.out_tracks, self.out_vias = [], []
        self.via_factor = 1.0

    def build(self):
        bd, o = self.bd, self.o
        pts = [p["at"] for p in self.pads]
        ys, xs = np.nonzero(bd.lab["F"] == self.c)
        ys2, xs2 = np.nonzero(bd.lab["B"] == self.c)
        for a, b_ in ((xs, ys), (xs2, ys2)):
            if len(a): pts += [(bd.X0 + a.min() * RES, bd.Y0 + b_.min() * RES), (bd.X0 + a.max() * RES, bd.Y0 + b_.max() * RES)]
        j0, j1, i0, i1 = self.win = bd.window(pts, o["margin"])
        self.ftw, self.ft, self.fn = {}, {}, {}
        M = RES + 0.01
        need_t = o["clr"] + o["w"] / 2 + M; need_n = o["clr"] + o["neck"] / 2 + M; need_v = o["clr"] + bd.via_r + M
        fv = np.ones((j1 - j0, i1 - i0), bool)
        # distances need the copper just outside the window too, or a route along its edge can run onto a pad there
        cx_ = int(math.ceil(1.0 / RES)); J0, I0 = max(j0 - cx_, 0), max(i0 - cx_, 0)
        J1, I1 = min(j1 + cx_, bd.H), min(i1 + cx_, bd.W)
        crop = (slice(j0 - J0, j0 - J0 + j1 - j0), slice(i0 - I0, i0 - I0 + i1 - i0))
        self.big, self.crop = (J0, J1, I0, I1), crop
        dist = lambda m: (ndimage.distance_transform_edt(~m) * RES)[crop]
        for L in "FB":
            lab = bd.lab[L][J0:J1, I0:I1]
            foreign = (lab != 0) & (lab != self.c)
            if bd.soft_any: foreign &= ~bd.soft[L][J0:J1, I0:I1]
            d = dist(foreign)
            for c, codes in bd.wide_clr:            # a stricter class's fixed copper: its clearance, not this net's
                if c > o["clr"]:
                    m = np.isin(lab, codes) & (lab != self.c)
                    if m.any(): d = np.minimum(d, dist(m) - (c - o["clr"]))
            dk = bd.static_edt("kt" + L)[j0:j1, i0:i1]
            ex = self.extra_keep(L)
            if ex is not None: dk = np.minimum(dk, ex)
            ok = L in o["layers"]
            self.ftw[L] = ((d >= need_t) & (dk >= o["w"] / 2 + RES) & ok)[::STEP, ::STEP]
            self.fn[L] = ((d >= need_n) & (dk >= o["neck"] / 2 + RES) & ok)[::STEP, ::STEP]
            fv &= d >= need_v
        dkv = bd.static_edt("kv")[j0:j1, i0:i1]
        exv = self.extra_keep("v")
        if exv is not None: dkv = np.minimum(dkv, exv)
        lin = bd.lab_in[J0:J1, I0:I1]
        if ((lin != 0) & (lin != self.c)).any():
            fv &= dist((lin != 0) & (lin != self.c)) >= need_v
        dh = dist(bd.holes[J0:J1, I0:I1])
        fv &= (dkv >= bd.via_r + RES) & (dh >= bd.drill_r + o["hole_clr"] + RES)
        fv &= bd.static_edt("smd")[j0:j1, i0:i1] >= bd.via_r + o["via_pad_gap"] + RES   # no via in or against a pad
        self.fv = fv[::STEP, ::STEP]
        rr = o["own_via_gap"] / (RES * STEP); r = int(rr) + 1; hn, wn = self.fv.shape
        for x, y, c in bd.vias:                  # nor beside a via of this net: the route joins that one instead
            if c != self.c: continue
            cx, cy = self.node(x, y)
            a0, a1, b0, b1 = max(cx - r, 0), min(cx + r + 1, wn), max(cy - r, 0), min(cy + r + 1, hn)
            if a1 <= a0 or b1 <= b0: continue
            yy, xx = np.mgrid[b0:b1, a0:a1]
            self.fv[b0:b1, a0:a1] &= (xx - cx) ** 2 + (yy - cy) ** 2 > rr * rr
        if len(o["layers"]) < 2: self.fv[:] = False
        # plane taps: a new via anywhere well inside this net's inner-plane fill joins the plane
        self.taps = np.zeros_like(self.fv)
        if o.get("taps", True):
            for L in "12":
                plane = bd.lab[L][j0:j1, i0:i1] == self.c
                if plane.any():
                    self.taps |= (ndimage.distance_transform_edt(plane) * RES >= bd.via_r + 0.3)[::STEP, ::STEP]
            self.taps &= self.fv
        self.Hn, self.Wn = self.fv.shape
        # free: near this net's pads every direction costs its length. Neck: lattice nodes that may use the narrow
        # width: within neck_r of a pad's edge
        self.free = np.zeros((self.Hn, self.Wn), bool)
        for p in self.pads:
            cx, cy = self.node(*p["at"]); r = int(o["free_r"] / (RES * STEP)) + 1
            self.free[max(cy - r, 0):cy + r + 1, max(cx - r, 0):cx + r + 1] = True
        self.neck = np.zeros_like(self.free)
        for p in self.pads:
            sl, near, _, _ = bd.pad_style(p); w = self.clip(sl)
            if w: self.neck[w[0]] |= near[w[1]]
        self.wide = dict(self.ftw)              # where emit lays the full width
        self.neg.adjust(self); self.style()
        for L in "FB":
            self.ft[L] = self.ftw[L] | (self.neck & self.fn[L])

    def clip(self, sl):
        """lattice slices of the whole board -> (slices in this window, slices in the array they index), or None"""
        j0, i0 = self.win[0] // STEP, self.win[2] // STEP
        y0, y1 = max(sl[0].start, j0), min(sl[0].stop, j0 + self.Hn)
        x0, x1 = max(sl[1].start, i0), min(sl[1].stop, i0 + self.Wn)
        if y1 <= y0 or x1 <= x0: return None
        return ((slice(y0 - j0, y1 - j0), slice(x0 - i0, x1 - i0)),
                (slice(y0 - sl[0].start, y1 - sl[0].start), slice(x0 - sl[1].start, x1 - sl[1].start)))

    def style(self):
        """the costs of the rules, on top of the hook's (see the docstring)"""
        o, bd = self.o, self.bd; a, c = self.win[0] // STEP, self.win[2] // STEP
        lat = (slice(a, a + self.Hn), slice(c, c + self.Wn))
        if o["under"]:
            for li, L in enumerate("FB"): self.pen[li] += o["under"] * bd.under[L][lat]
        if o["neck_k"]:
            for li, L in enumerate("FB"): self.pen[li] += o["neck_k"] * (self.neck & ~self.wide[L])
        if o["pin_via"]: self.penv += o["pin_via"] * UNIT * bd.pin_area[lat]
        site = np.zeros((self.Hn, self.Wn), bool)
        for p in self.pads:
            sl, _, off, s = bd.pad_style(p); w = self.clip(sl)
            if not w: continue
            site[w[0]] |= s[w[1]]
            if o["entry"]:
                for L in p["layers"]: self.pen["FB".index(L)][w[0]] += o["entry"] * off[w[1]]
        if o["pin_site"]: self.penv -= o["pin_site"] * UNIT * site

    def extra_keep(self, L):
        """distance (mm) to this job's keep-out rectangles on layer L ('v' = vias), or None."""
        j0, j1, i0, i1 = self.win; bd = self.bd
        m = None
        for k in self.o.get("keep", []):
            if self.net in k.get("except", []): continue
            if L == "v" and not k.get("vias", True): continue
            if L != "v" and L not in k.get("layers", "FB"): continue
            x0, y0, x1, y1 = k["rect"]
            a0 = int((x0 - bd.X0) / RES) - i0; a1 = int((x1 - bd.X0) / RES) + 1 - i0
            b0 = int((y0 - bd.Y0) / RES) - j0; b1 = int((y1 - bd.Y0) / RES) + 1 - j0
            if a1 <= 0 or b1 <= 0 or a0 >= i1 - i0 or b0 >= j1 - j0: continue
            if m is None: m = np.zeros((j1 - j0, i1 - i0), bool)
            m[max(b0, 0):max(b1, 0), max(a0, 0):max(a1, 0)] = True
        if m is None: return None
        return (ndimage.distance_transform_edt(~m) * RES).astype(np.float32)

    def node(self, x, y):
        j0, j1, i0, i1 = self.win
        return (int(round(((x - self.bd.X0) / RES - i0) / STEP)), int(round(((y - self.bd.Y0) / RES - j0) / STEP)))

    def xy(self, ix, iy):
        j0, j1, i0, i1 = self.win
        return (round(self.bd.X0 + (i0 + ix * STEP) * RES, 4), round(self.bd.Y0 + (j0 + iy * STEP) * RES, 4))

    def components(self):
        """label this net's copper per layer, join layers through vias and through-hole pads."""
        j0, j1, i0, i1 = self.win; bd = self.bd
        labs, n = {}, 0
        for L in "FB12":
            m = (bd.lab[L][j0:j1, i0:i1] == self.c)[::STEP, ::STEP]
            lab, k = ndimage.label(m, structure=np.ones((3, 3)))
            lab[lab > 0] += n; labs[L] = lab; n += k
        parent = list(range(n + 1))
        def find(a):
            while parent[a] != a: parent[a] = parent[parent[a]]; a = parent[a]
            return a
        links = [(x, y) for x, y, c in bd.vias if c == self.c]
        links += [p["at"] for p in self.pads if len(p["layers"]) == 2]
        for x, y in links:
            ix, iy = self.node(x, y)
            if 0 <= ix < self.Wn and 0 <= iy < self.Hn:
                ids = [labs[L][iy, ix] for L in "FB12" if labs[L][iy, ix]]
                for a in ids[1:]: parent[find(a)] = find(ids[0])
        lut = np.array([find(i) for i in range(n + 1)]); lut[0] = 0
        return {L: lut[labs[L]] for L in "FB12"}

    def pad_comp(self, comp, p):
        ix, iy = self.node(*p["at"])
        for L in p["layers"]:
            if 0 <= iy < self.Hn and 0 <= ix < self.Wn and comp[L][iy, ix]: return comp[L][iy, ix]
        return 0

    def route(self):
        t0 = time.time(); self.build(); o = self.o
        failed, left = set(), []                 # the pads of islands that couldn't be joined (labels change at each join)
        for it in range(200):
            comp = self.components()
            pc = {}
            for p in self.pads + self.trunks:
                pc.setdefault(self.pad_comp(comp, p), []).append(p)
            if 0 in pc:
                bad = [f'{p["ref"]}.{p["num"]}' for p in pc[0]]
                print(f"  {self.net}: pads outside the window or unpainted: {bad}"); pc.pop(0)
            ids = list(pc)
            if len(ids) <= 1: left = []; break
            cnt = sum(np.bincount(comp[L].ravel(), minlength=max(ids) + 1)[:max(ids) + 1] for L in "FB12")
            size = {i: int(cnt[i]) for i in ids}
            root = None
            if "root" in o:
                rp = self.bd.pads[o["root"]]; root = self.pad_comp(comp, rp)
            if not root or root not in ids: root = max(ids, key=lambda i: size[i])
            left = [i for i in ids if i != root]
            others = [i for i in left if not any(id(p) in failed for p in pc[i])]
            if not others: break
            # nearest island first
            rl = {L: np.argwhere(comp[L] == root) for L in "FB"}
            rpts = np.concatenate([rl["F"], rl["B"]])
            def dist(i):
                q = np.array([self.node(*p["at"])[::-1] for p in pc[i]])
                return min(np.abs(rpts - qq).sum(axis=1).min() for qq in q)
            nxt = min(others, key=dist)
            starts = [(L, int(ix), int(iy)) for L in "FB" for iy, ix in np.argwhere(comp[L] == nxt) if self.ft[L][iy, ix]]
            tgt = {L: ((comp[L] == root) | self.taps) & self.ft[L] for L in "FB"}
            names = ",".join(f'{p["ref"]}.{p["num"]}' for p in pc[nxt])
            if not starts:
                print(f"  {self.net}: no free start at {names}"); failed |= {id(p) for p in pc[nxt]}; continue
            path = self.astar(starts, tgt)
            if path is None:
                print(f"  {self.net}: FAILED {names}"); failed |= {id(p) for p in pc[nxt]}; continue
            li, ix, iy, _ = path[-1]
            self.emit(path, tap=comp["FB"[li]][iy, ix] != root)
        print(f"  {self.net}: {len(self.out_tracks)} runs, {len(self.out_vias)} vias, {len(left)} failed, {time.time() - t0:.1f}s")
        return left

    def astar(self, starts, tgt):
        o = self.o
        anyt = tgt["F"] | tgt["B"]
        if not anyt.any(): return None
        hd = (heuristic(anyt) * o["hw"]).astype(np.float32)
        ft = np.stack([self.ft["F"], self.ft["B"]]); tg = np.stack([tgt["F"], tgt["B"]])
        st = np.array([("FB".index(L), iy, ix) for L, ix, iy in starts], np.int64)
        vf = self.via_factor
        cost = np.array([o["diag"], o["diag_s"], o["wrong"], o["wrong_s"], o["via"] * UNIT * vf, o["via_s"] * UNIT * vf,
                         o["bend"] * UNIT, 0.5 ** (RES * STEP / o["half"]), o["turn"]], np.float32)
        kv = int(round(o["via_gap"] / (RES * STEP)))
        tc = self.centre_cost(st, tg)
        p = search(ft, self.fv, tg, hd, st, self.free, cost, kv, int(o["max_exp"]), tc, self.pen, self.penv)
        if not len(p): print("    no path"); return None
        return [(int(li), int(ix), int(iy), 0) for li, iy, ix in p]

    def centre_cost(self, st, tg):
        """cost of leaving or arriving away from this net's pad centres, vias and track centre lines, so routes join
        copper where a person would (0 on plane taps); a join on a track costs `join` mm on top, so a pad nearby wins.
        Only the start and target nodes need it"""
        pts = [p["at"] for p in self.pads] + [(x, y) for x, y, c in self.bd.vias if c == self.c]
        segs = [it["seg"] for it in self.bd.g["items"] if it["net"] == self.net and it["kind"] == "track" and it["layer"] in "FB"]
        segs += [(x0, y0, x1, y1) for t in self.out_tracks for (x0, y0), (x1, y1) in zip(t["pts"], t["pts"][1:])]
        on = []
        for x0, y0, x1, y1 in segs:
            k = max(int(math.hypot(x1 - x0, y1 - y0) / (RES * STEP)) + 1, 1)
            f = np.arange(k + 1) / k
            on += list(zip(x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
        j0, j1, i0, i1 = self.win
        tc = np.zeros((self.Hn, self.Wn), np.float32)
        need = tg[0] | tg[1]; need[st[:, 1], st[:, 2]] = True
        q = np.argwhere(need)                    # (y, x)
        org = (self.bd.X0 + i0 * RES, self.bd.Y0 + j0 * RES)
        cen = lambda v: np.round((np.array(v) - org) / (RES * STEP))
        join = self.o["join"] * UNIT
        if len(q) and (pts or on):
            d = np.full(len(q), np.inf)
            if pts: d = cKDTree(cen(pts)[:, ::-1]).query(q)[0] * self.o["centre"]
            if on: d = np.minimum(d, cKDTree(cen(on)[:, ::-1]).query(q)[0] * self.o["centre"] + join)
            tc[q[:, 0], q[:, 1]] = d
        tc[self.taps] = 0
        return tc

    def emit(self, path, tap=False):
        o = self.o; LN = "FB"
        runs, cur, vias = [], [path[0]], []
        for s in path[1:]:
            if s[0] != cur[-1][0]:
                runs.append(cur); vias.append(self.xy(s[1], s[2])); cur = [s]
            else: cur.append(s)
        runs.append(cur)
        if tap: vias.append(self.xy(path[-1][1], path[-1][2]))
        self.out_vias += vias
        for run in runs:
            L = LN[run[0][0]]
            pts = [(s[1], s[2]) for s in run]
            # split into wide / neck pieces, then drop collinear points
            piece, pw = [pts[0]], None
            def wide(p): return bool(self.wide[L][p[1], p[0]])
            thin = [not (wide(a) and wide(b_)) for a, b_ in zip(pts, pts[1:])]
            thin = self.one_neck(pts, thin)
            for (a, b_), t in zip(zip(pts, pts[1:]), thin):
                w = o["neck"] if t else o["w"]
                if pw is not None and w != pw:
                    self.add_run(L, piece, pw); piece = [a]
                piece.append(b_); pw = w
            if len(piece) > 1: self.add_run(L, piece, pw or o["w"])
        rr = o["own_via_gap"] / (RES * STEP); r = int(rr) + 1
        for x, y in vias:
            self.bd.add_via(x, y, self.c)
            cx, cy = self.node(x, y)
            a0, a1, b0, b1 = max(cx - r, 0), min(cx + r + 1, self.Wn), max(cy - r, 0), min(cy + r + 1, self.Hn)
            if a1 > a0 and b1 > b0:
                yy, xx = np.mgrid[b0:b1, a0:a1]
                self.fv[b0:b1, a0:a1] &= (xx - cx) ** 2 + (yy - cy) ** 2 > rr * rr
                self.taps[b0:b1, a0:a1] &= self.fv[b0:b1, a0:a1]
        if tap:                                  # the tap via lands on the plane: paint it there too
            for L in "12":
                if (self.bd.lab[L] == self.c).any(): self.bd.put(self.bd.lab[L], self.bd.cdisc(*vias[-1], self.bd.via_r), self.c)

    def one_neck(self, pts, thin):
        """from each end of a run, inside the neck zone, the narrow width holds out to the last narrow step: a run
        changes width once at a pad, not back and forth"""
        n = len(thin); inz = [bool(self.neck[p[1], p[0]]) for p in pts]; out = list(thin)
        least = int(self.o["neck_min"] / (RES * STEP))
        for rng in (range(n), range(n - 1, -1, -1)):
            last, walked = None, []
            for i in rng:
                if not (inz[i] and inz[i + 1]): break
                walked.append(i)
                if thin[i]: last = len(walked)
            for i in walked[:last or 0]: out[i] = True
            if last:
                for i in rng[:least]: out[i] = True
        return out

    def add_run(self, L, pts, w):
        q = [pts[0]]
        for p in pts[1:]:
            if len(q) > 1 and (q[-1][0] - q[-2][0]) * (p[1] - q[-1][1]) == (q[-1][1] - q[-2][1]) * (p[0] - q[-1][0]):
                q[-1] = p
            else: q.append(p)
        xy = [self.xy(*p) for p in q]
        if len(xy) == 1: return
        self.out_tracks.append({"layer": L, "w": w, "pts": xy})
        for (x0, y0), (x1, y1) in zip(xy, xy[1:]):
            self.bd.paint_seg(L, x0, y0, x1, y1, w, self.c)


def heuristic(t, f=4):
    """distance (lattice steps) to the nearest True node of t, from a grid f times coarser: never more than the
    true distance, at a sixteenth of the cost"""
    H, W = t.shape; Hc, Wc = -(-H // f), -(-W // f)
    c = np.zeros((Hc * f, Wc * f), bool); c[:H, :W] = t
    c = c.reshape(Hc, f, Wc, f).any(axis=(1, 3))
    d = np.maximum(ndimage.distance_transform_edt(~c) * f - (f - 1) * math.sqrt(2), 0)
    return np.repeat(np.repeat(d, f, axis=0), f, axis=1)[:H, :W]


def ideal_length(pts):
    """Manhattan spanning tree over the pads (Prim), the via budget's length."""
    pts = list(dict.fromkeys(tuple(p) for p in pts))
    if len(pts) < 2: return 0.0
    d = {p: abs(p[0] - pts[0][0]) + abs(p[1] - pts[0][1]) for p in pts[1:]}; total = 0.0
    while d:
        p = min(d, key=d.get); total += d.pop(p)
        for q in d: d[q] = min(d[q], abs(q[0] - p[0]) + abs(q[1] - p[1]))
    return total
