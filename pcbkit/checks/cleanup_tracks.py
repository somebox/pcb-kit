"""Tidy the track work left by an autorouter import or a grid router.

  pcbkit cleanup BOARD.kicad_pcb [--dry-run] [--dangling]

Unlocked track segments only (scripted power necks are locked and untouched):
  * zero-length segments are removed;
  * exact duplicates (same layer, net, width and end points) are reduced to one;
  * a segment lying entirely within a collinear same-net, same-layer segment of the same or larger width is removed;
  * two collinear segments of the same net, layer and width that meet end to end and continue in the same
    direction are merged into one;
  * with --dangling, router leftovers go too: a segment with an end that touches nothing of its net on its layer
    (no pad, via, other track, or pour outline), whose other neighbours all meet at its other end, so removing it
    never splits a net; such leaves are peeled until none is left, so a dead-end chain goes whole. Net-less copper
    stays.
Nothing changes the copper that connects: every merged or removed piece is covered by what stays. Refill and run
DRC and `pcbkit check` afterwards, as for any board edit. Note that a zero-length segment can be the only thing
bridging a near-miss joint; DRC then reports a dangling track, which is the real defect.

The plan is computed from pcbnew's view of the board and applied to the file text by uuid: BOARD.Remove() on
tracks corrupts the pcbnew bindings mid-run. Harvested from dodeca v1.3's cleanup_tracks.py.
"""
import argparse
import collections
import re
import sys
from pathlib import Path

from ..kicad import ensure_pcbnew

EPS = 1000  # nm: endpoint coincidence


class Seg:
    def __init__(self, t):
        self.uuid = t.m_Uuid.AsString()
        self.layer, self.net, self.width = t.GetLayer(), t.GetNetCode(), t.GetWidth()
        self.a = (t.GetStart().x, t.GetStart().y)
        self.b = (t.GetEnd().x, t.GetEnd().y)
        self.changed = False


def key(p):
    return (round(p[0] / EPS), round(p[1] / EPS))


def collinear(a, b, c):
    cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    length = max(abs(b[0] - a[0]) + abs(b[1] - a[1]), 1)
    return abs(cross) / length < EPS


def within(p, a, b):
    return (min(a[0], b[0]) - EPS <= p[0] <= max(a[0], b[0]) + EPS
            and min(a[1], b[1]) - EPS <= p[1] <= max(a[1], b[1]) + EPS)


def plan(segs):
    """Returns (gone: uuid -> reason, merged: [Seg with new ends])."""
    gone = {}
    seen = set()
    for s in segs:
        a, b = key(s.a), key(s.b)
        if a == b:
            gone[s.uuid] = "zero-length"
            continue
        k = (s.layer, s.net, s.width, min(a, b), max(a, b))
        if k in seen:
            gone[s.uuid] = "duplicate"
        else:
            seen.add(k)
    changed = True
    while changed:
        changed = False
        groups = collections.defaultdict(list)
        for s in segs:
            if s.uuid not in gone:
                groups[(s.layer, s.net)].append(s)
        for group in groups.values():
            for s in group:
                if s.uuid in gone:
                    continue
                for o in group:
                    if o is s or o.uuid in gone or o.width < s.width:
                        continue
                    if (collinear(o.a, o.b, s.a) and collinear(o.a, o.b, s.b)
                            and within(s.a, o.a, o.b) and within(s.b, o.a, o.b)):
                        gone[s.uuid] = "covered"
                        changed = True
                        break
            ends = collections.defaultdict(list)
            for s in group:
                if s.uuid not in gone:
                    ends[key(s.a)].append(s)
                    ends[key(s.b)].append(s)
            for pt, pair in ends.items():
                if len(pair) != 2 or pair[0] is pair[1] or any(x.uuid in gone for x in pair):
                    continue
                s, o = pair
                if s.width != o.width:
                    continue
                far_s = s.b if key(s.a) == pt else s.a
                far_o = o.b if key(o.a) == pt else o.a
                mid = s.a if key(s.a) == pt else s.b
                if not collinear(far_s, far_o, mid) or not within(mid, far_s, far_o):
                    continue
                s.a, s.b, s.changed = far_s, far_o, True
                gone[o.uuid] = "merged"
                changed = True
    return gone, [s for s in segs if s.changed and s.uuid not in gone]


