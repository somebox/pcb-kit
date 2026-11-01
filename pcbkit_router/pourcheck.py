"""Check the pours that refill round signals: how wide each one stays between the pads and vias
it joins, before and after the signals cut into it.

For each pour, its fill (as geom.py dumps it, refilled) and its net's pads and vias on that layer are rastered;
the terminals are the pads and vias the fill touches. Between two terminals the pour carries current through its
narrowest point on the widest path: the largest width w such that the copper at least w wide (and the terminals)
still joins them. The table gives that width for every pair before and after, on each outer layer the pour is on, and
flags a pair that lost more than LOSS of it or got narrower than MIN_W, and a terminal the pour no longer touches. Run with
the router's Python.
Usage: pourcheck.py BEFORE.json AFTER.json ZONE[,ZONE...] [--png=OUT.png]"""
import sys, json
import numpy as np
from scipy import ndimage
from PIL import Image, ImageDraw
from . import pour

RES, LOSS, MIN_W = 0.025, 0.2, 1.0


def raster(polys, box, out=None):
    x0, y0, x1, y1 = box
    W, H = int((x1 - x0) / RES) + 1, int((y1 - y0) / RES) + 1
    im = Image.new("L", (W, H), 0); d = ImageDraw.Draw(im)
    for poly in polys:
        d.polygon([((x - x0) / RES, (y - y0) / RES) for x, y in poly[0]], fill=1)
        for h in poly[1:]: d.polygon([((x - x0) / RES, (y - y0) / RES) for x, y in h], fill=0)
    m = np.asarray(im, bool)
    return m if out is None else out | m


def terminals(g, net, layer, fill, box):
    """(name, mask) of the pads and vias of `net` on `layer` that touch the fill"""
    x0, y0 = box[0], box[1]; near = ndimage.binary_dilation(fill, iterations=2); out = []
    for p in g["pads"]:
        if p["net"] != net or layer not in p["polys"]: continue
        m = raster(p["polys"][layer], box)
        if (m & near).any(): out.append((f'{p["ref"]}.{p["num"]}', m))
    for it in g["items"]:
        if it["kind"] == "via" and it["net"] == net:
            x, y, r = it["circle"]
            yy, xx = np.mgrid[0:fill.shape[0], 0:fill.shape[1]]
            m = (xx * RES + x0 - x) ** 2 + (yy * RES + y0 - y) ** 2 <= r * r
            if (m & near).any(): out.append((f"via({x:.2f},{y:.2f})", m))
    return out


def widths(fill, terms):
    cu = fill.copy()
    for _, m in terms: cu |= m
    return pour.widths(cu, terms, RES)


def zones(g, name):
    """{outer layer: the pour's item there}"""
    out = {}
    for it in g["items"]:
        if it["kind"] == "zone" and it.get("name") == name and it["layer"] in "FB": out.setdefault(it["layer"], it)
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    png = next((a[6:] for a in sys.argv[1:] if a.startswith("--png=")), None)
    before, after = json.load(open(args[0])), json.load(open(args[1]))
    pics, bad = [], 0
    for name in args[2].split(","):
        layers, now = zones(before, name), zones(after, name)
        if not layers: print(f"{name}: not on the board before"); bad += 1
        for L, zb in sorted(layers.items()):
            za = now.get(L)
            if not za: print(f"{name}: gone from {L}.Cu  << narrower"); bad += 1; continue
            pts = [p for poly in zb["polys"] + za["polys"] for p in poly[0]]
            box = (min(p[0] for p in pts) - 1, min(p[1] for p in pts) - 1, max(p[0] for p in pts) + 1, max(p[1] for p in pts) + 1)
            fb, fa = raster(zb["polys"], box), raster(za["polys"], box)
            tb = terminals(before, zb["net"], L, fb, box); ta = terminals(after, za["net"], L, fa, box)
            keep = {n for n, _ in tb} & {n for n, _ in ta}
            wb = widths(fb, [t for t in tb if t[0] in keep]); wa = widths(fa, [t for t in ta if t[0] in keep])
            lost = (fb & ~fa).sum() * RES * RES
            print(f"{name} ({zb['net'].split('/')[-1]}, {L}.Cu): fill {fb.sum() * RES * RES:.2f} -> "
                  f"{fa.sum() * RES * RES:.2f} mm2 ({lost:.2f} cut), {len(keep)} terminals")
            for n, _ in tb:
                if n not in keep: print(f"   {n:>18s} no longer on the pour  << narrower"); bad += 1
            for k in sorted(wb, key=lambda k: wb[k]):
                b, a = wb[k], wa.get(k, 0.0)
                flag = "  << narrower" if b > 0 and (a < b * (1 - LOSS) or (a < MIN_W <= b)) else ""
                bad += bool(flag)
                if flag or a != b: print(f"   {k[0]:>18s} - {k[1]:<18s} {b:5.2f} -> {a:5.2f} mm{flag}")
            pics.append((f"{name} {L}", box, fb, fa))
    print(f"{bad} narrower or cut off" if bad else "no pour narrower where it carries current")
    if png and pics:
        S = 4; tiles = []
        for name, box, fb, fa in pics:
            rgb = np.full(fb.shape + (3,), 255, np.uint8)
            rgb[fb & fa] = (150, 180, 220); rgb[fb & ~fa] = (230, 60, 40); rgb[fa & ~fb] = (60, 160, 60)
            im = Image.fromarray(rgb).resize((fb.shape[1] * S // 4, fb.shape[0] * S // 4), Image.NEAREST)
            ImageDraw.Draw(im).text((4, 4), name, fill=(0, 0, 0)); tiles.append(im)
        W = sum(t.width for t in tiles) + 10 * len(tiles); H = max(t.height for t in tiles)
        out = Image.new("RGB", (W, H), "white"); x = 0
        for t in tiles: out.paste(t, (x, 0)); x += t.width + 10
        out.save(png); print(png)
    return bad


if __name__ == "__main__":
    main()
