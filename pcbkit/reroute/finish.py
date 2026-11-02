"""The finished board: test pads on the spots tpplace.py chose, the routes written in, DRC, and what leads nowhere taken
out: vias joined on one side only (ground stitching vias the routing walled in, a kept via whose other side was
re-routed), tracks with a dead end, and any track of no length. What takes out a connection DRC counted goes back, and
locked copper stays, dead end or not.

    python -m pcbkit.reroute.finish FULL.kicad_pcb TP.json OUT.kicad_pcb [--via=0.45,0.25]        (KiCad's Python)

TP.json is tpplace.py's. Prints DRC's counts by kind. OUT.rpt is the last report.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from ..kicad import KICAD_CLI, save_board

COSMETIC = ("silk_", "lib_", "text_")
SELF = "pcbkit.reroute.finish"


def drc(pcb, save=False):
    """DRC of a board, zones refilled, and written back with save so the board holds the fills DRC saw: (counts by kind,
    cosmetic kinds left out; the report's text)"""
    rpt = Path(pcb).with_suffix(".rpt")
    if rpt.exists():
        rpt.unlink()
    cmd = [KICAD_CLI, "pcb", "drc", "--refill-zones", *(["--save-board"] if save else []), "-o", str(rpt), str(pcb)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not rpt.exists():
        sys.exit(f"kicad-cli wrote no report for {pcb}:\n{r.stdout[-1000:]}{r.stderr[-1000:]}")
    text = rpt.read_text()
    counts = {}
    for k in re.findall(r"^\[(\w+)\]", text, re.M):
        if not k.startswith(COSMETIC):
            counts[k] = counts.get(k, 0) + 1
    return counts, text


def child(mode, *a):
    subprocess.run([sys.executable, "-m", SELF, "--" + mode, *a], check=True, capture_output=True)


def dangling(text):
    """the vias and the tracks DRC finds a dead end on: [(x, y, net)], [(x, y, length, net, layer)]"""
    vias = re.findall(r"\[via_dangling\][^\n]*\n[^\n]*\n\s*@\(([-\d.]+) mm, ([-\d.]+) mm\): Via \[(.*?)\] on ", text)
    tracks = re.findall(r"\[track_dangling\][^\n]*\n[^\n]*\n\s*@\(([-\d.]+) mm, ([-\d.]+) mm\): Track \[(.*?)\] on ([^,\n]+), "
                        r"length ([\d.]+) mm", text)
    return [(float(x), float(y), net) for x, y, net in vias], [(float(x), float(y), float(n), net, L) for x, y, net, L, n in tracks]


def place_pads(board, tpf, out):
    """the test pads on their spots (one board per process, so this runs as a child)"""
    import pcbnew
    b = pcbnew.LoadBoard(board)
    with open(tpf) as fh:
        moves = json.load(fh).get("moves", {})
    for fp in b.GetFootprints():
        m = moves.get(fp.GetReference())
        if m:
            p = fp.GetPosition()
            fp.SetPosition(pcbnew.VECTOR2I(p.x + pcbnew.FromMM(m["dx"]), p.y + pcbnew.FromMM(m["dy"])))
    save_board(b, out)


def drop(board, pts, stubs):
    """the dead ends out (a child too)"""
    import pcbnew
    b = pcbnew.LoadBoard(board)
    mm = pcbnew.ToMM

    def near(p, x, y):
        return abs(mm(p.x) - x) < 0.01 and abs(mm(p.y) - y) < 0.01

    def on(t, net):                             # the name as DRC prints it
        return t.GetNetnameMsg() == f"[{net}]"

    for t in list(b.GetTracks()):
        if t.IsLocked():
            continue
        if t.Type() == pcbnew.PCB_VIA_T:
            if any(near(t.GetPosition(), x, y) and on(t, net) for x, y, net in pts):
                b.Remove(t)
        elif t.GetLength() == 0:
            b.Remove(t)
        elif any((near(t.GetStart(), x, y) or near(t.GetEnd(), x, y)) and abs(mm(t.GetLength()) - n) < 0.01
                 and on(t, net) and t.GetLayerName() == L for x, y, n, net, L in stubs):
            b.Remove(t)
    save_board(b, board)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[0] == "--tp":
        return place_pads(*argv[1:])
    if argv[0] == "--drop":
        return drop(argv[1], json.loads(argv[2]), json.loads(argv[3]))
    args = [a for a in argv if not a.startswith("--")]
    via = [a for a in argv if a.startswith("--via=")]
    full, tpf, out = args[:3]
    base = os.path.splitext(out)[0] + "-base.kicad_pcb"
    child("tp", full, tpf, base)
    routes = os.path.join(tempfile.mkdtemp(), "routes.json")
    with open(tpf) as fh, open(routes, "w") as w:
        json.dump(json.load(fh)["routes"], w)
    subprocess.run([sys.executable, "-m", "pcbkit.reroute.apply", base, routes, out, *via], check=True, capture_output=True)
    for e in ("kicad_pro", "kicad_dru"):
        p = os.path.splitext(full)[0] + "." + e
        if os.path.exists(p):
            for t in (base, out):
                shutil.copy(p, os.path.splitext(t)[0] + "." + e)
    c, text = drc(out, save=True)
    for _ in range(3):                          # a dead branch goes a segment at a time
        pts, stubs = dangling(text)
        if not pts and not stubs:
            break
        keep = out + ".keep"
        shutil.copy(out, keep)
        child("drop", out, json.dumps(pts), json.dumps(stubs))
        c2, text2 = drc(out, save=True)
        if dangling(text2) == (pts, stubs):     # all that's left is locked
            print(f"left in, locked: {pts}, {stubs}")
            os.remove(keep)
            break
        if c2.get("unconnected_items", 0) > c.get("unconnected_items", 0):
            shutil.copy(keep, out)
            c, text = drc(out, save=True)
            print(f"left in, a connection hung on them: {pts}, {stubs}")
            os.remove(keep)
            break
        print(f"out: {len(pts)} vias joined on one side, {len(stubs)} tracks with a dead end")
        os.remove(keep)
        c, text = c2, text2
    print(json.dumps({k: c[k] for k in sorted(c)}))


if __name__ == "__main__":
    main()
