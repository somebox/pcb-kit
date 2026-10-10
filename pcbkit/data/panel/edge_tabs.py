"""KiKit tabs plugin: tabs on the long (top and bottom) edges of stacked modules, where both edges are copper-free.

  "tabs": {"type": "plugin", "code": "edge_tabs.py.EdgeTabs", "width": "4mm",
           "arg": "depth=0.8; prefer=gaps:D; spacing=55"}

How it places tabs:
- Every module copy is identical, so the first one is examined. A tab position x is usable when no track, via or pad
  lies within `depth` mm of the top or bottom edge over the tab width plus 1.2 mm each side (the mouse-bite
  line runs 0.5 mm past the tab and its end holes notch the edge there). Zones are refilled later
  and don't count.
- `prefer=gaps:D` favours the empty columns between groups of D* footprints, which for the character modules are
  the gaps between characters. `prefer=P@O` favours O + k*P mm from the module's left edge instead.
- One tab goes near each end. Then a tab goes into the middle of any span longer than `spacing` mm (JLC: a set every
  50-60 mm).
- A span that has no usable position stays long and is reported, so the board can be fixed.
- The same x positions are used on the top and bottom edges of every module; the outer edges tab to the rails.
"""
from kikit.annotations import TabAnnotation
from kikit.plugin import TabsPlugin
import pcbnew


def _args(text):
    out = {}
    for part in (text or "").replace(",", ";").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class EdgeTabs(TabsPlugin):
    def buildTabAnnotations(self, panel):
        a = _args(self.userArg)
        mm, ToMM = pcbnew.FromMM, pcbnew.ToMM
        depth, spacing = mm(float(a.get("depth", 0.8))), mm(float(a.get("spacing", 55)))
        prefer = a.get("prefer", "")
        width = self.preset["tabs"]["hwidth"]
        # the mouse-bite line runs `prolong` (0.5 mm) past each tab side, and its end holes (0.3 mm radius) notch the
        # module edge there: keep copper 1.2 mm clear sideways (0.5 + 0.3 + 0.4 clearance)
        half, end = width / 2 + mm(1.2), mm(1.0)
        minx, miny, maxx, maxy = panel.substrates[0].bounds()

        # copper near either long edge of the first module copy: x intervals that block a tab
        blocked = []
        items = list(panel.board.GetTracks()) + [p for fp in panel.board.GetFootprints() for p in fp.Pads()]
        for it in items:
            r = it.GetBoundingBox()
            if r.GetRight() < minx or r.GetLeft() > maxx or r.GetBottom() < miny or r.GetTop() > maxy:
                continue
            if r.GetTop() < miny + depth or r.GetBottom() > maxy - depth:
                blocked.append((r.GetLeft(), r.GetRight()))
        free = lambda x: minx + end + half <= x <= maxx - end - half and \
            not any(l < x + half and r > x - half for l, r in blocked)
        step = mm(0.25)
        cands = [x for x in range(int(minx), int(maxx), int(step)) if free(x)]
        # preferred positions: "gaps:D" = the empty columns between groups of footprints whose reference starts with D
        # (the gaps between characters), or "P@O" = O + k*P mm from the module's left edge
        grid, pitch = [], 0.0
        if prefer.startswith("gaps:"):
            pre = prefer[5:]
            xs = sorted({round(fp.GetPosition().x, -4) for fp in panel.board.GetFootprints()
                         if fp.GetReference().startswith(pre) and fp.GetReference()[len(pre):len(pre) + 1].isdigit()
                         and minx <= fp.GetPosition().x <= maxx and miny <= fp.GetPosition().y <= maxy})
            if len(xs) > 2:
                steps = sorted(b - a for a, b in zip(xs, xs[1:]))
                unit = steps[len(steps) // 2]
                grid = [int((a + b) / 2) for a, b in zip(xs, xs[1:]) if b - a > 1.5 * unit]
                pitch = ToMM(min((b - a for a, b in zip(grid, grid[1:])), default=0))
        elif "@" in prefer:
            p_, o_ = (float(x) for x in prefer.split("@"))
            pitch, k = p_, 1
            while minx + mm(o_ + k * p_) < maxx:
                grid.append(int(minx + mm(o_ + k * p_)))
                k += 1
        grid = [x for x in grid if free(x)]

        def nearest(target, lo, hi):
            pref = [x for x in grid if lo < x < hi and abs(x - target) <= mm(pitch)]
            pool = pref or [x for x in cands if lo < x < hi]
            return min(pool, key=lambda x: abs(x - target)) if pool else None

        if not cands:
            raise RuntimeError("edge_tabs: no copper-free position for a tab on the long edges")
        chosen = sorted({nearest(minx, minx, maxx), nearest(maxx, minx, maxx)})
        warn = []
        while True:
            gaps = [(b - a, a, b) for a, b in zip(chosen, chosen[1:]) if b - a > spacing]
            gaps = [g for g in gaps if (g[1], g[2]) not in warn]
            if not gaps:
                break
            _, lo, hi = max(gaps)
            x = nearest((lo + hi) / 2, lo + mm(5), hi - mm(5))
            if x is None:
                warn.append((lo, hi))
            else:
                chosen = sorted(chosen + [x])
        rel = [round(ToMM(x - minx), 2) for x in chosen]
        span = max(ToMM(b - a) for a, b in zip(chosen, chosen[1:])) if len(chosen) > 1 else 0
        print(f"edge_tabs: tabs at x = {rel} mm from the module's left edge, longest span {span:.1f} mm")
        for lo, hi in warn:
            print(f"edge_tabs: WARNING no copper-free tab position between x = {ToMM(lo - minx):.1f} and "
                  f"{ToMM(hi - minx):.1f} mm (JLC wants a tab every 50-60 mm); clear the long edges there")

        for s in panel.substrates:
            sminx, sminy, _, smaxy = s.bounds()
            for x in rel:
                X = int(sminx + mm(x))
                s.annotations.append(TabAnnotation(None, (X, sminy), (0, 1), width))
                s.annotations.append(TabAnnotation(None, (X, smaxy), (0, -1), width))
