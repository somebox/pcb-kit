"""After the negotiation: every step here looks at one net at a time, with every other net a wall.

- **Tightening.** History keeps a net on the detour a conflict once sent it round, after the conflict is gone. So every
  net is routed once more, worst first, no history; the new route stays when it is cheaper (failed connections, then
  `via` mm per via plus length plus what the rules charge).
- **Shortcuts.** The routed copper is cut into chains (copper.py), and a chain dearer than the straight line between its
  ends by `shortcut_min` mm is taken out and its two sides joined again on true length. The new join stays when the net
  is `shortcut_gain` mm cheaper and its pads are as joined as before; up to `shortcut` rounds. Dead ends go first (`tidy`).
- **Joins at pads (`retee`).** A join on a track is charged while routing, so a pad nearby wins, but chains can still end
  on a T away from a pad. Each is taken out and joined again at `tee_join` mm; kept when the net has fewer such T's and
  costs no more than `tee_gain` mm extra.
- **Smoothing.** Each run between pads, vias, branch points and width changes is pulled taut: a stretch is replaced by an
  octilinear link, or a corner by a 45-degree cut, where that lowers its length plus the cost of its turns and the new
  copper clears everything. A run's first or last segment on a pad stays, so the pad entry stays straight. A narrow run
  that clears everything at the net's full width gets it, measured exactly on the polygons (`exact_gap`): the raster's
  margin narrows tracks that fit, at QFN pins above all.
- **Exact clearance.** The overlap raster lets a gap up to a cell (0.025 mm) under the clearance through, and a net that
  keeps a route from a negotiation pass is never checked again. So at the very end every pair of routed segments and vias
  of different nets is measured exactly, and the quicker net of a pair that is too close is repaired, unless that would
  lose a connection: an open is worse than a gap a hair short."""
import math, multiprocessing, time
import numpy as np
from .board import capsule, seg_box, seg_dist, short
from .costs import RES, STEP, DEF, TRUE


