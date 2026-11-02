"""A router job on a board with parts moved: the moves (placeopt.py's) are applied to job.py's boards, and the test points
listed as free come off the router's board, to go back on after the routing (tpplace.py) where their nets ended up.

    python -m pcbkit.reroute.moves SRC_DIR OUT_DIR MOVES.json        (KiCad's Python)

MOVES: {"moves": {"R2": {"dx": 0.5, "dy": -1.0, "turn": 90}, ...}, "free": ["TP1", ...]}. dx, dy in mm, turn in degrees
counter-clockwise on screen as KiCad counts it, about the part's anchor. A locked part stays where it is, whatever MOVES
says, and so does a free test pad whose net has fewer than two other pads: nothing would be routed for it to sit on. Writes OUT_DIR/in.kicad_pcb (the router's board,
free parts out), full.kicad_pcb (the same with every pour, free parts kept for tpplace.py), hand.kicad_pcb (the original),
job.json (SRC_DIR's, with nets left without two pads taken out), in.json (geom.py of in) and moves.json (MOVES with the
test pads actually freed).
"""
import collections
import json
import os
import shutil
import subprocess
import sys

from ..kicad import save_board


def move(b, moves, free, ground, keep_free):
    import pcbnew
    mm = pcbnew.FromMM
    gnd = [t for t in b.GetTracks() if t.Type() != pcbnew.PCB_VIA_T and t.GetNetname() == ground]
    for fp in list(b.GetFootprints()):
        r = fp.GetReference()
        if r in free and not keep_free:
            b.Remove(fp)
            continue
        m = moves.get(r)
        if not m:
            continue
        for pad in fp.Pads():                   # a kept ground stub goes with the part: the pour reaches it where it lands
            if pad.GetNetname() != ground:
                continue
            box = pad.GetBoundingBox()
            box.Inflate(mm(0.05))
            for t in [t for t in gnd if pad.IsOnLayer(t.GetLayer()) and (box.Contains(t.GetStart()) or box.Contains(t.GetEnd()))]:
                if t.IsLocked():                # placeopt.py doesn't move such a part, but MOVES can be anyone's
                    print(f"!! {r} moved off a locked track of {ground}, which stays", flush=True)
                    continue
                b.Remove(t)
                gnd.remove(t)
        p = fp.GetPosition()
        fp.SetPosition(pcbnew.VECTOR2I(p.x + mm(m.get("dx", 0)), p.y + mm(m.get("dy", 0))))
        if m.get("turn"):
            fp.SetOrientationDegrees(fp.GetOrientationDegrees() + m["turn"])


def main(argv=None):
    import pcbnew
    argv = sys.argv[1:] if argv is None else argv
    src, out, mv = argv[:3]
    one = argv[3] if len(argv) > 3 else None
    os.makedirs(out, exist_ok=True)
    with open(mv) as fh:
        cfg = json.load(fh)
    with open(f"{src}/job.json") as fh:
        job = json.load(fh)
    moves, free, ground = cfg.get("moves", {}), set(cfg.get("free", [])), job.get("ground", "GND")
    if one:                                     # one board per process
        b = pcbnew.LoadBoard(f"{src}/{one}.kicad_pcb")
        move(b, moves, free, ground, one == "full")
        save_board(b, f"{out}/{one}.kicad_pcb")
        return
    b = pcbnew.LoadBoard(f"{src}/in.kicad_pcb")     # the parts that may really go
    locked = {fp.GetReference() for fp in b.GetFootprints() if fp.IsLocked()}
    if locked & (set(moves) | free):
        print(f"!! locked, staying: {sorted(locked & (set(moves) | free))}", flush=True)
    moves, free = {r: m for r, m in moves.items() if r not in locked}, free - locked
    others = collections.Counter(p.GetNetname() for fp in b.GetFootprints() if fp.GetReference() not in free
                                 for p in fp.Pads())
    stay = {fp.GetReference() for fp in b.GetFootprints() if fp.GetReference() in free
            and any(p.GetNetname() and others[p.GetNetname()] < 2 for p in fp.Pads())}
    if stay:
        print(f"staying, nothing else to route on their nets: {sorted(stay)}")
    free -= stay
    mv = f"{out}/moves.json"
    with open(mv, "w") as fh:
        json.dump(dict(cfg, moves=moves, free=sorted(free)), fh, indent=1)
    for name in ("in", "full"):
        subprocess.run([sys.executable, "-m", "pcbkit.reroute.moves", src, out, mv, name], check=True)
    if os.path.abspath(src) != os.path.abspath(out):
        for e in ("kicad_pro", "kicad_dru"):
            for n in ("in", "full", "hand"):
                if os.path.exists(f"{src}/in.{e}"):
                    shutil.copy(f"{src}/in.{e}", f"{out}/{n}.{e}")
        shutil.copy(f"{src}/hand.kicad_pcb", f"{out}/hand.kicad_pcb")
    subprocess.run([sys.executable, "-m", "pcbkit.reroute.geom", f"{out}/in.kicad_pcb", f"{out}/in.json"], check=True)
    with open(f"{out}/in.json") as fh:
        geom = json.load(fh)
    n = collections.Counter(p["net"] for p in geom["pads"])
    gone = [s["net"] for s in job["nets"] if n[s["net"]] < 2]
    job["nets"] = [s for s in job["nets"] if n[s["net"]] >= 2]
    with open(f"{out}/job.json", "w") as fh:
        json.dump(job, fh, indent=1)
    print(f"{len(moves)} parts moved, {len(free)} free; nets left with one pad, not routed: {gone}")


if __name__ == "__main__":
    main()
