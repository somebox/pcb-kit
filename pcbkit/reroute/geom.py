"""A board's routing geometry for the router: copper on F.Cu and B.Cu by net, holes, rule areas, the outline and every
pad, in the router's frame (KiCad - 100 mm). Zones are refilled first. This is all the router knows of a board.

    python -m pcbkit.reroute.geom BOARD.kicad_pcb OUT.json        (KiCad's Python)
"""
import json
import sys

OFF = 100.0


def main(argv=None):
    import pcbnew
    argv = sys.argv[1:] if argv is None else argv
    b = pcbnew.LoadBoard(argv[0])
    pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    F, B = b.GetLayerID("F.Cu"), b.GetLayerID("B.Cu")
    names = {F: "F", B: "B"}
    inner = {b.GetLayerID("In1.Cu"): "1", b.GetLayerID("In2.Cu"): "2"}
    err = pcbnew.FromMM(0.005)

    def mmv(v):
        return round(pcbnew.ToMM(v) - OFF, 4)

    def chain(c):
        return [[mmv(c.CPoint(j).x), mmv(c.CPoint(j).y)] for j in range(c.PointCount())]

    def polys(ps):
        """SHAPE_POLY_SET -> [[outline, hole, hole...], ...]"""
        return [[chain(ps.COutline(i))] + [chain(ps.CHole(i, k)) for k in range(ps.HoleCount(i))]
                for i in range(ps.OutlineCount())]

    items, pads = [], []
    for fp in b.GetFootprints():
        ref = fp.GetReference()
        for p in fp.Pads():
            net = p.GetNetname()
            ls = [L for L in (F, B) if p.IsOnLayer(L)]
            shapes = {}
            for L in ls:
                ps = pcbnew.SHAPE_POLY_SET()
                p.TransformShapeToPolygon(ps, L, 0, err, pcbnew.ERROR_INSIDE)
                shapes[names[L]] = polys(ps)
                items.append({"net": net, "layer": names[L], "kind": "pad", "ref": ref, "polys": shapes[names[L]],
                              "smd": p.GetAttribute() == pcbnew.PAD_ATTRIB_SMD})
            if p.HasHole():
                d = pcbnew.ToMM(max(p.GetDrillSize().x, p.GetDrillSize().y))
                items.append({"net": net, "layer": "*", "ref": ref,
                              "kind": "npth" if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH else "hole",
                              "circle": [mmv(p.GetPosition().x), mmv(p.GetPosition().y), d / 2]})
            if net and ls:
                pads.append({"ref": ref, "num": p.GetNumber(), "net": net, "layers": [names[L] for L in ls],
                             "at": [mmv(p.GetPosition().x), mmv(p.GetPosition().y)], "polys": shapes})
    for t in b.GetTracks():
        net = t.GetNetname()
        if t.Type() == pcbnew.PCB_VIA_T:
            x, y, r = mmv(t.GetPosition().x), mmv(t.GetPosition().y), pcbnew.ToMM(t.GetWidth(F)) / 2
            items.append({"net": net, "layer": "*", "kind": "via", "circle": [x, y, r],
                          "drill": pcbnew.ToMM(t.GetDrillValue()) / 2})
        elif t.GetLayer() in names or t.GetLayer() in inner:
            ps = pcbnew.SHAPE_POLY_SET()
            t.TransformShapeToPolygon(ps, t.GetLayer(), 0, err, pcbnew.ERROR_INSIDE)
            items.append({"net": net, "layer": {**names, **inner}[t.GetLayer()], "kind": "track", "polys": polys(ps),
                          "seg": [mmv(t.GetStart().x), mmv(t.GetStart().y), mmv(t.GetEnd().x), mmv(t.GetEnd().y)],
                          "w": pcbnew.ToMM(t.GetWidth())})
    keep = []
    for z in list(b.Zones()) + [z for fp in b.GetFootprints() for z in fp.Zones()]:
        if z.GetIsRuleArea():
            for L in (F, B):
                if z.IsOnLayer(L):
                    keep.append({"name": z.GetZoneName(), "layer": names[L], "tracks": z.GetDoNotAllowTracks(),
                                 "vias": z.GetDoNotAllowVias(), "polys": polys(z.Outline())})
            continue
        for L in (F, B):
            if z.IsOnLayer(L):
                items.append({"net": z.GetNetname(), "layer": names[L], "kind": "zone", "name": z.GetZoneName(),
                              "polys": polys(z.GetFilledPolysList(L))})
        for L, n in inner.items():
            if z.IsOnLayer(L):                  # inner planes: connectivity only, never routed
                items.append({"net": z.GetNetname(), "layer": n, "kind": "zone", "name": z.GetZoneName(),
                              "polys": polys(z.GetFilledPolysList(L))})
    parts = []                                  # for placement moves, and for what lies under a part
    for fp in b.GetFootprints():
        side = "F" if fp.GetLayer() == F else "B"
        ps = fp.GetCourtyard(b.GetLayerID(f"{side}.CrtYd"))
        bb = ps.BBox() if ps.OutlineCount() else fp.GetBoundingBox(False, False)
        p = fp.GetPosition()
        parts.append({"ref": fp.GetReference(), "at": [mmv(p.x), mmv(p.y)], "rot": fp.GetOrientationDegrees(),
                      "side": side, "value": fp.GetValue(),
                      "court": [mmv(bb.GetLeft()), mmv(bb.GetTop()), mmv(bb.GetRight()), mmv(bb.GetBottom())]})
    ol = pcbnew.SHAPE_POLY_SET()
    b.GetBoardPolygonOutlines(ol, True)
    with open(argv[1], "w") as fh:
        json.dump({"items": items, "pads": pads, "keepouts": keep, "outline": polys(ol), "parts": parts}, fh)
    print(f"{len(items)} items, {len(pads)} pads, {len(keep)} keep-outs -> {argv[1]}")


if __name__ == "__main__":
    main()