class Polish:
    """Negotiation's methods for the steps after the passes"""
    def score(self, n):
        s = self.st[n]; ln = sum(math.dist(a, b) for t in s["tracks"] for a, b in zip(t["pts"], t["pts"][1:]))
        return s["failed"], len(s["vias"]) * DEF["via"] + ln + self.rule_cost(n)

    def rule_cost(self, n):
        """mm the routing rules charge net n's route: track under ICs and vias in pin areas, so that tightening
        and shortcuts don't take a route back there for being shorter"""
        s, bd = self.st[n], self.bd; o = dict(DEF, **s["spec"]); c = 0.0; h = RES * STEP
        node = lambda v, v0: int(round((v - v0) / h))
        if o["under"]:
            for t in s["tracks"]:
                u = bd.under[t["layer"]]
                for a, b in zip(t["pts"], t["pts"][1:]):
                    L = math.dist(a, b); k = int(L / h) + 1
                    ix = np.round((a[0] + (b[0] - a[0]) * np.arange(k + 1) / k - bd.X0) / h).astype(int)
                    iy = np.round((a[1] + (b[1] - a[1]) * np.arange(k + 1) / k - bd.Y0) / h).astype(int)
                    c += o["under"] * L * u[iy, ix].mean()
        if o["pin_via"]:
            c += o["pin_via"] * sum(bool(bd.pin_area[node(y, bd.Y0), node(x, bd.X0)]) for x, y in s["vias"])
        return c

    def tighten(self):
        """every net once more, others as walls, no history; kept when cheaper"""
        t0 = time.time(); self.hard, self.use_hist = True, False; better = 0
        order = sorted(self.st, key=lambda n: -(self.st[n].get("excess", 0) * 100 + len(self.st[n]["vias"])))
        for n in order:
            s = self.st[n]
            if not s["tracks"] and not s["vias"]: continue
            old = self.saved(n); k0 = self.score(n)
            self.route_net(s["spec"])
            if self.score(n) < k0: better += 1
            else: self.place(n, old["tracks"], old["vias"], old["failed"], old["secs"])
        self.hard, self.use_hist = False, True
        nf, nv, ln = self.totals()
        print(f"== tightened: {better} nets better, {nf} failed, {nv} vias, {ln:.0f} mm, {time.time() - t0:.0f}s", flush=True)

    def too_close(self):
        """{net: [(kind, index)]}: the segments ("t", track index, segment index) and vias ("v", index) of routed
        nets that come closer than the clearance to another net's routed copper, measured exactly"""
        items = {"F": [], "B": []}                               # (net, key, a, b, half width)
        for n, s in self.st.items():
            for i, t in enumerate(s["tracks"]):
                for k, (a, b) in enumerate(zip(t["pts"], t["pts"][1:])): items[t["layer"]].append((n, ("t", i, k), a, b, t["w"] / 2))
            for i, (x, y) in enumerate(s["vias"]):
                for L in "FB": items[L].append((n, ("v", i), (x, y), (x, y), self.bd.via_r))
        bad = {}
        for L, its in items.items():
            grid = {}
            for j, (n, key, a, b, r) in enumerate(its):
                g = r + 0.25                                    # copper and clearance reach past the centreline's cell
                for gx in range(int((min(a[0], b[0]) - g) // 1.0), int((max(a[0], b[0]) + g) // 1.0) + 1):
                    for gy in range(int((min(a[1], b[1]) - g) // 1.0), int((max(a[1], b[1]) + g) // 1.0) + 1):
                        grid.setdefault((gx, gy), []).append(j)
            seen = set()
            for js in grid.values():
                for u in range(len(js)):
                    for v in range(u + 1, len(js)):
                        p, q = its[js[u]], its[js[v]]
                        if p[0] == q[0] or (js[u], js[v]) in seen: continue
                        seen.add((js[u], js[v]))
                        clr = max(self.st[p[0]]["clr"], self.st[q[0]]["clr"])
                        if p[1][0] == q[1][0] == "v":            # two drills: the hole-to-hole rule, as a gap between the vias
                            hc = max(self.st[m]["spec"].get("hole_clr", DEF["hole_clr"]) for m in (p[0], q[0]))
                            clr = max(clr, hc - 2 * (self.bd.via_r - self.bd.drill_r))
                        if seg_dist(p[2], p[3], q[2], q[3]) - p[4] - q[4] < clr - 1e-4:
                            bad.setdefault(p[0], set()).add(p[1]); bad.setdefault(q[0], set()).add(q[1])
        return bad

    def clearance(self):
        """repair the nets that come too close to another, measured exactly: the quicker net of a pair loses the pieces
        that are too close and is joined again with every other net as a wall; then measure again. A repair that costs a
        connection is put back and the other net of the pair tried: a gap a hair short is DRC's to report, an open is worse"""
        self.hard = True; stuck = set()
        for _ in range(10):
            bad = self.too_close(); todo = [n for n in bad if n not in stuck]
            if not todo: break
            n = min(todo, key=lambda n: self.st[n]["t"]); s = self.st[n]; drop = bad[n]; old = self.saved(n)
            print(f"== too close: {', '.join(f'{short(m)} ({len(k)})' for m, k in bad.items())}; repairing {short(n)}", flush=True)
            kt = [{"layer": t["layer"], "w": t["w"], "pts": [a, b]} for i, t in enumerate(s["tracks"])
                  for k, (a, b) in enumerate(zip(t["pts"], t["pts"][1:])) if ("t", i, k) not in drop]
            kv = [v for i, v in enumerate(s["vias"]) if ("v", i) not in drop]
            self.route_net(s["spec"], keep=(kt, kv))
            if s["failed"] > old["failed"]:
                print(f"   {short(n)}: the repair would lose a connection, put back", flush=True)
                self.place(n, **old); stuck.add(n); continue
            g0, old = self.pad_groups(n), self.saved(n)    # a via taken out leaves the run beyond it on nothing
            if self.tidy(n) and self.pad_groups(n) > g0: self.place(n, **old)
        left = self.too_close()
        if left: print(f"== still too close, for DRC to list: {', '.join(short(m) for m in left)}", flush=True)
        self.hard = False

    def rejoin(self, n, ch, accept, **spec):
        """chain ch of net n taken out and joined again on true length; accept(cost before, cost after) keeps it.
        Returns the record of what was kept, or None"""
        s = self.st[n]; old = self.saved(n)
        k0 = self.score(n); nv0 = len(s["vias"]); vf = s["vf"]; s["vf"] = 1.0
        self.route_net(dict(s["spec"], via=DEF["via"], **TRUE, **spec), keep=ch["keep"])
        s["vf"] = vf; self.tidy(n); k1 = self.score(n)
        if accept(k0, k1):
            return dict(net=short(n), ends=ch["ends"], mm=round(ch["cost"] - DEF["via"] * len(ch["vias"]), 1),
                        vias=len(ch["vias"]), saved=round(k0[1] - k1[1], 1), dvias=len(s["vias"]) - nv0)
        self.place(n, **old)
        return None

    def shortcut_net(self, n):
        """one net's shortcuts (see shortcut): its dead ends out, then its chains, dearest first, until none is
        cheaper, then the T's away from pads, joined again where a join on a track is dear. Returns (kept records,
        tries, tidied)"""
        o = self.o; kept, tries, tidied, tried = [], 0, 0, set()
        g0 = self.pad_groups(n)
        old = self.saved(n)
        if self.tidy(n):
            if self.pad_groups(n) > g0: self.place(n, **old)
            else: tidied = 1
        sig = lambda ch: (tuple(sorted(ch["ends"])), round(ch["cost"], 1))
        while True:
            todo = [ch for ch in self.chains(n) if ch["excess"] >= o["shortcut_min"] and sig(ch) not in tried]
            if not todo: break
            ch = max(todo, key=lambda ch: ch["excess"]); tried.add(sig(ch)); tries += 1
            rec = self.rejoin(n, ch, lambda k0, k1: (k1[0] < k0[0] or (k1[0] == k0[0] and k1[1] < k0[1] - o["shortcut_gain"]))
                              and self.pad_groups(n) <= g0)
            if rec: kept.append(rec)
        if o["retee"]:
            while True:
                todo = [ch for ch in self.chains(n) if ch["tees"] and sig(ch) not in tried]
                if not todo: break
                ch = min(todo, key=lambda ch: ch["cost"]); tried.add(sig(ch)); tries += 1
                t0 = self.tees(n)
                rec = self.rejoin(n, ch, lambda k0, k1: k1[0] <= k0[0] and k1[1] <= k0[1] + o["tee_gain"] and self.tees(n) < t0
                                  and self.pad_groups(n) <= g0, join=o["tee_join"])
                if rec: kept.append(rec)
        return kept, tries, tidied

    def shortcut(self):
        """each net alone, the others walls: its dead ends out, then a chain dearer than the straight line between its
        ends is taken out and its two sides joined again on true length; kept when the net is cheaper by
        `shortcut_gain` mm and its pads stay as joined as they were. Up to `shortcut` rounds. The nets go on `workers`
        processes, each net seeing the others as they were; a net whose new copper then comes too close to another's
        is put back and done again on its own"""
        t0 = time.time(); o = self.o; self.hard, self.use_hist = True, False; kept, tries, tidied = [], 0, 0
        nets = [n for n in self.st if not self.st[n]["plane"] and self.st[n]["tracks"]]
        for rnd in range(o["shortcut"]):
            order = sorted(nets, key=lambda n: -sum(ch["excess"] for ch in self.chains(n) if ch["excess"] > 0))
            k = min(o["workers"], len(order)); res, again = [], []
            if k > 1:
                share = [order[i::k] for i in range(k)]
                global _NG
                _NG = self
                with multiprocessing.get_context("fork").Pool(k) as pool:
                    res = [r for part in pool.map(_shortcut_share, share) for r in part]
            else: again = order
            got = 0
            for n, tracks, vias, failed, recs, t, tid in res:
                tries += t
                if not recs and not tid: continue
                s = self.st[n]; old = self.saved(n)
                self.place(n, tracks, vias, failed, s["secs"])
                if self.conflicted(n): self.place(n, **old); again.append(n); continue
                kept += recs; tidied += tid; got += len(recs)
            for n in again:                         # side by side they met: on its own, the others as they are now
                recs, t, tid = self.shortcut_net(n); kept += recs; tries += t; tidied += tid; got += len(recs)
            for kp in kept[len(kept) - got:]:
                (x0, y0), (x1, y1) = kp["ends"]
                print(f"   {kp['net']}: ({x0:.1f},{y0:.1f})-({x1:.1f},{y1:.1f}) {kp['mm']} mm, {kp['vias']} vias: "
                      f"{kp['saved']:.1f} mm cheaper, {kp['dvias']:+d} vias", flush=True)
            nf, nv, ln = self.totals()
            print(f"== shortcut round {rnd + 1}: {got} kept ({len(again)} nets done again on their own), {nf} failed, "
                  f"{nv} vias, {ln:.0f} mm", flush=True)
            if not got: break
        self.hard, self.use_hist = False, True
        nf, nv, ln = self.totals()
        print(f"== shortcuts: {len(kept)} of {tries} kept, {sum(k['saved'] for k in kept):.0f} mm cheaper, "
              f"{sum(k['dvias'] for k in kept):+d} vias, {tidied} nets tidied; {nf} failed, {nv} vias, {ln:.0f} mm, "
              f"{time.time() - t0:.0f}s", flush=True)

    def in_keep(self, n, L, a, b, w):
        """does this segment touch one of the job's keep-out rectangles?"""
        for k in self.st[n]["spec"].get("keep", []):
            if short(n) in k.get("except", []) or L not in k.get("layers", "FB"): continue
            x0, y0, x1, y1 = k["rect"]; g = w / 2 + RES
            if seg_box(a, b, (x0 - g, y0 - g, x1 + g, y1 + g)): return True
        return False

    def legal_width(self, n, L, pts, w):
        """can this run be w wide: clear of the rule areas, keep-outs and the board's edge margin? (exact_gap has the copper)"""
        bd = self.bd
        for a, b in zip(pts, pts[1:]):
            m = bd.cmask([capsule(*a, *b, w / 2)])
            if m and (bd.kt[L][m[0]][m[1]].any() or (bd.lab0[L][m[0]][m[1]] < 0).any()): return False
            if self.in_keep(n, L, a, b, w): return False
        return True

    def free_seg(self, n, L, a, b, w):
        """can net n, its own route out of the shared state, lay this segment? Clear of the fixed copper, rule areas,
        keep-outs and every routed net"""
        bd, s = self.bd, self.st[n]; c, clr = bd.code(n), s["clr"]
        m = bd.cmask([capsule(*a, *b, w / 2 + clr + RES)])          # a cell to spare: the rasters are that coarse
        if m:
            lab = bd.lab0[L][m[0]][m[1]]
            if ((lab != 0) & (lab != c) & ~bd.soft[L][m[0]][m[1]]).any(): return False
        for cc, codes in bd.wide_clr:
            m = bd.cmask([capsule(*a, *b, w / 2 + cc + RES)]) if cc > clr else None
            if m and (np.isin(bd.lab0[L][m[0]][m[1]], codes) & (bd.lab0[L][m[0]][m[1]] != c)).any(): return False
        m = bd.cmask([capsule(*a, *b, w / 2 - RES)])
        if m and bd.kt[L][m[0]][m[1]].any(): return False
        if self.in_keep(n, L, a, b, w): return False
        m = bd.cmask([capsule(*a, *b, w / 2 + clr / 2 + RES)])
        return not (m and (self.occ[L][m[0]][m[1]] > 0).any())

    def exact_gap(self, n, L, pts, w):
        """the least gap from a run at width w to anything of another net on layer L, exactly, on the polygons"""
        rings = self.cache.get("rings")
        if rings is None:
            rings = self.cache["rings"] = {"F": [], "B": []}
            for it in self.geom["items"]:
                if it["kind"] in ("pad", "track", "zone", "graphic") and it["layer"] in "FB":
                    for poly in it["polys"]:
                        for ring in poly:
                            r = np.array(ring + ring[:1]); rings[it["layer"]].append((it["net"], r, r.min(0), r.max(0)))
        a0 = np.min(pts, 0) - w / 2 - 1.0; a1 = np.max(pts, 0) + w / 2 + 1.0; best = 9.0
        wide = self.job.get("clr_nets", {}); own = self.st[n]["clr"]
        for net, r, lo, hi in rings[L]:
            if net == n or (lo > a1).any() or (hi < a0).any(): continue
            extra = max(0.0, wide.get(net, 0.0) - own)      # measured against the net's own clearance by the caller
            for a, b in zip(pts, pts[1:]):
                best = min(best, min((seg_dist(tuple(a), tuple(b), tuple(p), tuple(q)) for p, q in zip(r, r[1:])), default=9.0) - w / 2 - extra)
        circles = [(*it["circle"][:2], it["circle"][2]) for it in self.geom["items"] if it["kind"] in ("via", "hole", "npth") and it["net"] != n]
        for m, t in self.st.items():
            if m == n: continue
            circles += [(x, y, self.bd.via_r) for x, y in t["vias"]]
            for tr in t["tracks"]:
                if tr["layer"] != L: continue
                for p, q in zip(tr["pts"], tr["pts"][1:]):
                    if max(p[0], q[0]) < a0[0] or min(p[0], q[0]) > a1[0] or max(p[1], q[1]) < a0[1] or min(p[1], q[1]) > a1[1]: continue
                    for a, b in zip(pts, pts[1:]): best = min(best, seg_dist(tuple(a), tuple(b), tuple(p), tuple(q)) - w / 2 - tr["w"] / 2)
        for x, y, r in circles:
            if a0[0] <= x <= a1[0] and a0[1] <= y <= a1[1]:
                for a, b in zip(pts, pts[1:]): best = min(best, seg_dist(tuple(a), tuple(b), (x, y), (x, y)) - w / 2 - r)
        return best

    def smooth_run(self, n, L, w, pts, pin0, pin1):
        """pts pulled taut: the cheapest of what can be reached by swapping a stretch for an octilinear link, or a
        corner for a 45-degree cut, the cost being length plus netroute's for the turns. A segment on a pad stays"""
        o = dict(DEF, **self.st[n]["spec"]); bend, turn = o["bend"], o["turn"]; E = 1e-6
        def cost(q):
            c = sum(math.dist(p, r) for p, r in zip(q, q[1:]))
            for p, m, r in zip(q, q[1:], q[2:]):
                u, v = (m[0] - p[0], m[1] - p[1]), (r[0] - m[0], r[1] - m[1])
                cr, dot = u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1]
                if abs(cr) > E or dot < 0:
                    ang = math.degrees(math.atan2(abs(cr), dot))
                    c += bend if ang < 60 else bend * turn if ang < 120 else bend * turn * turn
            return c
        def simplify(q):
            r = [q[0]]
            for p in q[1:]:
                if math.dist(p, r[-1]) < E: continue
                if len(r) > 1:
                    u, v = (r[-1][0] - r[-2][0], r[-1][1] - r[-2][1]), (p[0] - r[-1][0], p[1] - r[-1][1])
                    if abs(u[0] * v[1] - u[1] * v[0]) < 1e-5 and u[0] * v[0] + u[1] * v[1] > 0: r[-1] = p; continue
                r.append(p)
            return r
        def links(a, b):
            dx, dy = b[0] - a[0], b[1] - a[1]; ax, ay = abs(dx), abs(dy)
            if ax < E or ay < E or abs(ax - ay) < E: return [[a, b]]
            sx, sy = math.copysign(1, dx), math.copysign(1, dy); m = min(ax, ay)
            diag1 = (a[0] + sx * m, a[1] + sy * m); diag2 = (b[0] - sx * m, b[1] - sy * m)
            return [[a, tuple(round(v, 4) for v in diag1), b], [a, tuple(round(v, 4) for v in diag2), b]]
        pts = simplify([tuple(p) for p in pts]); moved = 0
        for _ in range(40):
            c0 = cost(pts); best = None; lo = 1 if pin0 else 0; hi = len(pts) - (2 if pin1 else 1)
            for i in range(lo, hi):
                for j in range(i + 2, hi + 1):
                    for link in links(pts[i], pts[j]):
                        q = simplify(pts[:i] + link + pts[j + 1:]); c = cost(q)
                        if c < c0 - 0.02 and (best is None or c < best[0]):
                            if all(self.free_seg(n, L, a, b, w) for a, b in zip(link, link[1:])): best = (c, q)
            for i in range(max(lo, 1), min(hi + 1, len(pts) - 1)):
                p, m, r = pts[i - 1], pts[i], pts[i + 1]
                u, v = (m[0] - p[0], m[1] - p[1]), (r[0] - m[0], r[1] - m[1])
                if abs(u[0] * v[0] + u[1] * v[1]) > E: continue            # only right angles
                lu, lv = math.hypot(*u), math.hypot(*v)
                for cut in (0.4, 0.3, 0.2, 0.1):
                    if cut > lu - 0.05 or cut > lv - 0.05: continue
                    a = (round(m[0] - u[0] / lu * cut, 4), round(m[1] - u[1] / lu * cut, 4))
                    b = (round(m[0] + v[0] / lv * cut, 4), round(m[1] + v[1] / lv * cut, 4))
                    q = pts[:i] + [a, b] + pts[i + 1:]; c = cost(q)
                    if c < c0 - 0.02 and (best is None or c < best[0]) and self.free_seg(n, L, a, b, w):
                        best = (c, q); break
            if best is None: break
            pts = best[1]; moved += 1
        return pts, moved

    def smooth(self):
        """every net's runs pulled taut, the others as they are; undone for a net whose pads no longer join up"""
        t0 = time.time(); moved = nets = widened = 0
        for n in self.st:
            s = self.st[n]
            if not s["tracks"]: continue
            g0 = self.pad_groups(n); old = self.saved(n)
            self.occ_add(s["fp"], -1); new, k = [], 0
            full = s["spec"].get("w", DEF["w"])
            for L, w, pts, pin0, pin1 in self.runs(n):
                q, m = self.smooth_run(n, L, w, pts, pin0, pin1); k += m
                if w < full and self.exact_gap(n, L, q, full) >= s["clr"] + 0.003 and self.legal_width(n, L, q, full):
                    w = full; k += 1; widened += 1
                new.append({"layer": L, "w": w, "pts": q})
            self.place(n, new if k else old["tracks"], old["vias"], old["failed"], old["secs"], sub=False)
            if k and (self.pad_groups(n) > g0 or self.conflicted(n)): self.place(n, **old); k = 0
            if k: moved += k; nets += 1
        nf, nv, ln = self.totals()
        print(f"== smoothed: {moved} moves on {nets} nets, {widened} narrow runs widened, {nf} failed, {nv} vias, {ln:.0f} mm, {time.time() - t0:.0f}s", flush=True)


_NG = None


def _shortcut_share(nets):
    out = []
    for n in nets:
        recs, t, tid = _NG.shortcut_net(n); s = _NG.st[n]
        out.append((n, s["tracks"], s["vias"], s["failed"], recs, t, tid))
    return out
