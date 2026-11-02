"""nroute: negotiated routing of a job's nets, PathFinder at the detailed router's resolution. Every net is routed by
netroute.py's NetRouter, but another net's routed copper is a cost instead of a wall; only the fixed copper stays a wall.
docs/ROUTER.md has the whole flow and why each step is there.

The order, proved on a bench of boards and by eye:
1. **Passes, directions fading.** Every net, then the nets in conflict, at a present cost `pf0` x `pf_mul`^pass, and a
   history cost that stays at the spots still contested. The layers' directions organise lanes while nets compete and leave
   staircases once they don't, so they fade to true length over the first `fade` passes. A net over its via budget goes
   again with dearer vias; a net in conflict `widen` passes in a row gets more room. At a stall (no tenth less overlap in
   `stall` passes) the nets out of conflict with copper within `expand_mm` of the overlaps go too, up to `expand` times:
   the room a spot lacks is the room its neighbours need.
2. **Branches.** The state after the passes is a rewind point. Refining runs from it once per value of `branches`, each
   settled and legalised, and the one with the fewest failed connections, then the cheapest, goes on. A branch's cost
   counts each chain's excess over its straight line squared (`detour_k`), so one long detour weighs more than a few small ones.
3. **Refining.** The negotiation again on true length, every net first, from a later pass's present cost. A net whose
   chains run `detour_free` mm or more longer than straight lines has its present cost capped at that excess, so crossing
   another net costs it about what it would save, and is routed again up to `detour_tries` times though it has no conflict.
4. **Settling.** With nets still in conflict, every net near an overlap loses that copper; those in conflict are joined
   again first, then their neighbours, every other net a wall. Kept when it leaves fewer failures than legalising.
5. **Legalising.** What is still in conflict is repaired one net at a time, the overlapping pieces taken out and joined
   again, every other net a wall (a whole new route of a big net loses connections where a repair loses one).
6. **Polish** (polish.py): tightening, shortcuts, joins at pads, smoothing, the exact clearance check.

Overlap is judged on a raster of each net's copper grown by half the clearance: two nets overlap there exactly where their
copper is closer than the clearance. Nets are coloured so that no two whose copper overlaps share a colour; each colour
is routed on `workers` forked processes, shared out by an effort estimate, not the clock, so a run repeats exactly.
Pours named in the job's "soft_pours" are no wall: a track may cut into one for `pour` per step, and KiCad refills it round
the track. Each pour's spine stays a wall (pour.py), so a notch never severs it or narrows it by more than a fifth.

    nroute.py GEOM.json JOB.json OUT.json [--key=value ...]

Keys are NEG's or DEF's (costs.py), except what a job's nets set or the fade rewrites (NOT_PER_RUN). GEOM is geom.py's. JOB: {"nets": [{"net": "+3V3", "w": 0.3, "neck": 0.2,
"root": "U1.3", "clr": 0.15, ...}, ...], "defaults": {...}, "via_size": [0.45, 0.25], "neg": {...}, "soft_pours": [...],
"keep": [{"rect": [x0, y0, x1, y1], "layers": "FB", "vias": true, "except": [nets]}, ...], "clr_nets": {net: mm}}.
OUT is a list of {"net", "tracks", "vias", "failed"} for apply.py; OUT-passes.json has the per-pass statistics."""
import json, math, sys, time, multiprocessing
import numpy as np
from numba import njit
from scipy import ndimage
from . import pour
from .board import Board, capsule, seg_box, short
from .copper import Copper
from .costs import RES, STEP, DEF, TRUE, NEG
from .netroute import NetRouter, ideal_length
from .polish import Polish

LAT_ERR = math.sqrt(2) * RES      # distances on the lattice (near_lattice) are off by at most this
NOT_PER_RUN = {"w", "neck", "wrong", "wrong_s", "diag", "diag_s", "via", "via_s", "edge", "neck_r", "mount_keep", "retries"}