def dangling(board, pcbnew):
    """uuid -> "dangling" for unlocked segments that are leaves: one end touches no other copper of the net on the
    segment's layer (pad, via, track, or a pour's outline, filled or not), and everything else of the net that
    touches the segment also holds its other end, so those pieces stay joined there without it. Removing such a
    segment cannot split a net; they are taken one at a time, so a dead-end chain goes whole."""
    by = collections.defaultdict(list)                 # (net, layer) -> [(kind, item, box)]

    def box(o):
        bb = o.GetBoundingBox()
        return (bb.GetLeft(), bb.GetTop(), bb.GetRight(), bb.GetBottom())
    layers = list(board.GetEnabledLayers().CuStack())
    tracks = [t for t in board.GetTracks() if t.GetNetCode() > 0]
    for t in tracks:
        if t.Type() == pcbnew.PCB_VIA_T:
            for L in layers:
                by[(t.GetNetCode(), L)].append(("via", t, box(t)))
        else:
            by[(t.GetNetCode(), t.GetLayer())].append(("track", t, box(t)))
    for f in board.GetFootprints():
        for q in f.Pads():
            for L in layers:
                if q.GetNetCode() > 0 and q.IsOnLayer(L):
                    by[(q.GetNetCode(), L)].append(("pad", q, box(q)))
    for z in board.Zones():
        if not z.GetIsRuleArea() and z.GetNetCode() > 0:
            for L in z.GetLayerSet().CuStack():
                by[(z.GetNetCode(), L)].append(("zone", z, box(z)))
    shapes = {}

    def shape(kind, o, L):
        k = (id(o), L)
        if k not in shapes:
            shapes[k] = o.Outline() if kind == "zone" else o.GetEffectiveShape() if kind == "track" else o.GetEffectiveShape(L)
        return shapes[k]

    def holds(kind, o, L, pt):
        return o.Outline().Contains(pt) if kind == "zone" else shape(kind, o, L).Collide(pt, 0)

    gone, cut = {}, True
    candidates = [t for t in tracks if t.Type() == pcbnew.PCB_TRACE_T and not t.IsLocked()]
    while cut:
        cut = False
        for t in candidates:
            u = t.m_Uuid.AsString()
            if u in gone:
                continue
            L, tb, ts = t.GetLayer(), box(t), shape("track", t, t.GetLayer())
            touching = [(k, o) for k, o, ob in by[(t.GetNetCode(), L)]
                        if o is not t and not (k == "track" and o.m_Uuid.AsString() in gone)
                        and ob[0] <= tb[2] and tb[0] <= ob[2] and ob[1] <= tb[3] and tb[1] <= ob[3]
                        and (o.Outline().Collide(ts, 0) if k == "zone" else shape(k, o, L).Collide(ts, 0))]
            ends = (t.GetStart(), t.GetEnd())
            for i in (0, 1):
                if not any(holds(k, o, L, ends[i]) for k, o in touching) and \
                        all(holds(k, o, L, ends[1 - i]) for k, o in touching):
                    gone[u] = "dangling"
                    cut = True
                    break
    return gone


def mm(v):
    return f"{v / 1e6:.6f}".rstrip("0").rstrip(".")


def apply(path, gone, merged):
    text = path.read_text()
    block = re.compile(r'\t\(segment\n(?:\t\t[^\n]*\n)*?\t\t\(uuid "([0-9a-f-]+)"\)\n\t\)\n')
    by_uuid = {s.uuid: s for s in merged}
    found = set()

    def edit(m):
        u = m.group(1)
        found.add(u)
        if u in gone:
            return ""
        s = by_uuid.get(u)
        if not s:
            return m.group(0)
        body = m.group(0)
        body = re.sub(r"\(start [^)]*\)", f"(start {mm(s.a[0])} {mm(s.a[1])})", body, count=1)
        body = re.sub(r"\(end [^)]*\)", f"(end {mm(s.b[0])} {mm(s.b[1])})", body, count=1)
        return body

    new = block.sub(edit, text)
    missing = (set(gone) | set(by_uuid)) - found
    if missing:
        sys.exit(f"could not find {len(missing)} segment blocks in the file text, nothing written")
    path.write_text(new)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dangling", action="store_true", help="also remove segments with a free end (router leftovers)")
    a = ap.parse_args(argv)
    ensure_pcbnew()
    import pcbnew
    path = Path(a.board)
    board = pcbnew.LoadBoard(str(path))
    loose = dangling(board, pcbnew) if a.dangling else {}
    segs = [Seg(t) for t in board.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T and not t.IsLocked()
            and t.m_Uuid.AsString() not in loose]
    gone, merged = plan(segs)
    gone.update(loose)
    print(f"{'would change' if a.dry_run else 'changed'}:", dict(collections.Counter(gone.values())) or "nothing")
    if a.dry_run or not gone:
        return
    apply(path, gone, merged)
    check = pcbnew.LoadBoard(str(path))
    print("saved", path, "- segments now", sum(1 for t in check.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T))


if __name__ == "__main__":
    main()
