"""The board as the router sees it: fixed copper, rule areas and holes on a 0.025 mm raster, the ICs' pin areas and each
pad's zones on the 0.05 mm lattice. Geometry comes from geom.py (KiCad's Python), in the router's frame (KiCad - 100 mm)."""
import math
import numpy as np
from scipy import ndimage
from PIL import Image, ImageDraw
from .costs import RES, STEP, DEF, VIA_SIZE, REFS, PIN_MARGIN, HALO, BELLY


class Board:
    def __init__(self, geom, soft=(), via=None, refs=None):
        """soft: names of pours that refill round signals: their fill is no wall for other nets. via: (diameter, drill).
        refs: a job's reference prefixes (costs.py's REFS)"""
        self.g = geom
        self.refs = dict(REFS, **(refs or {}))
        d, drill = via or VIA_SIZE
        self.via_r, self.drill_r = d / 2, drill / 2
        xs = [p[0] for poly in geom["outline"] for p in poly[0]]; ys = [p[1] for poly in geom["outline"] for p in poly[0]]
        self.X0 = math.floor(min(xs)) - 1.0; self.Y0 = math.floor(min(ys)) - 1.0
        self.W = int(round((max(xs) + 1.0 - self.X0) / RES)) // STEP * STEP + 1
        self.H = int(round((max(ys) + 1.0 - self.Y0) / RES)) // STEP * STEP + 1
        self.codes = {"": -3}
        self.lab = {L: np.zeros((self.H, self.W), np.int32) for L in "FB12"}
        self.lab_in = np.zeros((self.H, self.W), np.int32)      # inner-layer tracks: obstacles for vias only
        self.kt = {L: np.zeros((self.H, self.W), bool) for L in "FB"}
        self.kv = np.zeros((self.H, self.W), bool)
        self.holes = np.zeros((self.H, self.W), bool)       # other-net drills (hole-to-hole for new vias)
        self.smd = np.zeros((self.H, self.W), bool)         # SMD pads of every net: no via in or against a pad
        self.vias = []                                       # (x, y, code)
        self.soft = {L: np.zeros((self.H, self.W), bool) for L in "FB"}     # soft pour fill, not under other copper
        self.soft_any = bool(soft)
        self.dirty = None
        self.wide_clr = []                                   # (clearance, nets' codes): set_clearances()
        for it in geom["items"]:
            if it["kind"] == "zone":
                self.paint_item(it)
                if it.get("name") in soft and it["layer"] in "FB":
                    for poly in it["polys"]: self.put(self.soft[it["layer"]], self.cmask(poly), True)
        if soft:                    # paint the rest apart, so a pour's own pads and tracks stay hard copper
            zl = {L: self.lab[L].copy() for L in "FB"}
            for L in "FB": self.lab[L][:] = 0
        for it in geom["items"]:
            if it["kind"] != "zone": self.paint_item(it)
        if soft:
            for L in "FB":
                self.soft[L] &= self.lab[L] == 0
                self.lab[L] = np.where(self.lab[L] != 0, self.lab[L], zl[L])
        inside = np.zeros((self.H, self.W), bool)
        for poly in geom["outline"]:
            inside |= self.mask(poly)
        inner = ndimage.distance_transform_edt(inside) * RES >= (DEF["edge"] - DEF["clr"])
        for L in "FB":
            self.lab[L][(~inner) & (self.lab[L] == 0)] = -1
        for k in geom["keepouts"]:
            for poly in k["polys"]:
                m = self.mask(poly)
                if k["tracks"]: self.kt[k["layer"]] |= m
                if k["vias"]: self.kv |= m
        self.pads = {}
        for p in geom["pads"]:
            self.pads[f'{p["ref"]}.{p["num"]}'] = p
        self.parts(); self.styles = {}

    def parts(self):
        """the ICs (6 SMD pads or more, most of the part's pads): self.ics, [(ref, side, box)]; on the lattice
        self.pin_area (their pin areas) and self.under[L] (inside the box of an IC on the other side of L); and each
        pin's way out, self.rows: {(ref, x, y of the pad's centre): (dx, dy, stagger)}"""
        by = {}
        for it in self.g["items"]:
            if it["kind"] == "pad": by.setdefault(it["ref"], []).append(it)
        Hl, Wl = (self.H - 1) // STEP + 1, (self.W - 1) // STEP + 1
        self.pin_area = np.zeros((Hl, Wl), bool); self.under = {L: np.zeros((Hl, Wl), bool) for L in "FB"}
        self.ics, self.rows = [], {}
        court = {p["ref"]: p["court"] for p in self.g.get("parts", [])}
        lat = lambda v, v0: (v - v0) / (RES * STEP)
        def sub(box):
            x0, y0, x1, y1 = box
            return (slice(max(math.ceil(lat(y0, self.Y0)), 0), max(math.floor(lat(y1, self.Y0)) + 1, 0)),
                    slice(max(math.ceil(lat(x0, self.X0)), 0), max(math.floor(lat(x1, self.X0)) + 1, 0)))
        for ref, its in by.items():
            smd = [it for it in its if it.get("smd")]
            if len(smd) < 6 or len(smd) * 2 <= len(its) - len(smd): continue
            boxes = [pad_box(it["polys"]) for it in smd]
            box = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
            side = smd[0]["layer"]; self.ics.append((ref, side, box))
            self.pin_area[sub((box[0] - PIN_MARGIN, box[1] - PIN_MARGIN, box[2] + PIN_MARGIN, box[3] + PIN_MARGIN))] = True
            self.under["FB"["BF".index(side)]][sub(box)] = True
            if self.is_ref(ref, "ic") and ref in court: self.under["FB"["BF".index(side)]][sub(court[ref])] = True
            if box[2] - box[0] > 2 * BELLY and box[3] - box[1] > 2 * BELLY:
                self.under[side][sub((box[0] + BELLY, box[1] + BELLY, box[2] - BELLY, box[3] - BELLY))] = True
            rows = {}
            for b in boxes:
                long_x = b[2] - b[0] >= b[3] - b[1]
                gaps = ((b[0] - box[0], (-1, 0), long_x), (box[2] - b[2], (1, 0), long_x),
                        (b[1] - box[1], (0, -1), not long_x), (box[3] - b[3], (0, 1), not long_x))
                gap, d, _ = min(gaps, key=lambda e: (e[0] > 0.05, not e[2], e[0]))
                if gap <= 0.3: rows.setdefault(d, []).append(b)           # deeper than that: not on a pin row
            for d, row in rows.items():
                row.sort(key=lambda b: b[0] + b[1])
                cs = [(b[0] + b[2]) / 2 if d[1] else (b[1] + b[3]) / 2 for b in row]
                pitch = min([q - p for p, q in zip(cs, cs[1:]) if q - p > 0.05], default=9.0)
                need = 2 * self.via_r + DEF["clr"] + 0.04                       # vias of neighbouring pins, centre to centre
                st = math.sqrt(need * need - pitch * pitch) if pitch < need else 0.0
                for i, b in enumerate(row):
                    self.rows[(ref, round((b[0] + b[2]) / 2, 2), round((b[1] + b[3]) / 2, 2))] = (d, st if i % 2 else 0.0)

    def pad_style(self, p):
        """a pad's zones on the lattice, computed once: (slices, near, off, site), near within neck_r of its edge, off
        the track round it that isn't on its centre lines, site where a via is in line with it"""
        key = (p["ref"], p["num"], *p["at"])                   # a part can have several pads of one number
        if key in self.styles: return self.styles[key]
        D = DEF; box = pad_box([q for ps in p["polys"].values() for q in ps])
        cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        out = self.rows.get((p["ref"], round(cx, 2), round(cy, 2)))
        dep0 = self.via_r + D["via_pad_gap"] + RES; site_r = 0.1
        reach = max(D["neck_r"], HALO, dep0 + 1.0) + RES * STEP
        node = lambda v, v0: int(round((v - v0) / (RES * STEP)))
        gx0, gx1 = node(box[0] - reach, self.X0), node(box[2] + reach, self.X0) + 1
        gy0, gy1 = node(box[1] - reach, self.Y0), node(box[3] + reach, self.Y0) + 1
        gx0, gy0 = max(gx0, 0), max(gy0, 0)
        gx = self.X0 + np.arange(gx0, gx1) * RES * STEP; gy = self.Y0 + np.arange(gy0, gy1) * RES * STEP
        xx, yy = np.meshgrid(gx, gy)
        ms = [q for ps in p["polys"].values() for q in ps]
        i0, i1, j0, j1 = gx0 * STEP, min((gx1 - 1) * STEP + 1, self.W), gy0 * STEP, min((gy1 - 1) * STEP + 1, self.H)
        cells = np.zeros((j1 - j0, i1 - i0), bool)
        for q in ms:
            c = self.cmask(q)
            if c:
                (sy, sx), u = c
                a0, a1, b0, b1 = max(sy.start, j0), min(sy.stop, j1), max(sx.start, i0), min(sx.stop, i1)
                if a1 > a0 and b1 > b0:
                    cells[a0 - j0:a1 - j0, b0 - i0:b1 - i0] |= u[a0 - sy.start:a1 - sy.start, b0 - sx.start:b1 - sx.start]
        dist = (ndimage.distance_transform_edt(~cells) * RES)[::STEP, ::STEP]
        xx, yy = xx[:dist.shape[0], :dist.shape[1]], yy[:dist.shape[0], :dist.shape[1]]
        near = dist <= D["neck_r"]
        lane = RES * STEP * 0.75
        on_v, on_h = np.abs(xx - cx) <= lane, np.abs(yy - cy) <= lane
        off = (dist <= HALO) & (dist > 0) & ~(on_v | on_h)
        site = np.zeros_like(near)
        ways = [out] if out else [(d, 0.0) for d in ((1, 0), (-1, 0), (0, 1), (0, -1))]
        for (dx, dy), st in ways:
            end = (box[2] if dx > 0 else box[0] if dx < 0 else box[3] if dy > 0 else box[1])
            along = ((xx - end) * dx if dx else (yy - end) * dy)
            across = yy - cy if dx else xx - cx
            site |= (np.abs(across) <= RES * STEP * 1.25) & (along >= dep0 + st - RES) & (along <= dep0 + st + 2 * site_r)
        sl = (slice(gy0, gy0 + dist.shape[0]), slice(gx0, gx0 + dist.shape[1]))
        self.styles[key] = (sl, near, off, site)
        return self.styles[key]

    # --- rasterising ---------------------------------------------------------------------------------------------
    def code(self, net):
        if net not in self.codes: self.codes[net] = len(self.codes) + 1
        return self.codes[net]

    def px(self, x, y):
        return ((x - self.X0) / RES + 0.5, (y - self.Y0) / RES + 0.5)

    def cmask(self, poly):
        """(slice, bool sub-mask) of [outline, holes...]."""
        pts = [self.px(x, y) for x, y in poly[0]]
        if len(pts) < 3: return None
        x0 = max(int(min(p[0] for p in pts)) - 1, 0); x1 = min(int(max(p[0] for p in pts)) + 2, self.W)
        y0 = max(int(min(p[1] for p in pts)) - 1, 0); y1 = min(int(max(p[1] for p in pts)) + 2, self.H)
        if x1 <= x0 or y1 <= y0: return None
        im = Image.new("L", (x1 - x0, y1 - y0), 0); d = ImageDraw.Draw(im)
        d.polygon([(a - x0, b - y0) for a, b in pts], fill=1)
        for h in poly[1:]:
            hp = [self.px(x, y) for x, y in h]
            if len(hp) >= 3: d.polygon([(a - x0, b - y0) for a, b in hp], fill=0)
        return (slice(y0, y1), slice(x0, x1)), np.asarray(im, bool)

    def cdisc(self, x, y, r):
        cx, cy = self.px(x, y); rr = r / RES
        x0, x1 = max(int(cx - rr) - 1, 0), min(int(cx + rr) + 2, self.W)
        y0, y1 = max(int(cy - rr) - 1, 0), min(int(cy + rr) + 2, self.H)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        return (slice(y0, y1), slice(x0, x1)), (xx + 0.5 - cx) ** 2 + (yy + 0.5 - cy) ** 2 <= rr * rr

    def mask(self, poly):
        out = np.zeros((self.H, self.W), bool); m = self.cmask(poly)
        if m: out[m[0]] = m[1]
        return out

    def put(self, arr, m, v):
        if m:
            arr[m[0]][m[1]] = v
            if self.dirty is not None:          # the box painted since watch()
                (a, b_), (c, d) = (m[0][0].start, m[0][0].stop), (m[0][1].start, m[0][1].stop)
                self.dirty = (min(self.dirty[0], a), max(self.dirty[1], b_), min(self.dirty[2], c), max(self.dirty[3], d))

    def is_ref(self, ref, kind):
        return ref.startswith(tuple(self.refs[kind]))

    def paint_item(self, it):
        c = self.code(it["net"]) if it["net"] else -3
        if "circle" in it:
            x, y, r = it["circle"]
            if it["kind"] == "npth":
                keep = DEF["mount_keep"] if self.is_ref(it.get("ref", ""), "mount") else r + DEF["hole_clr"]
                m = self.cdisc(x, y, keep - DEF["clr"])
                for L in "FB": self.put(self.lab[L], m, -2)
                self.put(self.holes, self.cdisc(x, y, r), True)
                return
            if it["kind"] == "hole":
                self.put(self.holes, self.cdisc(x, y, r), True); return
            self.add_via(x, y, c, r, it.get("drill", self.drill_r))
            return
        for poly in it["polys"]:
            if it["layer"] in "12" and it["kind"] != "zone": self.put(self.lab_in, self.cmask(poly), c)
            else: self.put(self.lab[it["layer"]], self.cmask(poly), c)
            if it.get("smd"): self.put(self.smd, self.cmask(poly), True)

    def add_via(self, x, y, c, r=None, drill=None):
        m = self.cdisc(x, y, r or self.via_r)
        for L in "FB": self.put(self.lab[L], m, c)
        self.vias.append((x, y, c)); self.put(self.holes, self.cdisc(x, y, drill or self.drill_r), True)

    def paint_seg(self, L, x0, y0, x1, y1, w, c):
        self.put(self.lab[L], self.cmask([capsule(x0, y0, x1, y1, w / 2)]), c)

    def static_edt(self, key):
        """distance (mm) to the rule areas, computed once: they never change during a run."""
        if not hasattr(self, "_edt"): self._edt = {}
        if key not in self._edt:
            arr = self.kv if key == "kv" else (self.smd if key == "smd" else self.kt[key[2]])
            self._edt[key] = (ndimage.distance_transform_edt(~arr) * RES).astype(np.float32) if arr.any() \
                else np.full(arr.shape, 1e3, np.float32)
        return self._edt[key]

    def set_clearances(self, nets):
        """{net: mm}, the nets asking for more than a job's default: DRC takes the larger of two nets' clearances, so a net
        keeps theirs from their fixed copper"""
        by = {}
        for n, c in nets.items(): by.setdefault(c, []).append(self.code(n))
        self.wide_clr = sorted((c, np.array(v)) for c, v in by.items())

    def gap(self, lab, c, clr, own=0.0):
        """distance (mm) to the copper in lab of every net but c, as something keeping clr sees it: less what's asked beyond
        clr, by this net's own clearance or by a stricter class's for its copper (DRC takes the larger of the two)"""
        m = (lab != 0) & (lab != c)
        if not m.any(): return np.full(lab.shape, 1e3, np.float32)
        d = ndimage.distance_transform_edt(~m) * RES - max(own - clr, 0.0)
        for cl, codes in self.wide_clr:
            w = np.isin(lab, codes) & (lab != c)
            if cl > max(clr, own) and w.any(): d = np.minimum(d, ndimage.distance_transform_edt(~w) * RES - (cl - clr))
        return d

    def keep_mask(self, keep, net, layer):
        """a job's keep-out rectangles that hold for net on a layer ("v": for vias), as a raster"""
        out = np.zeros((self.H, self.W), bool)
        for k in keep:
            if net in k.get("except", []): continue
            if not (k.get("vias", True) if layer == "v" else layer in k.get("layers", "FB")): continue
            x0, y0, x1, y1 = k["rect"]
            out[max(int((y0 - self.Y0) / RES), 0):max(int((y1 - self.Y0) / RES) + 1, 0),
                max(int((x0 - self.X0) / RES), 0):max(int((x1 - self.X0) / RES) + 1, 0)] = True
        return out

    # --- a route painted for a moment, the fixed copper put back --------------------------------------------------
    def freeze(self):
        """what is painted now is the fixed copper: thaw() goes back to it"""
        self.lab0 = {L: self.lab[L].copy() for L in "FB12"}
        self.holes0, self.vias0 = self.holes.copy(), list(self.vias)

    def watch(self):
        self.dirty = (self.H, 0, self.W, 0)

    def thaw(self):
        """undo what was painted since watch()"""
        a, b, c, d = self.dirty; self.dirty = None
        if b > a and d > c:
            sl = (slice(a, b), slice(c, d))
            for L in "FB12": self.lab[L][sl] = self.lab0[L][sl]
            self.holes[sl] = self.holes0[sl]
        self.vias[:] = self.vias0

    # --- per-net maps ----------------------------------------------------------------------------------------------
    def window(self, pts, margin):
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        i0 = max(int((min(xs) - margin - self.X0) / RES) // STEP * STEP, 0)
        j0 = max(int((min(ys) - margin - self.Y0) / RES) // STEP * STEP, 0)
        i1 = min(int((max(xs) + margin - self.X0) / RES) + 1, self.W)
        j1 = min(int((max(ys) + margin - self.Y0) / RES) + 1, self.H)
        return j0, j1, i0, i1


def pad_box(polys):
    """(x0, y0, x1, y1) of [[outline, holes...], ...]"""
    xs = [p[0] for poly in polys for p in poly[0]]; ys = [p[1] for poly in polys for p in poly[0]]
    return min(xs), min(ys), max(xs), max(ys)


def capsule(x0, y0, x1, y1, r, n=8):
    a = math.atan2(y1 - y0, x1 - x0) if (x1, y1) != (x0, y0) else 0.0
    pts = [(x1 + r * math.cos(a - math.pi / 2 + math.pi * k / n), y1 + r * math.sin(a - math.pi / 2 + math.pi * k / n))
           for k in range(n + 1)]
    pts += [(x0 + r * math.cos(a + math.pi / 2 + math.pi * k / n), y0 + r * math.sin(a + math.pi / 2 + math.pi * k / n))
            for k in range(n + 1)]
    return pts


def short(n): return n.split("/")[-1]


def seg_dist(a, b, c, d):
    """distance between the segments a-b and c-d (points: zero-length segments)"""
    def pt(p, a, b):
        ax, ay = b[0] - a[0], b[1] - a[1]; L = ax * ax + ay * ay
        t = 0.0 if L == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * ax + (p[1] - a[1]) * ay) / L))
        return math.hypot(p[0] - a[0] - t * ax, p[1] - a[1] - t * ay)
    o = lambda p, q, r: (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    if a != b and c != d and o(a, b, c) * o(a, b, d) < 0 and o(c, d, a) * o(c, d, b) < 0: return 0.0
    return min(pt(a, c, d), pt(b, c, d), pt(c, a, b), pt(d, a, b))


def seg_box(a, b, box):
    """does the segment a-b touch the box (x0, y0, x1, y1)? (Liang-Barsky)"""
    x0, y0, x1, y1 = box; (ax, ay), (bx, by) = a, b; t0, t1 = 0.0, 1.0
    for p, q in ((ax - bx, ax - x0), (bx - ax, x1 - ax), (ay - by, ay - y0), (by - ay, y1 - ay)):
        if p == 0:
            if q < 0: return False
        else:
            t = q / p
            if p < 0: t0 = max(t0, t)
            else: t1 = min(t1, t)
            if t0 > t1: return False
    return True