class Negotiation(Copper, Polish):
    def __init__(self, geom, job, o):
        self.o, self.job, self.geom = o, job, geom
        self.bd = bd = Board(geom, soft=job.get("soft_pours", []), via=job.get("via_size"), refs=job.get("refs"))
        bd.set_clearances(job.get("clr_nets", {}))
        bd.freeze()                                             # the fixed copper: a route is undone back to it
        for k in ("ktF", "ktB", "kv", "smd"): bd.static_edt(k)      # once, before the workers fork
        for p in geom["pads"]: bd.pad_style(p)
        self.spines(job.get("soft_pours", []))
        self.occ = {L: np.zeros((bd.H, bd.W), np.int16) for L in "FB"}     # nets whose grown copper covers a cell
        self.Hl, self.Wl = (bd.H - 1) // STEP + 1, (bd.W - 1) // STEP + 1
        self.hist = {L: np.zeros((self.Hl, self.Wl), np.float32) for L in "FB"}
        self.pf, self.hard, self.use_hist = o["pf0"], False, True
        self.crit = False                      # refining with detours: nets far from direct pay capped present cost
        self.cache = {}                        # per run: pad boxes, the extent of a net's fixed copper, exact_gap's rings
        names = {p["net"] for p in geom["pads"]}
        self.specs, self.st = [], {}
        for spec in job["nets"]:
            spec = dict(job.get("defaults", {}), **spec)
            if "keep" in job: spec.setdefault("keep", job["keep"])
            if spec["net"] not in names:
                full = [n for n in names if n.split("/")[-1] == spec["net"]]
                if len(full) != 1: print(f"  ?? net {spec['net']}: {full}"); continue
                spec = dict(spec, net=full[0])
            n = spec["net"]; c = bd.code(n); od = dict(DEF, **spec)
            L = ideal_length([p["at"] for p in geom["pads"] if p["net"] == n])
            npads = sum(1 for p in geom["pads"] if p["net"] == n)
            self.st[n] = dict(spec=spec, tracks=[], vias=[], failed=0, fp={}, vf=1.0, tries=0, streak=0, secs=0.0,
                              t=L + 5.0 * npads,    # effort estimate: schedules the workers (not the clock: repeatable)
                              plane=any((bd.lab[k] == c).any() for k in "12"),
                              budget=max(1, int(L // od["budget_mm"])), have=sum(1 for v in bd.vias if v[2] == c),
                              clr=od["clr"])
            self.specs.append(spec)

    def spines(self, names):
        """take each soft pour's spine out of the soft fill: it stays a wall"""
        bd = self.bd
        for it in self.geom["items"]:
            if it["kind"] != "zone" or it.get("name") not in names or it["layer"] not in "FB": continue
            L, net, c = it["layer"], it["net"], bd.code(it["net"])
            ms = [m for poly in it["polys"] for m in [bd.cmask(poly)] if m]
            if not ms: continue
            j0 = max(min(m[0][0].start for m in ms) - 40, 0); j1 = min(max(m[0][0].stop for m in ms) + 40, bd.H)
            i0 = max(min(m[0][1].start for m in ms) - 40, 0); i1 = min(max(m[0][1].stop for m in ms) + 40, bd.W)
            def local(m):
                out = np.zeros((j1 - j0, i1 - i0), bool)
                if not m: return out
                (sy, sx), u = m
                a0, a1, b0, b1 = max(sy.start, j0), min(sy.stop, j1), max(sx.start, i0), min(sx.stop, i1)
                if a1 > a0 and b1 > b0:
                    out[a0 - j0:a1 - j0, b0 - i0:b1 - i0] |= u[a0 - sy.start:a1 - sy.start, b0 - sx.start:b1 - sx.start]
                return out
            fill = np.zeros((j1 - j0, i1 - i0), bool)
            for m in ms: fill |= local(m)
            near = ndimage.binary_dilation(fill, iterations=2); terms = []
            for p in self.geom["pads"]:
                if p["net"] != net or L not in p["polys"]: continue
                m = np.zeros_like(fill)
                for poly in p["polys"][L]: m |= local(bd.cmask(poly))
                if (m & near).any(): terms.append((f'{p["ref"]}.{p["num"]}', m))
            for x, y, vc in bd.vias:
                if vc != c: continue
                m = local(bd.cdisc(x, y, bd.via_r))
                if (m & near).any(): terms.append((f"via({x:.2f},{y:.2f})", m))
            keep, edges = pour.spines(fill | np.any([m for _, m in terms], axis=0) if terms else fill, terms, RES)
            bd.soft[L][j0:j1, i0:i1] &= ~(keep & fill)
            print(f"  {it['name']}: spine over {len(terms)} terminals, "
                  f"{min((e[2] for e in edges), default=0):.2f}..{max((e[2] for e in edges), default=0):.2f} mm wide")

    # --- the copper of routed nets, grown by half the clearance -----------------------------------------------------
    def footprint(self, tracks, vias, clr):
        bd, parts = self.bd, {"F": [], "B": []}
        for t in tracks:
            r = t["w"] / 2 + clr / 2 - RES / 2
            for (x0, y0), (x1, y1) in zip(t["pts"], t["pts"][1:]):
                m = bd.cmask([capsule(x0, y0, x1, y1, r)])
                if m: parts[t["layer"]].append(m)
        for x, y in vias:
            m = bd.cdisc(x, y, bd.via_r + clr / 2 - RES / 2)
            parts["F"].append(m); parts["B"].append(m)
        fp = {}
        for L, ms in parts.items():
            if not ms: continue
            y0 = min(s[0].start for s, _ in ms); y1 = max(s[0].stop for s, _ in ms)
            x0 = min(s[1].start for s, _ in ms); x1 = max(s[1].stop for s, _ in ms)
            u = np.zeros((y1 - y0, x1 - x0), bool)
            for (sy, sx), m in ms: u[sy.start - y0:sy.stop - y0, sx.start - x0:sx.stop - x0] |= m
            fp[L] = ((slice(y0, y1), slice(x0, x1)), u)
        return fp

    def occ_add(self, fp, k):
        for L, (sl, u) in fp.items(): self.occ[L][sl] += k * u.astype(np.int16)

    def conflicted(self, n):
        return any((self.occ[L][sl][u] >= 2).any() for L, (sl, u) in self.st[n]["fp"].items())

    def overlap_mm2(self):
        return sum(int((self.occ[L] >= 2).sum()) for L in "FB") * RES * RES

    # --- NetRouter hook: costs (or, when legalising, walls) for everything that isn't fixed copper -----------------
    def adjust(self, r):
        bd, o, no = self.bd, self.o, r.o
        J0, J1, I0, I1 = r.big; crop = r.crop; j0, j1, i0, i1 = r.win
        lat = (slice(j0 // STEP, j0 // STEP + r.Hn), slice(i0 // STEP, i0 // STEP + r.Wn))
        pen = np.zeros((2, r.Hn, r.Wn), np.float32); penv = np.zeros((r.Hn, r.Wn), np.float32)
        cv = np.zeros((r.Hn, r.Wn), bool); sv = np.zeros((r.Hn, r.Wn), bool); cvh = np.zeros((r.Hn, r.Wn), bool)
        mg, hc = RES + 0.01, no["clr"] / 2
        pf = self.pf; cap = self.st[r.net].get("cap", 0.0) if self.crit else 0.0
        if cap: pf = min(pf, cap)
        hk = pf / self.pf
        lc = (slice(crop[0].start // STEP, crop[0].start // STEP + r.Hn), slice(crop[1].start // STEP, crop[1].start // STEP + r.Wn))
        lattice = lambda m: (ndimage.distance_transform_edt(~near_lattice(m)) * (RES * STEP))[lc]
        lo = min(no["w"], no["neck"]) / 2 + hc + mg - LAT_ERR; hi = bd.via_r + hc + mg + LAT_ERR
        for li, L in enumerate("FB"):
            occ = self.occ[L][J0:J1, I0:I1]
            if (occ > 0).any():
                d = exact(occ > 0, lattice(occ > 0), lo, hi, crop[0].start, crop[1].start)
                ct = d < no["w"] / 2 + hc + mg; cn = d < no["neck"] / 2 + hc + mg
                r.wide[L] = r.wide[L] & ~(r.neck & ct)         # near pads, narrow where the full width would touch
                if self.hard:
                    cvh |= d < bd.via_r + hc + mg
                    r.ftw[L] = r.ftw[L] & ~ct; r.fn[L] = r.fn[L] & ~cn; r.wide[L] = r.wide[L] & r.ftw[L]
                else:
                    cv |= d < bd.via_r + hc + mg
                    pen[li] += pf * np.where(r.neck, cn, ct)
            if self.use_hist: pen[li] += hk * self.hist[L][lat]
            if bd.soft_any and o["pour"]:
                sp = bd.soft[L][J0:J1, I0:I1] & (bd.lab[L][J0:J1, I0:I1] != r.c)
                if sp.any():
                    d = lattice(sp)
                    pen[li] += o["pour"] * (d < no["w"] / 2 + o["pour_clr"] + LAT_ERR + mg)
                    sv |= d < bd.via_r + o["pour_clr"] + LAT_ERR + mg
        if cvh.any(): r.fv &= ~cvh; r.taps &= r.fv
        penv += o["via_k"] * pf * cv
        if self.use_hist: penv += hk * o["via_k"] * np.maximum(self.hist["F"][lat], self.hist["B"][lat])
        penv += o["pour_via"] * sv
        r.pen, r.penv = pen, penv

    # --- one net ---------------------------------------------------------------------------------------------------
    def route_net(self, spec, keep=None):
        """route a net again; keep: (tracks, vias) of its old route that stay and are joined, not routed again"""
        n = spec["net"]; s = self.st[n]; bd = self.bd; t0 = time.time()
        self.occ_add(s["fp"], -1)
        bd.watch()
        kt, kv = keep or ([], [])
        c = bd.code(n)
        for t in kt:
            for (x0, y0), (x1, y1) in zip(t["pts"], t["pts"][1:]): bd.paint_seg(t["layer"], x0, y0, x1, y1, t["w"], c)
        for x, y in kv: bd.add_via(x, y, c)
        r = NetRouter(bd, spec, neg=self); r.via_factor = s["vf"]
        failed = r.route()
        r.out_tracks = kt + r.out_tracks; r.out_vias = kv + r.out_vias
        bd.thaw()                               # only fixed copper stays in the board
        self.place(n, r.out_tracks, r.out_vias, len(failed), time.time() - t0, sub=False)

    def place(self, n, tracks, vias, failed, secs, sub=True):
        """a net's new route into the shared state (sub: take the old one out first)"""
        s = self.st[n]
        if sub: self.occ_add(s["fp"], -1)
        s.update(tracks=tracks, vias=vias, failed=failed, secs=secs, fp=self.footprint(tracks, vias, s["clr"]))
        self.occ_add(s["fp"], 1)
        s["excess"] = 0 if s["plane"] else max(0, s["have"] + len(vias) - s["budget"])

    def saved(self, n):
        """net n's route, for place(n, **saved) to put back"""
        return {k: self.st[n][k] for k in ("tracks", "vias", "failed", "secs")}

    def partners(self, a, b):
        """do the copper of nets a and b overlap?"""
        for L in "FB":
            if L not in self.st[a]["fp"] or L not in self.st[b]["fp"]: continue
            (sa, ua), (sb, ub) = self.st[a]["fp"][L], self.st[b]["fp"][L]
            y0, y1 = max(sa[0].start, sb[0].start), min(sa[0].stop, sb[0].stop)
            x0, x1 = max(sa[1].start, sb[1].start), min(sa[1].stop, sb[1].stop)
            if y1 <= y0 or x1 <= x0: continue
            if (ua[y0 - sa[0].start:y1 - sa[0].start, x0 - sa[1].start:x1 - sa[1].start] &
                ub[y0 - sb[0].start:y1 - sb[0].start, x0 - sb[1].start:x1 - sb[1].start]).any(): return True
        return False

    def route_all(self, todo):
        """route the nets of a pass. Nets whose copper overlaps are routed in turn, the others side by side: the
        nets are coloured so that no two overlapping ones share a colour, and each colour is routed on `workers`
        processes (forks of the state after the colour before), each process routing its share in turn"""
        names = [sp["net"] for sp in sorted(todo, key=lambda sp: -self.st[sp["net"]]["t"])]
        col = {}
        for n in names:
            used = {col[m] for m in col if self.partners(n, m)}
            col[n] = min(c for c in range(len(names) + 1) if c not in used)
        for c in range(max(col.values()) + 1):
            group = [n for n in names if col[n] == c]
            k = min(self.o["workers"], len(group))
            if k <= 1:
                for n in group: self.route_net(self.st[n]["spec"])
                continue
            share, load = [[] for _ in range(k)], [0.0] * k
            for n in group:
                i = load.index(min(load)); share[i].append(n); load[i] += self.st[n]["t"]
            global _NG
            _NG = self
            with multiprocessing.get_context("fork").Pool(k) as pool:
                res = pool.map(_work, share)
            for part in res:
                for n, tracks, vias, failed, t in part: self.place(n, tracks, vias, failed, t)
        return max(col.values()) + 1

    def history(self):
        for L in "FB":
            over = ndimage.binary_dilation(self.occ[L] >= 2)[::STEP, ::STEP]
            if over.any():
                self.hist[L] += self.o["hf"] * ndimage.binary_dilation(over, iterations=4)

    def totals(self):
        nv = sum(len(s["vias"]) for s in self.st.values())
        ln = sum(math.dist(a, b) for s in self.st.values() for t in s["tracks"] for a, b in zip(t["pts"], t["pts"][1:]))
        nf = sum(s["failed"] for s in self.st.values())
        return nf, nv, ln

    def run(self):
        o, log = self.o, []
        self.passes([self.st[sp["net"]]["spec"] for sp in self.specs], 0, o["iters"], log, fade=True)
        froms = [int(x) for x in str(o["branches"]).split(",")]
        start, hist = self.snapshot(), {L: h.copy() for L, h in self.hist.items()}   # each branch starts from here
        best = None
        for i, rf in enumerate(froms):
            if i:
                self.restore(start); self.hist = {L: h.copy() for L, h in hist.items()}
            self.refine(rf, log)
            self.settle()
            self.legalise()
            nf, nv, ln = self.totals(); dp = self.detour_cost(); key = (nf, nv * DEF["via"] + ln + dp)
            print(f"== branch {i + 1} (refined from pass {rf}): {nf} failed, {nv} vias, {ln:.0f} mm, detours {dp:.0f}", flush=True)
            if best is None or key < best[0]: best = (key, i, self.snapshot(), self.hist)
        self.restore(best[2]); self.hist = best[3]; print(f"== branch {best[1] + 1} kept", flush=True)
        nf, nv, ln = self.totals()
        log.append(dict(pass_="legal", failed=nf, vias=nv, mm=round(ln)))
        self.tighten()
        if o["shortcut"]: self.shortcut()
        if o["smooth"]: self.smooth()
        self.clearance()
        nf, nv, ln = self.totals()
        left = [n for n in self.st if self.conflicted(n)]
        log.append(dict(pass_="final", conflicted=len(left), failed=nf, vias=nv, mm=round(ln)))
        print(f"== final: {nf} failed, {nv} vias, {ln:.0f} mm, {len(left)} nets in conflict", flush=True)
        return log

    def detours(self):
        """each net's excess over straight lines; the present cost a net far from direct pays while refining"""
        o = self.o; n_over = 0
        for n, s in self.st.items():
            s["detour"] = sum(max(0.0, ch["over"]) for ch in self.chains(n)) if s["tracks"] else 0.0
            s["cap"] = s["detour"] if s["detour"] >= o["detour_free"] else 0.0
            n_over += s["cap"] > 0
        return n_over

    def detour_cost(self):
        """what the branches are compared on: chains far from direct, the excess counted squared"""
        o = self.o
        return o["detour_k"] * sum(max(0.0, ch["over"] - 2.0) ** 2 for n, s in self.st.items() if s["tracks"] for ch in self.chains(n))

    def refine(self, rf, log):
        """negotiation again on true length, from the pass whose present cost is pass rf's: every net first, then the
        passes as before"""
        o = self.o
        print(f"== refining: every net again on true length, from pf {o['pf0'] * o['pf_mul'] ** rf:.1f}", flush=True)
        keep = {}
        for n, s in self.st.items():
            keep[n] = s["spec"]; s["spec"] = dict(s["spec"], via=DEF["via"], **TRUE)
            s["vf"], s["tries"], s["streak"] = 1.0, 0, 0; s["dt"] = 0
        if o["detour"]:
            self.crit = True; k = self.detours()
            print(f"== detours: {k} nets over {o['detour_free']} mm", flush=True)
        self.passes([self.st[sp["net"]]["spec"] for sp in self.specs], rf, rf + o["refine"], log)
        self.crit = False
        for n, s in self.st.items(): s["spec"] = keep[n]

    def passes(self, first, it0, last, log, fade=False):
        """negotiation passes it0 .. last - 1 (the pass number sets the present cost). The first routes `first`, the
        later ones the nets in conflict, those over their via budget and, after a stall, the conflicts' neighbours."""
        o = self.o; retries = DEF["retries"]
        since, force, left, it = len(log), set(), o["expand"], it0 - 1
        while it + 1 < last:
            it += 1
            t0 = time.time(); self.pf = o["pf0"] * o["pf_mul"] ** it
            if fade and o["fade"]:                # the layers' directions fade to true length over `fade` passes
                f = max(0.0, 1.0 - it / o["fade"]); D = DEF
                mix = dict(wrong=1 + (D["wrong"] - 1) * f, wrong_s=D["wrong_s"] * f, diag=1 + (D["diag"] - 1) * f,
                           diag_s=D["diag_s"] * f, via_s=D["via_s"] * f)
                for s in self.st.values(): s["spec"] = dict(s["spec"], **mix)
                first = [self.st[sp["net"]]["spec"] for sp in self.specs] if it == it0 else first
            if it == it0: todo = list(first)
            else:
                todo = []
                for spec in self.specs:
                    s = self.st[spec["net"]]
                    if self.conflicted(spec["net"]):
                        s["streak"] += 1
                        if s["streak"] % o["widen"] == 0:          # no way round in its window: more room
                            s["spec"] = dict(s["spec"], margin=s["spec"].get("margin", DEF["margin"]) + o["widen_mm"])
                        todo.append(s["spec"])
                    elif spec["net"] in force: todo.append(s["spec"])   # near a stalled conflict: make room
                    elif s["excess"] and s["tries"] < retries:      # over its via budget: dearer vias
                        s["streak"] = 0; s["vf"] *= 2; s["tries"] += 1; todo.append(s["spec"])
                    elif self.crit and s.get("cap") and s["dt"] < o["detour_tries"]:   # far from direct: once more
                        s["streak"] = 0; s["dt"] += 1; todo.append(s["spec"])
                    else: s["streak"] = 0
            force = set()
            if not todo: break
            print(f"== pass {it + 1}: pf {self.pf:.2f}, {len(todo)} nets", flush=True)
            ncol = self.route_all(todo)
            self.history()
            conf = [n for n in self.st if self.conflicted(n)]
            nf, nv, ln = self.totals()
            if self.crit: self.detours()
            log.append(dict(pass_=it + 1, pf=round(self.pf, 3), routed=len(todo), colours=ncol, conflicted=len(conf),
                            overlap_mm2=round(self.overlap_mm2(), 3), failed=nf, vias=nv, mm=round(ln),
                            s=round(time.time() - t0, 1)))
            print(f"== pass {it + 1}: {len(conf)} nets in conflict ({log[-1]['overlap_mm2']} mm2), {nf} failed, "
                  f"{nv} vias, {ln:.0f} mm, {ncol} colours, {time.time() - t0:.0f}s: {', '.join(short(n) for n in conf)}", flush=True)
            if not conf and not any(s["excess"] and s["tries"] < retries for s in self.st.values()) and \
                    not (self.crit and any(s.get("cap") and s["dt"] < o["detour_tries"] for s in self.st.values())): break
            win = log[since:]
            low = min(e["overlap_mm2"] for e in win[:-o["stall"]]) if len(win) > o["stall"] else None
            if conf and low is not None and min(e["overlap_mm2"] for e in win[-o["stall"]:]) > 0.9 * low:
                if left:                            # the nets in conflict alone found no way: move their neighbours too
                    left -= 1
                    force = self.neighbours(conf)
                    since = len(log); last = max(last, it + 2 + o["stall"])
                    print(f"== stalled: {len(force)} nets near the conflicts routed again too: "
                          f"{', '.join(short(n) for n in sorted(force))}", flush=True)
                    continue
                print(f"== stalled: no tenth less overlap in {o['stall']} passes", flush=True); break

    def snapshot(self):
        return {n: (s["tracks"], s["vias"], s["failed"], s["vf"]) for n, s in self.st.items()}

    def restore(self, snap):
        for n, (tracks, vias, failed, vf) in snap.items():
            self.place(n, tracks, vias, failed, self.st[n]["secs"]); self.st[n]["vf"] = vf

    def hot_boxes(self, m):
        """boxes round each patch where nets overlap, grown by m mm"""
        boxes = []
        for L in "FB":
            lab, k = ndimage.label(self.occ[L] >= 2)
            for sy, sx in ndimage.find_objects(lab):
                boxes.append((self.bd.X0 + sx.start * RES - m, self.bd.Y0 + sy.start * RES - m,
                              self.bd.X0 + sx.stop * RES + m, self.bd.Y0 + sy.stop * RES + m))
        return boxes

    def neighbours(self, conf):
        """the nets out of conflict with copper within `expand_mm` mm of where nets overlap"""
        boxes = self.hot_boxes(self.o["expand_mm"])
        return {n for n, s in self.st.items() if n not in conf and any(outside(s["tracks"], s["vias"], b)[2] for b in boxes)}

    def settle(self):
        """when the passes end with nets in conflict: every net with copper within `expand_mm` of an overlap loses
        that copper; the nets that were in conflict are joined again first, then their neighbours, each with every
        other net a wall. Kept when it leaves fewer failed connections (then a cheaper board) than legalising would"""
        conf = [n for n in self.st if self.conflicted(n)]
        if not conf: return
        t0 = time.time(); snap = self.snapshot()
        self.legalise(); nf, nv, ln = self.totals(); k_leg = (nf, nv * DEF["via"] + ln); legal = self.snapshot()
        self.restore(snap)
        boxes = self.hot_boxes(self.o["expand_mm"]); near = sorted(self.neighbours(conf), key=lambda n: -self.st[n]["t"])
        keep = {}
        for n in conf + near:
            kt, kv = self.st[n]["tracks"], self.st[n]["vias"]
            for bx in boxes: kt, kv, _ = outside(kt, kv, bx)
            keep[n] = (kt, kv)
        for n in conf + near:                      # what each keeps stays in the shared state: a wall for the others
            self.place(n, *keep[n], self.st[n]["failed"], self.st[n]["secs"])
        self.hard = True
        for n in sorted(conf, key=lambda n: -self.st[n]["t"]) + near: self.route_net(self.st[n]["spec"], keep=keep[n])
        self.hard = False
        nf, nv, ln = self.totals(); k_set = (nf, nv * DEF["via"] + ln)
        still = [short(n) for n in self.st if self.conflicted(n)]
        if still: print(f"   still in conflict after settling: {', '.join(still)}", flush=True)
        ok = k_set < k_leg and not still
        print(f"== settling {len(conf)} nets in conflict and {len(near)} near them: {k_set[0]} failed, {k_set[1]:.0f} "
              f"where legalising leaves {k_leg[0]} failed, {k_leg[1]:.0f}{', kept' if ok else ''}, {time.time() - t0:.0f}s",
              flush=True)
        if not ok: self.restore(legal)

    def overlapping(self, n):
        """(tracks, vias) of net n that stay clear of every other net, one segment per track"""
        s, bd = self.st[n], self.bd; hit = lambda L, m: m is not None and (self.occ[L][m[0]][m[1]] >= 2).any()
        r = s["clr"] / 2 - RES / 2; kt, kv = [], []
        for t in s["tracks"]:
            for a, b in zip(t["pts"], t["pts"][1:]):
                if not hit(t["layer"], bd.cmask([capsule(*a, *b, t["w"] / 2 + r)])):
                    kt.append({"layer": t["layer"], "w": t["w"], "pts": [a, b]})
        for x, y in s["vias"]:
            m = bd.cdisc(x, y, bd.via_r + r)
            if not (hit("F", m) or hit("B", m)): kv.append((x, y))
        return kt, kv

    def legalise(self):
        """repair the nets still in conflict with every other net as a wall, the quickest nets first: the first of a
        pair to move frees the other. A repair that leaves a net with more failed connections is undone (its partner
        moves instead); whatever is still in conflict after that is repaired anyway."""
        conf = sorted((n for n in self.st if self.conflicted(n)), key=lambda n: self.st[n]["t"])
        if not conf: return
        print(f"== legalising {len(conf)} nets: others are walls", flush=True)
        self.hard = True
        for force in (False, True):
            for n in conf:
                if not self.conflicted(n): continue
                s = self.st[n]; old = self.saved(n)
                self.route_net(s["spec"], keep=self.overlapping(n))
                if not force and s["failed"] > old["failed"]:
                    print(f"   {short(n)}: {s['failed']} failed with walls, put back", flush=True)
                    self.place(n, old["tracks"], old["vias"], old["failed"], old["secs"])
        self.hard = False

    def result(self):
        return [{"net": n, "tracks": s["tracks"], "vias": s["vias"], "failed": s["failed"]} for n, s in self.st.items()]


def outside(tracks, vias, box):
    """(tracks, vias) clear of the box, one segment per track, and whether any of them is in it"""
    x0, y0, x1, y1 = box; kt, kv, hit = [], [], False
    for t in tracks:
        for a, b in zip(t["pts"], t["pts"][1:]):
            if seg_box(a, b, box): hit = True
            else: kt.append({"layer": t["layer"], "w": t["w"], "pts": [a, b]})
    for x, y in vias:
        if x0 <= x <= x1 and y0 <= y <= y1: hit = True
        else: kv.append((x, y))
    return kt, kv, hit


def near_lattice(m):
    """lattice nodes (every STEP cells from m's origin) with a True cell of m within one cell"""
    e, od = m[0::2], m[1::2]
    r = e.copy(); r[:od.shape[0]] |= od; r[1:] |= od[:r.shape[0] - 1]
    e, od = r[:, 0::2], r[:, 1::2]
    c = e.copy(); c[:, :od.shape[1]] |= od; c[:, 1:] |= od[:, :c.shape[1] - 1]
    return c


@njit(cache=True)
def exact(m, d, lo, hi, oy, ox):
    """d (lattice distances, within LAT_ERR of the truth) made exact where lo <= d < hi: the distance from the node's
    cell to the nearest True cell of the raster m, whose cell (oy, ox) is lattice node (0, 0)"""
    H, W = d.shape; R = int(hi / RES) + 2; out = d.copy()
    for y in range(H):
        for x in range(W):
            if d[y, x] < lo or d[y, x] >= hi: continue
            cy = oy + y * STEP; cx = ox + x * STEP; best = 1e18
            for yy in range(max(cy - R, 0), min(cy + R + 1, m.shape[0])):
                for xx in range(max(cx - R, 0), min(cx + R + 1, m.shape[1])):
                    if m[yy, xx]:
                        q = (yy - cy) ** 2 + (xx - cx) ** 2
                        if q < best: best = q
            out[y, x] = math.sqrt(best) * RES if best < 1e18 else hi
    return out


_NG = None


def _work(nets):
    out = []
    for n in nets:
        _NG.route_net(_NG.st[n]["spec"]); s = _NG.st[n]
        out.append((n, s["tracks"], s["vias"], s["failed"], s["secs"]))
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    geom = json.load(open(args[0])); job = json.load(open(args[1]))
    o = dict(NEG, **job.get("neg", {}))
    for a in sys.argv[1:]:
        if a.startswith("--") and "=" in a:
            k, v = a[2:].split("=", 1)
            if k in NEG: o[k] = type(NEG[k])(v)
            elif k in DEF and k not in NOT_PER_RUN: job.setdefault("defaults", {})[k] = float(v)
            else: sys.exit(f"no such option: {a} (jobs set widths, costs.py the rest)")
    t0 = time.time()
    ng = Negotiation(geom, job, o)
    log = ng.run()
    json.dump(ng.result(), open(args[2], "w"), indent=0)
    json.dump({"options": o, "passes": log, "s": round(time.time() - t0),
               "nets": {short(n): dict(failed=s["failed"], vias=len(s["vias"]), budget=s["budget"], have=s["have"],
                                       excess=s.get("excess", 0), vf=s["vf"]) for n, s in ng.st.items()}},
              open(args[2].replace(".json", "-passes.json"), "w"), indent=1)
    print(f"{time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
