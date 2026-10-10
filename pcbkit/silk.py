"""Silkscreen label placement for scripted boards (fresh or locked, hand-routed ones).

A label takes the first free slot around its anchor box (above, below, left, right, corners, a second tier; then
rotated 90 degrees) that stays inside the given area and clears pads and open vias by 0.15 mm and other silk by
0.1 mm. Tented vias are masked, so silk may cross them. Needs pcbnew. Harvested from the projects' silk_place.py.

    from pcbkit.silk import Placer
    p = Placer(board, area=(x0, y0, x1, y1))
    p.label("CH1", "B", anchor_bbox_mm, size=1.0)
"""
import math

import pcbnew

mm = pcbnew.FromMM
V = lambda x, y: pcbnew.VECTOR2I_MM(x, y)


def BB(item):
    """Bounding box in mm. Text uses its stroke outline (what DRC and the fab see), not the larger text cell."""
    r = item.GetEffectiveTextShape().BBox() if item.GetClass() in ("PCB_TEXT", "PCB_FIELD") else item.GetBoundingBox()
    return pcbnew.ToMM(r.GetLeft()), pcbnew.ToMM(r.GetTop()), pcbnew.ToMM(r.GetRight()), pcbnew.ToMM(r.GetBottom())


hit = lambda a, z, g=0.0: a[0] < z[2] + g and z[0] < a[2] + g and a[1] < z[3] + g and z[1] < a[3] + g
inside = lambda r, area: r[0] >= area[0] and r[1] >= area[1] and r[2] <= area[2] and r[3] <= area[3]

# small moves around a preferred spot, nearest first: 0.25 mm sideways, 0.05 mm up/down
NUDGES = sorted(((dx * 0.25, dy * 0.05) for dx in range(-3, 4) for dy in range(-8, 9)), key=lambda d: math.hypot(*d))

SILK_MIN_H = 1.0       # JLC: text height >= 1.0 mm
SILK_MIN_W = 0.15      # JLC: line / stroke width >= 0.15 mm (also the silk-to-pad clearance)


def stroke(size):
    return max(SILK_MIN_W, size * 0.15)


def set_size(item, size, width=None):
    """Text height `size`, stroke from it; `width` (character width) defaults to the height. References use 0.8."""
    item.SetTextSize(V(width or size, size))
    item.SetTextThickness(mm(stroke(size)))
    item.SetLineSpacing(0.9)            # two-line block labels fit between part rows at 1.0 mm


class Placer:
    def __init__(self, board, area):
        self.b, self.area = board, area
        self.obst, self.taken = {"F": [], "B": []}, {"F": [], "B": []}
        ds = board.GetDesignSettings()
        for fp in board.GetFootprints():
            s = "B" if fp.IsFlipped() else "F"
            for pad in fp.Pads():
                self.obst[s].append(BB(pad))
            for g in fp.GraphicalItems():
                if g.GetLayer() in (pcbnew.F_SilkS, pcbnew.B_SilkS):
                    self.taken["B" if g.GetLayer() == pcbnew.B_SilkS else "F"].append(BB(g))
        for t in board.GetTracks():
            if t.Type() == pcbnew.PCB_VIA_T and not (ds.m_TentViasFront and ds.m_TentViasBack):
                self.obst["F"].append(BB(t))
                self.obst["B"].append(BB(t))

    def free(self, item, s, area=None):
        r = BB(item)
        return inside(r, area or self.area) and not any(hit(r, o, SILK_MIN_W) for o in self.obst[s]) \
            and not any(hit(r, o, 0.1) for o in self.taken[s])

    def commit(self, item, s):
        self.taken[s].append(BB(item))

    def try_slots(self, item, s, anchor, size, area=None, gap=0.25, count=False, width=None):
        """Move text item next to the anchor box. Commits the first free slot and returns True, or with count=True
        just returns how many slots are free."""
        n_free = 0
        x0, y0, x1, y1 = anchor
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        set_size(item, size, width)
        for rot in (0, 90):
            item.SetTextAngleDegrees(rot)
            item.SetPosition(V(cx, cy))
            w, h = (lambda r: (r[2] - r[0], r[3] - r[1]))(BB(item))
            slots = [(cx, y0 - gap - h / 2), (cx, y1 + gap + h / 2), (x0 - gap - w / 2, cy), (x1 + gap + w / 2, cy),
                     (x0 - gap - w / 2, y0 - gap - h / 2), (x1 + gap + w / 2, y0 - gap - h / 2),
                     (x0 - gap - w / 2, y1 + gap + h / 2), (x1 + gap + w / 2, y1 + gap + h / 2),
                     (cx, y0 - gap - 1.6 * h), (cx, y1 + gap + 1.6 * h)]          # second tier, one line further out
            for sx, sy in slots:
                item.SetPosition(V(sx, sy))
                if self.free(item, s, area):
                    if count:
                        n_free += 1
                        continue
                    self.commit(item, s)
                    return True
        return n_free if count else False

    def label(self, text, s, anchor, size, area=None):
        t = pcbnew.PCB_TEXT(self.b)
        t.SetText(text)
        t.SetLayer(pcbnew.B_SilkS if s == "B" else pcbnew.F_SilkS)
        t.SetMirrored(s == "B")
        if self.try_slots(t, s, anchor, size, area):
            self.b.Add(t)
            return True
        return False
