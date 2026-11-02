"""A board as SVG, from geom.py's geometry, in KiCad's colours: F.Cu red over B.Cu blue, pads, vias with their drill,
pours faint, references in silk yellow. Rectangles are in the router's frame (KiCad - 100 mm)."""

COL = dict(bg="#001023", F="#c83434", B="#4d7fc4", tht="#c2a33a", via="#c8c8c8", hole="#1a1a1a", edge="#d0d2cd",
           silk="#f2eda1", rat="#f8f9fa")


def clip_ring(ring, r):
    """Sutherland-Hodgman against the rectangle r = (x0, y0, x1, y1)"""
    x0, y0, x1, y1 = r
    edges = [(lambda p: p[0] >= x0, lambda a, b: (x0, a[1] + (b[1] - a[1]) * (x0 - a[0]) / (b[0] - a[0]))),
             (lambda p: p[0] <= x1, lambda a, b: (x1, a[1] + (b[1] - a[1]) * (x1 - a[0]) / (b[0] - a[0]))),
             (lambda p: p[1] >= y0, lambda a, b: (a[0] + (b[0] - a[0]) * (y0 - a[1]) / (b[1] - a[1]), y0)),
             (lambda p: p[1] <= y1, lambda a, b: (a[0] + (b[0] - a[0]) * (y1 - a[1]) / (b[1] - a[1]), y1))]
    pts = list(ring)
    for inside, cut in edges:
        if not pts: break
        out, prev = [], pts[-1]
        for p in pts:
            if inside(p):
                if not inside(prev): out.append(cut(prev, p))
                out.append(p)
            elif inside(prev): out.append(cut(prev, p))
            prev = p
        pts = out
    return pts


def bbox(pts):
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def meets(b, r):
    return b[0] <= r[2] and b[2] >= r[0] and b[1] <= r[3] and b[3] >= r[1]


def path(rings):
    return " ".join("M" + " ".join(f"{x:.3f},{y:.3f}" for x, y in ring) + "Z" for ring in rings if len(ring) >= 3)


def svg(geom, r, px_mm, labels=True, zones=True, rats=()):
    """the board inside r as SVG, in mm (viewBox = r); r grown by 1 mm for what is drawn"""
    x0, y0, x1, y1 = r; R = (x0 - 1, y0 - 1, x1 + 1, y1 + 1)
    w, h = x1 - x0, y1 - y0
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.3f} {y0:.3f} {w:.3f} {h:.3f}" '
         f'width="{w * px_mm:.0f}" height="{h * px_mm:.0f}">', f'<rect x="{x0}" y="{y0}" width="{w}" height="{h}" fill="{COL["bg"]}"/>']
    it = geom["items"]
    if zones:
        for L in "BF":
            ps = []
            for z in it:
                if z["kind"] != "zone" or z["layer"] != L: continue
                for poly in z["polys"]:
                    if not poly or not meets(bbox(poly[0]), R): continue
                    ps.append(path([clip_ring(ring, R) for ring in poly]))
            if ps: o.append(f'<path d="{" ".join(ps)}" fill="{COL[L]}" fill-opacity="0.22" fill-rule="evenodd"/>')
    for L in "BF":
        segs = {}
        for t in it:
            if t["kind"] != "track" or t["layer"] != L: continue
            a, b = t["seg"][:2], t["seg"][2:]
            if not meets((min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])), R): continue
            segs.setdefault(round(t["w"], 3), []).append(f"M{a[0]:.3f},{a[1]:.3f}L{b[0]:.3f},{b[1]:.3f}")
        for wd, ss in sorted(segs.items()):
            o.append(f'<path d="{"".join(ss)}" stroke="{COL[L]}" stroke-width="{wd}" stroke-linecap="round" '
                     f'stroke-linejoin="round" fill="none" opacity="{0.78 if L == "B" else 0.88}"/>')
        ps, tht = [], []
        for p in it:
            if p["kind"] != "pad" or p["layer"] != L: continue
            for poly in p["polys"]:
                if poly and meets(bbox(poly[0]), R): (ps if p.get("smd", True) else tht).append(path(poly))
        if ps: o.append(f'<path d="{" ".join(ps)}" fill="{COL[L]}" fill-rule="evenodd"/>')
        if tht and L == "F": o.append(f'<path d="{" ".join(tht)}" fill="{COL["tht"]}" fill-rule="evenodd"/>')
    for v in it:
        if v["kind"] in ("via", "hole", "npth"):
            x, y, rr = v["circle"]
            if not meets((x - rr, y - rr, x + rr, y + rr), R): continue
            if v["kind"] == "via":
                o.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{rr:.3f}" fill="{COL["via"]}"/>'
                         f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{v.get("drill", rr / 2):.3f}" fill="{COL["hole"]}"/>')
            else:
                o.append(f'<circle cx="{x:.3f}" cy="{y:.3f}" r="{rr:.3f}" fill="{COL["hole"]}"/>')
    for poly in geom["outline"]:
        o.append(f'<path d="{path(poly)}" stroke="{COL["edge"]}" stroke-width="0.15" fill="none"/>')
    sw = 0.06 if w < 25 else 0.18
    for a, b in rats:       # unrouted connections, as KiCad's ratsnest
        if meets((min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])), R):
            o.append(f'<line x1="{a[0]:.3f}" y1="{a[1]:.3f}" x2="{b[0]:.3f}" y2="{b[1]:.3f}" stroke="{COL["rat"]}" '
                     f'stroke-width="{sw}" stroke-dasharray="{sw * 4} {sw * 2}"/>'
                     + "".join(f'<circle cx="{p[0]:.3f}" cy="{p[1]:.3f}" r="{sw * 2.5}" fill="none" stroke="{COL["rat"]}" '
                               f'stroke-width="{sw}"/>' for p in (a, b)))
    if w < 25:      # a close-up: a 1 mm scale bar in the bottom left corner
        bx, by = x0 + 0.5, y1 - 0.5
        o.append(f'<path d="M{bx},{by - 0.25}V{by}H{bx + 1}V{by - 0.25}" stroke="{COL["edge"]}" stroke-width="0.08" fill="none"/>'
                 f'<text x="{bx + 1.25}" y="{by}" font-size="0.6" font-family="Helvetica,Arial,sans-serif" '
                 f'fill="{COL["edge"]}">1 mm</text>')
    if labels:
        refs = {}
        for p in geom["pads"]: refs.setdefault(p["ref"], []).append(p["at"])
        fs = 0.75 if w < 25 else 1.6
        for ref, pts in refs.items():
            cx = sum(p[0] for p in pts) / len(pts); cy = sum(p[1] for p in pts) / len(pts)
            if x0 < cx < x1 and y0 < cy < y1:
                o.append(f'<text x="{cx:.2f}" y="{cy:.2f}" font-size="{fs}" font-family="Helvetica,Arial,sans-serif" '
                         f'fill="{COL["silk"]}" fill-opacity="0.75" text-anchor="middle" dominant-baseline="middle" '
                         f'stroke="{COL["bg"]}" stroke-width="{fs / 8:.2f}" paint-order="stroke">{ref}</text>')
    o.append("</svg>")
    return "\n".join(o)
