"""A routed board as a router job: the board's signal routing is taken out, what is fixed stays, and each net is routed
again at the width it had.

    python -m pcbkit.reroute.job BOARD.kicad_pcb OUTDIR [--keep=GND,5V] [--keep-w=0.5] [--lift=GND] [--clr=0.2]
                                 [--keepout=x0,y0,x1,y1] [--ground=GND]        (KiCad's Python)

- Nets in --keep (full names, or the last part of a hierarchical one) keep all their copper (tracks, vias); so does every
  track of --keep-w mm or wider (power trunks), with the vias on it, every locked track and via, and every net with a
  track on an inner layer: the router lays F.Cu and B.Cu only. Everything else is taken out, and every net with two pads
  or more that isn't kept is routed, at the width most of its taken-out track had (its class's width when it had none),
  narrowing at a pad as far as the track it had did, down to the board's minimum.
- A board with inner layers pays less for track under an IC (pcbkit_router's costs.py has the cost for two layers, where
  the far side is the ground return).
- The rules come from the board: hole-to-hole, the via most of the taken-out vias had (the Default class's when there are
  none), and the Default class's clearance (--clr overrides it); a net in a class with a wider one than that has its
  class's own. The job's "clr_nets" lists them all, kept nets too: DRC takes the larger of two nets' clearances, so others
  keep clear.
- --lift: the pours of these nets (a 2-layer board's ground pours) are left out of the router's board, so they are no
  wall; the other pours stay, filled, as walls. The rule areas stay, and --keepout adds rectangles (KiCad mm, no tracks or
  vias on either layer), e.g. a module's antenna where the board's own rule area only keeps pours out.
- A line drawn on a solder-mask layer (bare copper on purpose, a pad for a wire) becomes a keep-out for every net but the
  one drawn under it: another net's track there would lie exposed.

Writes OUTDIR/in.kicad_pcb (the router's board), full.kicad_pcb (the same with every pour, for apply.py, DRC and
pictures), hand.kicad_pcb (the board as it was) and job.json (nroute's job, in the router's frame, KiCad - 100 mm).
"""
import collections
import json
import os
import shutil
import sys

from ..kicad import save_board

OFF = 100.0


def short(n):
    return n.split("/")[-1]


def main(argv=None):
    import pcbnew
    argv = sys.argv[1:] if argv is None else argv
    args = [a for a in argv if not a.startswith("--")]
    opt = dict(a[2:].split("=", 1) for a in argv if a.startswith("--") and "=" in a)
    src, out = args[0], args[1]
    os.makedirs(out, exist_ok=True)
    ground = opt.get("ground", "GND")
    keep_nets = set(filter(None, opt.get("keep", ground).split(",")))
    keep_w = float(opt.get("keep-w", 0.5))
    lift = set(filter(None, opt.get("lift", ground).split(",")))
    mm = pcbnew.ToMM
    base = os.path.splitext(src)[0]
    pro = {}
    if os.path.exists(f"{base}.kicad_pro"):
        with open(f"{base}.kicad_pro") as fh:
            pro = json.load(fh)
    classes = {c["name"]: c for c in pro.get("net_settings", {}).get("classes", [])}
    dflt = classes.get("Default", {})
    clr = float(opt.get("clr", dflt.get("clearance", 0.2)))
    rules = pro.get("board", {}).get("design_settings", {}).get("rules", {})
    min_w, hole = rules.get("min_track_width", 0.0), max(0.25, rules.get("min_hole_to_hole", 0.25))

    b = pcbnew.LoadBoard(src)
    F, B_Cu = b.GetLayerID("F.Cu"), b.GetLayerID("B.Cu")
    tracks = [t for t in b.GetTracks() if t.Type() != pcbnew.PCB_VIA_T]
    vias = [t for t in b.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]
    inner = {t.GetNetname() for t in tracks if t.GetLayer() not in (F, B_Cu)}
    net_class = {n.GetNetname(): n.GetNetClassName() for n in b.GetNetsByName().values()}
    held = {n for n in net_class if n in keep_nets or short(n) in keep_nets or n.startswith("unconnected-")} | inner
    kept = {id(t) for t in tracks if t.GetNetname() in held or t.IsLocked() or mm(t.GetWidth()) >= keep_w - 1e-6}

    def on_kept(v):
        """a via on a kept track of its net (a power trunk changing layer)"""
        if v.GetNetname() in held or v.IsLocked():
            return True
        p, r = v.GetPosition(), v.GetWidth(F) / 2
        return any(id(t) in kept and t.GetNetname() == v.GetNetname()
                   and any((e - p).EuclideanNorm() <= r + t.GetWidth() / 2 for e in (t.GetStart(), t.GetEnd()))
                   for t in tracks)

    widths = collections.defaultdict(collections.Counter)
    for t in tracks:
        if id(t) not in kept:
            widths[t.GetNetname()][round(mm(t.GetWidth()), 3)] += mm(t.GetLength())
    gone = [t for t in tracks if id(t) not in kept] + [v for v in vias if not on_kept(v)]
    sizes = collections.Counter((round(mm(v.GetWidth(F)), 3), round(mm(v.GetDrillValue()), 3))
                                for v in gone if v.Type() == pcbnew.PCB_VIA_T)
    via = sizes.most_common(1)[0][0] if sizes else (dflt.get("via_diameter", 0.45), dflt.get("via_drill", 0.25))
    for t in gone:
        b.Remove(t)
    shutil.copy(src, f"{out}/hand.kicad_pcb")
    save_board(b, f"{out}/full.kicad_pcb")
    lifted = [z for z in b.Zones() if not z.GetIsRuleArea() and (z.GetNetname() in lift or short(z.GetNetname()) in lift)]
    for z in lifted:
        b.Remove(z)
    save_board(b, f"{out}/in.kicad_pcb")
    for e in ("kicad_pro", "kicad_dru"):
        if os.path.exists(f"{base}.{e}"):
            for n in ("in", "full", "hand"):
                shutil.copy(f"{base}.{e}", f"{out}/{n}.{e}")

    pads = collections.Counter(p.GetNetname() for fp in b.GetFootprints() for p in fp.Pads() if p.GetNetname())
    nets = []
    for n in sorted(pads):
        if n in held or pads[n] < 2 or n.startswith("unconnected-"):
            continue
        cls = classes.get(net_class.get(n), {})
        w = widths[n].most_common(1)[0][0] if widths[n] else cls.get("track_width", 0.25)
        spec = {"net": n, "w": w, "neck": min(w, 0.2, max(min(widths[n], default=w), min_w))}   # as narrow as the board went
        if cls.get("clearance", 0) > max(clr, dflt.get("clearance", 0)):
            spec["clr"] = cls["clearance"]
        nets.append(spec)
    keep = []
    for r in filter(None, opt.get("keepout", "").split(";")):
        x0, y0, x1, y1 = map(float, r.split(","))
        keep.append({"rect": [x0 - OFF, y0 - OFF, x1 - OFF, y1 - OFF], "layers": "FB", "vias": True, "why": "--keepout"})
    for L, name in (("F", "F.Mask"), ("B", "B.Mask")):
        for d in b.GetDrawings():
            if d.GetLayer() != b.GetLayerID(name):
                continue
            x0, x1 = sorted((mm(d.GetStart().x), mm(d.GetEnd().x)))
            y0, y1 = sorted((mm(d.GetStart().y), mm(d.GetEnd().y)))
            g = mm(d.GetWidth()) / 2
            rect = [x0 - g - OFF, y0 - g - OFF, x1 + g - OFF, y1 + g - OFF]
            lid = b.GetLayerID(f"{L}.Cu")
            own = sorted({short(t.GetNetname()) for t in tracks if t.GetLayer() == lid and id(t) not in kept
                          and rect[0] - 0.2 <= mm(t.GetStart().x) - OFF <= rect[2] + 0.2
                          and rect[1] - 0.2 <= mm(t.GetStart().y) - OFF <= rect[3] + 0.2})
            keep.append({"rect": rect, "layers": L, "vias": False, "except": own, "why": "mask opening"})
    defaults = {"w": 0.25, "neck": 0.2, "clr": clr, "hole_clr": hole}
    if b.GetCopperLayerCount() > 2:
        defaults["under"] = 3.0                 # an inner plane is the return path, not the far side of an IC
    wide = {n: classes[c]["clearance"] for n, c in net_class.items() if n and classes.get(c, {}).get("clearance", 0) > clr}
    job = {"why": f"{os.path.basename(src)}: kept {sorted(keep_nets)} and tracks >= {keep_w} mm; lifted pours {sorted(lift)}",
           "defaults": defaults, "via_size": list(via), "keep": keep, "ground": ground, "soft_pours": [],
           "clr_nets": wide, "nets": nets}
    with open(f"{out}/job.json", "w") as fh:
        json.dump(job, fh, indent=1)
    print(f"via {via}, clearance {clr}, {len(inner)} nets on inner layers kept")
    print(f"took out {len(gone)} tracks and vias, kept {len(kept)} tracks; lifted {len(lifted)} pours; "
          f"{len(nets)} nets to route -> {out}")


if __name__ == "__main__":
    main()
